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


# ── 分域规则 md（业主 2026-10-30：「不同类型报告各一份 md，内容大多不能复用」）──

def test_domain_rules_come_from_separate_md():
    from app.services.content_rules import load_domain_rules

    rec = load_domain_rules("reclamation", buzzwords="套话A、套话B")
    bid = load_domain_rules("bidding", buzzwords="套话A")
    assert "不套用其他报告类型的写法" in rec
    assert "以勘测定界成果为准" in rec
    assert "配图要有数据解读" in rec
    assert "套话A" in rec            # 通用块占位符已替换
    assert "投标专属禁忌" in bid
    assert "复垦专属" not in bid


def test_reclamation_prompt_uses_own_rules_not_stability_wording():
    p = _prompt("reclamation", 3)
    assert "不套用其他报告类型的写法" in p
    # 复垦提示词里不再出现土地征收口径的遁词
    assert "以现状调查确认结果为准" not in p


# ── 报告类型必须落到工作流上（业主 2026-10-31：选了复垦却出稳评报告）──

def test_requested_domain_overrides_stale_session():
    from app.routers.report import _apply_requested_domain

    state = {"_domain": "stability", "chapters": {1: {"markdown": "稳评正文"}},
             "outline_list": [{"chapter_no": 1}], "template_path": "x.docx",
             "template_name": "社会稳定风险评估报告"}
    _apply_requested_domain(state, "reclamation")
    assert state["_domain"] == "reclamation"
    assert state["domain"] == "reclamation"
    # 旧域产物必须清掉，否则复垦会话里会冒出稳评章节
    assert state["chapters"] == {} and state["outline_list"] == []
    assert state["template_path"] == ""
    assert "复垦" in state["template_name"]


def test_requested_domain_noop_when_same_or_empty():
    from app.routers.report import _apply_requested_domain

    state = {"_domain": "reclamation", "chapters": {1: {"markdown": "x"}}}
    _apply_requested_domain(state, "reclamation")
    assert state["chapters"] == {1: {"markdown": "x"}}

    state2 = {"_domain": "reclamation"}
    _apply_requested_domain(state2, "")
    assert state2["_domain"] == "reclamation"
