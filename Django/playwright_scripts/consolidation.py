"""Turn successful exploratory operations into one coherent replay candidate."""
import hashlib
import json
import time
from .contract import normalize, validate

PROMPT = '''把真实浏览器中的探索记录整理为一个简洁、可复跑的 Python Playwright 脚本。只返回 JSON {"code":"async def run(page, params, record):..."}。
输入 executed_code 含已成功的操作，但可能因为探索、重新打开浮层和恢复而重复；它不是最终要逐行照抄的脚本。
保留所有原用例步骤及顺序，使用 async with record.step(原编号)。保留必要准备和连续业务操作，关键导航、输入值、明显错误提示检查。相同目标只建立一次，删除无意义的重复导航、重复选择、调试读取、探索性重新打开和重复断言。不要改变关键检查的预期值，不得通过 try/except、return、force 或跳过步骤取得通过。
只使用 executed_code 中已经观察并执行过的定位器及公共 helper；可以更明确地限定已有的活动或可见面板，不猜新 CSS 或值。参数保持原样，相对日期使用已有模板。
页面初始状态可变：使用幂等设置或条件分支。不要把日期先选好再重复点击已关闭面板中的同一天；选择完成后检查输入值即可。切换面板后等待其状态，弹层关闭动画期间不要把旧可见节点当成新面板。
保留所有原用例的操作步骤，但当前仅验收可复跑，无需为视觉高亮、几何布局等补充断言。不要引入新业务动作。
支持局部变量、if、for attempt in range(最多12)、break、assert、re.compile；page/locator 常用操作和读取、expect断言；record.ensure_open/choose_option/dismiss_panel/set_toggle/expect_closed 等已出现的 helper。
无需 import。每步重新定义用到的定位器变量。优先简洁代码，不重复多个等价判断。最后必须在新浏览器复跑成功才会交付。
'''


def consolidate(script, code, parameters, previous_failure=None):
    from guicase_workbench.tasks import invoke, parse_json
    from langgraph_integration.models import LLMConfig
    from .services import redact
    started = time.monotonic()
    metadata = {'ok': False, 'input_sha256': hashlib.sha256(code.encode()).hexdigest()}
    try:
        data = parse_json(invoke(LLMConfig.objects.get(pk=script.model_id), PROMPT,
            json.dumps({'case': script.snapshot, 'executed_code': code, 'parameters': parameters,
                        'previous_error': (previous_failure or {}).get('error', '')}, ensure_ascii=False),
            timeout=90, strict_timeout=True, max_tokens=10000))
        candidate, _ = normalize(data.get('code') if isinstance(data, dict) else '')
        issues = validate(candidate, script.snapshot, parameters, require_assertions=False)
        if issues: raise ValueError('；'.join(issues[:3]))
        if redact(candidate, script.project) != candidate: raise ValueError('整理结果含敏感值，未保存。')
        metadata.update(ok=True, output_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                        input_chars=len(code), output_chars=len(candidate))
        return candidate, [], metadata
    except Exception as exc:
        metadata['error'] = str(exc)[:500]
        # A model outage is not a pass. The original collected candidate still
        # has to complete the same independent replay before it is downloadable.
        return code, ['脚本精简未完成，本次保留已执行代码并进行实际复跑。'], metadata
    finally:
        metadata['seconds'] = round(time.monotonic()-started, 3)
