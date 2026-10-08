"""勘测定界解析参数化：默认 profile 与现状等价；复垦 profile 用自己的表头。

注：公开入口的真实名字是 `parse_land_survey`（land_survey_parser.py:407），
不是 `parse_land_survey_report`。
"""
import inspect

from app.services.land_survey_parser import (
    DEFAULT_PROFILE,
    RECLAMATION_PROFILE,
    parse_land_survey,
)


def test_default_profile_matches_legacy_hardcoded_values():
    """默认 profile 必须逐字等于改动前的硬编码 —— 稳评行为不变的依据。"""
    assert DEFAULT_PROFILE.classification_keys == ("被征", "权属类别", "面积总计")
    assert DEFAULT_PROFILE.summary_keys == ("地块号", "土地坐落", "土地用途", "界址点")
    assert DEFAULT_PROFILE.owner_header == "被征（用）地单位"
    assert DEFAULT_PROFILE.split_marks == (
        "地块号", "土地坐落", "被征", "被占", "权属类别", "面积总计")


def test_reclamation_profile_is_temporary_land():
    assert "临时用地" in RECLAMATION_PROFILE.table_caption
    assert "被征" not in RECLAMATION_PROFILE.classification_keys
    assert "临时用地" in RECLAMATION_PROFILE.owner_header


def test_parser_accepts_profile_with_default():
    sig = inspect.signature(parse_land_survey)
    assert "profile" in sig.parameters
    assert sig.parameters["profile"].default is DEFAULT_PROFILE
