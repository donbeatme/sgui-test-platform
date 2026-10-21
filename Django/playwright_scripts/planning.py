"""Frozen requirements, current-page facts and step-local repair bookkeeping.

No site names/selectors, browser actions, or alternate definitions of success.
The plan helps the generator; the original case and real assertions remain the
authority. Replaying a cached block never inherits its previous pass verdict.
"""
import ast
from copy import deepcopy
import json


PLAN_PROMPT = '''将冻结用例整理为执行准备和验收计划，不编写代码、不改写原需求。
只返回 JSON {"setup":[{"source_id":"P1","step_number":1,"prepare":"如何建立前置","verify":"如何验证","uncertainty":""}],
"steps":[{"step_number":1,"action":"完整操作及停止边界","checks":[{"source_id":"E1","verify":"原预期的具体检查，不降低要求"}]}]}。
setup 必须覆盖 obligations 中全部 P 编号且各一次；steps 按 case.steps 顺序，每步 checks 覆盖该步全部 E 编号各一次。
前置是脚本必须建立的状态，不是假定用户已完成。把准备安排在最早合理步骤；依赖前次校验的恢复场景，必须先实际触发该校验。
明确数量、字段值、选中状态、星期、保持不变须分别考虑。不可将成人数1改成仅两个儿童选项未勾选。
页面暂时没有显示不等于功能不存在，可以计划打开已知面板观察。当前可调整状态不符应先设置，不可直接判不适用。
没有可靠证据的检查写入 uncertainty，随后实际探索；不在这里宣判失败或通过。默认值与历史成功动作只是线索。
只依据当前页面、原用例及来源原文。禁止增加登录、付款或其他未授权动作。
保持计划简短：action 和 verify 写具体实现方法，不重复整段原文，每项不超过80字；不要增加原文没有要求的人数等条件。'''


def obligations(snapshot):
    rows = []
    precondition = str(snapshot.get('precondition') or '').strip()
    if precondition:
        rows.append({'id': 'P1', 'source': precondition})
    for step in snapshot['steps']:
        number = step['step_number']
        rows.append({'id': f'E{number}', 'step_number': number,
                     'source': step.get('expected_result') or step.get('expected') or ''})
    return rows


def validate_plan(data, snapshot):
    if not isinstance(data, dict) or len(json.dumps(data, ensure_ascii=False)) > 16000:
        raise ValueError('执行计划格式无效或过长。')
    numbers = [s['step_number'] for s in snapshot['steps']]
    required = [r['id'] for r in obligations(snapshot) if r['id'].startswith('P')]
    setup, steps = data.get('setup'), data.get('steps')
    if not isinstance(setup, list) or not isinstance(steps, list):
        raise ValueError('执行计划需要 setup 和 steps。')
    if [s.get('source_id') for s in setup if isinstance(s, dict)] != required or len(setup) != len(required):
        raise ValueError('执行计划必须逐项保留全部前置条件。')
    for row in setup:
        if row.get('step_number') not in numbers or not all(isinstance(row.get(k), str) and row[k].strip() for k in ('prepare', 'verify')):
            raise ValueError('每个前置条件需要准备步骤和验收方法。')
    if [s.get('step_number') for s in steps if isinstance(s, dict)] != numbers or len(steps) != len(numbers):
        raise ValueError('执行计划必须保留全部原始步骤顺序。')
    for row in steps:
        checks = row.get('checks')
        if (not isinstance(row.get('action'), str) or not row['action'].strip()
                or not isinstance(checks, list) or len(checks) != 1
                or not isinstance(checks[0], dict) or checks[0].get('source_id') != f"E{row['step_number']}"
                or not isinstance(checks[0].get('verify'), str) or not checks[0]['verify'].strip()):
            raise ValueError('执行计划遗漏原始预期或验收方法。')
    # Preserve the literal business requirements alongside suggested methods.
    # A planner may suggest a technique but cannot invent/replace an expectation.
    for row, original in zip(steps, snapshot['steps']):
        row['source_action'] = original.get('description', '')
        row['source_expected'] = original.get('expected_result', '')
    return {'setup': setup, 'steps': steps}


def compact_page(page):
    """Keep current values/state and unique paths, not repeated ancestor dumps."""
    result = {k: page[k] for k in ('url', 'title', 'note') if k in page}
    result['aria_snapshot'] = str(page.get('aria_snapshot', ''))[:14000]
    try:
        dom = json.loads(page.get('dom', '{}')) if isinstance(page.get('dom'), str) else page.get('dom', {})
        result['dom'] = {'truncated': dom.get('truncated', False), 'nodes': [
            {k: n[k] for k in ('css', 'tag', 'text', 'value', 'attrs', 'visible', 'disabled', 'readOnly', 'styles', 'box') if k in n}
            for n in dom.get('nodes', []) if isinstance(n, dict)]}
    except (ValueError, TypeError, AttributeError):
        result['note'] = 'DOM 证据不完整，不可据此断言控件不存在。'
    return result


