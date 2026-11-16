# -*- coding: utf-8 -*-
"""数据来源与计算口径：来源表、工程量算式、标点归一。"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def test_source_rows_and_markdown():
    from app.services.data_sources import as_markdown, build_source_rows
    filled = {
        "project_name": "某项目",
        "area_m2": "26800",
        "topsoil_thickness": "30",
        "hard_area_m2": "11000",
        "_field_sources": {"area_m2": "material", "topsoil_thickness": "user"},
    }
    rows = build_source_rows("reclamation", filled)
    by = {r[0]: r for r in rows}
    assert "临时用地面积" in by
    assert "材料识别" in by["临时用地面积"][2]
    assert "用户填写" in by["表土剥离厚度"][2]
    md = as_markdown("reclamation", filled)
    assert "数据来源与计算口径说明" in md and "| 数据项 |" in md


def test_quantity_derivation_table_has_formulas():
    from app.services.reclamation_quantities import build_derivation_table
    filled = {
        "land_classification": {"合计_m2": 26800, "rows": [
            {"一级地类": "耕地", "二级地类": "旱地", "面积_m2": 14000},
            {"一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 3800},
        ]},
        "topsoil_thickness": "30",
        "hard_area_m2": 11000,
        "hard_surface_type": "碎石垫层+钢板",
        "surface_thickness_cm": "10",
        "excavation_area_m2": 9200,
        "excavation_depth_m": 1.5,
    }
    t = build_derivation_table(filled)
    rows = {r[0]: r for r in t["rows"]}
    assert "表土剥离" in rows and "公顷 ×" in rows["表土剥离"][1]
    assert "沟槽开挖" in rows and "0.9200 公顷 × 1.50 m" in rows["沟槽开挖"][1]
    assert "11040.0" in rows["沟槽开挖"][2]
    assert "垫层清理" in rows and "10 cm" in rows["垫层清理"][1]


def test_fix_punctuation_runs():
    from app.services.report_content_repairs import fix_punctuation_runs
    out, n = fix_punctuation_runs("项目可行，，总投资 1 万元。。  \n| --- | --- |")
    assert "，，" not in out and "。。" not in out
    assert "| --- | --- |" in out
    assert n >= 2
