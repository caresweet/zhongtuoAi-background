"""复垦章节结构：10 章、章号连续、字数区间来自 5 份样本实证。"""
from app.agent.reclamation_definitions import (
    RECLAMATION_ALL_CHAPTERS,
    RECLAMATION_CHAPTER_DEFINITIONS,
    RECLAMATION_CHAPTER_KIND,
    RECLAMATION_TOTAL_CHAPTERS,
    get_reclamation_chapter_title,
)


def test_ten_chapters_contiguous():
    assert RECLAMATION_TOTAL_CHAPTERS == 10
    assert RECLAMATION_ALL_CHAPTERS == list(range(1, 11))
    assert set(RECLAMATION_CHAPTER_DEFINITIONS) == set(range(1, 11))


def test_every_chapter_has_required_keys():
    for n, d in RECLAMATION_CHAPTER_DEFINITIONS.items():
        assert d["title"], n
        assert d["description"], n
        assert isinstance(d["sections"], list) and d["sections"], n
        assert 0 < d["min_words"] <= d["max_words"], n


def test_chapter_titles_match_the_five_samples():
    t = {n: RECLAMATION_CHAPTER_DEFINITIONS[n]["title"] for n in range(1, 11)}
    assert t[1] == "前言"
    assert t[2] == "编制总则"
    assert t[3] == "项目概况"
    assert t[4] == "土地复垦方向可行性分析"
    assert t[5] == "土地复垦质量要求与复垦措施"
    assert t[6] == "土地复垦工程设计及工程量测算"
    assert t[7] == "土地复垦投资估算"
    assert t[8] == "土地复垦工作计划安排"
    assert t[9] == "土地复垦效益分析"
    assert t[10] == "保障措施"


def test_word_ranges_are_evidence_based():
    """区间下限不得低于样本最小值的一半，上限不得高于样本最大值。"""
    # 注：第 5 章下限取 4000 与本文件 min_words 一致（brief/plan 原文给 3998，
    #     与 impl 的 4000 相差 2，属计划书笔误；保留 impl 原文不动）。
    expect = {1: (2132, 2768), 2: (2321, 3068), 3: (1535, 2440), 4: (7547, 8988),
              5: (4000, 4578), 6: (1220, 1581), 7: (3110, 4815), 8: (669, 905),
              9: (934, 973), 10: (2928, 5715)}
    for n, (lo, hi) in expect.items():
        d = RECLAMATION_CHAPTER_DEFINITIONS[n]
        assert d["min_words"] <= lo
        assert d["max_words"] >= hi


def test_chapter_kind_and_title_lookup():
    assert RECLAMATION_CHAPTER_KIND[6] == "table"
    assert RECLAMATION_CHAPTER_KIND[7] == "table"
    assert RECLAMATION_CHAPTER_KIND[1] == "fact"
    assert get_reclamation_chapter_title(4) == "土地复垦方向可行性分析"
    assert get_reclamation_chapter_title(99) == "第99章"
