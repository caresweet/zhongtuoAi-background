# -*- coding: utf-8 -*-
"""可研/项目资料 Excel：按标签取数，先于弹窗（业主 2026-11-10）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook


def _make(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "可研主要技术指标"
    rows = [
        ("项目名称", "某某临时用地复垦项目"),
        ("临时用地面积", "3.26公顷"),
        ("表土剥离厚度", "30cm"),
        ("自然坡度", "小于5°"),
        ("有效土层厚度", "30cm"),
        ("土壤质地", "中壤土"),
        ("有机质含量", "0.8%"),
        ("地貌部位", "平地"),
        ("灌溉条件", "一般满足，有灌溉系统"),
        ("灌溉方式", "地面灌溉"),
        ("排水条件", "排水体系一般"),
        ("道路条件", "良好"),
        ("田块平整", "比较平坦"),
    ]
    for i, (k, v) in enumerate(rows, 1):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
    wb.save(path)
    return path


def test_scan_workbook_fills_fields(tmp_path):
    from app.services.reclamation_fs_excel import scan_workbook

    p = _make(tmp_path / "可研.xlsx")
    filled = {}
    added = scan_workbook(p, filled)
    assert "soil_texture" in added
    assert filled["project_name"] == "某某临时用地复垦项目"
    assert filled["area_m2"] == "32600"
    assert filled["topsoil_thickness"] == "30"
    assert filled["slope_grade"] == "<5°"
    assert filled["effective_soil_depth_cm"] == "30"
    assert filled["soil_texture"] == "中壤土"
    assert filled["organic_matter"] == "0.8"
    assert filled["landform"] == "平地"
    assert filled["irrigation_guarantee"] == "一般满足"
    assert filled["irrigation_method"] == "地面灌溉"
    assert filled["drainage_condition"] == "一般"
    assert filled["road_access"] == "良好"
    assert filled["field_flatness"] == "比较平坦"


def test_scan_workbook_does_not_overwrite_existing(tmp_path):
    from app.services.reclamation_fs_excel import scan_workbook

    p = _make(tmp_path / "可研.xlsx")
    filled = {"soil_texture": "轻壤土", "_confirmed_soil_texture": True}
    scan_workbook(p, filled)
    assert filled["soil_texture"] == "轻壤土"


def test_scanned_factors_score_in_table_4_9(tmp_path):
    from app.services.reclamation_fs_excel import scan_workbook
    from app.services.reclamation_ch4_tables import table_4_9

    filled = {"_land_use_zones": [
        {"name": "施工场站", "area_m2": 11800, "degree": "重度"}]}
    scan_workbook(_make(tmp_path / "可研.xlsx"), filled)
    t = table_4_9(filled)
    row = t["rows"][0]
    assert row[1] == "100"     # 坡度 <5°
    assert row[2] == "100"     # 有效土层厚度 30cm
    assert row[3] == "90"      # 中壤土
    assert row[4] == "80"      # 有机质 0.8%
    assert row[12] == "一等地"


def test_apply_intake_from_state_scans_fs_excel(tmp_path):
    """界面/上传通道：基础资料 Excel + 可研 Excel 一起扫，顺序与返回值都要对。"""
    from app.services.reclamation_intake import (
        apply_intake_from_state, build_intake_workbook)

    base = tmp_path / "复垦基础资料_测试.xlsx"
    build_intake_workbook(base)
    fs = _make(tmp_path / "可研资料.xlsx")
    state = {
        "_domain": "reclamation",
        "_uploaded_files": [
            {"path": str(base), "original_name": base.name},
            {"path": str(fs), "original_name": fs.name},
        ],
    }
    n = apply_intake_from_state(state)
    assert isinstance(n, int) and n > 0
    f = state["filled_data"]
    assert f["soil_texture"] == "中壤土"
    assert f["slope_grade"] == "<5°"
