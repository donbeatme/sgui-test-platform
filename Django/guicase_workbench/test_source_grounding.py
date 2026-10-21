import copy
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError

from projects.models import Project
from langgraph_integration.models import LLMConfig
from . import grounding as ground
from . import source_grounding as sources
from .models import GenerationJob
from .pipeline import Runner
from .services import configuration
from .views import job_data


def unit(uid, text='显示姓名输入框。'):
    return {'id': uid, 'text': text, 'document': '旅客.pdf', 'pages': [3], 'kind': 'text'}


def fact(quote, text=None):
    return {'kind': 'requirement', 'text': text or quote, 'quote': quote}


def response(uid, facts):
    return {'id': uid, 'facts': facts, 'unknowns': []}


class SourceValidationTests(SimpleTestCase):
    def test_case_sources_are_recovered_only_from_real_evidence(self):
        units = [unit('U1'), unit('U2', '显示保存按钮。')]
        for u in units:
            ground.attach_evidence(u, [fact(u['text'])], [])
        case = {'tc_name': '字段与保存', 'source_unit_ids': ['U1', 'nonexistent'], 'steps': [
            {'desc': '查看字段', 'expected': '显示', 'basis_type': 'document', 'evidence_ids': ['U1-E01', 'U2-E01']}]}
        ground.reconcile_case_sources(case, units)
        self.assertEqual(case['source_unit_ids'], ['U1', 'U2'])
        ground.validate_case_basis(case, units)
        case['source_review'] = {'verdict': 'supported', 'issues': [], 'fingerprint': ground.fingerprint(case)}
        self.assertFalse(ground.confirmation_reasons(case))
        case['source_unit_ids'] = []
        ground.reconcile_case_sources(case, units)
        self.assertEqual(case['source_unit_ids'], ['U1', 'U2'])

    def test_unknown_evidence_is_not_trusted_and_requires_human_confirmation(self):
        units = [unit('U1')]
        ground.attach_evidence(units[0], [fact('显示姓名输入框。')], [])
        case = {'tc_name': '姓名规则', 'source_unit_ids': ['U1'], 'steps': [
            {'seq': 1, 'desc': '输入姓名', 'expected': '至少二十字', 'basis_type': 'document', 'evidence_ids': ['U999-E01']}]}
        ground.reconcile_case_sources(case, units)
        ground.validate_case_basis(case, units)
        case['source_review'] = {'verdict': 'supported', 'issues': [], 'fingerprint': ground.fingerprint(case)}
        self.assertEqual(case['steps'][0]['basis_type'], 'inference')
        self.assertEqual(case['steps'][0]['evidence_refs'], [])
        self.assertEqual(case['steps'][0]['unresolved_evidence_ids'], ['U999-E01'])
        self.assertTrue(ground.confirmation_reasons(case))
        self.assertIn('需人工核实', case['steps'][0]['uncertainty'])

    def test_no_traceable_source_still_cannot_be_fabricated(self):
        case = {'tc_name': '无来源', 'source_unit_ids': ['U999'], 'steps': [{'evidence_ids': ['unknown']} ]}
        with self.assertRaisesMessage(ValidationError, 'U1'):
            ground.reconcile_case_sources(case, [unit('U1')])

    def test_large_catalog_keeps_all_groups_titles_and_original_source(self):
        from .structure import catalog
        groups = [{'id': f'G{i}', 'document': '需求.pdf', 'hint': f'业务{i}',
                   'titles': [f'开头业务{i}', f'尾部业务{i}'], 'preview': '开始规则。' + '明确要求。' * 1600 + '末尾规则。',
                   'context_only': False, 'unit_ids': [f'U{i}']} for i in range(21)]
        frozen = copy.deepcopy(groups)
        encoded = catalog(groups)
        value = json.loads(encoded)
        self.assertLessEqual(len(encoded), 60000)
        self.assertEqual(groups, frozen)
        self.assertEqual([g['id'] for g in value], [g['id'] for g in groups])
        self.assertEqual([g['titles'] for g in value], [g['titles'] for g in groups])
        self.assertTrue(all(g['preview_is_excerpt'] for g in value))
        self.assertTrue(all('开始规则。' in g['preview'] and '末尾规则。' in g['preview'] for g in value))

    def test_one_invalid_quote_does_not_discard_peer_facts_or_units(self):
        units = [unit('U1'), unit('U2', '显示保存按钮。')]
        payload = {'units': [response('U1', [fact('显示姓名输入框。'), fact('姓名必须至少十个字。')]),
                             response('U2', [fact('显示保存按钮。')])]}
        parsed = sources.inspect_response(payload, units)
        self.assertEqual(len(parsed['U1']['facts']), 1)
        self.assertEqual(parsed['U1']['issues'][0]['fact_index'], 2)
        self.assertEqual(parsed['U1']['issues'][0]['pages'], [3])
        self.assertEqual(parsed['U2']['issues'], [])
        self.assertEqual(len(parsed['U2']['facts']), 1)

    def test_whitespace_allowed_but_paraphrase_and_cross_unit_quote_rejected(self):
        units = [unit('U1', '姓名\n输入框。'), unit('U2', '显示保存按钮。')]
        parsed = sources.inspect_response({'units': [response('U1', [
            fact('姓名 输入框。'), fact('输入姓名的框。'), fact('显示保存按钮。')]), response('U2', [])]}, units)
        self.assertEqual(len(parsed['U1']['facts']), 1)
        self.assertEqual(len(parsed['U1']['issues']), 2)
        self.assertIn('姓名', parsed['U1']['issues'][0]['source_excerpt'])
        with self.assertRaises(ValidationError):
            ground.validate_sources({'units': [response('U1', [fact('姓名必填')])]}, units[:1])

    def test_missing_duplicate_malformed_units_are_diagnosed_without_losing_valid(self):
        units = [unit('U1'), unit('U2'), unit('U3')]
        for payload in [None, [], {'units': []}, {'units': [None, {'id': []}, response('U1', []), response('U1', []), response('U2', [fact('显示姓名输入框。')])]}]:
            parsed = sources.inspect_response(payload, units)
            self.assertEqual(set(parsed), {'U1', 'U2', 'U3'})
            self.assertTrue(parsed['U1']['issues'])
        self.assertEqual(len(parsed['U2']['facts']), 1)
        self.assertTrue(parsed['U3']['issues'])

    def test_partial_repair_keeps_verified_facts_and_deduplicates(self):
        u = unit('U1', '显示姓名输入框。显示保存按钮。')
        previous = {'attempts': 1, 'facts': [fact('显示姓名输入框。')], 'unknowns': ['权限待明确']}
        current = {'facts': [fact('显示姓名输入框。'), fact('显示保存按钮。')], 'unknowns': ['权限待明确'], 'issues': []}
        result = sources.accumulate(u, previous, current)
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(len(result['facts']), 2)
        self.assertEqual(result['unknowns'], ['权限待明确'])

    def test_empty_repair_cannot_silently_erase_an_unresolved_quote(self):
        u = unit('U1')
        previous = {'attempts': 1, 'facts': [], 'issues': [sources.issue(u, '引用不存在', fact('姓名必填'))]}
        result = sources.accumulate(u, previous, {'facts': [], 'unknowns': [], 'issues': []})
        self.assertEqual(result['issues'], previous['issues'])


class SourceRecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('source-repair', password='test-only')
        self.project = Project.objects.create(name='source-repair', creator=self.user)
        config = configuration(self.project)
        self.model = LLMConfig.objects.create(config_name='source-repair', name='test', api_url='https://example.invalid/v1', is_active=True, max_retries=0)
        self.job = GenerationJob.objects.create(project=self.project, creator=self.user, status='running',
            options={'grounding_version': 1, 'rules': config.rules, 'prompts': config.prompts},
            progress={'version': 2, 'phase': 'ground', 'units': [unit('U1'), unit('U2', '显示保存按钮。')], 'sources': []})

    def runner(self, invoke):
        self.job.refresh_from_db()
        return Runner(self.job, str(self.job.run_token), invoke)

    def initial_bad(self, *args, **kwargs):
        return json.dumps({'units': [response('U1', [fact('显示姓名输入框。'), fact('姓名必须十个字')]),
                                     response('U2', [fact('显示保存按钮。')])]})

    def test_checkpoint_resume_repairs_only_failed_unit_and_keeps_success(self):
        self.runner(self.initial_bad).ground_sources()
        self.job.refresh_from_db()
        self.assertNotIn('evidence_items', self.job.progress['units'][0])
        saved = copy.deepcopy(self.job.progress['units'][1])
        def repair(config, prompt, content, **kwargs):
            body = json.loads(content)
            self.assertEqual([u['id'] for u in body['units']], ['U1'])
            self.assertEqual(body['repair_feedback'][0]['quote'], '姓名必须十个字')
            self.assertEqual(body['previously_verified'][0]['quote'], '显示姓名输入框。')
            return json.dumps({'units': [{'id': 'U1', 'facts': [], 'unknowns': ['未规定姓名字数限制']}]})
        self.runner(repair).ground_sources()
        self.job.refresh_from_db()
        self.assertEqual(self.job.progress['units'][1], saved)
        self.assertEqual(self.job.progress['units'][0]['evidence_items'][0]['quote'], '显示姓名输入框。')
        self.assertEqual(self.job.progress['source_checks']['U1']['attempts'], 2)
        self.runner(repair).ground_sources()
        self.job.refresh_from_db()
        self.assertEqual(self.job.progress['phase'], 'outline')

    def test_repeated_bad_quote_becomes_visible_warning_and_cannot_be_evidence(self):
        self.runner(self.initial_bad).ground_sources()
        calls = []
        def bad(config, prompt, content, **kwargs):
            calls.append(json.loads(content))
            return json.dumps({'units': [response('U1', [fact('姓名必须十个字')])]})
        self.runner(bad).ground_sources()
        self.runner(bad).ground_sources()
        self.runner(bad).ground_sources()
        self.job.refresh_from_db()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.job.progress['phase'], 'outline')
        self.assertEqual([e['quote'] for e in ground.evidence_index(self.job.progress['units']).values()], ['显示姓名输入框。', '显示保存按钮。'])
        self.assertIn('待人工核对', self.job.progress['units'][0]['unknowns'][0])
        data = job_data(self.job)['progress']['source_extraction']
        self.assertEqual((data['completed'], data['warning_count']), (2, 1))
        self.assertEqual(data['warnings'][0]['issues'][0]['quote'], '姓名必须十个字')
        self.assertEqual(ground.build_coverage(self.job.progress['units'], [])['total'], 2)

    def test_malformed_json_is_bounded_but_network_failure_does_not_consume_repair(self):
        self.runner(self.initial_bad).ground_sources()
        def disconnected(*args, **kwargs):
            raise TimeoutError('secret provider response')
        with self.assertRaises(TimeoutError):
            self.runner(disconnected).ground_sources()
        self.job.refresh_from_db()
        self.assertEqual(self.job.progress['source_checks']['U1']['attempts'], 1)
        for _ in range(2):
            self.runner(lambda *a, **k: '{invalid').ground_sources()
        self.job.refresh_from_db()
        self.assertEqual(self.job.progress['source_checks']['U1']['status'], 'needs_confirmation')
        self.assertEqual(len(self.job.progress['units'][0]['evidence_items']), 1)

    def test_cancellation_during_model_response_cannot_save_extraction(self):
        def cancel(*args, **kwargs):
            GenerationJob.objects.filter(pk=self.job.pk).update(status='cancelled')
            return self.initial_bad()
        with self.assertRaises(InterruptedError):
            self.runner(cancel).ground_sources()
        self.job.refresh_from_db()
        self.assertFalse(self.job.progress.get('source_checks'))
        self.assertFalse(any('evidence_items' in u for u in self.job.progress['units']))

    def test_legacy_completed_units_are_never_reprocessed(self):
        ground.attach_evidence(self.job.progress['units'][0], [fact('显示姓名输入框。')], [])
        self.job.save()
        def reply(config, prompt, content, **kwargs):
            self.assertEqual([u['id'] for u in json.loads(content)['units']], ['U2'])
            return json.dumps({'units': [response('U2', [fact('显示保存按钮。')])]})
        self.runner(reply).ground_sources()
        self.job.refresh_from_db()
        self.assertEqual(sources.summary(self.job.progress)['completed'], 2)
