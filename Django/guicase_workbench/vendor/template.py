# -*- coding: utf-8 -*-
"""
模板化导出：Excel（主交付物）/ JSON（browser-use 消费）/ Word（评审用）/ Gherkin feature。

导出格式严格对齐课题材料：
  - 04 散客预定功能+回归测试用例.pdf 的列名与层级路径范式
  - 05 测试用例各个属性的含义-20260324.pdf 的属性取值
  - 03 测试用例模版.pdf 的步骤/预期结果/测试数据写法
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .schema import (ACTION_LABEL, EXPORT_COLUMNS, COLUMN_WIDTH, DataType,
                     JudgeType, Objective, Priority, TestCase, SubObjective)

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
CELL_FONT = Font(name="微软雅黑", size=10)
GROUP_FILL = PatternFill("solid", fgColor="DCE6F1")
THIN = Side(style="thin", color="B7C6E3")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP_TOP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------

def _row_for_step(case: TestCase, st) -> Dict[str, Any]:
    from .schema import Step
    s: Step = st
    return {
        "用例排序": case.ordering,
        "ModuleName": case.module_name,
        "ModulePath": case.module_path,
        "TcSno": case.tc_sno,
        "TcName": case.tc_name,
        "前置条件": case.precondition,
        "摘要": case.summary,
        "TcStep": s.to_row_text(),
        "TcStepAction": f"{ACTION_LABEL.get(s.action, s.action)}",
        "TcStepTarget": s.target,
        "TcStepValue": s.value,
        "TcStepLocator": s.locator,
        "TcExpectedResult": f"{s.seq}. {s.expected}",
        "TcPriority": case.priority,
        "TcJudgeType": case.judge_type,
        "TcDataType": case.data_type,
        "TcObjective": case.objective,
        "TcSubObjective": case.sub_objective,
        "执行方式": case.automation,
        "关联截图": ", ".join(case.source_images),
        "自定义标签": ", ".join(case.tags),
        "备注": case.remark,
    }


def export_excel(cases: Iterable[TestCase], out_path: str | Path,
                 sheet_title: str = "功能测试用例",
                 with_guide: bool = True) -> Path:
    """导出模板化 Excel。一条用例占多行（每个步骤一行），与存量用例排版一致。"""
    cases = list(cases)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title

    # 表头
    for c, name in enumerate(EXPORT_COLUMNS, 1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(c)].width = COLUMN_WIDTH.get(name, 18)
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 26

    r = 2
    prev_sno = None
    for case in cases:
        # 每个用例之间空一行，视觉分组（沿用存量用例的可读性写法）
        if prev_sno is not None:
            r += 1
        first_row = r
        for st in case.steps:
            data = _row_for_step(case, st)
            for c, name in enumerate(EXPORT_COLUMNS, 1):
                cell = ws.cell(row=r, column=c, value=data.get(name, ""))
                cell.font = CELL_FONT
                cell.alignment = WRAP_TOP
                cell.border = BORDER
            r += 1
        # 同用例的标识列合并，避免重复刷屏
        for name in ("用例排序", "ModuleName", "ModulePath", "TcSno", "TcName", "前置条件",
                     "摘要", "TcPriority", "TcJudgeType", "TcDataType", "TcObjective",
                     "TcSubObjective", "执行方式", "关联截图", "自定义标签", "备注"):
            col = EXPORT_COLUMNS.index(name) + 1
            if r - 1 > first_row:
                ws.merge_cells(start_row=first_row, start_column=col,
                               end_row=r - 1, end_column=col)
                ws.cell(row=first_row, column=col).alignment = WRAP_TOP
        # 用例首行底色，便于快速分块
        for c in range(1, len(EXPORT_COLUMNS) + 1):
            ws.cell(row=first_row, column=c).fill = GROUP_FILL
        prev_sno = case.tc_sno

    ws.auto_filter.ref = f"A1:{get_column_letter(len(EXPORT_COLUMNS))}{r - 1}"

    # 枚举下拉，防止手填越界
    _add_validation(ws, "TcPriority", Priority.ALL, r)
    _add_validation(ws, "TcJudgeType", JudgeType.ALL, r)
    _add_validation(ws, "TcDataType", DataType.ALL, r)
    _add_validation(ws, "TcObjective", Objective.ALL, r)

    if with_guide:
        _add_guide_sheet(wb)

    wb.save(out_path)
    return out_path


def _add_validation(ws, col_name: str, values: List[str], last_row: int):
    col = get_column_letter(EXPORT_COLUMNS.index(col_name) + 1)
    dv = DataValidation(type="list", formula1='"' + ",".join(values) + '"',
                        allow_blank=True, showErrorMessage=True)
    dv.error = f"请从下拉中选择：{ ' / '.join(values) }"
    dv.errorTitle = "取值超出模板定义"
    ws.add_data_validation(dv)
    dv.add(f"{col}2:{col}{max(last_row, 200)}")


def _add_guide_sheet(wb: Workbook):
    """属性填写说明页，内容取自《05 测试用例各个属性的含义》。"""
    ws = wb.create_sheet("属性填写说明")
    rows = [
        ["属性", "是否必填", "可选值", "含义"],
        ["测试目的", "是", " / ".join(Objective.ALL),
         "验证新功能：当前版本新功能；回归测试：后续版本验证已有功能；其它：无法归入前两者"],
        ["测试目的细分", "是", " / ".join(SubObjective.ALL),
         "可多选，多个值用分号分隔并以分号结尾，如 系统;端到端;"],
        ["优先级", "是", " / ".join(Priority.ALL),
         "高：核心主流程/写操作正用例/已发生故障；中：一般正向或高频反向；低：低频、严苛异常、易用性与感官显示"],
        ["测试场景类型", "是", " / ".join(JudgeType.ALL),
         "正常场景：输入合法、环境正常、操作正确；异常场景：输入非法、环境异常、操作错误"],
        ["测试数据类型", "是", " / ".join(DataType.ALL),
         "典型值：常见输入；边界值：等价类边界；枚举值：枚举全部可选值"],
        ["前置条件", "否", "自由文本",
         "《03 模板》：前提可写在用例描述中，也可写在测试步骤中，一般写在测试步骤里"],
        ["操作步骤", "是", "序号 1.2.3.", "按操作顺序编号；页面输入/选择过多时，步骤中直接写明输入数据"],
        ["预期结果", "是", "每步必填",
         "《03 模板》：添加每步的测试步骤时，必须添加预期结果"],
        ["测试数据写法", "是", "A/B/C",
         "A 步骤中带测试数据值；B 步骤中不带测试数据；C 输入过多时提供测试数据截图"],
        ["目录层级", "是", "栏目-子栏目-标签页",
         "《04 说明》：S05 预订为栏目，散客预定为子栏目；层级过深时用 栏目-子栏目 两级"],
        ["自动化扩展列", "否", "TcStepAction/Target/Value/Locator",
         "供 browser-use 消费：navigate/click/input/select/check/upload/hover/wait/assert/screenshot"],
    ]
    for i, row in enumerate(rows, 1):
        for j, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = HEADER_FONT if i == 1 else CELL_FONT
            cell.alignment = WRAP_TOP
            cell.border = BORDER
            if i == 1:
                cell.fill = HEADER_FILL
    widths = [18, 10, 40, 78]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w


# --------------------------------------------------------------------------
# JSON（browser-use 直接消费）
# --------------------------------------------------------------------------

def export_json(cases: Iterable[TestCase], out_path: str | Path,
                meta: Dict[str, Any] | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta or {},
        "version": "1.0",
        "fieldSpec": {
            "priority": Priority.ALL,
            "judgeType": JudgeType.ALL,
            "dataType": DataType.ALL,
            "objective": Objective.ALL,
            "subObjective": SubObjective.ALL,
            "actions": list(ACTION_LABEL.keys()),
        },
        "cases": [c.to_dict() for c in cases],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_json(path: str | Path) -> List[TestCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [TestCase.from_dict(c) for c in data.get("cases", [])]


# --------------------------------------------------------------------------
# Word（评审/归档用）
# --------------------------------------------------------------------------

def export_docx(cases: Iterable[TestCase], out_path: str | Path,
                title: str = "测试用例说明书") -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.oxml.ns import qn

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.styles["Normal"].font.name = "宋体"
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.styles["Normal"].element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    doc.add_heading(title, level=1)
    cases = list(cases)
    doc.add_paragraph(f"用例总数：{len(cases)}")

    for i, c in enumerate(cases, 1):
        doc.add_heading(f"{i}. {c.tc_name}", level=2)
        tbl = doc.add_table(rows=0, cols=2)
        tbl.style = "Light Grid Accent 1"
        meta = [
            ("用例编号", c.tc_sno),
            ("所属模块", c.module_name),
            ("模块路径", c.module_path),
            ("前置条件", c.precondition),
            ("摘要", c.summary),
            ("优先级", c.priority),
            ("测试场景类型", c.judge_type),
            ("测试数据类型", c.data_type),
            ("测试目的", f"{c.objective}（{c.sub_objective}）"),
            ("执行方式", c.automation),
            ("关联截图", ", ".join(c.source_images)),
        ]
        for k, v in meta:
            if not v:
                continue
            row = tbl.add_row().cells
            row[0].text = k
            row[1].text = str(v)
            row[0].paragraphs[0].runs[0].bold = True

        doc.add_paragraph("操作步骤与预期结果：")
        st = doc.add_table(rows=1, cols=3)
        st.style = "Light Grid Accent 1"
        hdr = st.rows[0].cells
        for j, h in enumerate(["序号", "操作步骤", "预期结果"]):
            hdr[j].text = h
            hdr[j].paragraphs[0].runs[0].bold = True
        for s in c.steps:
            row = st.add_row().cells
            row[0].text = str(s.seq)
            row[1].text = s.desc
            row[2].text = s.expected
        doc.add_paragraph("")

    doc.save(out_path)
    return out_path


# --------------------------------------------------------------------------
# Gherkin feature（对接 02 散客预定feature文件.pdf 的写法）
# --------------------------------------------------------------------------

def export_feature(cases: Iterable[TestCase], out_path: str | Path,
                   feature_name: str = "散客预订与出票") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"Feature: {feature_name}", ""]
    for c in cases:
        lines.append(f"  # {c.tc_sno}  优先级:{c.priority}  {c.judge_type}  {c.data_type}")
        lines.append(f"  Scenario: {c.tc_name}")
        if c.precondition:
            lines.append(f"    Given {c.precondition}")
        for s in c.steps:
            kw = "When"
            verb = {"click": "点击", "input": "输入", "select": "选择",
                    "check": "勾选", "navigate": "打开", "assert": "校验"}.get(s.action, "执行")
            target = f"【{s.target}】" if s.target else ""
            value = f' 值为 "{s.value}"' if s.value else ""
            lines.append(f"    {kw} {verb}{target}{value}    # {s.desc}")
            lines.append(f"    Then {s.expected}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
