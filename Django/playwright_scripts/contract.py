"""Small auditable Playwright subset; no filesystem, imports or arbitrary evaluation.

This is a format validator, not an OS sandbox. The authenticated runner executes
only this format, in a fresh browser context and with a wall-clock deadline.
"""
import ast
import re

LOCATORS = {'get_by_role', 'get_by_label', 'get_by_placeholder', 'get_by_text',
            'get_by_test_id', 'get_by_title', 'get_by_alt_text', 'locator', 'filter', 'nth'}
ACTIONS = {'goto', 'click', 'dblclick', 'fill', 'press', 'check', 'uncheck', 'set_checked',
           'select_option', 'hover', 'focus', 'wait_for', 'wait_for_url', 'wait_for_load_state',
           'scroll_into_view_if_needed', 'go_back', 'go_forward', 'reload'}
ASSERTIONS = {'to_be_visible', 'to_be_hidden', 'to_be_enabled', 'to_be_disabled',
              'to_be_checked', 'not_to_be_checked', 'to_have_text', 'to_contain_text',
              'to_have_value', 'to_have_values', 'to_have_count', 'to_have_url',
              'to_have_title', 'to_have_attribute', 'to_have_css', 'not_to_contain_text',
              'not_to_have_text', 'to_be_empty', 'to_be_attached', 'to_have_class',
              'to_be_focused', 'to_be_editable'}
ASSERTIONS |= {'not_' + name for name in ASSERTIONS if name.startswith('to_')}
HELPERS = {'ensure_open', 'choose_option', 'expect_closed', 'dismiss_panel', 'observe_state', 'guard_state', 'stop', 'set_toggle', 'expect_aligned'}


def runtime_required(code, parameters=None):
    if any(isinstance(v, str) and v.startswith('{{date:') for v in (parameters or {}).values()):
        return 8
    try:
        names = {n.func.attr for n in ast.walk(ast.parse(code))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and _root(n) == 'record'}
        if 'dismiss_panel' in names:
            return 8
        if names & {'set_toggle','expect_aligned'} or any(isinstance(n, ast.keyword) and n.arg=='value_mode' for n in ast.walk(ast.parse(code))):
            return 5
        return 3 if 'guard_state' in names else 2 if names & {'observe_state', 'stop'} else int(bool(names & HELPERS))
    except (SyntaxError, ValueError):
        return 0


def _locator(node):
    if isinstance(node, ast.Attribute) and node.attr in {'first', 'last'}:
        return _locator(node.value)
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in LOCATORS:
        return False
    parent = node.func.value
    return (isinstance(parent, ast.Name) and parent.id == 'page') or _locator(parent)


