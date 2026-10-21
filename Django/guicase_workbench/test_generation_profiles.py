import json
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from .generation_profiles import validate_profile, profile_for
from .structure import prompt_material, batch_context, make_batches
from . import tests as workbench_tests
from .models import GenerationJob
from .tasks import generate_job


class ProfileMaterialTests(SimpleTestCase):
    def scene_fixture(self):
        units = [dict(id='U1', kind='text', document='需求', text='空城市应被拦截，修正后恢复查询。'),
                 dict(id='U2', kind='vision', document='界面', text='画面', source_images=['screen.jpg']),
                 dict(id='U3', kind='text', document='公共约束', text='不创建订单。')]
        groups = [{'id': 'G1', 'unit_ids': ['U1']}, {'id': 'G2', 'unit_ids': ['U2']}, {'id': 'G3', 'unit_ids': ['U3']}]
        plan = {'title': '场景', 'shared_group_ids': ['G3'], 'modules': [{'id': 'M1', 'path': '场景', 'description': '查询与恢复', 'group_ids': ['G1', 'G2']}]}
        return units, groups, plan

    def test_scene_keeps_text_images_and_shared_sources_citable_together(self):
        units, groups, plan = self.scene_fixture()
        batches = make_batches(units, groups, plan, budget=5000, max_images=6, atomic=True)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]['unit_ids'], ['U1', 'U2', 'U3'])
        context = json.loads(batch_context(batches[0], units, plan))
        self.assertEqual({m['id'] for m in context['materials']}, {'U1', 'U2', 'U3'})
        self.assertEqual(context['materials'][2]['text'], '不创建订单。')

    def test_large_scene_rejected_without_splitting_or_truncation(self):
        units, groups, plan = self.scene_fixture()
        units[0]['text'] = '完整原文' * 1500
        with self.assertRaisesMessage(ValidationError, '未被分开或截断'):
            make_batches(units, groups, plan, budget=4000, max_images=6, atomic=True)
        self.assertEqual(len(units[0]['text']), 6000)
        units[0]['text'] = '需求'
        units[1]['source_images'] = [f'{i}.jpg' for i in range(7)]
        with self.assertRaisesMessage(ValidationError, '最多 6 张'):
            make_batches(units, groups, plan, budget=5000, max_images=6, atomic=True)

    def test_scene_has_task_wide_limit_without_changing_legacy_default(self):
        self.assertEqual(validate_profile('scenario'), 'scenario')
        profile = profile_for({'generation_profile': 'scenario'})
        self.assertEqual(profile['max_modules'] * profile['batch_limit'], 2)
        self.assertTrue(profile['atomic'])
        self.assertFalse(profile_for({}).get('atomic', False))

    def test_compaction_removes_only_repeated_provenance_from_prompt(self):
        evidence = {'id': 'U1-E1', 'kind': 'requirement', 'text': '修正后恢复查询',
                    'quote': '选择上海后再次搜索应恢复查询。', 'unit_id': 'U1',
                    'document': '业务需求.md', 'pages': [2], 'source_images': [], 'basis_type': 'document'}
        unit = {'id': 'U1', 'kind': 'text', 'document': '业务需求.md', 'pages': [2],
                'text': evidence['quote'], 'evidence_items': [evidence]}
        material = prompt_material(unit)
        self.assertEqual(material['document'], '业务需求.md')
        self.assertEqual(material['pages'], [2])
        self.assertEqual(material['evidence_items'][0], {k: evidence[k] for k in ('id', 'kind', 'text', 'quote')})
        self.assertEqual(unit['evidence_items'][0]['unit_id'], 'U1')

    def test_resume_reuses_validated_plan_after_context_budget_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from .pipeline import Runner
        units, groups, plan = self.scene_fixture()
        job = SimpleNamespace(options={'generation_profile': 'scenario'}, notes=[],
                              progress={'units': units, 'groups': groups, 'plan': plan})
        runner = Runner(job, 'token', Mock())
        runner.stage, runner.save = Mock(), Mock()
        runner.json_call = Mock(side_effect=AssertionError('should not pay for the same plan again'))
        with patch('guicase_workbench.tasks.resolve_model', return_value=SimpleNamespace(context_limit=128000)):
            runner.plan()
        runner.json_call.assert_not_called()
        self.assertEqual(len(runner.state['batches']), 1)
        self.assertEqual(runner.state['batches'][0]['unit_ids'], ['U1', 'U2', 'U3'])
        self.assertEqual(runner.state['shared_batches'], [])

    def test_compact_visual_context_keeps_evidence_exclusions_and_raw_checkpoint(self):
        unit = {'id': 'U1', 'kind': 'vision', 'document': 'screen', 'text': 'duplicated raw control facts',
                'source_images': ['screen.jpg'], 'controls': [{'name': 'ad', 'text': '广告', 'excluded': True}],
                'evidence_items': [{'id': 'U1-C1', 'text': '搜索按钮', 'bbox': [0, 0, .1, .1]}], 'unknowns': ['提交后的结果未知']}
        compact = prompt_material(unit)
        self.assertEqual(compact['evidence_items'], unit['evidence_items'])
        self.assertEqual(compact['unknowns'], unit['unknowns'])
        self.assertEqual(compact['excluded_controls'][0]['name'], 'ad')
        self.assertEqual(unit['text'], 'duplicated raw control facts')
        self.assertNotEqual(compact['text'], unit['text'])
        self.assertEqual(prompt_material({**unit, 'kind': 'prose'})['text'], unit['text'])

    def test_image_budget_and_actual_prompt_size_are_respected(self):
        units = [{'id': f'U{i}', 'kind': 'vision', 'text': 'short', 'source_images': [f'{i}.jpg'], 'evidence_items': [{'text': 'x' * 3000}]} for i in range(3)]
        groups = [{'id': 'G1', 'unit_ids': [u['id'] for u in units]}]
        plan = {'modules': [{'id': 'M1', 'group_ids': ['G1']}]}
        batches = make_batches(units, groups, plan, budget=4000, max_images=6)
        self.assertEqual(len(batches), 3)
        self.assertEqual([u for b in batches for u in b['unit_ids']], ['U0', 'U1', 'U2'])

    def test_unknown_profile_rejected_and_old_jobs_keep_default(self):
        with self.assertRaises(ValidationError):
            validate_profile('disable_review')
        self.assertEqual(profile_for({})['max_modules'], 16)

    def test_provider_errors_explain_action_without_exposing_response(self):
        from .pipeline import safe_error
        for code in (402, 401, 403, 429):
            error = RuntimeError('private provider response')
            error.status_code = code
            text = safe_error(error, '图片解析')
            self.assertIn(str(code), text)
            self.assertNotIn('private', text)


