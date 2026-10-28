import uuid
import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from . import tests as fixtures
from .models import ScriptRun, SavedScript
from .services import save_version
from .builds import start_build, claim_observation, generated, cancel_build
from .guided import update, fragment_statements, assemble, plan, reusable_version, dispatch, review_events
from .views import script_data


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class GuidedTests(TestCase):
    setUp = fixtures.ReplayTests.setUp
    payload = fixtures.ReplayTests.payload
    complete = fixtures.ReplayTests.complete

    def start(self):
        self.script.generation_mode = 'case'
        self.script.generation_context = {'base_url': 'http://127.0.0.1:8765'}
        self.script.save()
        self.worker.runtime_version = 7
        self.worker.save()
        self.build = start_build(self.script, self.user, {
            'worker_id': self.worker.worker_id, 'headless': True,
            'timeout_seconds': 180, 'strategy': 'guided-v1'})
        self.job = claim_observation(self.user, self.worker.worker_id)
        self.identity = {'run_id': self.build.pk, 'lease_token': self.job['lease_token']}
        self.build.refresh_from_db()

    def next(self, seq, outcomes=None):
        with patch('playwright_scripts.guided.plan.delay'), patch('playwright_scripts.guided.finalize.delay'):
            return update(self.user, self.worker.worker_id, 'next', {
                **self.identity, 'seq': seq, 'outcomes': outcomes, 'evidence': {'url': 'http://127.0.0.1:8765'}})

    def command(self, statements, complete=False):
        self.build.refresh_from_db()
        self.build.observation['guided'].update(pending=False, command={
            'seq': 0, 'step_number': 1, 'statements': statements, 'complete': complete})
        self.build.save()

    def test_partial_failure_keeps_only_acknowledged_actions_and_retry_is_idempotent(self):
        self.start()
        self.next(0)
        self.command(['await page.locator("#a").click()', 'await page.locator("#b").click()'])
        self.next(1, [{'ok': True}, {'ok': False, 'error': 'not visible'}])
        self.next(1, [{'ok': True}, {'ok': False, 'error': 'not visible'}])
        self.build.refresh_from_db()
        code = assemble(self.build.observation['guided'], self.script.snapshot)
        self.assertEqual(code.count('locator("#a")'), 1)
        self.assertNotIn('locator("#b")', code)
        self.assertEqual(self.build.observation['guided']['failures'], 1)

    def test_ack_requires_all_statements_and_sequence(self):
        self.start()
        self.next(0)
        self.command(['await page.locator("#a").click()', 'await page.locator("#b").click()'])
        for seq, outcomes in [(2, [{'ok': True}]), (1, [{'ok': True}]), (1, [{'ok': False}, {'ok': True}])]:
            with self.assertRaises(ValidationError):
                self.next(seq, outcomes)

    def test_no_assertion_cannot_advance_and_cancel_fences_late_results(self):
        self.start()
        self.next(0)
        self.command(['await page.locator("#a").click()'], complete=True)
        self.next(1, [{'ok': True}])
        self.build.refresh_from_db()
        self.assertEqual(self.build.observation['guided']['step_index'], 0)
        cancel_build(self.script, self.user)
        self.assertTrue(self.next(2, [{'ok': True}])['finished'])
        self.assertEqual(ScriptRun.objects.count(), 0)
        with self.assertRaises(PermissionDenied):
            update(self.user, self.worker.worker_id, 'heartbeat', {**self.identity, 'lease_token': str(uuid.uuid4())})

    def test_completed_step_can_ack_without_repeating_actions(self):
        self.start()
        self.next(0)
        self.command(['await expect(page.get_by_role("heading")).to_be_visible()'])
        self.next(1, [{'ok': True}])
        with patch('guicase_workbench.tasks.invoke', return_value='{"body":"","parameters":{},"complete":true}'), \
             patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 1)
        self.build.refresh_from_db()
        self.assertEqual(self.build.observation['guided']['command']['statements'], [])
        self.assertTrue(self.next(2, [])['finished'])
        self.build.refresh_from_db()
        self.assertEqual(self.build.status, 'generating')
        self.assertEqual(len(self.build.observation['guided']['accepted']['1']), 2)

    def test_partial_contract_still_rejects_unsafe_code(self):
        for body in ['await page.evaluate("1")', 'await page.locator("#a").click(force=True)',
                     'import os', 'await page.locator("#a").click()\nreturn',
                     'await page.goto(params["missing"])']:
            with self.assertRaises(ValueError):
                fragment_statements(body, 1, {})
        self.assertEqual(fragment_statements('await page.locator("#a").click()', 1, {}),
                         ["await page.locator('#a').click()"])

    def test_practical_generation_keeps_python_locals_between_steps(self):
        self.start()
        self.next(0)
        self.script.snapshot['steps'].append({'step_number': 2, 'description': '确认原值保持', 'expected_result': '字段值不变'})
        self.script.save()
        self.build.refresh_from_db()
        self.build.observation.update(strategy='guided-v3', acceptance='replay_once')
        self.build.observation['guided'].update(step_index=1, accepted={
            '1': ['await page.goto(params["base_url"])', 'before = await page.locator("#x").input_value()'], '2': []})
        self.build.save()
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps({
                'body': 'await expect(page.locator("#x")).to_have_value(before)',
                'parameters': {}, 'complete': True})) as invoke, \
             patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 0)
        context = json.loads(invoke.call_args.args[2])
        self.assertEqual(context['available_variables']['before'], 'scalar')
        self.assertNotIn('obligations', context)
        self.build.refresh_from_db()
        command = self.build.observation['guided']['command']
        self.assertTrue(command['complete'])
        self.assertEqual(command['statements'], ["await expect(page.locator('#x')).to_have_value(before)"])

    def test_broker_failure_ends_generation_without_delivering_draft(self):
        self.start()
        with patch('playwright_scripts.guided.plan.delay', side_effect=RuntimeError('broker offline')):
            dispatch(self.build.pk, self.job['lease_token'], 0)
        self.build.refresh_from_db()
        self.assertEqual(self.build.status, 'error')
        self.script.refresh_from_db()
        self.assertFalse(script_data(self.script)['usable'])
        self.assertTrue(update(self.user, self.worker.worker_id, 'heartbeat', self.identity)['cancelled'])

    def test_review_preserves_intermediate_panel_evidence_without_repeating_whole_page(self):
        button = {'visible': True, 'css': '#open', 'tag': 'button', 'text': 'Choose'}
        panel = {'visible': True, 'css': '#panel', 'tag': 'section', 'text': 'Premium'}
        history = [{'statements': ['open'], 'success': True, 'page_before': {'dom': json.dumps({'nodes': [button]})}},
                   {'statements': ['choose'], 'success': True, 'page_before': {'dom': json.dumps({'nodes': [button, panel]})}}]
        rows = review_events(history)
        self.assertEqual([n['css'] for n in rows[1]['observed_controls']], ['#panel'])
        self.assertNotIn('page_before', rows[0])
        self.assertEqual(rows[1]['statements'], ['choose'])

    def test_model_cannot_change_previously_used_parameter_or_ack_itself(self):
        self.start()
        self.next(0)
        with patch('guicase_workbench.tasks.invoke', return_value='{"body":"await page.goto(params[\\"base_url\\"])","parameters":{"base_url":"http://other.example"},"complete":false}'), \
             patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 0)
            plan(self.build.pk, self.job['lease_token'], 0)
        self.build.refresh_from_db()
        state = self.build.observation['guided']
        self.assertIn('不能更改', state['error'])
        self.assertIsNone(state['command'])
        self.assertEqual(len(state['accepted']['1']), 1)

    def test_repeated_toggle_click_is_repaired_in_same_live_session(self):
        self.start()
        self.next(0)
        self.command(["await page.locator('#open').click()"])
        self.next(1, [{'ok': True}])
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps({
                'body': 'await page.locator("#open").click()', 'parameters': {}, 'complete': False})), \
             patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 1)
        self.build.refresh_from_db()
        state = self.build.observation['guided']
        self.assertTrue(state['pending'])
        self.assertIn('连续重复', state['format_feedback']['error'])
        self.assertEqual(self.build.attempt, 1)
        self.assertEqual(state['accepted']['1'].count("await page.locator('#open').click()"), 1)

    def test_two_independent_runs_required_and_explicit_blockers_not_warning_keywords(self):
        self.start()
        self.build.status = 'generating'
        self.build.generation_token = self.script.generation_token
        self.build.save()
        version = save_version(self.script.pk, fixtures.CODE, {'base_url': 'http://127.0.0.1:8765'},
            origin='guided', warnings=['未实现未要求的登录流程，所有原预期已覆盖。'])
        generated(self.script.pk, self.build.generation_token, version)
        for index in range(2):
            job = self.payload(self.client.post('/api/playwright-worker/claim/', {
                'worker_id': self.worker.worker_id, 'runtime_version': 7, 'supports_headed': True}, format='json'))['job']
            with self.captureOnCommitCallbacks(execute=True):
                self.payload(self.complete(job))
            self.script.refresh_from_db()
            self.assertEqual(script_data(self.script)['usable'], index == 1)
        self.assertEqual(ScriptRun.objects.filter(version=version, status='pass').count(), 2)

    def test_review_failure_retries_inside_generation_and_keeps_frozen_case(self):
        self.start()
        snapshot = self.script.snapshot.copy()
        self.build.status = 'generating'
        self.build.generation_token = self.script.generation_token
        self.build.save()
        version = save_version(self.script.pk, fixtures.CODE, {'base_url': 'http://127.0.0.1:8765'},
            origin='guided', extra_issues=['缺少原用例断言'])
        generated(self.script.pk, self.build.generation_token, version)
        self.build.refresh_from_db()
        self.assertEqual(self.build.status, 'pending_observation')
        self.assertFalse(ScriptRun.objects.exists())
        self.script.refresh_from_db()
        self.assertEqual(self.script.snapshot, snapshot)
        response = self.client.post(self.base+f'{self.script.pk}/run/',
            {'version': version.pk, 'worker_id': self.worker.worker_id}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_reuse_is_across_records_but_never_across_case_revisions(self):
        run = self.payload(self.client.post(self.base+f'{self.script.pk}/run/',
            {'version': self.version.pk, 'worker_id': self.worker.worker_id}, format='json'), 201)
        job = self.payload(self.client.post('/api/playwright-worker/claim/',
            {'worker_id': self.worker.worker_id}, format='json'))['job']
        self.payload(self.complete(job))
        other = SavedScript.objects.create(project=self.project, testcase=self.case, name='New request',
            snapshot=self.script.snapshot, generation_mode='case', generation_context={'base_url':'http://127.0.0.1:8765'})
        self.assertIsNone(reusable_version(other))  # Manual code has no semantic coverage review.
        self.version.origin = 'ai_case'
        self.version.save()
        self.assertEqual(reusable_version(other).pk, self.version.pk)
        other.snapshot = {**other.snapshot, 'revision': 'different'}
        self.assertIsNone(reusable_version(other))