def _helper_errors(call):
    name = call.func.attr
    if name == 'expect_aligned':
        values={k.arg:k.value for k in call.keywords}
        if call.args or not {'target','reference'}<=values.keys() or not values.keys()<={'target','reference','axis','tolerance_px','timeout_ms'}:
            return ['expect_aligned 需要 target/reference 定位器，可选 axis、tolerance_px、timeout_ms。']
        errors=[]
        for key in ('target','reference'):
            if not _locator(values[key]) or any(isinstance(n,ast.Call) and _root(n)=='page' and
                    (not isinstance(n.func,ast.Attribute) or n.func.attr not in LOCATORS) for n in ast.walk(values[key])):
                errors.append('expect_aligned 的目标和参考必须是页面定位器。')
        if ast.dump(values['target'])==ast.dump(values['reference']):
            errors.append('位置关系不能将同一定位器用作目标和参考。')
        if 'axis' in values and (not isinstance(values['axis'],ast.Constant) or values['axis'].value not in ('row','column')):
            errors.append('expect_aligned.axis 必须为 row 或 column。')
        for key,lo,hi in [('tolerance_px',0,20),('timeout_ms',500,15000)]:
            if key in values and (not isinstance(values[key],ast.Constant) or type(values[key].value) is not int or not lo<=values[key].value<=hi):
                errors.append(f'expect_aligned.{key} 必须是 {lo}–{hi} 整数。')
        return errors
    if name == 'set_toggle':
        values = {k.arg:k.value for k in call.keywords}
        required={'control','selected','unselected','checked'}
        if call.args or not required <= values.keys() or not values.keys() <= required|{'label','timeout_ms'}:
            return ['set_toggle 需要 control、selected、unselected、checked；可选 label、timeout_ms。']
        errors=[]
        if any(not _locator(values[k]) or any(isinstance(n,ast.Call) and _root(n)=='page' and
                   (not isinstance(n.func,ast.Attribute) or n.func.attr not in LOCATORS)
                   for n in ast.walk(values[k])) for k in ('control','selected','unselected')):
            errors.append('set_toggle 的控件、选中与未选中标记必须是页面定位器。')
        if not isinstance(values['checked'],ast.Constant) or type(values['checked'].value) is not bool:
            errors.append('set_toggle.checked 必须是 true/false 对应的 Python 布尔常量。')
        if 'timeout_ms' in values and (not isinstance(values['timeout_ms'],ast.Constant) or
                type(values['timeout_ms'].value) is not int or not 500<=values['timeout_ms'].value<=15000):
            errors.append('公共操作 timeout_ms 必须是 500–15000 的整数常量。')
        return errors
    if name in {'observe_state', 'guard_state', 'stop'}:
        values = {k.arg: k.value for k in call.keywords}
        required = {'states', 'outcomes'} if name == 'guard_state' else {'states'} if name == 'observe_state' else {'reason'}
        allowed = required | ({'timeout_ms'} if name != 'stop' else set())
        errors = []
        if call.args or not required <= values.keys() or not values.keys() <= allowed or len(values) != len(call.keywords):
            return [f'record.{name} 只支持关键字参数 {", ".join(sorted(allowed))}。']
        if name in {'observe_state', 'guard_state'}:
            states = values['states']
            if (not isinstance(states, ast.Dict) or not 1 <= len(states.keys) <= 8
                    or any(not isinstance(k, ast.Constant) or not isinstance(k.value, str)
                           or not k.value.strip() for k in states.keys)
                    or any(not _locator(v) or any(isinstance(n, ast.Call) and _root(n) == 'page'
                           and (not isinstance(n.func, ast.Attribute) or n.func.attr not in LOCATORS)
                           for n in ast.walk(v)) for v in states.values)):
                errors.append('observe_state.states 必须是 1–8 个明确状态名称到页面定位器的映射。')
            if isinstance(states, ast.Dict) and len({ast.dump(k) for k in states.keys if k}) != len(states.keys):
                errors.append('observe_state 的状态名称不能重复。')
            if name == 'guard_state':
                outcomes = values['outcomes']
                if (not isinstance(outcomes, ast.Dict) or not isinstance(states, ast.Dict)
                        or len(outcomes.keys) != len(states.keys)
                        or {ast.dump(k) for k in outcomes.keys if k} != {ast.dump(k) for k in states.keys if k}
                        or any(not isinstance(v, ast.Constant) or v.value not in ('continue', 'blocked', 'skip')
                               for v in outcomes.values)):
                    errors.append('guard_state.outcomes 必须逐一对应 states 的状态名，值只能为 continue、blocked、skip。')
        elif not isinstance(values['reason'], ast.Constant) or not isinstance(values['reason'].value, str) or not values['reason'].value.strip():
            errors.append('stop.reason 必须是明确说明停止原因的文字常量。')
        if 'timeout_ms' in values:
            node = values['timeout_ms']
            if not isinstance(node, ast.Constant) or type(node.value) is not int or not 500 <= node.value <= 15000:
                errors.append('公共操作 timeout_ms 必须是 500–15000 的整数常量。')
        return errors
    required = {'panel'} if name == 'expect_closed' else {'trigger', 'panel'}
    if name == 'dismiss_panel':
        required = {'panel', 'dismiss'}
    allowed = required | {'label', 'timeout_ms'}
    if name == 'choose_option':
        required |= {'value', 'expected_value'}
        allowed |= required | {'option_name', 'option_role', 'option_selector', 'value_mode'}
    values = {k.arg: k.value for k in call.keywords}
    errors = []
    if call.args or not required <= values.keys() or not values.keys() <= allowed or len(values) != len(call.keywords):
        errors.append(f'record.{name} 参数无效；缺少：{", ".join(sorted(required-values.keys())) or "无"}；'
            f'不支持：{", ".join(sorted(str(k) for k in values.keys()-allowed)) or "无"}；'
            f'完整参数范围：{", ".join(sorted(allowed))}。')
    if name == 'choose_option' and (('option_name' in values) == ('option_selector' in values)):
        errors.append('choose_option 必须指定 option_name 或 option_selector 中的一项。')
    if 'value_mode' in values and (not isinstance(values['value_mode'], ast.Constant) or values['value_mode'].value not in ('value','text','text_contains')):
        errors.append('choose_option.value_mode 只支持 value、text、text_contains 常量。')
    for key in {'trigger', 'panel', 'value', 'dismiss'} & values.keys():
        if not _locator(values[key]) or any(
                isinstance(n, ast.Call) and _root(n) == 'page' and (
                    not isinstance(n.func, ast.Attribute) or n.func.attr not in LOCATORS)
                for n in ast.walk(values[key])):
            errors.append(f'{name}.{key} 必须是 page 下的定位器，不能是操作或其他对象。')
    for key in values.keys() - {'trigger', 'panel', 'value', 'dismiss', 'timeout_ms'}:
        if any(isinstance(n, ast.Call) and _root(n) != 'params' for n in ast.walk(values[key])):
            errors.append(f'{name}.{key} 只允许文本或参数取值，不能嵌入操作。')
    if 'timeout_ms' in values:
        node = values['timeout_ms']
        if not isinstance(node, ast.Constant) or type(node.value) is not int or not 500 <= node.value <= 15000:
            errors.append('公共操作 timeout_ms 必须是 500–15000 的整数常量。')
    return errors


