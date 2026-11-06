# -*- coding: utf-8 -*-
"""范文残留清理：数字 / 地名 / 文号 → 用户资料，缺则「待补充」。

业主 2026-11-02：「范文的数字、地名、面积、文号全部用用户提供的新报告的资料填充，
如果没有让人工介入填写，人工选择跳过的，就是占位文字『待补充』。」
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.validation.content_guardrails import (      # noqa: E402
    clean_broken_placeholders, scrub_sample_facts,
)

_SAMPLE = (
    "明远路以南地块商住项目位于淮安经济技术开发区枚乘街道许庄村，"
    "临时用地面积0.2354公顷，批复文号为苏复垦〔2023〕8号。"
)


def test_scrub_replaces_sample_place_area_and_doc():
    md = ("项目位于明远路以南地块，临时用地面积0.2354公顷，"
          "批复文号为苏复垦〔2023〕8号。")
    filled = {"location": "淮安市淮安区", "area_m2": 23248,
              "doc_reference": "苏复垦〔2026〕1号"}
    out, n = scrub_sample_facts(md, filled, _SAMPLE)
    assert n >= 3
    assert "明远路" not in out
    assert "淮安市淮安区" in out
    assert "0.2354" not in out and "2.3248" in out          # 23248 ㎡ = 2.3248 ha
    assert "苏复垦〔2023〕8号" not in out
    assert "苏复垦〔2026〕1号" in out


def test_scrub_keeps_policy_doc_no_inside_book_title():
    """书名号内的政策文号不是项目文号，绝不能被换掉。"""
    md = "依据《自然资源部关于规范临时用地管理的通知》（自然资规〔2019〕1号）办理。"
    out, _ = scrub_sample_facts(md, {"doc_reference": "苏复垦〔2026〕1号"},
                                md + "\n明远路以南地块")
    assert "自然资规〔2019〕1号" in out


def test_scrub_uses_placeholder_when_user_has_nothing():
    md = "项目位于明远路以南地块，临时用地面积0.2354公顷。"
    out, n = scrub_sample_facts(md, {}, _SAMPLE)
    assert n >= 2
    assert "待补充" in out
    assert "0.2354" not in out and "明远路" not in out


def test_scrub_keeps_user_own_values():
    """用户资料里本来就有的数字/地名不动。"""
    md = "项目位于淮安市淮安区，临时用地面积23248平方米。"
    out, n = scrub_sample_facts(md, {"location": "淮安市淮安区", "area_m2": 23248},
                                md + "\n明远路 0.2354公顷")
    assert out == md
    assert n == 0


def test_scrub_place_uses_project_area_not_daibuchong_when_no_location():
    """没有项目位置时，地名换成「项目区」，不能把「待补充」塞进句子。"""
    out, _ = scrub_sample_facts("矿区位于马甸镇，交通便利。", {},
                                "矿区位于马甸镇，交通便利。")
    assert "马甸" not in out
    assert "待补充" not in out
    assert "项目区" in out


def test_scrub_village_pair_becomes_project_area():
    out, n = scrub_sample_facts("矿区位于苏北灌溉总渠以南，在马甸镇到车桥镇之间，距下关矿区4公里。",
                                {}, "马甸镇到车桥镇之间 " + _SAMPLE)
    assert n >= 1
    assert "马甸镇到车桥镇" not in out
    assert "项目区范围内" in out


def test_scrub_fixes_broken_coordinates_with_centroid():
    md = "位于北纬 33163345、东经 1185911937 之间。"
    out, n = scrub_sample_facts(md, {"_centroid_lat": 33.1633, "_centroid_lon": 118.5912},
                                md)
    assert n == 1
    assert "33163345" not in out
    assert "33.163300" in out and "118.591200" in out


def test_clean_broken_placeholders_in_sentence():
    md = ("矿区位于苏北灌溉总渠以南，在待补充之间。待补充四通八达，"
          "有南北向的G2待补充、237省道。另有309待补充，交通便利。")
    out, n = clean_broken_placeholders(md)
    assert n >= 4
    assert "在项目区范围内" in out
    assert "项目区周边四通八达" in out
    assert "G2待补充" not in out and "309待补充" not in out


def test_scrub_keeps_common_nouns_ending_with_place_suffix():
    """「当地乡镇/农村道路/田间道路/农业农村」不是地名，不能被换成项目地址。

    2026-11-03 对照人工稿（井神 11.2）实测：这些通用词被地名正则误捕，
    成稿出现「淮安市淮安区（街道）、村委」「0.5km 淮安市淮安区整修使用」
    「淮安市淮安区明确专人管护」「自然资源、淮安市淮安区、水利」等病句。
    """
    md = ("在复垦方案编制过程中，充分听取当地乡镇（街道）、村委和项目区农民的意见；"
          "清理的碎石碎渣将送至0.5km外的农村道路整修使用；"
          "田间道路明确专人管护；可聘请自然资源、农业农村、水利等部门技术人员。")
    sample = md + "\n明远路以南地块"
    out, _ = scrub_sample_facts(md, {"location": "淮安市淮安区"}, sample)
    assert "淮安市淮安区（街道）" not in out
    assert "0.5km淮安市淮安区" not in out
    assert "淮安市淮安区明确专人管护" not in out
    assert "自然资源、淮安市淮安区、水利" not in out
    assert "当地乡镇（街道）" in out
    assert "农村道路整修使用" in out
    assert "田间道路明确专人管护" in out
    assert "农业农村" in out


def test_merge_broken_sentences_rejoins_dangling_period():
    from app.validation.content_guardrails import merge_broken_sentences
    md = ("并根据临时用地的损毁时间、损毁性质和损毁程度。合理确定复垦时间和复垦利用类型。"
          "使用期满后。立即组织复垦工程施工。")
    out, n = merge_broken_sentences(md)
    assert n == 2
    assert "程度。合理确定" not in out
    assert "使用期满后。立即" not in out
    assert "程度，合理确定" in out


def test_merge_keeps_independent_sentences():
    from app.validation.content_guardrails import merge_broken_sentences
    md = "本项目位于淮安市淮安区。项目区地势平坦。土壤以水稻土为主。"
    out, n = merge_broken_sentences(md)
    assert out == md and n == 0


def test_fix_area_conversions_recomputes_wrong_hectares():
    from app.validation.content_guardrails import fix_area_conversions
    md = "项目区水田面积22215平方米，折合2.3248 公顷，翻耕面积22215平方米，折合2.3公顷。"
    out, n = fix_area_conversions(md)
    assert n == 2
    assert "22215平方米，折合2.2215 公顷" in out
    assert "2.3248 公顷" not in out


def test_fix_area_conversions_leaves_correct_values():
    from app.validation.content_guardrails import fix_area_conversions
    md = "临时用地面积23248平方米，折合2.3248公顷。"
    out, n = fix_area_conversions(md)
    assert n == 0 and out == md


def test_scrub_does_not_treat_unit_investment_as_project_investment():
    """「1.61 万元/亩」是单位面积投资，不能被换成项目总投资。"""
    md = "静态单位面积投资为1.61万元/亩，动态单位面积投资为1.77万元/亩。"
    sample = md + "\n项目总投资651003.92万元。"
    out, _ = scrub_sample_facts(md, {"project_investment": "651003.92",
                                     "area_m2": 23248}, sample)
    assert "1.61万元/亩" in out
    assert "651003.92万元/亩" not in out


def test_fix_reclamation_area_total_uses_project_area():
    """复垦区总面积 = 永久性建设用地 + 临时用地，模型拿临时用地面积当总数要改回来。"""
    from app.validation.content_guardrails import fix_reclamation_area_total
    md = ("本项目复垦区总面积为2.3248 公顷，包括永久性建设用地8.0762公顷"
          "和拟损毁临时用地2.3248公顷。")
    out, n = fix_reclamation_area_total(
        md, {"project_area_m2": 104010, "area_m2": 23248})
    assert n == 1
    assert "复垦区总面积为10.4010 公顷" in out


def test_fix_reclamation_area_total_no_data_no_change():
    from app.validation.content_guardrails import fix_reclamation_area_total
    md = "本项目复垦区总面积为2.3248 公顷。"
    out, n = fix_reclamation_area_total(md, {})
    assert n == 0 and out == md
