# -*- coding: utf-8 -*-
"""地区识别：文号前缀、跨区县、线性工程与市级/区县消歧。"""

from __future__ import annotations

from app.services.region_detect import detect_region, template_region


def test_document_prefix_maps_to_district():
    cases = {
        "淮拟征地告〔2025〕第16号土地征收决策.docx": "淮阴区",
        "洪拟征告〔2026〕128号土地征收决策稳定评估.docx": "洪泽区",
        "金征预告〔2026〕2号（银涂工业储备地块项目）.docx": "金湖县",
        "清拟征告〔2026〕3号.docx": "清江浦区",
    }
    for name, want in cases.items():
        assert detect_region("", name)["region"] == want


def test_linear_project_is_city_level():
    got = detect_region("", "内蒙古库布齐沙漠基地送电上海工程淮安段工程项目稳评报告.docx")
    assert got["region"] == "淮安市"
    assert got["source"] == "线性工程"


def test_multi_district_is_city_level():
    text = ("项目沿线经过涟水县、淮安区。" + "涟水县 40 个村；" * 5
            + "淮安区 30 个村；" * 4 + "跨区县报批。")
    got = detect_region(text, "某输变电工程稳评报告.docx")
    assert got["region"] == "淮安市"


def test_single_district_wins_over_city_name():
    text = ("淮安市淮安区白马湖农场1号风电项目，位于淮安区范集镇，"
            "淮安区自然资源和规划局委托。") * 3
    got = detect_region(text, "白马湖农场1号风电项目（修改稿）.docx")
    assert got["region"] == "淮安区"


def test_template_region_aliases():
    assert template_region("洪泽区") == "洪泽"
    assert template_region("金湖县") == "金湖"
    assert template_region("淮安") == "淮安市"
    assert template_region("淮安区") == "淮安区"


def test_city_only_text_uses_city_template():
    got = detect_region("淮安市某输变电工程社会稳定风险评估报告", "")
    assert got["region"] == "淮安市"
    assert got["source"] == "市级"
    # 只写「淮安」不带「市」，不硬认市级
    assert detect_region("淮安某项目（未写区县）", "")["region"] == ""


def test_cross_county_title_with_city_name_is_city_level():
    text = ("淮安市境内，线路经过涟水县、淮安区。淮安市220千伏输变电工程"
            "土地征收决策（跨区县）社会稳定风险评估")
    assert detect_region(text)["region"] == "淮安市"


def test_one_dominant_district_not_treated_as_cross_county():
    text = ("淮安市淮安区白马湖农场1号风电项目，位于淮安区范集镇。"
            "淮安区自然资源和规划局委托；项目附近有淮阴区界线。") * 2
    assert detect_region(text)["region"] == "淮安区"
