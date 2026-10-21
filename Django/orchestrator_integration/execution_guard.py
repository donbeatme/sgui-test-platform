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
结果页明确要求登录、缺少候选数据且原用例要求停止时，保存当前状态并报告 blocked。
不得自行改换航线、日期或账户来寻找通过结果；只有原步骤明确要求重选数据时才允许重选，
并同步记录后续使用的同一个数据值。未执行的依赖步骤保持 not_run，不复用前轮结论。
冻结用例和项目入口已随执行指令提供时，直接使用，不要重复查询项目、模块和用例详情。
先观察页面当前值，只有与测试要求不一致时才修改，避免无意义地重复选择城市或排序。
城市、日期等选择完成后，核对弹层是否收起，再点击搜索或其他被遮挡的控件。
发生 intercepts pointer events、不可见或失效 ref 时，先取得最新快照，在可见界面中通过
关闭按钮、选择有效选项或点击弹层外安全区域恢复，再使用最新定位重试；Escape 无效就换策略。
禁止对同一遮挡目标盲目重复点击，禁止 force 点击、DOM click 或删除遮罩绕过正常交互。
同一页面状态已有快照和工具返回时复用其观察，不反复执行相同 snapshot/evaluate。
只补取本步骤缺失的信息，优先获取相关表单/航班区域的局部快照；同一步多个字段的只读核对
可在一次观察中完成，不把每个字段拆成多次 evaluate。正常操作已返回足够状态时不再读整页。
页面若没有发生变化，下一步骤可引用刚取得的观察，但仍需按原步骤逐一断言并保存本轮截图。
工具失败后优先排除原因；无法恢复时立即输出结构化 blocked 报告，不消耗剩余调用空转。
"""


def execution_tool_budget(case_snapshot=None):
    """Scale to frozen steps, with a hard ceiling; never trust a requested budget."""
    steps = case_snapshot.get('steps', []) if isinstance(case_snapshot, dict) else []
    count = len(steps) if isinstance(steps, list) else 0
    # Navigation + per-step action, observation, screenshot and upload, plus recovery.
    return min(200, max(80, 20 + count * 10))


def tool_error_detail(content):
    """Only persist error envelopes; ordinary page text may contain secrets."""
    blocks = content if isinstance(content, list) else [{"text": str(content)}]
    errors = []
    for block in blocks:
        value = block.get("text", "") if isinstance(block, dict) else str(block)
        try:
            data = json.loads(value)
            if isinstance(data, dict) and (data.get("status") == "error" or data.get("isError") is True):
                value = str(data.get("message") or data.get("error") or data.get("code") or '工具返回错误')
            elif not re.match(r"^(?:Error\b|### Error\b|文件不存在|文件未找到|HTTP错误|上传失败|上传截图时发生错误)", value.strip()):
                continue
        except (ValueError, TypeError):
            if not re.match(r"^(?:Error\b|### Error\b|文件不存在|文件未找到|HTTP错误|上传失败|上传截图时发生错误)", value.strip()):
                continue
        # Diagnostic excerpts never need authentication or URL query strings.
        value = re.sub(r'https?://[^\s<>"\']+', lambda m: m[0].split('?')[0].split('#')[0], value)
        value = re.sub(r'(?i)(api[_-]?key|authorization|password|token|secret)([\s:=]+)[^\s,;]+', r'\1\2[redacted]', value)
        errors.append(value[:1600])
    return '\n'.join(errors)[:2000]


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
        self.last_error = ''

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
            failed = tool_failed(result)
            self.failures[name] = self.failures[name] + 1 if failed else 0
            if failed:
                self.last_error = tool_error_detail(result.content) or '工具返回错误，未完成操作'
                if 'browser_' in name:
                    recovery = '\n执行恢复提示：先读取最新页面快照，确认可见弹层、控件状态及定位。用正常界面操作消除遮挡后再试；不要重复同一失败点击或用脚本强制点击。无法恢复请输出结构化 blocked 结果。'
                    if isinstance(result.content, list):
                        result = result.model_copy(update={'content': [*result.content, {'type': 'text', 'text': recovery}]})
                    else:
                        result = result.model_copy(update={'content': str(result.content) + recovery})
            if self.failures[name] >= self.max_failures:
                self.stop_reason = f"工具 {name} 连续失败 {self.max_failures} 次。最后错误：{self.last_error}"
        return result

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        if self.stop_reason:
            summary = f"执行已停止（阻塞）：{self.stop_reason}。本次执行未完成，不能标记为通过。未报告的步骤记为未执行；截图以本次实际保存记录为准。"
            report = {'execution_result': {'status': 'blocked', 'summary': summary, 'steps': []}}
            return {"messages": [AIMessage(content=summary + '\n```json\n' + json.dumps(report, ensure_ascii=False) + '\n```')], "jump_to": "end"}
        return None


def guarded_execution_middleware(middleware, case_snapshot=None):
    # Retrying a click can submit twice; let the agent inspect the first failure.
    from langchain.agents.middleware import ToolRetryMiddleware
    return [ExecutionGuardMiddleware(max_calls=execution_tool_budget(case_snapshot))] + [m for m in middleware if not isinstance(m, ToolRetryMiddleware)]
