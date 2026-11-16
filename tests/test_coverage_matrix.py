# -*- coding: utf-8 -*-
"""交付闸门（覆盖矩阵 + 字数预算）单元测试。"""

from app.agent import chapter_catalog
from app.services.coverage_matrix import (
    REQUIREMENTS,
    attach,
    check_coverage,
    check_word_budget,
    evaluate,
    plain_char_count,
    summarize,
)

_STABILITY_TEXT = (
    "项目名称为××土地征收项目，征收面积 12.34 亩。"
    "项目位于××镇，东至规划路、西至河道、南至村道、北至村民组。"
    "征地补偿标准按区片综合地价执行，涉及被征地农民社会保障。"
    "征收行为符合国民经济和社会发展规划，依据《土地管理法》第四十七条履行程序。"
    "本次共发放调查问卷 120 份，召开座谈会 2 场，并按规定公示公告。"
    "利益相关者包括村集体、受访村民和村民代表。"
    "经识别，主要风险因素为补偿标准争议，风险源集中于个别村民，风险等级为低风险，评分 88 分。"
    "针对上述风险因素提出了防范措施和化解措施。"
    "稳评结论：本项目风险等级为低风险，建议准予实施。"
    "附件含评审表。"
)


def _stability_chapters(text: str = _STABILITY_TEXT):
    from app.agent.chapter_definitions import ALL_CHAPTERS
    return {n: {"markdown": text} for n in ALL_CHAPTERS}


def _full_length_chapters(domain: str = "stability"):
    """每章都写到字数下限的假章节（用于字数预算通过用例）。"""
    out = {}
    for n in chapter_catalog.all_chapters(domain):
        if chapter_catalog.chapter_kind(domain, n) != "fact":
            continue
        lo, _hi = chapter_catalog.target_words(domain, n)
        out[n] = {"markdown": "本项目" + ("正" * int(lo))}
    return out


def test_plain_char_count_ignores_markdown():
    md = "## 标题\n\n| 表头 | 值 |\n|---|---|\n| 甲 | 乙 |\n\n正文**加粗**。"
    assert plain_char_count(md) == len("标题表头值甲乙正文加粗。")


def test_stability_coverage_covers_all_critical():
    cov = check_coverage(_stability_chapters(), "stability")
    assert cov["total"] == len(REQUIREMENTS["stability"])
    assert cov["missing_critical"] == []
    assert cov["covered"] >= cov["total"] - 5          # 允许个别 warn 项未写


def test_missing_required_item_becomes_blocker():
    text = _STABILITY_TEXT.replace(
        "东至规划路、西至河道、南至村道、北至村民组", "四至详见勘测定界图")
    gate = evaluate(_stability_chapters(text), {}, "stability")
    missing_ids = {m["id"] for m in gate["coverage"]["missing_critical"]}
    assert "stab-boundary" in missing_ids
    assert gate["deliverable"] is False
    assert any(b["kind"] == "coverage" and "四至" in b["message"]
               for b in gate["blockers"])


def test_word_budget_passes_when_every_chapter_reaches_minimum():
    budget = check_word_budget(_full_length_chapters(), "stability")
    assert budget["passed"] is True
    assert budget["missing_chapters"] == []
    assert budget["thin_chapters"] == []
    assert budget["total_ratio"] >= 1.0


def test_word_budget_flags_short_and_missing_chapters():
    chapters = _full_length_chapters()
    chapters[4] = {"markdown": "太短了。"}
    del chapters[6]
    budget = check_word_budget(chapters, "stability")
    assert budget["passed"] is False
    assert [r["chapter"] for r in budget["thin_chapters"]] == [4]
    assert [r["chapter"] for r in budget["missing_chapters"]] == [6]


def test_word_budget_skips_table_and_attachment_chapters():
    rows = {r["chapter"] for r in
            check_word_budget(_full_length_chapters(), "stability")["chapters"]}
    assert 3 not in rows        # 稳评第 3 章 = 评审表
    assert 14 not in rows       # 稳评第 14 章 = 附件


def test_reclamation_requirements_are_domain_specific():
    stability_ids = {r.id for r in REQUIREMENTS["stability"]}
    reclamation_ids = {r.id for r in REQUIREMENTS["reclamation"]}
    assert stability_ids.isdisjoint(reclamation_ids)

    text = ("本方案服务年限 3 年，复垦区面积 5.20 公顷，土地利用现状为耕地。"
            "土地损毁以压占和挖损为主，采用评价单元开展适宜性评价。"
            "复垦方向为旱地，实施表土剥离，工程量见土方测算表。"
            "静态总投资 74.15 万元，复垦率 100%。"
            "保障措施包括组织保障和资金保障。")
    cov = check_coverage({1: {"markdown": text}, 4: {"markdown": text},
                          6: {"markdown": text}, 7: {"markdown": text},
                          10: {"markdown": text}}, "reclamation")
    missing = {m["id"] for m in cov["missing_critical"]}
    assert missing == set()                      # 关键要求都写到了


def test_evaluate_reports_both_missing_and_thin():
    gate = evaluate({1: {"markdown": "太短。"}}, {}, "reclamation")
    kinds = {b["kind"] for b in gate["blockers"]}
    assert kinds == {"coverage", "words"}
    assert gate["deliverable"] is False
    assert "不可交付" in summarize(gate) or "覆盖矩阵" in summarize(gate)


def test_attach_writes_state_and_logs():
    state = {"chapters": _stability_chapters(), "filled_data": {},
             "_domain": "stability"}
    gate = attach(state)
    assert state["_deliverable"] is gate["deliverable"]
    assert state["_coverage"]["total"] == len(REQUIREMENTS["stability"])
    assert state["_word_budget"]["total_min_words"] > 0
    assert isinstance(state["_deliverable_blockers"], list)


def test_string_chapter_keys_are_supported():
    chapters = {str(k): v for k, v in _full_length_chapters().items()}
    budget = check_word_budget(chapters, "stability")
    assert budget["total_words"] > 0
    assert budget["missing_chapters"] == []


def test_unsupported_domain_never_blocks():
    gate = evaluate({1: {"markdown": "投标文件正文"}}, {}, "bidding")
    assert gate["deliverable"] is True
    assert gate["blockers"] == []
    assert gate["word_budget"]["passed"] is True
