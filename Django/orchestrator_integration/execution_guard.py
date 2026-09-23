"""Bound UI-case tool execution; never silently retry a browser side effect."""
import json
import re
from collections import Counter

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage


EXECUTION_INSTRUCTION = """
测试用例执行约束：必须读取本次用例的完整步骤，逐步真实操作并观察断言。
不得把控件存在等同于交互成功，不得跳过步骤后宣布通过。每步结束立即截图并上传，
截图名称包含本次会话和用例编号；上传工具支持共享目录相对路径和配置过的宿主机绝对路径。
上传失败只能依据明确错误修正一次，禁止猜测目录或用无关工具查找系统文件。
最终按原步骤给出实际操作、预期、实际观察、结果、截图；有步骤未执行、断言不符或
截图未保存，整条用例不得标记通过。工具执行异常属于阻塞，不能当作产品缺陷。
遇到验证码、访问拦截或安全验证，记录并停止，不得绕过。
"""


def tool_failed(message):
    if getattr(message, "status", None) == "error":
        return True
    content = message.content
    blocks = content if isinstance(content, list) else [{"text": str(content)}]
    for block in blocks:
        text = block.get("text", "") if isinstance(block, dict) else str(block)
        try:
            data = json.loads(text)
            if isinstance(data, dict) and (data.get("status") == "error" or data.get("isError") is True):
                return True
        except (ValueError, TypeError):
            pass
        # Check tool error envelopes, not arbitrary occurrences in a page snapshot.
        if re.match(r"^(?:Error\b|### Error\b|文件不存在|文件未找到|HTTP错误|上传失败|上传截图时发生错误)", text.strip()):
            return True
    return False


class ExecutionGuardMiddleware(AgentMiddleware):
    def __init__(self, max_calls=80, max_failures=3):
        self.max_calls = max_calls
        self.max_failures = max_failures
        self.calls = 0
        self.failures = Counter()
        self.stop_reason = None

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        self.calls += 1
        if self.calls > self.max_calls or self.stop_reason:
            self.stop_reason = self.stop_reason or f"已达到 {self.max_calls} 次工具调用上限"
            return ToolMessage(content=self.stop_reason, tool_call_id=request.tool_call["id"], name=name, status="error")
        try:
            result = await handler(request)
        except Exception as exc:
            result = ToolMessage(content=f"Error: {type(exc).__name__}: {exc}", tool_call_id=request.tool_call["id"], name=name, status="error")
        if isinstance(result, ToolMessage):
            self.failures[name] = self.failures[name] + 1 if tool_failed(result) else 0
            if self.failures[name] >= self.max_failures:
                self.stop_reason = f"工具 {name} 连续失败 {self.max_failures} 次"
        return result

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        if self.stop_reason:
            return {"messages": [AIMessage(content=f"执行已停止（阻塞）：{self.stop_reason}。本次执行未完成，不能标记为通过。请查看最后的工具错误与已保存截图，修复后重新执行。")], "jump_to": "end"}
        return None


def guarded_execution_middleware(middleware):
    # Retrying a click can submit twice; let the agent inspect the first failure.
    from langchain.agents.middleware import ToolRetryMiddleware
    return [ExecutionGuardMiddleware()] + [m for m in middleware if not isinstance(m, ToolRetryMiddleware)]