class ProfilePipelineTests(TestCase):
    setUp = workbench_tests.WorkbenchTests.setUp
    model = workbench_tests.WorkbenchTests.model
    def test_excess_scene_images_rejected_before_upload_or_model_calls(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.model()
        images = [SimpleUploadedFile(f'{i}.png', b'not-yet-read', content_type='image/png') for i in range(7)]
        with patch('guicase_workbench.views.default_storage.save') as save, patch('guicase_workbench.views.generate_job.delay') as enqueue:
            response = self.api.post(self.base + 'generate/', {'generation_profile': 'scenario', 'images': images}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('6 张', str(response.data))
        self.assertFalse(GenerationJob.objects.exists())
        save.assert_not_called()
        enqueue.assert_not_called()

    def test_selected_profile_is_frozen_and_used_by_planning_and_generation(self):
        self.model()
        with patch('guicase_workbench.views.generate_job.delay'):
            response = self.api.post(self.base + 'generate/', {'text': '登录成功后进入首页。', 'generation_profile': 'quick'})
        self.assertEqual(response.status_code, 201, response.data)
        job = GenerationJob.objects.get()
        self.assertEqual(job.options['generation_profile'], 'quick')
        with patch('guicase_workbench.tasks.invoke', side_effect=workbench_tests.ai_reply) as invoke:
            generate_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)
        prompts = [c.args[1] for c in invoke.call_args_list]
        self.assertTrue(any('测试架构师' in p and '快速试生成' in p for p in prompts))
        self.assertTrue(any('本次仅生成当前模块' in p and '最多 3 条' in p for p in prompts))
        self.assertEqual(len(job.progress['model_calls']), invoke.call_count)
        self.assertTrue(job.progress['source_reviews'])
