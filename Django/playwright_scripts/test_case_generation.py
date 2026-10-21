import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from langgraph_integration.models import LLMConfig
from projects.models import Project, ProjectCredential
from testcases.models import TestCase as Case, TestCaseModule, TestCaseStep, TestExecution
from testcases.execution_tracking import snapshot_case
from .models import SavedScript, ScriptWorker
from .tasks import generate_script


CODE = '''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await expect(page.get_by_role("heading", name="Fixture", exact=True)).to_be_visible()
'''


class CaseGenerationTests(TestCase):
    def setUp(self):
        review = patch('playwright_scripts.tasks.semantic_review', return_value=([], []))
        self.quality_review = review.start()
        self.addCleanup(review.stop)
        self.user = User.objects.create_superuser(username='case-script-admin', password='fixture-only')
        self.project = Project.objects.create(name='Fixture', creator=self.user)
        self.credential = ProjectCredential.objects.create(project=self.project, system_url='http://localhost:8765')
        module = TestCaseModule.objects.create(project=self.project, name='Fixture')
        self.case = Case.objects.create(project=self.project, module=module, name='Visit fixture')
        TestCaseStep.objects.create(test_case=self.case, step_number=1, description='Open entry page',
                                    expected_result='Heading Fixture is visible')
        self.model = LLMConfig.objects.create(config_name='case-fixture', name='fixture',
                                              api_url='http://localhost/v1', is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = f'/api/projects/{self.project.pk}/playwright-scripts/'

    def data(self, response, status):
        self.assertEqual(response.status_code, status, response.content[:1000])
        data = response.json()
        return data.get('data', data)

    def create(self):
        with patch('playwright_scripts.tasks.generate_script.delay') as queue, self.captureOnCommitCallbacks(execute=True):
            data = self.data(self.client.post(self.base, {'testcase_id':self.case.pk}, format='json'), 202)
        queue.assert_called_once()
        return SavedScript.objects.get(pk=data['id'])

    def generate(self, script, output=None):
        output = output or {'code': CODE, 'parameters': {'base_url':'https://wrong.example'}, 'blockers':[], 'warnings':[]}
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(output)) as model:
            generate_script(script.pk, str(script.generation_token))
        script.refresh_from_db()
        return model

    def test_direct_generation_freezes_case_without_executing_or_mcp(self):
        script = self.create()
        self.assertEqual(script.snapshot, snapshot_case(self.case))
        self.assertEqual(script.generation_mode, 'case')
        self.assertIsNone(script.source_result_id)
        self.assertEqual(script.events, [])
        call = self.generate(script)
        self.assertEqual(call.call_count, 1)
        evidence = json.loads(call.call_args.args[2])
        self.assertEqual(evidence['context']['base_url'], 'http://localhost:8765')
        self.assertEqual(evidence['context']['generation_date'], timezone.localdate().isoformat())
        self.assertIn('本次不需要先执行用例', call.call_args.args[1])
        version = script.versions.first()
        self.assertEqual(version.origin, 'ai_case')
        self.assertEqual(version.parameters['base_url'], 'http://localhost:8765')
        self.assertEqual(version.issues, [])
        self.assertEqual(script.generation_status, 'ready')
        self.assertEqual(TestExecution.objects.count(), 0)
        data = self.data(self.client.get(self.base+f'{script.pk}/'), 200)
        self.assertFalse(data['latest']['verified'])
        self.assertTrue(data['latest']['warnings'])

    def test_generated_script_uses_existing_worker_and_history(self):
        self.case.precondition='Open http://localhost:8767'; self.case.save()
        script = self.create(); self.generate(script)
        worker = ScriptWorker.objects.create(user=self.user, worker_id='case-worker', heartbeat_at=timezone.now())
        run = self.data(self.client.post(self.base+f'{script.pk}/run/',
            {'version':script.versions.first().pk, 'worker_id':worker.worker_id}, format='json'), 201)
        self.assertEqual(TestExecution.objects.get(pk=run['execution_id']).source, 'playwright_script')
        claim = self.data(self.client.post('/api/playwright-worker/claim/',
            {'worker_id':worker.worker_id, 'supports_headed':True}, format='json'), 200)
        self.assertEqual(claim['job']['code'], CODE)
        self.assertEqual(claim['job']['parameters']['base_url'], 'http://localhost:8767')

    def test_live_build_does_not_repeat_large_historical_snapshots(self):
        script=self.create()
        script.events=[{'tool':'browser_snapshot','output':'old snapshot '*25000}]
        script.save(update_fields=['events'])
        original_events=script.events
        with patch('playwright_scripts.builds.generation_evidence',
                   return_value={'live_page':{'dom':'{"nodes":[]}'}}):
            call=self.generate(script)
        evidence=json.loads(call.call_args.args[2])
        self.assertEqual(evidence['events'],[])
        self.assertEqual(evidence['historical_events']['count'],1)
        self.assertEqual(evidence['case'],snapshot_case(self.case))
        self.assertEqual(script.events,original_events)
        self.assertEqual(script.generation_status,'ready')

    def test_duplicate_click_reuses_pending_task(self):
        script = self.create()
        with patch('playwright_scripts.tasks.generate_script.delay') as queue, self.captureOnCommitCallbacks(execute=True):
            data = self.data(self.client.post(self.base, {'testcase_id':self.case.pk}, format='json'), 200)
        self.assertEqual(data['id'], script.pk)
        self.assertEqual(SavedScript.objects.count(), 1)
        queue.assert_not_called()

    def test_changed_case_gets_new_frozen_task(self):
        script = self.create()
        self.case.steps.update(expected_result='Different heading')
        other = self.create()
        self.assertNotEqual(script.pk, other.pk)
        self.assertNotEqual(script.snapshot['revision'], other.snapshot['revision'])

    def test_regenerate_without_browser_events_preserves_original_snapshot(self):
        script = self.create(); self.generate(script)
        self.case.steps.update(expected_result='Changed later')
        with patch('playwright_scripts.tasks.generate_script.delay') as queue, self.captureOnCommitCallbacks(execute=True):
            self.data(self.client.post(self.base+f'{script.pk}/regenerate/', {}, format='json'), 200)
        queue.assert_called_once()
        script.refresh_from_db()
        call = self.generate(script)
        self.assertEqual(script.versions.count(), 2)
        self.assertEqual(json.loads(call.call_args.args[2])['case']['steps'][0]['expected_result'], 'Heading Fixture is visible')

    def test_missing_url_model_or_steps_fails_before_queuing(self):
        for change in ('url', 'model', 'steps'):
            with self.subTest(change=change):
                if change == 'url': self.credential.system_url=''; self.credential.save()
                if change == 'model': self.model.is_active=False; self.model.save()
                if change == 'steps': self.case.steps.update(expected_result='')
                self.data(self.client.post(self.base, {'testcase_id':self.case.pk}, format='json'), 400)
                self.credential.system_url='http://localhost:8765'; self.credential.save()
                self.model.is_active=True; self.model.save()
        self.assertEqual(SavedScript.objects.count(), 0)

    def test_case_entry_overrides_ambiguous_project_entries(self):
        ProjectCredential.objects.create(project=self.project, system_url='http://localhost:8766')
        self.data(self.client.post(self.base, {'testcase_id':self.case.pk}, format='json'), 400)
        self.case.precondition='Open http://localhost:8767'; self.case.save()
        self.assertEqual(self.create().generation_context['base_url'], 'http://localhost:8767')

    def test_permissions_and_cross_project_case(self):
        other = Project.objects.create(name='Other', creator=self.user)
        self.data(self.client.post(f'/api/projects/{other.pk}/playwright-scripts/',
            {'testcase_id':self.case.pk}, format='json'), 404)
        self.client.force_authenticate(User.objects.create_user('outside-case'))
        self.data(self.client.post(self.base, {'testcase_id':self.case.pk}, format='json'), 403)
        self.assertEqual(SavedScript.objects.count(), 0)

    def test_broker_failure_is_retryable_and_tokens_are_fenced(self):
        with patch('playwright_scripts.tasks.generate_script.delay', side_effect=RuntimeError('broker down')), self.captureOnCommitCallbacks(execute=True):
            data = self.data(self.client.post(self.base, {'testcase_id':self.case.pk}, format='json'), 202)
        script = SavedScript.objects.get(pk=data['id'])
        self.assertEqual(script.generation_status, 'error')
        old_token = str(script.generation_token)
        with patch('playwright_scripts.tasks.generate_script.delay'), self.captureOnCommitCallbacks(execute=True):
            self.data(self.client.post(self.base+f'{script.pk}/regenerate/', {}, format='json'), 200)
        with patch('guicase_workbench.tasks.invoke') as invoke:
            generate_script(script.pk, old_token)
        invoke.assert_not_called()

    def test_unresolved_assertions_and_unsafe_code_remain_blocked(self):
        script = self.create()
        output = {'code':CODE, 'parameters':{}, 'blockers':['步骤 1 缺少关键断言。']}
        self.assertEqual(self.generate(script, output).call_count, 2)
        self.assertEqual(script.generation_status, 'draft')
        self.data(self.client.post(self.base+f'{script.pk}/run/',
            {'version':script.versions.first().pk}, format='json'), 400)

    def test_secrets_not_sent_to_model_and_model_failure_visible(self):
        self.credential.password='fixture-private-secret'; self.credential.save()
        self.case.precondition='Test account password: fixture-private-secret'; self.case.save()
        script = self.create()
        with patch('guicase_workbench.tasks.invoke', side_effect=RuntimeError('provider-secret')) as model:
            generate_script(script.pk, str(script.generation_token))
        self.assertNotIn('fixture-private-secret', model.call_args.args[2])
        script.refresh_from_db()
        self.assertEqual(script.generation_status, 'error')
        self.assertNotIn('provider-secret', script.generation_error)

    def test_semantic_omission_is_repaired_once_then_preserved_as_draft(self):
        self.quality_review.return_value = (['步骤 1 只检查 URL，未验证正常表单。'], [])
        script = self.create()
        self.assertEqual(self.generate(script).call_count, 2)
        self.assertEqual(self.quality_review.call_count, 2)
        self.assertEqual(script.generation_status, 'draft')
        self.assertTrue(script.versions.first().issues)

    def test_locator_hints_only_include_verified_matching_entry(self):
        from .models import ScriptVersion, ScriptRun
        from testcases.models import TestCaseResult
        from .quality import locator_hints
        script = self.create()
        code = CODE + '''        await record.choose_option(trigger=page.get_by_label("Region"), panel=page.get_by_role("listbox"), value=page.get_by_label("Region"), expected_value="Private fixture value", option_name="Private fixture option", label="Region")
        await expect(page.locator("#imaginary-popup")).to_be_hidden()
        await expect(page.locator("#absent-error")).to_have_count(0)
        await record.expect_closed(panel=page.locator("#never-observed"))
'''
        version = ScriptVersion.objects.create(script=script, number=1, code=code, parameters={})
        execution = TestExecution.objects.create(project=self.project, executor=self.user, name='fixture')
        result = TestCaseResult.objects.create(execution=execution, testcase=self.case)
        run = ScriptRun.objects.create(version=version, result=result, status='pass',
            parameters={'base_url':'http://localhost:8765'}, worker_user=self.user)
        hints = locator_hints(script)
        self.assertEqual(hints[0]['run_id'], run.pk)
        self.assertIn('get_by_role', str(hints))
        self.assertNotIn('to_be_visible', str(hints))
        self.assertEqual(hints[0]['interactions'][0]['operation'],'choose_option')
        self.assertIn('listbox',hints[0]['interactions'][0]['roles']['panel'])
        self.assertNotIn('Private fixture',str(hints))  # preserve roles, not another case's data
        for absent in ('imaginary-popup','absent-error','never-observed'):
            self.assertNotIn(absent,str(hints))
        run.parameters = {'base_url':'http://other.test'}; run.save()
        self.assertEqual(locator_hints(script), [])
        run.parameters = {'base_url':'http://localhost:8765'}; run.status = 'fail'; run.save()
        self.assertEqual(locator_hints(script), [])
