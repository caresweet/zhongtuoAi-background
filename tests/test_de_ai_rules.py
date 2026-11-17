# -*- coding: utf-8 -*-
"""去 AI 味规则（表达层）测试。"""

from app.services import builtin_skills as bs
from app.services.de_ai_rules import DE_AI_STYLE_TEXT, find_hits, strip_filler
from app.validation.content_guardrails import AI_BUZZWORD_PATTERNS


def _labels(text: str) -> set:
    return {label for _, label in find_hits(text)}


def test_detects_ai_transitions_and_hotwords():
    text = "值得注意的是，本项目通过系统治理的方式，形成组合拳。"
    labels = _labels(text)
    assert "AI过渡词" in labels
    assert "翻译腔" in labels
    assert "AI热词" in labels


def test_detects_empty_openers_and_dash_runs():
    text = "随着复垦工作的不断发展，项目全面推进——这是关键——也是底线。"
    labels = _labels(text)
    assert "空洞开头" in labels
    assert "破折号堆叠" in labels


def test_normal_official_sentences_are_not_flagged():
    for text in [
        "根据《中华人民共和国土地管理法》第四十七条，拟征收土地面积12.34亩。",
        "本项目位于淮安市淮阴区马头镇，东至运河堤顶路，北至马头干渠。",
        "复垦后耕地质量等别不低于占用前等别，检测数据见附件3。",
        "经专家评审通过后，报自然资源主管部门备案。",
    ]:
        assert find_hits(text) == [], text


def test_builtin_skills_include_de_ai_rules():
    ids = {s.id for s in bs.CATALOG}
    for sid in ("b-audit-006", "b-audit-007", "b-audit-008",
                "b-audit-009", "b-audit-010", "b-style-001", "b-style-002"):
        assert sid in ids
    rule = next(s for s in bs.CATALOG if s.id == "b-audit-006")
    assert rule.skill_type == "rule" and rule.replacement == ""


def test_guardrails_include_de_ai_patterns():
    labels = {name for _, name in AI_BUZZWORD_PATTERNS}
    assert {"AI过渡词", "AI热词", "空洞开头", "翻译腔", "破折号堆叠"} <= labels


def test_style_text_protects_facts():
    assert "数字" in DE_AI_STYLE_TEXT and "文号" in DE_AI_STYLE_TEXT
    assert "不改事实" in DE_AI_STYLE_TEXT


def test_strip_filler_removes_transitions_but_keeps_facts():
    text = "值得注意的是，征收面积12.34亩。总的来说，补偿标准为每亩5.2万元。"
    out, n = strip_filler(text)
    assert "值得注意的是" not in out and "总的来说" not in out
    assert "12.34亩" in out and "每亩5.2万元" in out
    assert n == 2
    # 幂等
    out2, n2 = strip_filler(out)
    assert n2 == 0 and out2 == out
