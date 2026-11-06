# -*- coding: utf-8 -*-
"""设计参数先从材料里找，找不到才弹窗问（业主 2026-11-03）。

用井神安井组人工稿 11.2 的原文片段验算：这些参数材料里都写着，
系统应当识别出来，不再重复问用户。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.reclamation_design_extract import extract_design_params   # noqa: E402

_MATERIAL = """
三井组临时用地土地复垦方案
项目区面积8.0762hm2，临时用地面积2.3248hm2。
江苏盐业井神楚州张兴储气库工程总投资额为651003.92万元。
项目性质：新建工程。
项目临时用地使用时间为24个月，从2026年10月至2028年9月。
拟安排在使用结束后6个月内完成所有临时用地的复垦和验收工作，之后考虑6个月的管护期。
最终确定土地复垦方案服务年限为36个月，从2026年10月到2029年9月。
临时用地施工前，表土剥离主要针对现状的耕地，剥离表层30cm，共计剥离表土6664.5m³。
场地采用15cm厚的碎石垫层，上面铺钢板，钢板由建设单位收回。
项目区需清除地面垫层的面积为2.0048hm2。
"""


def test_extract_design_params_from_jingshen_material():
    filled: dict = {}
    keys = extract_design_params(filled, [("复垦方案.docx", _MATERIAL)])
    assert "topsoil_thickness" in keys and filled["topsoil_thickness"] == "30"
    assert filled["hard_surface_type"] == "碎石垫层+钢板"
    assert filled["surface_thickness_cm"] == "15"
    assert filled["hard_area_m2"] == "20048"
    assert filled["service_years"] == "36 个月"
    assert filled["reclaim_period_months"] == "6"
    assert filled["care_period_months"] == "6"
    assert filled["project_area_m2"] == "80762"
    assert filled["project_investment"] == "651003.92"
    assert filled["construction_nature"] == "新建"


def test_extract_does_not_overwrite_user_values():
    filled = {"topsoil_thickness": "20", "service_years": "30 个月",
              "_field_sources": {"topsoil_thickness": "user"}}
    keys = extract_design_params(filled, [("方案.docx", _MATERIAL)])
    assert "topsoil_thickness" not in keys and filled["topsoil_thickness"] == "20"
    assert "service_years" not in keys and filled["service_years"] == "30 个月"


def test_extract_concrete_surface_thicknesses():
    text = ("场地铺设15cm混凝土面层，下设10cm垫层，硬化面积23248平方米。")
    filled: dict = {}
    extract_design_params(filled, [("施工组织设计.docx", text)])
    assert filled["hard_surface_type"] == "混凝土面层+垫层"
    assert filled["surface_thickness_cm"] == "15"
    assert filled["cushion_thickness_cm"] == "10"
    assert filled["hard_area_m2"] == "23248"


def test_extract_ignores_unrelated_lengths():
    """「耕作层厚度14～30cm」不是剥离厚度，不能抓成 30。"""
    filled: dict = {}
    extract_design_params(filled, [("概况.docx", "项目区耕作层厚度为14～30cm。")])
    assert "topsoil_thickness" not in filled


def test_extract_service_years_in_years():
    filled: dict = {}
    extract_design_params(filled, [("方案.docx", "土地复垦方案服务年限为3年。")])
    assert filled["service_years"] == "36 个月"


def test_extract_no_materials_returns_empty():
    assert extract_design_params({}, []) == []


def test_investment_ignores_reclamation_static_total():
    filled: dict = {}
    extract_design_params(filled, [("复垦方案.docx",
                                   "本项目土地复垦静态总投资126.92万元，动态总投资135.44万元。")])
    assert "project_investment" not in filled


def test_material_value_beats_popup_question():
    """识别到值后，弹窗构造逻辑看到有值就不会再问（模拟 fix 后的 filled）。"""
    from app.services.reclamation_design_extract import extract_design_params
    from app.agent.reclamation_definitions import RECLAMATION_NEED_FIELDS
    filled: dict = {}
    extract_design_params(filled, [("方案.docx", _MATERIAL)])
    missing = [k for k, *_ in RECLAMATION_NEED_FIELDS[3]
               if not str(filled.get(k) or "").strip()]
    for k in ("topsoil_thickness", "hard_surface_type", "hard_area_m2",
              "surface_thickness_cm", "service_years", "project_area_m2",
              "project_investment", "construction_nature"):
        assert k not in missing


def test_service_years_computed_from_periods_when_not_stated():
    from app.services.reclamation_design_extract import extract_design_params
    filled = {"land_use_months": "24"}
    text = ("拟安排在使用结束后6个月内完成所有临时用地的复垦和验收工作，"
            "之后考虑6个月的管护期。")
    extract_design_params(filled, [("方案.docx", text)])
    assert filled["reclaim_period_months"] == "6"
    assert filled["care_period_months"] == "6"
    assert filled["service_years"] == "36 个月"


def test_extract_accepts_material_dicts():
    filled: dict = {}
    extract_design_params(filled, [{"source_name": "方案.docx",
                                    "text_content": _MATERIAL}])
    assert filled["topsoil_thickness"] == "30"


_MATERIAL2 = """
临时用地施工前，对现状耕地和园地剥离表土，剥离厚度 25cm。
施工场站采用 10cm 厚碎石垫层，其上铺设钢板，钢板由建设单位回收。
需清除地面垫层的面积为 1.1000 公顷。
土地复垦方案服务年限为 36 个月，其中复垦施工期 6 个月，管护期 12 个月。
"""


def test_material_hard_area_beats_zone_derived():
    """材料写了硬化面积时，不能被「总面积−表土堆放区」倒算值顶掉。"""
    from app.services.field_fixer import fix_reclamation_fields
    filled = {
        "land_classification": {"合计_m2": 26800.0, "rows": []},
        "_land_use_zones": [
            {"name": "施工场站", "area_m2": 9800, "method": "压占",
             "degree": "重度", "land_types": "旱地"},
            {"name": "表土堆放区", "area_m2": 3000, "method": "压占",
             "degree": "中度", "land_types": "旱地"},
        ],
    }
    fix_reclamation_fields(filled, [("材料.txt", _MATERIAL2)], title="测试项目")
    assert filled["hard_area_m2"] == "11000"
    assert filled["topsoil_thickness"] == "25"


def test_zone_derived_hard_area_still_used_without_material():
    from app.services.field_fixer import fix_reclamation_fields
    filled = {
        "land_classification": {"合计_m2": 26800.0, "rows": []},
        "_land_use_zones": [
            {"name": "施工场站", "area_m2": 9800, "method": "压占",
             "degree": "重度", "land_types": "旱地"},
            {"name": "表土堆放区", "area_m2": 3000, "method": "压占",
             "degree": "中度", "land_types": "旱地"},
        ],
    }
    fix_reclamation_fields(filled, [], title="测试项目")
    assert filled["hard_area_m2"] == "23800.00"
