# -*- coding: utf-8 -*-
"""挖损（管道沟槽、基坑）单独计量：分区识别、工程量、材料抽取、单价。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.reclamation_zones import parse_zones              # noqa: E402
from app.services.reclamation_quantities import build_quantity_rows  # noqa: E402
from app.services.reclamation_design_extract import extract_design_params  # noqa: E402


_LAND = {"合计_m2": 20800, "rows": [
    {"一级地类": "耕地", "二级地类": "旱地", "面积_m2": 14000.0},
    {"一级地类": "水域及水利设施用地", "二级地类": "沟渠", "面积_m2": 3800.0},
    {"一级地类": "交通运输用地", "二级地类": "农村道路", "面积_m2": 3000.0},
]}

_ZONES = ("管道开挖带 0.9200公顷 挖损和压占 重度；"
          "施工场站 0.9800公顷 压占 重度")


def test_zone_method_inference_for_excavation():
    z = {x["name"]: x for x in parse_zones(
        "管道开挖带 0.9200公顷；沟槽 0.1000公顷；基坑 0.0500公顷；施工场站 0.9800公顷")}
    assert "挖损" in z["管道开挖带"]["method"]
    assert "挖损" in z["沟槽"]["method"]
    assert z["基坑"]["method"] == "挖损"
    assert z["施工场站"]["method"] == "压占"


def test_excavation_quantities_from_zone_area_and_depth():
    filled = {
        "land_classification": _LAND,
        "land_use_zones": _ZONES,
        "topsoil_thickness": "30",
        "hard_area_m2": 9800,
        "hard_surface_type": "碎石垫层+钢板",
        "surface_thickness_cm": "10",
        "excavation_depth_m": 1.5,
    }
    q = {r["name"]: r["qty"] for r in build_quantity_rows(filled)}
    # 挖损 9200 ㎡ × 1.5 m = 13800 m³；扣掉已单列的表土层 9200×0.3=2760 → 11040 m³
    assert q["沟槽开挖"] == 110.4
    assert q["沟槽回填"] == 110.4


def test_excavation_needs_depth():
    """有挖损分区但没开挖深度 → 不出开挖/回填，不能用经验深度顶替。"""
    filled = {
        "land_classification": _LAND,
        "land_use_zones": _ZONES,
        "topsoil_thickness": "30",
        "hard_area_m2": 9800,
        "hard_surface_type": "碎石垫层+钢板",
        "surface_thickness_cm": "10",
    }
    names = {r["name"] for r in build_quantity_rows(filled)}
    assert "沟槽开挖" not in names
    assert "沟槽回填" not in names


def test_explicit_excavation_area_overrides_zones():
    filled = {
        "land_classification": _LAND,
        "land_use_zones": _ZONES,
        "excavation_area_m2": 5000,
        "excavation_depth_m": 2.0,
        "topsoil_thickness": "0",      # 不剥离，不扣表土
        "hard_area_m2": 9800,
        "hard_surface_type": "碎石垫层+钢板",
        "surface_thickness_cm": "10",
    }
    q = {r["name"]: r["qty"] for r in build_quantity_rows(filled)}
    assert q["沟槽开挖"] == 100.0      # 5000 ㎡ × 2.0 m
    assert q["沟槽回填"] == 100.0


def test_design_extract_excavation_params():
    filled: dict = {}
    extract_design_params(filled, [(
        "施工组织设计.docx",
        "管道开挖带面积0.9200公顷，沟槽开挖深度1.5m，槽底宽度按管径确定。")])
    assert filled["excavation_area_m2"] == "9200"
    assert filled["excavation_depth_m"] == "1.5"


def test_excavation_unit_prices_exist():
    from app.services.reclamation_cost_model import CostItem, estimate
    est = estimate([CostItem("沟槽开挖", 100), CostItem("沟槽回填", 100)])
    assert est.construction > 0
    assert est.static_total > 0
