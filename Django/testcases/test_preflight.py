import json
from types import SimpleNamespace
from unittest.mock import patch
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from projects.models import Project, ProjectCredential
from langgraph_integration.models import LLMConfig
from mcp_tools.models import RemoteMCPConfig, MCPTool
from .models import TestCase as Case, TestCaseStep, TestExecution, TestCaseResult, TestCaseModule
from .preflight import execution_preflight
from .execution_stream import track_case_stream
from .execution_tracking import begin_execution, execution_instruction


class PreflightTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('preflight-user', password='test-only')
        self.project = Project.objects.create(name='Preflight fixture', creator=self.user)
        module = TestCaseModule.objects.create(project=self.project, name='Login')
        self.case = Case.objects.create(project=self.project, module=module, name='Observe login form')
        TestCaseStep.objects.create(test_case=self.case, step_number=1, description='查看页面', expected_result='显示登录按钮')
        self.model = LLMConfig.objects.create(config_name='fixture-model', name='fixture', api_url='https://example.invalid/v1', is_active=True)
        self.mcp = RemoteMCPConfig.objects.create(name='fixture-tools', url='http://localhost:8111/mcp')
        for name in ['browser_navigate', 'upload_screenshot']:
            MCPTool.objects.create(mcp_config=self.mcp, name=name)
        self.credential = ProjectCredential.objects.create(project=self.project, system_url='https://example.invalid/login', username='test-account', password='never-in-report')
        self.api = APIClient(); self.api.force_authenticate(self.user)

    def test_single_project_url_is_shared_and_injected_into_frozen_execution(self):
        report = execution_preflight(self.case)
        self.assertTrue(report['ready'], report)
        self.assertEqual(report['url_source'], 'project')
        self.assertEqual(report['target_urls'], ['https://example.invalid/login'])
        self.assertNotIn('never-in-report', json.dumps(report))
        run = begin_execution(self.project, self.user, self.case.pk, 'configured-url')
        self.assertIn('https://example.invalid/login', execution_instruction(run.pk))
        self.assertNotIn('never-in-report', execution_instruction(run.pk))

    def test_ambiguous_project_url_blocks_but_explicit_case_url_takes_precedence(self):
        ProjectCredential.objects.create(project=self.project, system_url='https://other.invalid/')
        self.assertIn('ambiguous_url', [e['code'] for e in execution_preflight(self.case)['errors']])
        self.case.precondition = '入口 https://example.invalid/login。'
        report = execution_preflight(self.case)
        self.assertTrue(report['ready'], report)
        self.assertEqual(report['url_source'], 'case')

    def test_missing_prerequisites_are_reported_together_without_launching_tools(self):
        self.credential.delete(); self.model.delete(); self.mcp.delete()
        self.case.precondition = '输入 {{测试账号}}'
        self.case.steps.update(expected_result='')
        report = execution_preflight(self.case)
        self.assertFalse(report['ready'])
        self.assertEqual({e['code'] for e in report['errors']}, {'missing_url', 'missing_assertion', 'unresolved_data', 'model', 'tools'})

    def test_empty_input_negative_test_is_allowed_and_unsynced_tools_are_explicit_warning(self):
        self.case.steps.update(description='用户名留空，点击登录', expected_result='显示必填提示')
        MCPTool.objects.all().delete()
        report = execution_preflight(self.case)
        self.assertTrue(report['ready'], report)
        self.assertIn('browser_tools', [w['code'] for w in report['warnings']])
        self.assertIn('evidence_tool', [w['code'] for w in report['warnings']])

    def test_preflight_api_is_read_only_and_project_scoped(self):
        url = f'/api/projects/{self.project.pk}/testcases/{self.case.pk}/execution-preflight/'
        response = self.api.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('never-in-report', response.content.decode())
        self.assertFalse(TestExecution.objects.exists())
        other = Project.objects.create(name='Other', creator=self.user)
        self.assertEqual(self.api.get(f'/api/projects/{other.pk}/testcases/{self.case.pk}/execution-preflight/').status_code, 404)
        self.api.force_authenticate(None)
        self.assertEqual(self.api.get(url).status_code, 401)

    def test_backend_blocks_without_invoking_agent_and_persists_suite_result(self):
        self.credential.delete()
        execution = TestExecution.objects.create(project=self.project, executor=self.user, source='suite', name='Suite preflight', status='running', total_count=1)
        row = TestCaseResult.objects.create(execution=execution, testcase=self.case)
        run = begin_execution(self.project, self.user, self.case.pk, 'suite-preflight', row.pk)
        calls = []
        @track_case_stream
        async def agent(self, request):
            calls.append('must not call')
            yield 'data: {"type":"complete"}\n\n'
        async def consume():
            return [chunk async for chunk in agent(None, SimpleNamespace(execution_result_id=run.pk, execution_preflight=execution_preflight_report))]
        execution_preflight_report = execution_preflight(self.case)
        events = async_to_sync(consume)()
        self.assertFalse(calls)
        row.refresh_from_db(); execution.refresh_from_db()
        self.assertEqual((row.status, execution.status, execution.pass_rate), ('blocked', 'blocked', 0))
        self.assertIn('项目地址', row.error_message)
        self.assertIn('execution_result', ''.join(events))
