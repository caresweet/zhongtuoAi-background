# -*- coding: utf-8 -*-
"""稳评《数据资料表》Excel：生成 → 填写 → 读回 → 写入事实卡。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook

from app.services.stability_intake import (
    apply_intake, apply_intake_from_state, build_intake_workbook,
    read_intake_workbook, SHEET_BASE, SHEET_TIME, SHEET_SURVEY,
    SHEET_CONTACT,
)


def _fill(path, sheet, pairs):
    wb = load_workbook(path)
    ws = wb[sheet]
    for r in range(2, ws.max_row + 1):
        label = str(ws.cell(row=r, column=1).value or "").strip()
        if label in pairs:
            ws.cell(row=r, column=2, value=pairs[label])
    wb.save(path)


def test_roundtrip(tmp_path):
    p = tmp_path / "稳评数据资料表.xlsx"
    build_intake_workbook(p)
    _fill(p, SHEET_BASE, {
        "项目名称": "洪拟征告〔2026〕7号土地征收决策",
        "稳评责任单位": "淮安市洪泽区人民政府",
        "项目位置": "高良涧街道",
        "征收面积（亩）": "100.00",
        "征收面积（平方米）": "66667",
        "土地用途": "工业用地",
        "涉及户数": "26",
    })
    _fill(p, SHEET_TIME, {
        "委托日期": "2026年9月1日",
        "稳评公示开始日期": "2026-07-23",
        "座谈会日期": "2026/9/6",
        "报告编写年月": "2026年9月",
    })
    _fill(p, SHEET_SURVEY, {
        "问卷份数": "50份",
        "群众支持率（%）": "100%",
        "座谈会参会人员": "张三、李四、王五",
    })
    _fill(p, SHEET_CONTACT, {
        "稳评工作组组长": "张三",
        "责任单位联系电话": "13800000000",
    })

    data = read_intake_workbook(p)
    assert data is not None
    f = data["fields"]
    assert f["project_name"] == "洪拟征告〔2026〕7号土地征收决策"
    assert f["area_mu"] == "100"
    assert f["area_m2"] == "66667"
    assert f["household_count"] == "26"
    assert f["commission_date"] == "2026-09-01"
    assert f["publicity_start_date"] == "2026-07-23"
    assert f["symposium_date"] == "2026-09-06"
    assert f["report_month"] == "2026-09"
    assert f["total_samples"] == "50"
    assert f["support_rate"] == "100"
    assert f["symposium_attendees"] == "张三、李四、王五"

    filled = {}
    changed = apply_intake(filled, data)
    assert "project_name" in changed
    assert filled["org_name"] == "淮安市洪泽区人民政府"
    assert filled["org_phone"] == "13800000000"


def test_locked_value_is_not_overwritten(tmp_path):
    p = tmp_path / "稳评数据资料表.xlsx"
    build_intake_workbook(p)
    _fill(p, SHEET_BASE, {"项目名称": "Excel 里的名字"})
    data = read_intake_workbook(p)
    filled = {"project_name": "用户填的名字", "_locked_project_name": True}
    apply_intake(filled, data)
    assert filled["project_name"] == "用户填的名字"


def test_rejects_unrelated_xlsx(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "随便.xlsx"
    wb = Workbook()
    wb.active["A1"] = "无关"
    wb.save(p)
    assert read_intake_workbook(p) is None


def test_apply_from_state_reads_template_and_generic_excel(tmp_path):
    p = tmp_path / "稳评数据资料表.xlsx"
    build_intake_workbook(p)
    _fill(p, SHEET_BASE, {"项目名称": "某土地征收决策"})
    state = {"_domain": "stability",
             "_uploaded_files": [{"path": str(p), "original_name": p.name}]}
    n = apply_intake_from_state(state)
    assert isinstance(n, int) and n > 0
    assert state["filled_data"]["project_name"] == "某土地征收决策"

    # 另一份普通项目资料 Excel（标签扫描）
    from openpyxl import Workbook
    q = tmp_path / "项目资料.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["土地用途", "工业用地"])
    wb.save(q)
    state2 = {"_domain": "stability",
              "_uploaded_files": [{"path": str(q), "original_name": q.name}]}
    apply_intake_from_state(state2)
    assert state2["filled_data"]["land_use"] == "工业用地"
