# -*- coding: utf-8 -*-
"""表4-9 适宜性因子：材料抽取 → 弹窗 → Excel 模板，三级取数。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TEXT = (
    "项目区属平原地貌，地形平坦，地面坡度小于 5°。表层土壤以中壤土为主，"
    "有机质含量 0.8%。灌溉条件一般满足，采用地面灌溉；排水体系一般；"
    "田间道路良好，田块比较平坦。覆土厚度 30cm。"
)


def test_extract_suitability_from_material_text():
    from app.services.reclamation_suitability import extract_suitability_params

    filled = {}
    added = extract_suitability_params(filled, [("可研报告.docx", _TEXT)])
    assert set(added) >= {
        "slope_grade", "soil_texture", "organic_matter", "landform",
        "irrigation_guarantee", "irrigation_method", "drainage_condition",
        "road_access", "field_flatness",
    }
    assert filled["slope_grade"] == "<5°"
    assert filled["soil_texture"] == "中壤土"
    assert filled["organic_matter"] == "0.8"
    assert filled["landform"] == "平地"
    assert filled["irrigation_guarantee"] == "一般满足"
    assert filled["irrigation_method"] == "地面灌溉"
    assert filled["drainage_condition"] == "一般"
    assert filled["road_access"] == "良好"
    assert filled["field_flatness"] == "比较平坦"


def test_extraction_does_not_overwrite_user_values():
    from app.services.reclamation_suitability import extract_suitability_params

    filled = {"soil_texture": "轻壤土"}
    extract_suitability_params(filled, [("m", _TEXT)])
    assert filled["soil_texture"] == "轻壤土"


def test_scores_follow_standard_table():
    from app.services.reclamation_suitability import score_factors

    s = score_factors({
        "slope_grade": "<5°", "effective_soil_depth_cm": "30",
        "soil_texture": "中壤土", "organic_matter": "0.8", "landform": "平地",
        "irrigation_guarantee": "一般满足", "irrigation_method": "地面灌溉",
        "drainage_condition": "一般", "road_access": "良好",
        "field_flatness": "比较平坦",
    })
    assert s == {"自然坡度": 100, "有效土层厚度": 100, "土壤质地": 90,
                 "有机质含量": 80, "地貌部位": 100, "灌溉保证程度": 80,
                 "灌溉方式": 80, "排水保证程度": 80, "道路通达性": 90,
                 "田块平整": 80}


def test_table_4_9_uses_project_factors():
    from app.services.reclamation_ch4_tables import table_4_9

    filled = {
        "_land_use_zones": [
            {"name": "施工场站", "area_m2": 11800, "degree": "重度",
             "reclaim_direction": "水田", "land_types": "水田、旱地"},
        ],
        "slope_grade": "<5°", "effective_soil_depth_cm": "30",
        "soil_texture": "中壤土", "organic_matter": "0.8", "landform": "平地",
        "irrigation_guarantee": "一般满足", "irrigation_method": "地面灌溉",
        "drainage_condition": "一般", "road_access": "良好",
        "field_flatness": "比较平坦",
    }
    t = table_4_9(filled)
    row = t["rows"][0]
    # 评价单元 + 10 个因子分 + 分值 + 等级
    assert row[1:11] == ["100", "100", "90", "80", "100", "80", "80", "80", "90", "80"]
    assert row[11] == "88.20"
    assert row[12] == "一等地"


def test_popup_and_excel_template_include_suitability_fields():
    from app.agent.reclamation_definitions import RECLAMATION_NEED_FIELDS
    from app.services.reclamation_intake import build_intake_workbook, SHEET_DESIGN
    from openpyxl import load_workbook
    import tempfile

    keys = {s[0] for s in RECLAMATION_NEED_FIELDS[3]}
    for k in ("slope_grade", "soil_texture", "organic_matter",
              "irrigation_guarantee", "field_flatness"):
        assert k in keys, k

    p = Path(tempfile.mkdtemp()) / "t.xlsx"
    build_intake_workbook(p)
    ws = load_workbook(p)[SHEET_DESIGN]
    labels = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    for label in ("自然坡度", "土壤质地", "有机质含量(%)", "田块平整度"):
        assert label in labels, label
