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
    """配置的区间必须**罩住**真实样本的取值范围：
    下限不高于实测最小值（否则最轻的真实报告也达不到我们的下限），
    上限不低于实测最大值（否则最重的真实报告会超上限）。
    """
    expect = {1: (2132, 2768), 2: (2321, 3068), 3: (1535, 2440), 4: (7547, 8988),
              5: (3998, 4578), 6: (1220, 1581), 7: (3110, 4815), 8: (669, 905),
              9: (934, 973), 10: (2928, 5715)}
    for n, (lo, hi) in expect.items():
        d = RECLAMATION_CHAPTER_DEFINITIONS[n]
        assert d["min_words"] <= lo, f"第{n}章 min_words 高于实测最小值"
        assert d["max_words"] >= hi, f"第{n}章 max_words 低于实测最大值"


def test_chapter_kind_and_title_lookup():
    # 🔴 第6/7章原标 "table"（本意是「以表格为主」）。但编排层对 "table" 的理解是
    #    「由**组装器**确定性渲染、不经 LLM」（稳评第3章评审表/第14章附件那类），
    #    命中即 `return state` **跳过该章** —— 而复垦的组装器并不渲染这两章，
    #    成稿于是静默少两章。复垦没有任何组装器渲染的章，故一律 "fact"。
    assert RECLAMATION_CHAPTER_KIND[6] == "fact"
    assert RECLAMATION_CHAPTER_KIND[7] == "fact"
    assert RECLAMATION_CHAPTER_KIND[1] == "fact"
    assert set(RECLAMATION_CHAPTER_KIND.values()) == {"fact"}, \
        "复垦不应出现 table/attachment 类章（会被编排层跳过）"
    assert get_reclamation_chapter_title(4) == "土地复垦方向可行性分析"
    assert get_reclamation_chapter_title(99) == "第99章"
