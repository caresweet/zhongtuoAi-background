# -*- coding: utf-8 -*-
"""复垦投资估算费用模型 —— 用**样本自己的数字**回验。

样本：江苏盐业井神…土地复垦方案.docx 表7-3（工程施工费估算表）+ 表7-4（费用构成）。
本文件的用例就是把样本表7-3 的工程量原样喂进去，要求复现表7-4 的全部 13 个数字。
"""

import pytest

from app.services.reclamation_cost_model import (
    UNIT_PRICES, CostItem, estimate,
)

# 样本表7-3 的工程量（工程名称 → 数量，单位与单价表一致）
_SAMPLE_ITEMS = [
    CostItem("表土剥离", 5.808),
    CostItem("播撒草籽", 0.0257),
    CostItem("袋装土加固编织袋土", 0.482),
    CostItem("排水沟开挖", 0.0452),
    CostItem("垫层清理", 1.8832),
    CostItem("混凝土拆除", 2.3540),
    CostItem("垃圾外运工程", 4.2372),
    CostItem("土地翻耕", 0.1936),
    CostItem("表土回填", 5.808),
    CostItem("田面平整", 1.936),
    CostItem("土壤培肥（有机肥）", 2.9040),
    CostItem("土壤培肥（复合肥）", 0.1452),
    CostItem("沟渠疏通", 5.895),
]


def _sample_estimate():
    return estimate(
        _SAMPLE_ITEMS, other=1.10, care_people=1, care_months=6,
        acceptance_items={"土壤污染监测": 4.20, "耕地质量评价": 4.80, "组织验收": 1.00},
        price_escalation=2.03,
    )


def test_reproduces_sample_cost_table():
    """复现样本表7-4 的全部 13 个数字（万元）。"""
    e = _sample_estimate()
    for got, want in [
        (e.construction, 6.79), (e.equipment, 0.0), (e.other, 1.10),
        (e.monitor_care, 10.70), (e.monitor, 0.10), (e.care, 0.60),
        (e.acceptance, 10.0), (e.reserve, 3.28), (e.basic_reserve, 1.12),
        (e.price_escalation, 2.03), (e.risk, 0.14),
        (e.static_total, 19.85), (e.dynamic_total, 21.88),
    ]:
        assert abs(got - want) <= max(0.02, abs(want) * 0.005), (got, want)


def test_static_total_excludes_price_escalation():
    """静态总投资**不含**价差预备费（样本：19.85 = 各项之和，21.88 才是含价差）。"""
    e = _sample_estimate()
    assert e.dynamic_total - e.static_total == pytest.approx(e.price_escalation, abs=1e-6)


def test_rate_formulas_match_sample():
    """三条费率按样本表7-4 的公式标记算。"""
    e = _sample_estimate()
    assert e.monitor == pytest.approx(round(e.construction * 0.015, 4), abs=1e-6)
    assert e.risk == pytest.approx(round(e.construction * 0.02, 4), abs=1e-6)
    assert e.basic_reserve == pytest.approx(
        round((e.construction + e.equipment + e.other + e.monitor_care) * 0.06, 4), abs=1e-6)


def test_care_fee_is_per_person_month():
    """管护费 = 1000 元/(人·月) × 人数 × 月数（样本：1 人 × 6 月 = 0.6 万）。"""
    e = estimate([], care_people=1, care_months=6)
    assert e.care == pytest.approx(0.6, abs=1e-6)
    e2 = estimate([], care_people=2, care_months=12)
    assert e2.care == pytest.approx(2.4, abs=1e-6)


def test_unit_prices_are_traceable_to_sample():
    """单价库每一项都要能说出出处 —— 不许有凭空的数。"""
    for name, (price, unit, src) in UNIT_PRICES.items():
        assert price > 0 and unit and src, name
        assert "样本" in src or "表7-3" in src, f"{name} 缺出处: {src}"


def test_unknown_item_without_price_raises():
    """单价库里没有的工程项必须显式给单价，不能静默按 0 算。"""
    with pytest.raises(KeyError):
        estimate([CostItem("没听说过的工程", 1.0)])


def test_explicit_unit_price_overrides_library():
    e = estimate([CostItem("表土剥离", 10.0, unit_price=100.0)])
    assert e.construction == pytest.approx(0.1, abs=1e-6)