def _root(node):
    while isinstance(node, (ast.Call, ast.Attribute)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else ''


def normalize(code):
    """Lossless-in-behavior fixes for known JS-to-Python API spelling mistakes."""
    if not isinstance(code, str) or len(code) > 100000:
        return code, []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return code, []
    fixes = []

    class LocatorProperties(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if (isinstance(node.func, ast.Attribute) and node.func.attr in {'first', 'last'}
                    and _root(node) == 'page' and not node.args and not node.keywords):
                fixes.append(f'已自动将 .{node.func.attr}() 转换为 Python Playwright 的 .{node.func.attr} 属性。')
                return ast.copy_location(node.func, node)
            return node

    tree = LocatorProperties().visit(tree)
    return (ast.unparse(tree) + '\n', list(dict.fromkeys(fixes))) if fixes else (code, [])


def validate(code, snapshot, parameters=None, *, partial=False, require_assertions=True):
    errors = []
    if not isinstance(code, str) or len(code) > 100000:
        return ['脚本为空或超过 100,000 字符。']
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as exc:
        return [f'Python 语法错误：{exc}']
    functions = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)]
    if len(tree.body) != 1 or len(functions) != 1:
        return ['只允许 async def run(page, params, record)，无需 import。']
    function = functions[0]
    if (function.name != 'run' or [a.arg for a in function.args.args] != ['page', 'params', 'record']
            or function.decorator_list or function.returns or function.args.defaults
            or function.args.kwonlyargs or function.args.posonlyargs or function.args.vararg or function.args.kwarg
            or any(a.annotation for a in function.args.args)):
        return ['函数签名须为 async def run(page, params, record):。']
    numbers = []
    for block in function.body:
        if not isinstance(block, ast.AsyncWith) or len(block.items) != 1:
            errors.append('每个业务步骤必须使用 async with record.step(步骤编号):。')
            continue
        context = block.items[0].context_expr
        if (block.items[0].optional_vars or not isinstance(context, ast.Call)
                or not isinstance(context.func, ast.Attribute) or _root(context) != 'record'
                or context.func.attr != 'step' or len(context.args) != 1 or context.keywords
                or not isinstance(context.args[0], ast.Constant) or type(context.args[0].value) is not int):
            errors.append('步骤编号必须是原用例中的整数。')
            continue
        numbers.append(context.args[0].value)
        assertion_count = 0
        for statement in block.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Await):
                errors.append('步骤内只允许 await Playwright 操作或 expect 断言；不允许循环、分支、赋值或跳过断言。')
                continue
            call = statement.value.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                errors.append('步骤必须调用 Playwright 方法。')
                continue
            root = _root(call)
            if root == 'expect' and call.func.attr in ASSERTIONS:
                assertion_count += 1
            elif root == 'record' and call.func.attr in HELPERS and isinstance(call.func.value, ast.Name):
                errors.extend(_helper_errors(call))
                if call.func.attr in {'observe_state', 'guard_state', 'stop', 'expect_aligned'}:
                    assertion_count += 1
                if call.func.attr == 'stop' and (block is not function.body[-1] or statement is not block.body[-1]):
                    errors.append('stop 必须是最后一个原用例步骤的最后操作；不得跳过后续步骤。')
            elif root != 'page' or call.func.attr not in ACTIONS:
                errors.append('不支持的操作或断言：' + call.func.attr)
        if require_assertions and not assertion_count and not partial:
            errors.append(f'步骤 {context.args[0].value} 缺少可执行的 expect 断言。')
    expected = [s['step_number'] for s in snapshot.get('steps', [])]
    if numbers != expected or not expected:
        errors.append('脚本步骤须按顺序完整对应来源用例，不得省略或重复。')
    allowed_nodes = (ast.Module, ast.AsyncFunctionDef, ast.arguments, ast.arg, ast.AsyncWith,
                     ast.withitem, ast.Expr, ast.Await, ast.Call, ast.Attribute, ast.Name,
                     ast.Load, ast.Constant, ast.keyword, ast.Subscript, ast.List, ast.Tuple, ast.Dict,
                     ast.JoinedStr, ast.FormattedValue)
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            errors.append('不支持的 Python 语法：' + type(node).__name__)
        if isinstance(node, ast.FormattedValue):
            value = node.value
            # Interpolation is only for scalar parameters, never arbitrary expressions
            # or Playwright calls with side effects hidden inside a string.
            parameter = (isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name)
                         and value.value.id == 'params' and isinstance(value.slice, ast.Constant)
                         and isinstance(value.slice.value, str)) or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name) and value.func.value.id == 'params'
                and value.func.attr == 'get' and 1 <= len(value.args) <= 2 and not value.keywords
                and all(isinstance(arg, ast.Constant) for arg in value.args)
                and isinstance(value.args[0].value, str))
            if not parameter or node.conversion != -1 or node.format_spec is not None:
                errors.append('字符串插值只支持 params["参数名"] 或 params.get("参数名")，不支持表达式或格式指令。')
        if isinstance(node, ast.Name) and node.id not in {'page', 'params', 'record', 'expect'}:
            errors.append('不支持的变量：' + node.id)
        if isinstance(node, ast.Attribute) and node.attr not in LOCATORS | ACTIONS | ASSERTIONS | HELPERS | {'step', 'get', 'first', 'last'}:
            errors.append('不支持的属性：' + node.attr)
        if isinstance(node, ast.Call):
            root = _root(node)
            if isinstance(node.func, ast.Attribute) and node.func.attr in {'first', 'last'}:
                errors.append('Python Playwright 的 first / last 是属性，不能加括号调用。')
            if root not in {'page', 'expect', 'params', 'record'} or any(k.arg is None for k in node.keywords):
                errors.append('不允许动态调用。')
            if isinstance(node.func, ast.Name) and node.func.id != 'expect':
                errors.append('只允许 expect 全局函数。')
            if root == 'params' and (not isinstance(node.func, ast.Attribute) or node.func.attr != 'get'):
                errors.append('参数只支持取值。')
            if root == 'record' and (not isinstance(node.func, ast.Attribute)
                    or not isinstance(node.func.value, ast.Name) or node.func.attr not in HELPERS | {'step'}):
                errors.append('record 只允许 step 和指定的公共浮层操作。')
        if isinstance(node, ast.Subscript) and (not isinstance(node.value, ast.Name) or node.value.id != 'params'
                or not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str)):
            errors.append('只允许 params["参数名"] 取值。')
    if not partial and 'base_url' not in parameter_keys(code):
        errors.append('入口地址须使用 params["base_url"] 或 params.get("base_url")。')
    # Business conditions are reviewed against the entire case, not inferred from
    # a keyword such as "do not pass" in one expected-result sentence.
    if parameters is not None:
        errors.extend(parameter_errors(tree, parameters))
    return list(dict.fromkeys(errors))[:30]


