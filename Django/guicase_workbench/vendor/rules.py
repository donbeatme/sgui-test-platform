# -*- coding: utf-8 -*-
"""
格式化校验规则。

课题里那些"每个步骤必须填预期结果""优先级只能取高中低"的要求，
以前是写死在 schema.validate() 里的 if 语句，改一条要动代码。

现在它们是数据：存在 data/rules.json，界面上能增删改、能关掉某条、能调整级别。
这些规则有两处用途：
  1. 约束模型 —— 渲染成自然语言塞进提示词，让模型从一开始就不跑偏
  2. 校验结果 —— 生成完逐条比对，产出问题清单

规则类型：
  required  字段必填
  enum      取值必须在给定集合内
  regex     必须匹配正则
  not_regex 不得匹配正则（用来抓"正常显示"这类不可判定的表述）
  length    字符长度范围
  count     数量范围（步骤数等）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
RULE_FILE = DATA_DIR / "rules.json"

TYPES = [
    {"key": "required", "name": "必填", "value_hint": "无需参数",
     "desc": "字段不能为空"},
    {"key": "enum", "name": "枚举", "value_hint": '["高","中","低"]',
     "desc": "取值必须落在给定集合内"},
    {"key": "regex", "name": "正则匹配", "value_hint": '^\\d{2,3}\\s+P[0-2]\\s+',
     "desc": "取值必须符合正则"},
    {"key": "not_regex", "name": "禁止匹配", "value_hint": "正常显示|正确显示|无异常",
     "desc": "取值不得出现这些表述"},
    {"key": "length", "name": "长度范围", "value_hint": '{"min":1,"max":100}',
     "desc": "字符数在区间内"},
    {"key": "count", "name": "数量范围", "value_hint": '{"min":1,"max":30}',
     "desc": "集合元素个数在区间内（如步骤数）"},
]

# 可校验的字段（界面下拉用）
FIELDS = [
    {"key": "tc_name", "name": "用例名称"},
    {"key": "module_name", "name": "末级功能名"},
    {"key": "module_path", "name": "模块路径"},
    {"key": "precondition", "name": "前置条件"},
    {"key": "summary", "name": "摘要"},
    {"key": "priority", "name": "优先级"},
    {"key": "judge_type", "name": "测试场景类型"},
    {"key": "data_type", "name": "测试数据类型"},
    {"key": "objective", "name": "测试目的"},
    {"key": "sub_objective", "name": "测试目的细分"},
    {"key": "steps", "name": "步骤（数量）"},
    {"key": "steps.desc", "name": "步骤描述"},
    {"key": "steps.expected", "name": "步骤预期结果"},
    {"key": "steps.action", "name": "步骤动作类型"},
    {"key": "steps.target", "name": "步骤操作对象"},
]

DEFAULT_RULES: List[Dict[str, Any]] = [
    {"id": "r_name_required", "field": "tc_name", "type": "required", "value": None,
     "level": "error", "enabled": True, "builtin": True,
     "message": "用例名称为空", "desc": "《03 测试用例模版》要求用例必须有名称"},

    {"id": "r_name_format", "field": "tc_name", "type": "regex",
     "value": r"^\d{2,3}\s+P[0-2]\s+\S+",
     "level": "warn", "enabled": True, "builtin": True,
     "message": "用例名称不符合「序号 + P0/P1/P2 + 场景简述」写法",
     "desc": "存量用例写法：01 P0 国内-单段-1成人1儿童1婴儿，有运价-生成PNR"},

    {"id": "r_name_len", "field": "tc_name", "type": "length",
     "value": {"min": 4, "max": 120},
     "level": "warn", "enabled": True, "builtin": True,
     "message": "用例名称长度应在 4-120 字之间", "desc": "过长不便检索，过短表述不清"},

    {"id": "r_steps_count", "field": "steps", "type": "count",
     "value": {"min": 1, "max": 40},
     "level": "error", "enabled": True, "builtin": True,
     "message": "用例必须至少有 1 个操作步骤", "desc": ""},

    {"id": "r_step_expected", "field": "steps.expected", "type": "required", "value": None,
     "level": "error", "enabled": True, "builtin": True,
     "message": "第{index}步缺少预期结果（模板要求：添加每步的测试步骤时，必须添加预期结果）",
     "desc": "课题硬性要求，缺预期结果的用例无法入库"},

    {"id": "r_step_desc", "field": "steps.desc", "type": "required", "value": None,
     "level": "error", "enabled": True, "builtin": True,
     "message": "第{index}步缺少步骤描述", "desc": ""},

    {"id": "r_expected_todo", "field": "steps.expected", "type": "not_regex",
     "value": "TODO|待补充|请补充|略|同上",
     "level": "error", "enabled": True, "builtin": True,
     "message": "第{index}步预期结果是占位符（{hit}），必须补充可判定的预期",
     "desc": "离线模式会填 TODO 占位，这类用例不能入库也不能自动化执行"},

    {"id": "r_expected_vague", "field": "steps.expected", "type": "not_regex",
     "value": "正常显示|正确显示|无异常|一切正常|成功即可",
     "level": "warn", "enabled": True, "builtin": True,
     "message": "第{index}步预期结果过于笼统（{hit}），无法断言，建议写明具体提示文案或数据变化",
     "desc": "「正常显示」这类表述在自动化执行时无法判定通过与否"},

    {"id": "r_priority", "field": "priority", "type": "enum",
     "value": ["高", "中", "低"], "level": "error", "enabled": True, "builtin": True,
     "message": "优先级取值非法：{value}（应为 高/中/低）", "desc": "《05 测试用例各个属性的含义》"},

    {"id": "r_judge", "field": "judge_type", "type": "enum",
     "value": ["正常场景", "异常场景"], "level": "error", "enabled": True, "builtin": True,
     "message": "测试场景类型取值非法：{value}（应为 正常场景/异常场景）", "desc": ""},

    {"id": "r_datatype", "field": "data_type", "type": "enum",
     "value": ["典型值", "边界值", "枚举值"], "level": "error", "enabled": True, "builtin": True,
     "message": "测试数据类型取值非法：{value}（应为 典型值/边界值/枚举值）", "desc": ""},

    {"id": "r_objective", "field": "objective", "type": "enum",
     "value": ["验证新功能", "回归测试", "其它"], "level": "error", "enabled": True, "builtin": True,
     "message": "测试目的取值非法：{value}（应为 验证新功能/回归测试/其它）", "desc": ""},

    {"id": "r_subobj_semicolon", "field": "sub_objective", "type": "regex",
     "value": r"(;|；)$", "level": "warn", "enabled": True, "builtin": True,
     "message": "测试目的细分应以分号结尾，例如「系统;端到端;」", "desc": "《05》规定的写法"},

    {"id": "r_module_path", "field": "module_path", "type": "regex",
     "value": r"^SGUI", "level": "warn", "enabled": True, "builtin": True,
     "message": "模块路径应以系统版本开头（如 SGUI2.0/...）", "desc": ""},
]


# ------------------------------------------------------------------ 读写
def _load_raw() -> List[Dict[str, Any]]:
    if RULE_FILE.exists():
        try:
            data = json.loads(RULE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_RULES))


def _save_raw(items: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RULE_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_rules(enabled_only: bool = False) -> List[Dict[str, Any]]:
    items = _load_raw()
    return [r for r in items if r.get("enabled")] if enabled_only else items


def get_rule(rid: str) -> Optional[Dict[str, Any]]:
    return next((r for r in _load_raw() if r.get("id") == rid), None)


def _validate_rule(r: Dict[str, Any]) -> None:
    if r.get("type") not in {t["key"] for t in TYPES}:
        raise ValueError(f"未知规则类型：{r.get('type')}")
    if r.get("level") not in ("error", "warn"):
        raise ValueError("级别只能是 error 或 warn")
    # 正则类规则先编译一遍，写错正则当场报错，别等到校验时才炸
    if r["type"] in ("regex", "not_regex"):
        try:
            re.compile(r.get("value") or "")
        except re.error as e:
            raise ValueError(f"正则无法编译：{e}")
    if r["type"] in ("length", "count"):
        v = r.get("value") or {}
        if not isinstance(v, dict):
            raise ValueError("length/count 规则的取值须为 {\"min\":n,\"max\":m}")
    if r["type"] == "enum":
        v = r.get("value")
        if not isinstance(v, list) or not v:
            raise ValueError("enum 规则的取值须为非空数组")


def create_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    items = _load_raw()
    rid = (rule.get("id") or "").strip()
    if not rid:
        import time
        slug = re.sub(r"\W+", "", rule.get("field", "")) or "field"
        rid = f"r_{slug}_{int(time.time()) % 100000}"
    if any(r.get("id") == rid for r in items):
        raise ValueError(f"规则标识已存在：{rid}")
    item = {"id": rid, "field": rule.get("field", ""), "type": rule.get("type", ""),
            "value": rule.get("value"), "level": rule.get("level", "error"),
            "enabled": bool(rule.get("enabled", True)), "builtin": False,
            "message": rule.get("message", ""), "desc": rule.get("desc", "")}
    _validate_rule(item)
    items.append(item)
    _save_raw(items)
    return item


def update_rule(rid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    items = _load_raw()
    for r in items:
        if r.get("id") == rid:
            for k in ("field", "type", "value", "level", "enabled", "message", "desc"):
                if k in patch:
                    r[k] = patch[k]
            _validate_rule(r)
            _save_raw(items)
            return r
    raise KeyError(f"规则不存在：{rid}")


def delete_rule(rid: str) -> bool:
    items = _load_raw()
    rest = [r for r in items if r.get("id") != rid]
    if len(rest) == len(items):
        return False
    _save_raw(rest)
    return True


def reset_all() -> int:
    _save_raw(json.loads(json.dumps(DEFAULT_RULES)))
    return len(DEFAULT_RULES)


def toggle_rule(rid: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
    items = _load_raw()
    for r in items:
        if r.get("id") == rid:
            r["enabled"] = (not r.get("enabled")) if enabled is None else bool(enabled)
            _save_raw(items)
            return r
    raise KeyError(f"规则不存在：{rid}")


# ------------------------------------------------------------------ 应用
def _get_field(case: Any, field: str) -> List[Any]:
    """取字段值。steps.xxx 会展开成每个步骤的对应属性列表。"""
    if field.startswith("steps."):
        attr = field.split(".", 1)[1]
        return [getattr(s, attr, "") for s in getattr(case, "steps", [])]
    return [getattr(case, field, "")]


def apply_rules(case: Any, rules: Optional[List[Dict[str, Any]]] = None
                ) -> List[Dict[str, Any]]:
    """
    对单条用例套用规则，返回问题清单。

    每条问题：{rule_id, field, level, message, index}
    index 为步骤序号（从第 1 步起），非步骤类规则为 0。
    """
    rules = rules if rules is not None else list_rules(enabled_only=True)
    issues: List[Dict[str, Any]] = []

    for r in rules:
        if not r.get("enabled"):
            continue
        rtype, rval = r.get("type"), r.get("value")
        msg_tpl = r.get("message") or "校验未通过"
        field = r.get("field", "")

        if field == "steps" and rtype == "count":
            n = len(getattr(case, "steps", []) or [])
            lo = (rval or {}).get("min", 0)
            hi = (rval or {}).get("max", 10 ** 6)
            if n < lo or n > hi:
                issues.append({"rule_id": r["id"], "field": field, "level": r.get("level"),
                               "message": msg_tpl.format(value=n, index=0), "index": 0})
            continue

        values = _get_field(case, field)
        if not values:
            values = [""]
        for i, v in enumerate(values, 1):
            v = "" if v is None else str(v)
            bad = None
            hit = ""

            if rtype == "required":
                if not v.strip():
                    bad = True
            elif rtype == "enum":
                if v.strip() not in [str(x) for x in (rval or [])]:
                    bad = True
            elif rtype == "regex":
                if not re.search(str(rval or ""), v):
                    bad = True
            elif rtype == "not_regex":
                m = re.search(str(rval or ""), v)
                if m:
                    bad = True
                    hit = m.group(0)
            elif rtype == "length":
                lo = (rval or {}).get("min", 0)
                hi = (rval or {}).get("max", 10 ** 6)
                if not (lo <= len(v) <= hi):
                    bad = True
            elif rtype == "count":
                lo = (rval or {}).get("min", 0)
                hi = (rval or {}).get("max", 10 ** 6)
                if not (lo <= len(v) <= hi):
                    bad = True

            if bad:
                issues.append({
                    "rule_id": r["id"], "field": field, "level": r.get("level"),
                    "message": msg_tpl.format(value=v, index=i, hit=hit),
                    "index": i if field.startswith("steps.") else 0,
                })
    return issues


def format_rules_natural(rules: Optional[List[Dict[str, Any]]] = None) -> str:
    """把规则渲染成自然语言，注入提示词，让模型生成时就遵守。"""
    rules = rules if rules is not None else list_rules(enabled_only=True)
    if not rules:
        return "【输出约束】\n（未配置校验规则）"
    lines = ["【输出约束】生成时必须满足以下规则："]
    for i, r in enumerate(rules, 1):
        rtype, rval = r.get("type"), r.get("value")
        if rtype == "required":
            cond = "不能为空"
        elif rtype == "enum":
            cond = "只能取 " + " / ".join(str(x) for x in (rval or []))
        elif rtype == "regex":
            cond = f"须匹配模式 `{rval}`"
        elif rtype == "not_regex":
            cond = f"不得出现 `{rval}` 这类表述"
        elif rtype == "length":
            cond = f"长度 {rval.get('min', 0)}-{rval.get('max', '∞')} 字"
        elif rtype == "count":
            cond = f"数量 {rval.get('min', 0)}-{rval.get('max', '∞')} 个"
        else:
            cond = ""
        level = "硬性" if r.get("level") == "error" else "建议"
        # 优先用说明（写给模型的通识解释）；没写说明就用提示语兜底，
        # 否则自建规则（往往只填了提示语）根本进不了提示词，等于配了不生效
        tail = (r.get("desc") or r.get("message") or "").strip()
        lines.append(f"{i}. [{level}] {r.get('field', '')} {cond}。{tail}".rstrip("。") + "。")
    return "\n".join(lines)


def summary(cases: List[Any], rules: Optional[List[Dict[str, Any]]] = None
            ) -> Dict[str, Any]:
    """对一批用例跑规则，汇总问题数。"""
    total_err = total_warn = 0
    per_case: List[Dict[str, Any]] = []
    for i, c in enumerate(cases):
        iss = apply_rules(c, rules)
        e = sum(1 for x in iss if x["level"] == "error")
        w = len(iss) - e
        total_err += e
        total_warn += w
        if iss:
            per_case.append({"index": i, "case": getattr(c, "tc_name", ""),
                             "errors": e, "warnings": w, "issues": iss})
    return {"errors": total_err, "warnings": total_warn,
            "cases_with_issue": len(per_case), "detail": per_case}


def meta() -> Dict[str, Any]:
    return {"types": TYPES, "fields": FIELDS}
