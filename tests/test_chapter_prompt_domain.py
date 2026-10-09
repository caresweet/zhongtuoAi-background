# -*- coding: utf-8 -*-
"""默认单章 system prompt（`chapter_base._default_system_prompt`）按域装配 —— 回归护栏。

`ChapterOrchestrator` 路径（前端「生成某章」走的就是它）不设 `_use_custom_prompt`，
这条默认提示词就是模型实际收到的 system prompt。它原先满篇稳评：身份叫「资深稳评
工程师」、规范写 DB32/T4013、结构/小标题/模板底稿/表格骨架全取自稳评目录 ——
复垦章于是由稳评工程师按稳评章结构写出来。本测试锁两件事：

  · 稳评（默认域）取值逐字等于改动前（下文写死字面量，改动即红）；
  · 复垦换成复垦口径的身份/规范/铁律/结构，且不再出现「稳评/DB32/社会稳定风险评估」。

不触网、不碰真实库：`_template_block_for_prompt` 打桩；复垦分支本就不读库。
"""
import pytest

from app.agent import chapter_catalog as cat


# ═══════════════════════════════════════════════════════════════
# 缝隙函数：稳评逐字 / 复垦换口径
# ═══════════════════════════════════════════════════════════════

_PERSONA_STABILITY = "你是一位资深稳评工程师，长期做土地征收社会稳定风险评估。"
_LEAD_STABILITY = "根据DB32/T4013-2021规范和你8年的实战经验来组织内容"
_TRANSITION_STABILITY = "- 基层过渡语：「结合入户走访」「根据社区书记反馈」\n"
_IRON_RULE_STABILITY_LINES = [
    "- 项目概况类数据（征收面积/地类面积/四至/户数）只在第4章「决策事项基本情况」出现，后续章节不得重复罗列",
    "- 勘测定界数据（各地类/各权属单位面积）只属于第4章，不得出现在第7章四性分析",
    "- 问卷/座谈数据（份数、支持率、参会人数、诉求）只属于第6章「各利益相关者对决策事项的反映」",
    "- 风险因素与评分只属于第8章「分析预测风险源」；防范化解措施只属于第9章",
    "- 文号（如洪拟征告〔2026〕X号）必须严格来自「可用数据」里的真实文号，绝不编造、不改号、不抄范文里的文号",
    "- 同一数据全文只写一次，避免同一组数字在多章反复出现",
]


def test_persona_stability_byte_identical_reclamation_swapped():
    assert cat.chapter_persona("stability") == _PERSONA_STABILITY
    assert cat.chapter_persona(None) == _PERSONA_STABILITY
    r = cat.chapter_persona("reclamation")
    from app.domains.reclamation import RECLAMATION_IDENTITY_PROMPT
    assert r == RECLAMATION_IDENTITY_PROMPT
    assert "稳评" not in r and "社会稳定风险评估" not in r


def test_principle_lead_stability_byte_identical_reclamation_swapped():
    assert cat.chapter_principle_lead("stability") == _LEAD_STABILITY
    assert cat.chapter_principle_lead(None) == _LEAD_STABILITY
    r = cat.chapter_principle_lead("reclamation")
    assert "DB32" not in r and "TD/T 1031" in r


def test_transition_examples_stability_byte_identical_reclamation_swapped():
    assert cat.chapter_transition_examples("stability") == _TRANSITION_STABILITY
    assert cat.chapter_transition_examples(None) == _TRANSITION_STABILITY
    r = cat.chapter_transition_examples("reclamation")
    assert "入户走访" not in r and "社区书记" not in r
    assert r.startswith("- ") and r.endswith("\n")


def test_data_iron_rule_stability_byte_identical():
    rule = cat.chapter_data_iron_rule("stability")
    assert cat.chapter_data_iron_rule(None) == rule
    assert rule.startswith("## 🔴 数据归位铁律（本报告各章数据各归其位，不得越界、不得重复）\n")
    for line in _IRON_RULE_STABILITY_LINES:
        assert line + "\n" in rule
    # 块尾换行、块后无空行 —— 与改动前内联版逐字一致
    assert rule.endswith(_IRON_RULE_STABILITY_LINES[-1] + "\n")


