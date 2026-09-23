import unittest
from types import SimpleNamespace
from langchain_core.messages import ToolMessage
from orchestrator_integration.execution_guard import ExecutionGuardMiddleware, tool_failed


class ExecutionGuardTest(unittest.IsolatedAsyncioTestCase):
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