def parameter_errors(tree, parameters):
    """Validate values as well as syntax; URL assertions accept exact strings, not globs."""
    if not isinstance(parameters, dict):
        return ['参数必须是 JSON 对象。']
    errors = []
    def scalar(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'params' and isinstance(node.slice, ast.Constant):
            return parameters.get(node.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _root(node) == 'params' and node.func.attr == 'get' and node.args:
            key = scalar(node.args[0])
            return parameters.get(key, scalar(node.args[1]) if len(node.args) > 1 else None)
        if isinstance(node, ast.JoinedStr):
            parts = [scalar(n.value) if isinstance(n, ast.FormattedValue) else scalar(n) for n in node.values]
            return ''.join(str(p) for p in parts) if all(p is not None for p in parts) else None
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'params' and isinstance(node.slice, ast.Constant):
            key = node.slice.value
            if key not in parameters and not re.search(r'password|secret|token|cookie', str(key), re.I):
                errors.append(f'缺少必填参数：{key}。')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ('to_have_url', 'not_to_have_url'):
                arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'url'), None)
                value = scalar(arg)
                if not isinstance(value, str) or not value.strip():
                    errors.append('to_have_url 必须提供非空的完整预期 URL。')
                elif '*' in value or value.startswith('^') or value.endswith('$'):
                    errors.append('to_have_url 的字符串是完整 URL 精确匹配，不支持 ** 通配符或正则字符串；请按原预期给出完整地址，不得删除导航断言。')
            if node.func.attr in {'click', 'dblclick', 'check', 'uncheck', 'set_checked'}:
                if any(k.arg == 'force' and not (isinstance(k.value, ast.Constant) and k.value.value is False) for k in node.keywords):
                    errors.append('不要使用 force=True 绕过可见性与可操作性检查；应修复定位和面板状态。')
    return errors


def parameter_keys(code):
    return set(re.findall(r'''params(?:\[\s*|\.get\(\s*)["']([^"']+)["']''', code or ''))
