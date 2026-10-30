# -*- coding: utf-8 -*-
"""复垦报告的版面/提问口径（业主 2026-10-30）。

三条口径：
  1. 复垦不追问「责任单位负责人/职务/联系人/电话」——那是稳评评审表的字段；
  2. 第1章「前言」只写文字：不排表、不配图、不做数据分析；
  3. 凡是配图的小节都要有对应的数据解读（图后 2～3 句带数字的分析）。
"""
from app.services.report_workflow import review_table_fields_for
from app.services.delivery_audit import audit_chapters
from app.agent.agents.chapters import get_chapter_agent


def test_review_table_fields_only_for_stability():
    stab = review_table_fields_for("stability")
    rec = review_table_fields_for("reclamation")
    assert stab and any(f[0] == "org_leader" for f in stab)
    assert rec == [], "复垦不该再问评审表字段"


def _prompt(domain: str, chapter: int) -> str:
    agent = get_chapter_agent(chapter, None, domain=domain)
    return agent._default_system_prompt({"filled_data": {"project_name": "测试项目"}})


def test_reclamation_ch1_prompt_is_text_only():
    p = _prompt("reclamation", 1)
    assert "本章专规" in p
    assert "只写文字" in p
    assert "不排表格" in p and "不做数据分析" in p


def test_other_chapters_get_figure_rule_but_not_ch1_rule():
    p = _prompt("reclamation", 3)
    assert "配图要有数据解读" in p
    assert "本章专规" not in p


def test_stability_has_no_reclamation_blocks():
    """稳评的行文块恒为空串（提示词逐字不变）。"""
    from app.agent import chapter_catalog as cat
    assert cat.style_rules("stability", 1) == ""
    assert cat.style_rules(None, 3) == ""
    assert "本章专规" in cat.style_rules("reclamation", 1)


def test_audit_flags_figure_without_analysis():
    md = "## 3.1 项目简介\n\n项目位于淮安市洪泽区。\n\n![图3-1 工程位置示意图](x.jpg)\n\n本项目位置优越。\n"
    a = audit_chapters({3: {"markdown": md}})
    assert a["by_kind"].get("figure_no_text") == 1


def test_audit_accepts_figure_with_analysis():
    md = ("## 3.1 项目简介\n\n项目位于淮安市洪泽区。\n\n![图3-1 工程位置示意图](x.jpg)\n\n"
          "图上红线范围为临时用地，东西长约120米、南北宽约65米，面积8070平方米。\n")
    a = audit_chapters({3: {"markdown": md}})
    assert a["by_kind"].get("figure_no_text") is None
