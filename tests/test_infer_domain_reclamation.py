"""文档自动归类要认出复垦，不能一律回落稳评。"""
from app.routers.knowledge import infer_domain


def test_infer_reclamation_by_keyword():
    assert infer_domain("江苏盐业井神张兴矿区临时用地土地复垦方案") == "reclamation"


def test_infer_reclamation_by_filename():
    assert infer_domain("王元佳苑小区项目土地复垦方案10.18---最终.pdf") == "reclamation"


def test_infer_still_falls_back_to_stability():
    """没有命中任何关键词时仍是稳评 —— 既有行为不变。"""
    assert infer_domain("一份普通的材料") == "stability"
