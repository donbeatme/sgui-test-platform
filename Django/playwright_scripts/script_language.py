"""Browser-only Python with local variables and bounded, state-aware control flow.

This is a capability/format check, not an operating-system sandbox. Generated
scripts cannot import modules, catch failures or access arbitrary Python objects.
"""
import ast

READS = {'count', 'is_visible', 'is_hidden', 'is_enabled', 'is_disabled',
         'is_checked', 'is_editable', 'input_value', 'inner_text', 'text_content',
         'get_attribute', 'all_text_contents', 'title', 'bounding_box'}
EXTRA_LOCATORS = {'frame_locator', 'and_', 'or_'}
SCALARS = {'str', 'int', 'float', 'bool', 'len', 'min', 'max'}
RESERVED = {'page', 'params', 'record', 'expect', 're', 'range', *SCALARS}
VALUES = {'scalar', 'match'}
REGEX_MATCH = {'match', 'search', 'fullmatch'}
REGEX_FLAGS = {'I', 'M', 'S', 'X', 'A', 'U', 'IGNORECASE', 'MULTILINE', 'DOTALL', 'VERBOSE', 'ASCII', 'UNICODE', 'NOFLAG'}


def required(tree):
    return any(isinstance(n, (ast.Assign, ast.If, ast.For, ast.Assert)) or
               (isinstance(n, ast.Attribute) and n.attr in READS | EXTRA_LOCATORS | REGEX_MATCH | {'compile', 'url', 'keyboard'})
               for n in ast.walk(tree))