def test_data_iron_rule_reclamation_is_reclamation_not_translated_stability():
    rule = cat.chapter_data_iron_rule("reclamation")
    # 复垦自己的章名（取自 reclamation_definitions），不是稳评那套
    for name in ("项目概况", "土地复垦方向可行性分析", "土地复垦质量要求与复垦措施",
                 "土地复垦工程设计及工程量测算", "土地复垦投资估算",
                 "土地复垦工作计划安排", "土地复垦效益分析"):
        assert name in rule
    # 不得带稳评器物/章号语义
    assert "稳评" not in rule and "DB32" not in rule
    assert "四性分析" not in rule and "征收面积" not in rule and "问卷" not in rule
    assert "第9章「防范" not in rule


def test_structure_sources_reclamation_are_empty_or_from_reclamation():
    # 复垦无稳评模板小标题/底稿/表格骨架 —— 一律空，绝不回落稳评那份
    assert cat.headings_for_prompt("reclamation", 3) == ""
    assert cat.template_block_for_prompt("reclamation", 3) == ""
    assert cat.table_skeletons("reclamation", 3) == {}


def test_table_skeletons_stability_delegates():
    from app.agent.agents.knowledge_agent import TEMPLATE_TABLE_SKELETONS
    for n in (3, 5, 9):
        assert cat.table_skeletons("stability", n) == TEMPLATE_TABLE_SKELETONS.get(n, {})
        assert cat.table_skeletons(None, n) == TEMPLATE_TABLE_SKELETONS.get(n, {})


# ═══════════════════════════════════════════════════════════════
# agent 级：`_default_system_prompt` 实际装配出的文本
# ═══════════════════════════════════════════════════════════════

def _state(domain):
    return {"_domain": domain, "report_title": "某项目",
            "filled_data": {"implement_unit": "某某有限公司"}, "messages": []}


def _reclamation_prompt(chapter=3):
    from app.agent.agents.chapters import get_chapter_agent
    a = get_chapter_agent(chapter, domain="reclamation")
    return a._default_system_prompt(_state("reclamation"))


@pytest.mark.parametrize("chapter", [1, 2, 3])
def test_reclamation_prompt_has_no_stability_flavour(chapter):
    p = _reclamation_prompt(chapter)
    assert "稳评" not in p
    assert "DB32" not in p
    assert "社会稳定风险评估" not in p
    assert "拟征收土地" not in p
    # 复垦身份与铁律在
    from app.domains.reclamation import RECLAMATION_IDENTITY_PROMPT
    assert p.startswith(RECLAMATION_IDENTITY_PROMPT)
    assert "## 🔴 数据归位铁律" in p
    assert cat.chapter_data_iron_rule("reclamation") in p


def test_reclamation_prompt_section_spec_is_reclamation_structure():
    p = _reclamation_prompt(3)
    assert "3.1 项目简介" in p and "3.4 项目区土地利用状况" in p


def test_stability_prompt_keeps_stability_identity(monkeypatch):
    """稳评默认提示词仍是稳评身份/规范/铁律（结构来源打桩免读库）。"""
    import app.services.template_chapter_chunker as tcc
    monkeypatch.setattr(tcc, "build_template_block_for_prompt",
                        lambda n, **k: "", raising=True)

    from app.agent.agents.chapters import get_chapter_agent
    a = get_chapter_agent(3)
    p = a._default_system_prompt(_state("stability"))
    assert p.startswith(_PERSONA_STABILITY)
    assert _LEAD_STABILITY in p
    assert cat.chapter_data_iron_rule("stability") in p
    assert "DB32/T4013-2021" in p
    assert _TRANSITION_STABILITY.strip() in p


def test_default_domain_is_stability():
    from app.agent.agents.chapters.chapter_base import ChapterAgentBase
    from app.agent.agents.chapters.reclamation_chapter_base import (
        ReclamationChapterAgentBase)
    assert ChapterAgentBase.domain == "stability"
    assert ReclamationChapterAgentBase.domain == "reclamation"
