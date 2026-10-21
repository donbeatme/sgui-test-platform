"""Regression contracts: real images survive generation/review; trust stays server-side."""
import copy
import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from langgraph_integration.models import LLMConfig
from projects.models import Project
from . import grounding as ground
from .models import GenerationJob, CaseExtension
from .services import configuration, normalize_cases, commit_job
from .structure import make_batches
from .tasks import generate_job
from .tests import fixture, ai_reply


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class GroundingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('grounding-test', password='test-only')
        self.project = Project.objects.create(name='Grounding regression', creator=self.user)
        config = configuration(self.project)
        self.text_model = LLMConfig.objects.create(config_name='text', name='text-only', api_url='https://example.invalid/v1', is_active=True)
        self.vision = LLMConfig.objects.create(config_name='vision', name='vision-model', api_url='https://example.invalid/v1', supports_vision=True)
        self.options = {'grounding_version': 1, 'rules': config.rules, 'prompts': config.prompts, 'stages': {'vision': self.vision.pk}}
        buffer = io.BytesIO(); Image.new('RGB', (16, 16), 'white').save(buffer, 'PNG')
        self.path = default_storage.save('tests/login.png', ContentFile(buffer.getvalue()))
        self.api = APIClient(); self.api.force_authenticate(self.user)
        self.base = f'/api/projects/{self.project.pk}/workbench/'

    def create_job(self, images=True):
        return GenerationJob.objects.create(project=self.project, creator=self.user, options=self.options,
            input={'text': '登录页显示账号输入框。', 'images': [{'path': self.path, 'mime': 'image/png', 'name': 'login.png'}] if images else []})

    def fake_model(self, config, prompt, content, **kwargs):
        if ground.VISION_SCHEMA in prompt:
            return json.dumps({'images': [{'name': 'login.png', 'page': '登录', 'facts': [
                {'kind': 'control', 'text': '页面有登录按钮', 'quote': '登录'}], 'unknowns': ['点击后的跳转未知']}]})
        if ground.SOURCE_SCHEMA in prompt:
            return json.dumps({'units': [{'id': u['id'], 'facts': [{'text': u['text'], 'quote': u['text']}], 'unknowns': []}
                                        for u in json.loads(content)['units']]})
        body = content[0]['text'] if isinstance(content, list) else content
        if ground.REVIEW_SCHEMA in prompt:
            cases = json.loads(body.split('\n待评审用例：')[1])
            return json.dumps({'cases': [{'tc_sno': c['tc_sno'], 'verdict': 'supported', 'issues': []} for c in cases], 'uncovered_evidence_ids': []})
        if ground.CASE_BASIS_SCHEMA in prompt:
            units = json.loads(body.split('\n【各模块通用规则】')[0])['materials']
            # Prompt evidence no longer repeats the containing material's
            # provenance; emulate the model using that material's ID/kind.
            unit, item = [(u, e) for u in units for e in u['evidence_items']][-1]
            nature = 'ui_fact' if unit['kind'] == 'vision' else 'document'
            return json.dumps({'cases': [fixture(precondition='打开项目配置的业务入口', source_unit_ids=[unit['id']], steps=[
                {'seq': 1, 'desc': '查看登录页面', 'expected': item['text'], 'basis_type': nature, 'evidence_ids': [item['id']]}])]})
        return ai_reply(config, prompt, body, **kwargs)

    def generate(self, images=True):
        job = self.create_job(images)
        with patch('guicase_workbench.tasks.invoke', side_effect=self.fake_model) as calls:
            generate_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)
        return job, calls

    def test_original_pixels_reach_both_generation_and_review_with_visual_model(self):
        job, calls = self.generate()
        stages = [c for c in calls.call_args_list if ground.CASE_BASIS_SCHEMA in c.args[1] or ground.REVIEW_SCHEMA in c.args[1]]
        self.assertEqual(len(stages), 2)
        for call in stages:
            self.assertEqual(call.args[0].pk, self.vision.pk)
            images = [x for x in call.args[2] if x['type'] == 'image_url']
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(job.cases[0]['source_images'], ['login.png'])
        self.assertFalse(ground.confirmation_reasons(job.cases[0]))
        coverage = job.progress['coverage']['evidence']
        self.assertEqual((coverage['total'], coverage['cited'], coverage['uncovered']), (2, 1, 1))
        self.assertEqual(coverage['unknowns'][0]['questions'], ['点击后的跳转未知'])

    def test_text_only_remains_text_and_every_requirement_quote_is_preserved(self):
        job, calls = self.generate(False)
        self.assertTrue(all(isinstance(c.args[2], str) for c in calls.call_args_list))
        self.assertEqual(job.cases[0]['steps'][0]['evidence_refs'][0]['quote'], '登录页显示账号输入框。')
        self.assertEqual(job.issues['errors'], 0)

    def test_bad_quote_does_not_block_complete_generation_or_become_valid_evidence(self):
        job = self.create_job(False)
        extracts = []
        def respond(config, prompt, content, **kwargs):
            if ground.SOURCE_SCHEMA in prompt:
                units = json.loads(content)['units']
                extracts.append(units)
                return json.dumps({'units': [{'id': u['id'], 'facts': [
                    {'text': u['text'], 'quote': u['text']},
                    {'text': '账号必须至少二十位', 'quote': '账号必须至少二十位'}]} for u in units]})
            return self.fake_model(config, prompt, content, **kwargs)
        with patch('guicase_workbench.tasks.invoke', side_effect=respond):
            generate_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)
        self.assertEqual(len(extracts), 3)
        self.assertTrue(job.cases)
        self.assertEqual(job.progress['source_checks']['U0001']['status'], 'needs_confirmation')
        self.assertEqual(job.progress['coverage']['evidence']['total'], 1)
        self.assertTrue(job.progress['coverage']['evidence']['unknowns'])
        self.assertNotIn('二十位', json.dumps(job.cases[0]['steps'], ensure_ascii=False))

    def test_inference_cannot_be_committed_and_forged_client_confirmation_is_ignored(self):
        job, _ = self.generate()
        case = job.cases[0]
        case['steps'][0].update(basis_type='inference', uncertainty='点击后是否跳转首页未知')
        job.save()
        with self.assertRaises(ValidationError):
            commit_job(job.pk, self.project, self.user, job.revision)
        payload = copy.deepcopy(job.cases)
        payload[0]['human_review'] = {'user_id': self.user.pk, 'note': '伪造确认', 'fingerprint': ground.fingerprint(case)}
        payload[0]['steps'][0].update(basis_type='ui_fact', uncertainty='')
        response = self.api.post(self.base+f'jobs/{job.pk}/draft/', {'cases': payload, 'revision': job.revision}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertNotIn('human_review', job.cases[0])
        self.assertTrue(ground.confirmation_reasons(job.cases[0]))
        response = self.api.post(self.base+f'jobs/{job.pk}/confirm-case/', {'tc_sno': case['tc_sno'], 'revision': job.revision,
            'note': '已向需求负责人确认点击后进入首页，并补充此规则。'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertFalse(ground.confirmation_reasons(job.cases[0]))
        self.assertEqual(job.cases[0]['human_review']['user_id'], self.user.pk)
        saved = commit_job(job.pk, self.project, self.user, job.revision)
        self.assertEqual(len(saved.saved_ids), 1)
        extension = CaseExtension.objects.get(job=job)
        self.assertIn('需求负责人', extension.original['human_review']['note'])

    def test_edit_invalidates_prior_confirmations_and_can_be_reconfirmed_after_commit(self):
        job, _ = self.generate(False)
        ground.confirm_case(job.cases[0], self.user, '确认当前规则与原需求一致。')
        job.save(); saved = commit_job(job.pk, self.project, self.user, job.revision)
        extension = CaseExtension.objects.get(job=job)
        step = extension.case.steps.get(); step.expected_result = '修改后的真实业务规则'; step.save()
        from testcases.preflight import case_material
        current, _ = case_material(extension.case)
        self.assertTrue(ground.confirmation_reasons(current))
        response = self.api.post(self.base+f'jobs/{job.pk}/confirm-case/', {'tc_sno': current['tc_sno'], 'revision': saved.revision,
            'note': '根据最新需求记录，已核实修改后的预期。'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        current, _ = case_material(extension.case)
        self.assertFalse(ground.confirmation_reasons(current))

    def test_missing_original_and_fabricated_document_quote_fail_closed(self):
        job = self.create_job()
        with self.assertRaises(ValidationError):
            ground.multimodal_content(job, 'Review', ['missing.png'])
        with self.assertRaises(ValidationError):
            ground.validate_sources({'units': [{'id': 'U1', 'facts': [{'text': '密码至少 8 位', 'quote': '密码至少 8 位'}]}]}, [{'id': 'U1', 'text': '页面有密码框'}])

    def test_image_limit_splits_whole_units_without_dropping_images(self):
        units = [{'id': f'U{i}', 'text': '图片事实', 'source_images': [f'{i}.png']} for i in range(13)]
        groups = [{'id': 'G1', 'unit_ids': [u['id'] for u in units]}]
        batches = make_batches(units, groups, {'modules': [{'id': 'M1', 'group_ids': ['G1']}]}, max_images=6)
        self.assertEqual([len(b['unit_ids']) for b in batches], [6, 6, 1])
        self.assertEqual([u for b in batches for u in b['unit_ids']], [u['id'] for u in units])

    def test_import_cannot_reuse_another_instances_confirmation(self):
        job, _ = self.generate(False)
        ground.confirm_case(job.cases[0], self.user, '之前已核实过此项明确规则。')
        response = self.api.post(self.base+'import/', {'payload': job.cases}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        imported = GenerationJob.objects.exclude(pk=job.pk).get()
        self.assertTrue(ground.confirmation_reasons(imported.cases[0]))
        self.assertNotIn('human_review', imported.cases[0])

    def test_scope_survives_module_splitting_and_is_not_counted_as_a_missing_test(self):
        from .structure import batch_context
        unit = {'id': 'U1', 'kind': 'text', 'document': 'requirements', 'text': '只测试按钮文字', 'pages': []}
        image = {'id': 'U2', 'kind': 'vision', 'document': 'login.png', 'text': '存在按钮', 'pages': []}
        ground.attach_evidence(unit, [{'kind': 'scope', 'text': '只测试按钮文字', 'quote': '只测试按钮文字'}], [])
        ground.attach_evidence(image, [{'kind': 'control', 'text': 'Login 按钮', 'quote': 'Login'}], [])
        plan = {'title': '登录页', 'modules': [{'id': 'M1', 'path': '登录', 'description': '按钮'}]}
        content = json.loads(batch_context({'module_id': 'M1', 'unit_ids': ['U2']}, [unit, image], plan))
        self.assertEqual(content['global_scope'][0]['quote'], '只测试按钮文字')
        coverage = ground.build_coverage([unit, image], [])
        self.assertEqual((coverage['total'], coverage['uncovered']), (1, 1))

    def test_document_and_image_can_jointly_support_one_assertion(self):
        units = [{'id': 'U1', 'kind': 'text', 'document': '需求'}, {'id': 'U2', 'kind': 'vision', 'document': '截图'}]
        for unit in units:
            ground.attach_evidence(unit, [{'text': '显示 Login', 'quote': 'Login'}], [])
        case = normalize_cases([fixture(source_unit_ids=['U1', 'U2'])])[0]
        case['steps'][0].update(basis_type='document', evidence_ids=['U1-E01', 'U2-E01'])
        ground.validate_case_basis(case, units)
        self.assertEqual(case['steps'][0]['basis_type'], 'document')
        self.assertEqual(case['steps'][0]['uncertainty'], '')

    def test_background_question_requires_review_reason_and_cannot_clear_inference(self):
        job, _ = self.generate(False)
        case = job.cases[0]
        case['open_questions'] = ['点击按钮之后跳转何处未知，本条不点击']
        payload = {'cases': [{'tc_sno': case['tc_sno'], 'verdict': 'supported', 'issues': [],
            'non_blocking_questions': [{'text': case['open_questions'][0], 'reason': '本条只查看静态文字，没有执行点击或断言跳转。'}]}], 'uncovered_evidence_ids': []}
        review = ground.validate_review(payload, [case], ground.evidence_index(job.progress['units']))
        case['source_review'] = {**review['cases'][case['tc_sno']], 'fingerprint': ground.fingerprint(case)}
        self.assertFalse(ground.confirmation_reasons(case))
        case['steps'][0]['basis_type'] = 'inference'
        self.assertTrue(ground.confirmation_reasons(case))
        payload['cases'][0]['non_blocking_questions'][0]['text'] = '模型编造的新问题'
        with self.assertRaises(ValidationError):
            ground.validate_review(payload, [case], {})
