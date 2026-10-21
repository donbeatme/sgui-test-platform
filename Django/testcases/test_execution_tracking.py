"""Regression tests for execution evidence, finalization, API statistics and SSE."""
import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from projects.models import Project, ProjectMember
from .models import TestCase as Case, TestCaseModule, TestCaseStep, TestCaseScreenshot, TestCaseResult, TestExecution, TestSuite
from .execution_tracking import begin_execution, finish_execution, cancel_execution, parse_report, screenshot_result, snapshot_case
from .execution_stream import track_case_stream


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class ExecutionTrackingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='execution-test', password='test-only')
        self.project = Project.objects.create(name='Execution regression', creator=self.user)
        ProjectMember.objects.get_or_create(project=self.project, user=self.user, defaults={'role': 'owner'})
        self.module = TestCaseModule.objects.create(project=self.project, name='Local fixture')
        self.case = Case.objects.create(project=self.project, module=self.module, name='Login form', precondition='http://localhost/login')
        TestCaseStep.objects.create(test_case=self.case, step_number=1, description='Open login form', expected_result='Login button is visible')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = f'/api/projects/{self.project.pk}/test-executions/'
        self.index = 0

    def begin(self):
        self.index += 1
        return begin_execution(self.project, self.user, self.case.pk, f'session-{self.index}')

    def report(self, run, status='pass', number=1):
        shot = TestCaseScreenshot.objects.create(test_case=self.case, execution_result=run, step_number=number, screenshot=f'tests/run-{run.pk}-{number}.png')
        return {'status': status, 'summary': 'Observed the fixture', 'steps': [{'step_number': number, 'status': status, 'expected': 'Login button is visible', 'actual': 'Login button text observed on page', 'screenshot_ids': [shot.pk]}]}

    def payload(self, response):
        self.assertEqual(response.status_code, 200, response.content[:500])
        data = response.json()
        return data.get('data', data) if isinstance(data, dict) else data

    def test_four_outcomes_history_and_dashboard_agree(self):
        first = self.begin(); finish_execution(first.pk, self.report(first))
        second = self.begin(); finish_execution(second.pk, self.report(second, 'fail'))
        third = self.begin(); finish_execution(third.pk, parse_report('Everything is fine, no errors.'))
        fourth = self.begin()
        with patch('orchestrator_integration.stop_signal.set_stop_signal'):
            cancel_execution(fourth.execution_id)
        records = list(TestExecution.objects.order_by('id'))
        self.assertEqual([r.status for r in records], ['completed', 'failed', 'blocked', 'cancelled'])
        self.assertEqual([r.pass_rate for r in records], [100, 0, 0, 0])
        listing = self.payload(self.client.get(self.base))
        rows = listing.get('results', []) if isinstance(listing, dict) else listing
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['source'] for r in rows}, {'ai_single'})
        stats = self.payload(self.client.get(f'/api/projects/{self.project.pk}/statistics/'))
        self.assertEqual(stats['executions']['case_results'], {'total': 4, 'passed': 1, 'failed': 1, 'blocked': 1, 'cancelled': 1, 'skipped': 0, 'error': 0})
        detail = self.payload(self.client.get(f'{self.base}{first.execution_id}/report/'))
        self.assertIsNone(detail['suite']['id'])
        self.assertEqual(detail['results'][0]['step_results'][0]['status'], 'pass')

    def test_missing_steps_cannot_pass(self):
        TestCaseStep.objects.create(test_case=self.case, step_number=2, description='Click login', expected_result='Validation appears')
        run = self.begin()
        result = finish_execution(run.pk, self.report(run))
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(result.step_results[1]['status'], 'not_run')

    def test_previous_run_evidence_is_rejected(self):
        old = self.begin(); old_report = self.report(old); finish_execution(old.pk, old_report)
        new = self.begin(); result = finish_execution(new.pk, old_report)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(result.screenshots, [])

    def test_wrong_step_evidence_is_rejected(self):
        run = self.begin(); report = self.report(run)
        run.evidence.update(step_number=2)
        self.assertEqual(finish_execution(run.pk, report).status, 'blocked')

    def test_changed_expected_result_is_rejected(self):
        run = self.begin(); report = self.report(run)
        report['steps'][0]['expected'] = 'Whatever the page happens to show'
        self.assertEqual(finish_execution(run.pk, report).status, 'blocked')

    def test_duplicate_or_invented_steps_are_rejected(self):
        for mode in ['duplicate', 'invented']:
            with self.subTest(mode=mode):
                run = self.begin(); report = self.report(run)
                if mode == 'duplicate': report['steps'].append(dict(report['steps'][0]))
                else: report['steps'][0]['step_number'] = 999
                self.assertEqual(finish_execution(run.pk, report).status, 'blocked')

    def test_cancelled_result_is_immutable_but_late_log_is_retained(self):
        run = self.begin(); report = self.report(run)
        with patch('orchestrator_integration.stop_signal.set_stop_signal'):
            cancel_execution(run.execution_id)
        result = finish_execution(run.pk, report, log='Tool browser_click ran before cancellation')
        self.assertEqual(result.status, 'cancelled')
        self.assertIn('browser_click', result.execution_log)
        execution = TestExecution.objects.get(pk=run.execution_id)
        self.assertEqual((execution.passed_count, execution.cancelled_count), (0, 1))

    def test_snapshot_survives_case_edit(self):
        run = self.begin(); report = self.report(run)
        self.case.name = 'Renamed later'; self.case.save()
        self.case.steps.update(expected_result='Edited later')
        result = finish_execution(run.pk, report)
        self.assertEqual(result.status, 'pass')
        self.assertEqual(result.case_snapshot['name'], 'Login form')

    def test_duplicate_finalization_does_not_double_count(self):
        run = self.begin(); report = self.report(run)
        finish_execution(run.pk, report); finish_execution(run.pk, report)
        self.assertEqual(TestExecution.objects.get(pk=run.execution_id).passed_count, 1)

    def test_system_error_and_unstructured_output_are_not_passes(self):
        for output in ['SUCCESS', '{"status":"pass"}', '```json\n{"steps": []}\n```']:
            self.assertIsNone(parse_report(output))
        run = self.begin()
        result = finish_execution(run.pk, forced='error', reason='Provider unavailable')
        self.assertEqual(result.status, 'error')
        self.assertEqual(TestExecution.objects.get(pk=run.execution_id).error_count, 1)

    def test_suite_reuses_pending_result_with_null_session(self):
        suite = TestSuite.objects.create(project=self.project, name='Suite')
        suite.testcases.add(self.case)
        execution = TestExecution.objects.create(suite=suite, executor=self.user, total_count=1)
        result = TestCaseResult.objects.create(execution=execution, testcase=self.case, case_snapshot=snapshot_case(self.case))
        run = begin_execution(self.project, self.user, self.case.pk, 'suite-session', result.pk)
        self.assertEqual(run.pk, result.pk)
        self.assertEqual(TestExecution.objects.count(), 1)
        finish_execution(run.pk, self.report(run))
        execution.refresh_from_db()
        self.assertEqual((execution.status, execution.passed_count), ('completed', 1))

    def test_result_cannot_be_reused_by_other_user_or_project(self):
        run = self.begin()
        other_user = User.objects.create_user('other')
        other_project = Project.objects.create(name='Other project')
        for project, user in [(self.project, other_user), (other_project, self.user)]:
            with self.subTest(project=project.pk, user=user.pk), self.assertRaises(ValidationError):
                begin_execution(project, user, self.case.pk, 'forged-session', run.pk)

    def test_foreign_project_api_and_record_edit_are_rejected(self):
        run = self.begin()
        other = Project.objects.create(name='Other project')
        self.assertEqual(self.client.get(f'/api/projects/{other.pk}/test-executions/{run.execution_id}/report/').status_code, 404)
        self.assertEqual(self.client.patch(f'{self.base}{run.execution_id}/', {'passed_count': 99}, format='json').status_code, 405)

    def test_upload_binds_only_the_active_attempt(self):
        run = self.begin()
        buffer = io.BytesIO(); Image.new('RGB', (2, 2), 'blue').save(buffer, format='PNG')
        name = f'run{run.pk}-{run.evidence_token}-step1.png'
        upload = SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')
        response = self.client.post(f'/api/projects/{self.project.pk}/testcases/{self.case.pk}/upload-screenshots/', {'screenshots': upload, 'step_number': 1}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(run.evidence.count(), 1)
        finish_execution(run.pk, forced='blocked')
        with self.assertRaises(ValidationError): screenshot_result(self.case, name, 1)
        self.assertIsNone(screenshot_result(self.case, 'legacy.png', 1))

    def test_pending_suite_cancellation_counts_every_scheduled_case(self):
        suite = TestSuite.objects.create(project=self.project, name='Queued suite')
        suite.testcases.add(self.case)
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(self.base, {'suite_id': suite.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        execution = TestExecution.objects.get(suite=suite)
        with patch('orchestrator_integration.stop_signal.set_stop_signal'):
            cancel_execution(execution.pk)
        execution.refresh_from_db()
        self.assertEqual((execution.total_count, execution.cancelled_count), (1, 1))

    def collect_stream(self, run, events, expected):
        @track_case_stream
        async def fake(_self, request):
            for event in events:
                yield 'data: ' + json.dumps(event) + '\n\n'
            yield 'data: [DONE]\n\n'
        async def collect():
            output = []
            async for chunk in fake(None, SimpleNamespace(execution_result_id=run.pk)):
                if '"type": "complete"' in chunk or '"type": "error"' in chunk:
                    state = await sync_to_async(lambda: TestCaseResult.objects.get(pk=run.pk).status)()
                    self.assertEqual(state, expected, 'Terminal SSE must follow database finalization')
                output.append(chunk)
            return output
        return async_to_sync(collect)()

    def test_sse_complete_is_emitted_after_result_is_persisted(self):
        run = self.begin(); report = self.report(run)
        self.collect_stream(run, [{'type': 'case_final_response', 'text': json.dumps({'execution_result': report})}, {'type': 'complete'}], 'pass')
        run.refresh_from_db(); self.assertEqual(run.status, 'pass')

    def test_sse_error_is_persisted_before_client_can_disconnect(self):
        run = self.begin()
        self.collect_stream(run, [{'type': 'error', 'message': 'Model service unavailable'}], 'error')
        run.refresh_from_db(); self.assertEqual(run.status, 'error')

    def test_guard_report_preserves_cause_without_fabricating_passes(self):
        run = self.begin()
        report = {'execution_result': {'status': 'blocked', 'summary': 'browser_click: city-picker-tabs intercepts pointer events', 'steps': []}}
        self.collect_stream(run, [
            {'type': 'tool_result', 'tool_name': 'browser_click', 'tool_output': [{'type': 'text', 'text': '### Error\ncity-picker-tabs intercepts pointer events'}]},
            {'type': 'case_final_response', 'text': json.dumps(report)}, {'type': 'complete'}], 'blocked')
        run.refresh_from_db()
        self.assertIn('intercepts pointer events', run.error_message)
        self.assertIn('工具错误详情', run.execution_log)
        self.assertEqual(run.step_results[0]['status'], 'not_run')
        self.assertNotIn('缺少有效的结构化', run.error_message)

    def test_sse_disconnection_does_not_leave_a_running_result(self):
        run = self.begin()
        @track_case_stream
        async def fake(_self, request):
            yield 'data: {"type":"start"}\n\n'
            raise asyncio.CancelledError()
        async def consume():
            try:
                async for _ in fake(None, SimpleNamespace(execution_result_id=run.pk)): pass
            except asyncio.CancelledError: pass
        async_to_sync(consume)()
        run.refresh_from_db(); self.assertEqual(run.status, 'cancelled')

    def test_real_agent_endpoint_records_configuration_failure(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        from langgraph_integration.models import LLMConfig
        # No external model calls: fail at the real endpoint's config validation.
        LLMConfig.objects.all().delete()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')
        with patch('orchestrator_integration.agent_loop_view.clear_stop_signal', return_value=False):
            response = client.post('/api/orchestrator/agent-loop/', {
                'project_id': self.project.pk, 'test_case_id': self.case.pk,
                'session_id': 'no-model-config', 'message': '执行这条用例', 'stream': True
            }, format='json')
        self.assertEqual(response.status_code, 200)
        async def consume():
            return [chunk async for chunk in response.streaming_content]
        async_to_sync(consume)()
        run = TestCaseResult.objects.get(mcp_session_id='no-model-config')
        self.assertEqual(run.status, 'blocked')
        self.assertIn('模型配置', run.error_message)

    def test_server_pagination_and_status_filter_include_older_attempts(self):
        for _ in range(3):
            run = self.begin(); finish_execution(run.pk, forced='blocked')
        passed = self.begin(); finish_execution(passed.pk, self.report(passed))
        payload = self.payload(self.client.get(self.base, {'status': 'blocked', 'page_size': 2, 'page': 2}))
        self.assertEqual(payload['count'], 3)
        self.assertEqual(len(payload['results']), 1)
        self.assertEqual(payload['results'][0]['status'], 'blocked')

from django.test import TransactionTestCase

class SuiteDispatchTests(TransactionTestCase):
    def test_suite_worker_persists_mixed_outcomes(self):
        from .tasks import execute_test_suite
        user = User.objects.create_superuser('suite-worker', password='test-only')
        project = Project.objects.create(name='Suite worker fixture', creator=user)
        module = TestCaseModule.objects.create(project=project, name='Fixture')
        suite = TestSuite.objects.create(project=project, name='Two cases')
        for number in range(2):
            case = Case.objects.create(project=project, module=module, name=f'Case {number}')
            TestCaseStep.objects.create(test_case=case, step_number=1, description='Observe', expected_result='Expected')
            suite.testcases.add(case)
        execution = TestExecution.objects.create(suite=suite, executor=user, status='pending')

        async def fake_api(result):
            def complete():
                run = begin_execution(project, user, result.testcase_id, f'suite-{result.pk}', result.pk)
                shot = TestCaseScreenshot.objects.create(test_case_id=result.testcase_id, execution_result=run, step_number=1, screenshot='tests/fixture.png')
                outcome = 'pass' if result.testcase.name.endswith('0') else 'fail'
                finish_execution(run.pk, {'status': outcome, 'steps': [{'step_number': 1, 'status': outcome, 'expected': 'Expected', 'actual': 'Compared fixture values', 'screenshot_ids': [shot.pk]}]})
            await sync_to_async(complete)()

        with patch('testcases.tasks._execute_testcase_via_chat_api', new=fake_api):
            response = execute_test_suite.run(execution.pk)
        execution.refresh_from_db()
        self.assertEqual(response.get('execution_id'), execution.pk, response)
        self.assertEqual((execution.status, execution.total_count, execution.passed_count, execution.failed_count, execution.pass_rate), ('failed', 2, 1, 1, 50))
        # Celery redelivery does not recreate or rerun completed results.
        execute_test_suite.run(execution.pk)
        self.assertEqual(execution.results.count(), 2)
