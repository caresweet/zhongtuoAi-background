"""数字三件套：可追溯；算法与结果不自洽要报出来。"""
import pytest

from app.services.reclamation_data_model import ReclamationDataModel, TracedValue


def test_input_value_carries_source():
    m = ReclamationDataModel()
    m.set_input("临时用地面积", 0.2354, unit="hm2", source="临时用地勘测定界报告")
    v = m.get("临时用地面积")
    assert v.value == 0.2354
    assert v.source == "临时用地勘测定界报告"
    assert v.unit == "hm2"


def test_derived_value_checks_its_own_arithmetic():
    m = ReclamationDataModel()
    m.set_input("建设用地面积", 2.3398, unit="hm2", source="主体项目勘测定界报告")
    m.set_input("临时用地面积", 0.2354, unit="hm2", source="临时用地勘测定界报告")
    m.derive("用地规模", "建设用地面积 + 临时用地面积", unit="hm2")
    assert m.get("用地规模").result == pytest.approx(2.5752)


def test_arithmetic_mismatch_is_reported_not_silently_accepted():
    """批注说 ×15cm、数字却是 ×8cm —— 这类不自洽必须被抓出来。"""
    m = ReclamationDataModel()
    m.set_input("功能区面积", 2354.0, unit="m2", source="临时用地勘测定界报告")
    m.derive("垫层工程量", "功能区面积 * 0.15", unit="m3", claimed=188.32)
    issues = m.audit()
    assert issues, "算术不自洽未被报出"
    assert "垫层工程量" in issues[0]


def test_consistent_derived_value_has_no_issue():
    m = ReclamationDataModel()
    m.set_input("功能区面积", 2354.0, unit="m2", source="临时用地勘测定界报告")
    m.derive("混凝土层工程量", "功能区面积 * 0.10", unit="m3", claimed=235.4)
    assert m.audit() == []


def test_missing_source_is_an_issue():
    """派生量没有来源也没有算法 —— 属于编造，必须报出。"""
    m = ReclamationDataModel()
    m.set_input("某面积", 1.0, unit="hm2", source="")
    issues = m.audit()
    assert any("某面积" in i for i in issues)
