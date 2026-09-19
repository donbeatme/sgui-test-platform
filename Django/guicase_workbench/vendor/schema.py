# -*- coding: utf-8 -*-
"""
用例数据模型与字段枚举。

字段定义来源（课题材料）：
  - 05 测试用例各个属性的含义-20260324.pdf  -> 各属性取值枚举与含义
  - 03 测试用例模版.pdf                     -> 用例概述 / 前置条件 / 操作步骤 / 预期结果 / 测试数据
  - 04 散客预定功能+回归测试用例.pdf         -> 存量用例的列名与 ModulePath 书写范式
  - 04 测试用例说明.pdf                     -> 目录层级（栏目-子栏目-标签页）
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional


# --------------------------------------------------------------------------
# 一、字段枚举（严格对齐《05 测试用例各个属性的含义》）
# --------------------------------------------------------------------------

class Priority:
    """优先级：高 / 中 / 低"""
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"
    ALL = [HIGH, MEDIUM, LOW]
    DESC = {
        HIGH: "核心功能主流程、写操作正用例、已发生故障的用例",
        MEDIUM: "一般功能正向用例，或核心功能发生频率高的反向用例",
        LOW: "较少使用功能、严苛异常条件、提示易用性与页面元素感官显示类",
    }


class JudgeType:
    """测试场景类型"""
    NORMAL = "正常场景"
    ABNORMAL = "异常场景"
    ALL = [NORMAL, ABNORMAL]


class DataType:
    """测试数据类型"""
    TYPICAL = "典型值"
    BOUNDARY = "边界值"
    ENUM = "枚举值"
    ALL = [TYPICAL, BOUNDARY, ENUM]


class Objective:
    """测试目的"""
    NEW_FEATURE = "验证新功能"
    REGRESSION = "回归测试"
    OTHER = "其它"
    ALL = [NEW_FEATURE, REGRESSION, OTHER]


class SubObjective:
    """测试目的细分（可多选，存量用例以分号分隔拼装）"""
    SYSTEM = "系统"
    INTEGRATION = "系统集成"
    E2E = "端到端"
    SMOKE = "冒烟"
    SECURITY_KC = "安全kc"
    DATA_PREP = "其它-测试数据准备"
    ENV_CHECK = "其它-环境验证"
    COMPLIANCE = "其它-合规性测试"
    NON_INNER = "其它-非内测使用"
    ALL = [SYSTEM, INTEGRATION, E2E, SMOKE, SECURITY_KC,
           DATA_PREP, ENV_CHECK, COMPLIANCE, NON_INNER]

    @staticmethod
    def join(items: List[str]) -> str:
        """拼成存量用例里的写法，如 '系统;端到端;'"""
        if not items:
            return ""
        return ";".join(items) + ";"


# --------------------------------------------------------------------------
# 二、browser-use 可执行的结构化动作
# --------------------------------------------------------------------------

ACTION_TYPES = [
    "navigate",   # 打开页面 / 跳转
    "click",      # 点击元素
    "input",      # 输入文本
    "select",     # 下拉选择
    "check",      # 勾选复选框
    "upload",     # 上传文件
    "hover",      # 悬停
    "wait",       # 等待
    "assert",     # 断言校验
    "screenshot",  # 截图留证
]

ACTION_LABEL = {
    "navigate": "打开",
    "click": "点击",
    "input": "输入",
    "select": "选择",
    "check": "勾选",
    "upload": "上传",
    "hover": "悬停",
    "wait": "等待",
    "assert": "校验",
    "screenshot": "截图",
}


@dataclass
class Step:
    """单个操作步骤。desc 供人读（写入 Excel），action/target/value 供机器执行。"""
    seq: int = 0
    desc: str = ""
    expected: str = ""
    action: str = "click"
    target: str = ""
    value: str = ""
    locator: str = ""          # 可选：CSS/XPath，优先使用；为空时 browser-use 用自然语言定位
    screenshot: str = ""       # 关联证据截图文件名
    timeout: int = 15          # 该步最长等待秒数

    def to_row_text(self) -> str:
        """写入 Excel 的操作步骤列：'1. 点击【航班】Tab'"""
        return f"{self.seq}. {self.desc}"


@dataclass
class TestCase:
    """一条测试用例。"""
    tc_sno: str = ""                      # 用例编号 SGUI-TC-xxxxxxxxxxx
    tc_name: str = ""                     # 用例名称
    module_name: str = ""                 # 模块名（末级）
    module_path: str = ""                 # 完整层级路径
    precondition: str = ""                # 前置条件
    summary: str = ""                     # 摘要
    steps: List[Step] = field(default_factory=list)
    priority: str = Priority.MEDIUM
    judge_type: str = JudgeType.NORMAL
    data_type: str = DataType.TYPICAL
    objective: str = Objective.NEW_FEATURE
    sub_objective: str = ""               # 如 '系统;端到端;'
    ordering: int = 0                     # 用例排序
    remark: str = ""                      # 备注
    source_images: List[str] = field(default_factory=list)  # 生成依据的截图
    tags: List[str] = field(default_factory=list)           # 自定义标签
    automation: str = "手工"              # 手工 / 自动化

    # ---------------- 序列化 ----------------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["steps"] = [asdict(s) for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TestCase":
        d = dict(d)
        steps = [Step(**s) for s in (d.pop("steps", []) or [])]
        known = {f for f in cls.__dataclass_fields__}
        extra = {k: v for k, v in d.items() if k not in known}
        body = {k: v for k, v in d.items() if k in known}
        obj = cls(**body)
        obj.steps = steps
        for k, v in extra.items():
            setattr(obj, k, v)
        return obj

    # ---------------- 校验 ----------------
    def validate(self) -> List[str]:
        """按《03 测试用例模版》校验：每步必须有预期结果。"""
        errs: List[str] = []
        if not self.tc_name:
            errs.append("用例名称为空")
        if not self.steps:
            errs.append("无操作步骤")
        for i, s in enumerate(self.steps, 1):
            if not s.expected.strip():
                errs.append(f"第{i}步缺少预期结果（模板要求：添加每步的测试步骤时，必须添加预期结果）")
            if s.seq != i:
                s.seq = i  # 自动纠正序号
        if self.priority not in Priority.ALL:
            errs.append(f"优先级取值非法：{self.priority}")
        if self.judge_type not in JudgeType.ALL:
            errs.append(f"测试场景类型取值非法：{self.judge_type}")
        if self.data_type not in DataType.ALL:
            errs.append(f"测试数据类型取值非法：{self.data_type}")
        if self.objective not in Objective.ALL:
            errs.append(f"测试目的取值非法：{self.objective}")
        return errs

    # ---------------- 辅助 ----------------
    def step_text(self) -> str:
        return "\n".join(s.to_row_text() for s in self.steps)

    def expected_text(self) -> str:
        return "\n".join(f"{s.seq}. {s.expected}" for s in self.steps)

    def gen_sno(self, seed: Optional[int] = None) -> str:
        """生成与存量同风格的用例编号 SGUI-TC-<11位数字>"""
        if self.tc_sno and re.match(r"^SGUI-TC-\d{11}$", self.tc_sno):
            return self.tc_sno
        rnd = random.Random(seed if seed is not None else (hash(self.tc_name) & 0xFFFFFFFF))
        self.tc_sno = f"SGUI-TC-{rnd.randint(31200000000, 31299999999)}"
        return self.tc_sno


# --------------------------------------------------------------------------
# 三、Excel 列定义（对齐存量用例表头）
# --------------------------------------------------------------------------

# 存量用例 Excel 的列名（04 散客预定功能+回归测试用例.pdf 第一个 sheet）
LEGACY_COLUMNS = [
    "ModuleName", "ModulePath", "TcSno", "TcName", "TcStep",
    "TcExpectedResult", "TcPriority", "TcJudgeType", "TcDataType",
    "TcObjective", "TcSubObjective",
]

# 导出模板列：存量列 + 自动化所需扩展列
EXPORT_COLUMNS = [
    "用例排序", "ModuleName", "ModulePath", "TcSno", "TcName",
    "前置条件", "摘要",
    "TcStep", "TcStepAction", "TcStepTarget", "TcStepValue", "TcStepLocator",
    "TcExpectedResult",
    "TcPriority", "TcJudgeType", "TcDataType", "TcObjective", "TcSubObjective",
    "执行方式", "关联截图", "自定义标签", "备注",
]

COLUMN_WIDTH = {
    "用例排序": 8, "ModuleName": 20, "ModulePath": 46, "TcSno": 22, "TcName": 34,
    "前置条件": 30, "摘要": 30,
    "TcStep": 46, "TcStepAction": 12, "TcStepTarget": 26, "TcStepValue": 22,
    "TcStepLocator": 24, "TcExpectedResult": 40,
    "TcPriority": 9, "TcJudgeType": 11, "TcDataType": 10,
    "TcObjective": 12, "TcSubObjective": 18,
    "执行方式": 10, "关联截图": 26, "自定义标签": 16, "备注": 24,
}
