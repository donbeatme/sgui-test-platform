import unittest
from types import SimpleNamespace
from langchain_core.messages import ToolMessage
from orchestrator_integration.execution_guard import ExecutionGuardMiddleware, tool_failed
from orchestrator_integration.execution_guard import tool_error_detail, execution_tool_budget, guarded_execution_middleware
from testcases.execution_tracking import parse_report


class ExecutionGuardTest(unittest.IsolatedAsyncioTestCase):
    def test_frozen_step_budget_scales_but_is_always_bounded(self):
        self.assertEqual(execution_tool_budget(), 80)
        self.assertEqual(execution_tool_budget({'steps': 'invalid'}), 80)
        self.assertEqual(execution_tool_budget({'steps': [{}] * 11}), 130)
        self.assertEqual(execution_tool_budget({'steps': [{}] * 15}), 170)
        self.assertEqual(execution_tool_budget({'steps': [{}] * 1000}), 200)
        guard = guarded_execution_middleware([], {'steps': [{}] * 15})[0]
        self.assertEqual(guard.max_calls, 170)
        self.assertEqual(guard.max_failures, 3)

    def request(self, name="browser_click"):
        return SimpleNamespace(tool_call={"name": name, "id": "call-test"})

    async def test_exception_is_not_automatically_retried(self):
        calls = []
        async def fail(request):
            calls.append(request)
            raise RuntimeError("click timeout")
        guard = ExecutionGuardMiddleware()
        for _ in range(3):
            await guard.awrap_tool_call(self.request(), fail)
        self.assertEqual(len(calls), 3)
        stop = await guard.abefore_model({}, None)
        self.assertEqual(stop["jump_to"], "end")
        self.assertIn("不能标记为通过", stop["messages"][0].content)

    async def test_success_resets_only_same_tool_failure(self):
        guard = ExecutionGuardMiddleware()
        async def answer(request):
            return ToolMessage(content="ok", tool_call_id="call-test")
        guard.failures["browser_click"] = 2
        await guard.awrap_tool_call(self.request("browser_snapshot"), answer)
        self.assertEqual(guard.failures["browser_click"], 2)
        await guard.awrap_tool_call(self.request(), answer)
        self.assertEqual(guard.failures["browser_click"], 0)

    async def test_budget_blocks_extra_tool_side_effect(self):
        guard = ExecutionGuardMiddleware(max_calls=1)
        calls = []
        async def answer(request):
            calls.append(request)
            return ToolMessage(content="ok", tool_call_id="call-test")
        await guard.awrap_tool_call(self.request(), answer)
        await guard.awrap_tool_call(self.request(), answer)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(await guard.abefore_model({}, None))

    def test_mcp_error_envelopes_not_page_error_word(self):
        for content in ['{"status":"error","code":"SCREENSHOT_PATH_ERROR"}', [{"type":"text","text":"### Error\nTimeout"}], "文件不存在: x.png"]:
            self.assertTrue(tool_failed(ToolMessage(content=content, tool_call_id="x")))
        self.assertFalse(tool_failed(ToolMessage(content="### Snapshot\n- text: Error reporting help", tool_call_id="x")))

    async def test_obstruction_feedback_preserves_error_and_stop_reason(self):
        guard = ExecutionGuardMiddleware()
        async def blocked(request):
            return ToolMessage(content=[{'type': 'text', 'text': '### Error\ncity-picker-tabs intercepts pointer events'}], tool_call_id='call-test')
        for _ in range(3):
            result = await guard.awrap_tool_call(self.request(), blocked)
        self.assertTrue(tool_failed(result))
        self.assertIn('执行恢复提示', result.content[-1]['text'])
        report = parse_report((await guard.abefore_model({}, None))['messages'][0].content)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['steps'], [])
        self.assertIn('city-picker-tabs intercepts pointer events', report['summary'])

    def test_diagnostics_do_not_copy_normal_page_output_or_secrets(self):
        self.assertEqual(tool_error_detail('### Snapshot\nPrivate page text'), '')
        message = 'Error: token=private-key https://example.org/path?password=secret'
        detail = tool_error_detail(message)
        self.assertNotIn('private-key', detail)
        self.assertNotIn('password=secret', detail)

    def test_balance_and_configuration_failures_are_not_retried(self):
        from orchestrator_integration.middleware_config import _model_retry_should_retry
        for code in (400, 401, 402, 403, 404, 422):
            error = RuntimeError('provider failure')
            error.status_code = code
            self.assertFalse(_model_retry_should_retry(error))
        error = RuntimeError('temporary unavailable')
        error.status_code = 503
        self.assertTrue(_model_retry_should_retry(error))

    async def test_agent_stops_and_persists_blocked_report(self):
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.tools import tool

        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        @tool
        def failing_click() -> str:
            """A browser failure used to verify the execution guard."""
            raise RuntimeError("timed out")

        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "failing_click", "args": {}, "id": str(i)}]) for i in range(3)])
        guard = ExecutionGuardMiddleware()
        agent = create_agent(model, [failing_click], middleware=[guard])
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "Execute case"}]})
        self.assertEqual(guard.calls, 3)
        self.assertIn("执行已停止（阻塞）", result["messages"][-1].content)