def compact_hints(hints):
    """Deduplicate evidence from nearby scripts instead of repeating whole sets."""
    locators, interactions, seen = [], [], set()
    for item in hints:
        for locator in item.get('locators', []):
            if locator not in locators and len(locators) < 40:
                locators.append(locator)
        for interaction in item.get('interactions', []):
            signature = json.dumps(interaction, ensure_ascii=False, sort_keys=True)
            if signature not in seen and len(interactions) < 20:
                interactions.append(interaction); seen.add(signature)
    return {'locators': locators, 'interactions': interactions,
            'note': '同入口历史成功定位线索，不是当前页面已执行结果；以 live_page 为准。'}


def contradictory_absence(target, page):
    """A visible value disproves an absence claim; absence here proves nothing."""
    if not isinstance(target, str) or len(target.strip()) < 2:
        return []
    target = ''.join(target.split()).casefold()
    dom = compact_page(page).get('dom', {})
    matches = []
    for node in dom.get('nodes', []):
        if node.get('visible') is not True:
            continue
        texts = [node.get('text', ''), node.get('value', ''), *node.get('attrs', {}).values()]
        if any(target in ''.join(str(t).split()).casefold() for t in texts):
            matches.append({k: node.get(k) for k in ('css', 'text', 'value')})
    return matches[:8]


def cached_steps(previous, snapshot, failure):
    """Only fully acknowledged steps can be replayed without model generation."""
    numbers = [s['step_number'] for s in snapshot['steps']]
    completed = set(numbers[:previous.get('step_index', 0)])
    affected = set(failure.get('step_numbers') or [])
    if not affected:
        number = (failure.get('page') or {}).get('step_number')
        if type(number) is int:
            affected.add(number)
    # Unknown scope: retain candidate for inspection, but do not blindly replay it.
    cache = {str(n): deepcopy(previous.get('accepted', {}).get(str(n), []))
             for n in completed - affected} if affected else {}
    candidates = {str(n): deepcopy(previous.get('accepted', {}).get(str(n), []))
                  for n in numbers if str(n) not in cache}
    index = previous.get('step_index', 0)
    if index < len(numbers) and previous.get('recovery_tail'):
        key = str(numbers[index])
        candidates.setdefault(key, []).extend(deepcopy(previous['recovery_tail']))
    return cache, candidates


def patch_statements(original, patches, compile_fragment, number, parameters):
    """Apply explicit inserts/replacements, leaving all other statements intact."""
    if not isinstance(patches, list) or not 1 <= len(patches) <= 12:
        raise ValueError('局部修复需要 1–12 个明确补丁。')
    edits, positions, removed = [], set(), set()
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError('修复补丁格式无效。')
        index, count = patch.get('index'), patch.get('delete_count', 0)
        if (type(index) is not int or not 0 <= index <= len(original) or index in positions
                or type(count) is not int or count < 0 or index + count > len(original)
                or set(range(index, index+count)) & removed):
            raise ValueError(f'补丁位置无效或重叠：当前候选只有 {len(original)} 条，index 为零基位置，delete_count 是从该处替换的条数。同一位置的操作合并到一个 body。')
        body = patch.get('body', '')
        if not isinstance(body, str) or not body.strip():
            raise ValueError('不能用空补丁删除操作或断言。')
        edits.append((index, count, compile_fragment(body, number, parameters)))
        positions.add(index)
        removed.update(range(index, index+count))
    if any(pos in removed and count == 0 for pos, count, _ in edits):
        raise ValueError('插入位置落在另一个替换区间内，请合并补丁。')
    output = list(original)
    for index, count, lines in sorted(edits, reverse=True):
        output[index:index + count] = lines
    if len(output) > 100 or len('\n'.join(output)) > 30000:
        raise ValueError('修复后的单步代码超过上限。')
    return output


def reject_frozen_relative_dates(statements, parameters, today):
    from .date_parameters import resolve_parameters
    resolved = resolve_parameters(parameters, today)
    dynamic = {resolved[key] for key, value in parameters.items()
               if isinstance(value, str) and value.startswith('{{date:')
               and any(part in value for part in (':iso}}', ':weekday_zh}}', ':weekday_zh_short}}'))}
    if not dynamic:
        return
    for statement in statements:
        for node in ast.walk(ast.parse(statement)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(value in node.value for value in dynamic):
                    raise ValueError('代码中写死了相对日期或星期，请用 params 引用对应日期模板；定位器中的日期也要参数化。')