class Language:
    def __init__(self):
        self.names = {'page': 'page', 'params': 'params', 'record': 'record', 're': 'regex_module'}
        self.assertions = 0

    def fail(self, message):
        raise ValueError(message)

    def expression(self, node):
        from .contract import LOCATORS, ACTIONS, ASSERTIONS, HELPERS
        if isinstance(node, ast.Constant):
            if type(node.value) not in (str, int, float, bool, type(None)):
                self.fail('只支持普通标量常量。')
            return 'scalar'
        if isinstance(node, ast.Name):
            if node.id not in self.names:
                self.fail('变量尚未定义：' + node.id)
            return self.names[node.id]
        if isinstance(node, ast.Subscript):
            parent = self.expression(node.value)
            if parent == 'params':
                if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
                    self.fail('params 下标须为参数名称。')
            elif parent == 'scalar':
                if isinstance(node.slice, ast.Slice):
                    for bound in (node.slice.lower, node.slice.upper, node.slice.step):
                        if bound is not None and self.expression(bound) != 'scalar':
                            self.fail('切片边界须为普通数值。')
                elif self.expression(node.slice) != 'scalar':
                    self.fail('下标须为普通数值或文本。')
            else:
                self.fail('只能对参数和读取到的普通数据使用下标。')
            return 'scalar'
        if isinstance(node, (ast.List, ast.Tuple, ast.Dict)):
            values = [*node.keys, *node.values] if isinstance(node, ast.Dict) else node.elts
            if any(n is None for n in values): self.fail('不支持字典展开。')
            for value in values:
                if self.expression(value) not in VALUES | {'regex'}:
                    self.fail('数据容器不能保存页面或运行时对象。')
            return 'scalar'
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    if self.expression(value.value) != 'scalar': self.fail('插值必须是标量。')
                    if value.format_spec: self.expression(value.format_spec)
            return 'scalar'
        if isinstance(node, ast.Await):
            value = self.expression(node.value)
            if not value.startswith('await:'): self.fail('await 必须用于异步页面操作。')
            return value[6:]
        if isinstance(node, ast.Attribute):
            parent = self.expression(node.value)
            if parent == 'locator' and node.attr in ('first', 'last'): return 'locator'
            if parent == 'page' and node.attr == 'url': return 'scalar'
            if parent == 'page' and node.attr == 'keyboard': return 'keyboard'
            if parent == 'regex_module' and node.attr in REGEX_FLAGS: return 'scalar'
            self.fail('不支持的属性：' + node.attr)
        if isinstance(node, ast.Compare):
            if any(not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot)) for op in node.ops):
                self.fail('不支持的比较。')
            if any(self.expression(n) not in VALUES for n in [node.left, *node.comparators]): self.fail('比较必须使用页面读取值或参数。')
            return 'scalar'
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                if self.expression(value) not in VALUES: self.fail('条件必须是读取值。')
            return 'scalar'
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
            if self.expression(node.operand) not in VALUES: self.fail('运算必须是读取值。')
            return 'scalar'
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.BitOr)):
            if self.expression(node.left) != 'scalar' or self.expression(node.right) != 'scalar': self.fail('运算必须是标量。')
            return 'scalar'
        if not isinstance(node, ast.Call): self.fail('不支持的表达式：' + type(node).__name__)
        if any(k.arg is None for k in node.keywords): self.fail('不支持动态参数展开。')
        args = [self.expression(n) for n in node.args]
        for keyword in node.keywords: self.expression(keyword.value)
        if isinstance(node.func, ast.Name):
            if node.func.id == 'expect' and args and args[0] in ('page', 'locator'):
                return 'assertion'
            if node.func.id in SCALARS and all(a in VALUES for a in args): return 'scalar'
            self.fail('不支持的全局调用：' + node.func.id)
        if not isinstance(node.func, ast.Attribute): self.fail('不支持动态调用。')
        parent, method = self.expression(node.func.value), node.func.attr
        if parent in ('page', 'locator', 'frame') and method in LOCATORS | EXTRA_LOCATORS:
            return 'frame' if method == 'frame_locator' else 'locator'
        if parent in ('page', 'locator', 'keyboard') and method in ACTIONS | {'insert_text', 'press_sequentially', 'clear'}:
            return 'await:scalar'
        if parent in ('page', 'locator') and method in READS: return 'await:scalar'
        if parent == 'assertion' and method in ASSERTIONS:
            self.assertions += 1
            return 'await:scalar'
        if parent == 'params' and method == 'get': return 'scalar'
        if parent == 'record' and method in HELPERS:
            if method in {'observe_state', 'guard_state', 'stop', 'expect_aligned'}: self.assertions += 1
            return 'await:scalar'
        if parent == 'regex_module' and method == 'compile': return 'regex'
        if parent in ('regex_module', 'regex') and method in REGEX_MATCH: return 'match'
        if parent in ('regex_module', 'regex') and method in {'sub', 'subn', 'split', 'findall'}: return 'scalar'
        if parent == 'regex_module' and method == 'escape': return 'scalar'
        if parent == 'match' and method in {'group', 'groups', 'groupdict', 'span', 'start', 'end'}: return 'scalar'
        if parent == 'scalar' and method in {'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'casefold', 'startswith', 'endswith', 'replace', 'split', 'rsplit', 'join', 'zfill', 'isdigit', 'get'}: return 'scalar'
        self.fail('不支持的页面方法：' + method)

    def statements(self, statements, in_loop=False):
        for statement in statements:
            if isinstance(statement, ast.Assign):
                if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name): self.fail('只支持局部变量赋值。')
                name = statement.targets[0].id
                if name in RESERVED or name.startswith('_'): self.fail('不能覆盖运行时对象：' + name)
                value = self.expression(statement.value)
                if value.startswith('await:') or value in ('page', 'params', 'record', 'regex_module'): self.fail('请 await 异步读取，不能复制运行时对象。')
                self.names[name] = value
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Await):
                self.expression(statement.value)
            elif isinstance(statement, ast.If):
                if self.expression(statement.test) not in VALUES: self.fail('if 条件须为页面状态或参数。')
                before = dict(self.names)
                self.statements(statement.body, in_loop)
                left = dict(self.names)
                self.names = dict(before)
                self.statements(statement.orelse, in_loop)
                self.names = {k: v for k, v in self.names.items() if left.get(k) == v}
            elif isinstance(statement, ast.For):
                iterator = statement.iter
                if (not isinstance(iterator, ast.Call) or not isinstance(iterator.func, ast.Name) or iterator.func.id != 'range'
                        or iterator.keywords or len(iterator.args) != 1 or not isinstance(iterator.args[0], ast.Constant)
                        or type(iterator.args[0].value) is not int or not 1 <= iterator.args[0].value <= 12
                        or not isinstance(statement.target, ast.Name) or statement.target.id in RESERVED
                        or statement.target.id.startswith('_')):
                    self.fail('循环只支持 for attempt in range(1至12的整数)。')
                self.names[statement.target.id] = 'scalar'
                before = dict(self.names)
                self.statements(statement.body, True)
                self.names = before
                self.statements(statement.orelse, in_loop)
            elif isinstance(statement, ast.Break) and in_loop:
                pass
            elif isinstance(statement, ast.Assert):
                if self.expression(statement.test) not in VALUES: self.fail('assert 条件必须是读取的页面值。')
                if statement.msg: self.expression(statement.msg)
                self.assertions += 1
            else:
                self.fail('不支持的语句：' + type(statement).__name__ + '；可使用变量、if、有限 for、页面操作和断言。')


def validate_function(function, snapshot, *, partial, require_assertions):
    language, numbers, errors = Language(), [], []
    for block in function.body:
        if not isinstance(block, ast.AsyncWith) or len(block.items) != 1:
            return ['每个业务步骤必须使用 async with record.step(步骤编号):。']
        context = block.items[0].context_expr
        if (block.items[0].optional_vars or not isinstance(context, ast.Call) or not isinstance(context.func, ast.Attribute)
                or not isinstance(context.func.value, ast.Name) or context.func.value.id != 'record' or context.func.attr != 'step'
                or len(context.args) != 1 or context.keywords or not isinstance(context.args[0], ast.Constant)
                or type(context.args[0].value) is not int):
            return ['步骤编号必须是原用例中的整数。']
        number = context.args[0].value
        numbers.append(number)
        before = language.assertions
        try:
            language.statements(block.body)
        except ValueError as exc:
            errors.append(str(exc))
        if require_assertions and not partial and language.assertions == before:
            errors.append(f'步骤 {number} 缺少可执行断言。')
    if numbers != [s['step_number'] for s in snapshot.get('steps', [])] or not numbers:
        errors.append('脚本步骤须按顺序完整对应来源用例，不得省略或重复。')
    return errors
