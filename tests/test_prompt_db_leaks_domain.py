# -*- coding: utf-8 -*-
"""复垦单章提示词「读库/标注」收口 —— 事实卡标注、few-shot、skill、学习提示、工具回合。

本文件的护栏分两类：
  · 稳评（默认域）**逐字不变** —— 收口改动不得动到稳评；
  · 复垦**拿到的是「没有」而不是稳评那套** —— 复垦域无数据时不得回落稳评 DB/缓存。
所有用例用 `tmp_path` 造库，不碰真实 `data/*.db`、不触网。
"""
import asyncio
import sqlite3

import pytest

import app.agent.chapter_catalog as catalog
import app.agent.agents.chapters.chapter_base as cb
import app.services.few_shot_service as fss
import app.services.master_orchestrator as mo


# ═══════════════════════════════════════════════════════════════
# 1. 事实卡字段标注（build_project_card）
# ═══════════════════════════════════════════════════════════════

def test_project_card_labels_seam_stability_is_the_stored_map():
    """稳评标注逐字等于 `_PROJECT_CARD_LABELS`（单源，未复制第二份）。"""
    assert catalog.project_card_labels("stability") == mo._PROJECT_CARD_LABELS
    assert catalog.project_card_labels(None) == mo._PROJECT_CARD_LABELS


def test_project_card_labels_reclamation_reuses_key_labels():
    """复垦标注复用其既有的 `RECLAMATION_KEY_LABELS`（与「可用数据」标注同源）。"""
    from app.agent.reclamation_definitions import RECLAMATION_KEY_LABELS
    assert catalog.project_card_labels("reclamation") == RECLAMATION_KEY_LABELS


def test_stability_card_text_unchanged_for_known_fields():
    """稳评事实卡：字段标注仍是稳评口径（护栏，非目测）。"""
    card = mo.build_project_card({
        "implement_unit": "某某公司", "doc_reference": "洪拟征告〔2026〕1号",
        "area_mu": "1.5", "support_rate": "95",
    }, "stability")
    assert "稳评实施单位：某某公司" in card
    assert "征地预公告文号：洪拟征告〔2026〕1号" in card
    assert "征收面积（亩）：1.5" in card
    assert "调查支持率：95" in card


def test_reclamation_card_has_no_stability_wording():
    """复垦事实卡不得出现稳评字段名/小节抬头（哪怕解析器误填了稳评字段）。"""
    card = mo.build_project_card({
        "build_unit": "某某公司", "location": "某镇某村", "area_mu": "25.5",
        "doc_reference": "苏自然资函〔2026〕1号",
        # 解析器若误填稳评字段，也**不得**把它们以稳评标注显示出来
        "implement_unit": "误填", "purpose_text": "征收目的X",
        "publicity_period_text": "稳评公示X", "public_demands": "诉求X",
        "_land_owners": ["A村"], "boundary_points": "12",
    }, "reclamation")
    for token in ("稳评", "征收", "社会稳定风险", "征收土地预公告",
                  "稳评公示期", "座谈会记录", "第6章", "第9章", "第10章"):
        assert token not in card, f"复垦事实卡泄漏稳评字样：{token}"
    # 复垦自己的标注在（字段名落在卡片 key 列表里的那几个）
    assert "临时用地位置：某镇某村" in card
    assert "批复/备案文号：苏自然资函〔2026〕1号" in card
    # 误填的稳评字段只回落原 key，绝不套上稳评标注
    assert "稳评实施单位" not in card


def test_reclamation_card_omits_announcement_and_symposium_blocks():
    """复垦无「征收预公告」「稳评座谈会」概念 —— 对应小节整块不出现，不换词冒充。"""
    card = mo.build_project_card({
        "purpose_text": "X", "scope_text": "Y",
        "public_demands": "A", "discussion_text": "B", "conclusion_text": "C",
    }, "reclamation")
    assert "预公告" not in card
    assert "座谈" not in card


def test_project_card_lexicon_stability_headers_byte_identical():
    """稳评三个附带小节的抬头逐字等于改动前的内联文本。"""
    lex = catalog.project_card_lexicon("stability")
    assert lex["ann_heading"] == "  —— 征收土地预公告正文摘录 ——"
    assert lex["land_heading"] == "  —— 勘测定界明细（第6章 1.5 涉及村组/地类构成、第10章合法性论证用）——"
    assert lex["meet_heading"] == "  —— 座谈会记录（第9章 各方意见汇总取材）——"
    assert lex["meet_fields"] == [
        ("public_demands", "座谈会群众诉求（记录原文）"),
        ("discussion_text", "座谈讨论情况（记录原文）"),
        ("conclusion_text", "座谈结论（记录原文）"),
    ]
    # 复垦：两小节字段表留空、勘测定界抬头去掉稳评章号
    rlex = catalog.project_card_lexicon("reclamation")
    assert rlex["ann_fields"] == [] and rlex["meet_fields"] == []
    assert "第6章" not in rlex["land_heading"] and "第10章" not in rlex["land_heading"]
    assert "勘测定界明细" in rlex["land_heading"]


def test_build_chapter_prompt_passes_domain_to_card(monkeypatch):
    """路径 B：`build_chapter_prompt` 把 domain 传给了 `build_project_card`。"""
    seen = {}
    monkeypatch.setattr(mo, "build_project_card",
                        lambda filled, domain=None: seen.setdefault("d", domain) or "")
    mo.build_chapter_prompt({"num": 3, "title": "项目概况"},
                            {"build_unit": "X"}, domain="reclamation")
    assert seen["d"] == "reclamation"


# ═══════════════════════════════════════════════════════════════
# 2. few-shot 缓存按域分区
# ═══════════════════════════════════════════════════════════════

def test_few_shot_cache_is_domain_partitioned():
    """缓存键含域：稳评缓存的范文不会漏给复垦同号章。"""
    fss._few_shot_cache["examples"] = {
        fss._few_shot_key("stability", 3): [{"similarity_score": 1, "chapter_content": "X"}],
    }
    assert fss.get_cached_few_shot(3, "stability") != ""
    assert fss.get_cached_few_shot(3, "reclamation") == ""
    assert fss.get_cached_few_shot(3) != ""  # 默认域 = 稳评


def test_refresh_few_shot_cache_uses_domain_chapters_and_domain(monkeypatch):
    """`refresh_few_shot_cache` 既按域取候选，也只遍历该域的章号。"""
    calls = []

    async def _fake_matches(self, project_info, chapter_num=0, domain="stability", top_k=3):
        calls.append((domain, int(chapter_num)))
        return []

    monkeypatch.setattr(fss.FewShotService, "find_best_matches", _fake_matches)
    fss._few_shot_cache["examples"] = {}
    fss._few_shot_cache["project_hash"] = {}
    fss._few_shot_cache["updated"] = {}
    asyncio.run(fss.refresh_few_shot_cache(
        {"location": "L", "area_mu": "1", "land_use": "U"}, "reclamation"))
    domains = {d for d, _c in calls}
    chapters = {c for _d, c in calls}
    assert domains == {"reclamation"}
    assert chapters == set(range(1, 11))          # 复垦 10 章，不是稳评 14 章


# ═══════════════════════════════════════════════════════════════
# 3/4. review_skills / 学习提示按域过滤
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def _kb_with_stability_skill(tmp_path, monkeypatch):
    """造一个只含稳评行的 knowledge_base.db，并把 settings.DATA_DIR 指过去。"""
    from app.config import settings
    p = tmp_path / "knowledge_base.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE review_skills (id INTEGER PRIMARY KEY, domain TEXT DEFAULT 'stability',"
                 " chapter_num INTEGER DEFAULT 0, skill_type TEXT, rule_pattern TEXT,"
                 " rule_desc TEXT, correction TEXT, is_active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO review_skills (domain, chapter_num, skill_type, rule_desc, correction)"
                 " VALUES ('stability', 0, 'text', '稳评专家意见', '改法')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return p


def test_expert_skill_hints_domain_filtered(_kb_with_stability_skill):
    """`_get_expert_skill_hints` 按域过滤：稳评有、复垦空。"""
    assert "稳评专家意见" in mo._get_expert_skill_hints(2, "stability")
    assert mo._get_expert_skill_hints(2, "reclamation") == ""


def test_get_few_shot_with_skills_domain_filtered(_kb_with_stability_skill):
    """`get_few_shot_with_skills` 的 skill 提示也按域过滤，范文缓存亦按域。"""
    fss._few_shot_cache["examples"] = {}
    stab = fss.get_few_shot_with_skills(2, "stability")
    assert "稳评专家意见" in stab
    assert fss.get_few_shot_with_skills(2, "reclamation") == ""


def test_cached_learning_hints_reads_domain_bucket(monkeypatch):
    """`_get_cached_learning_hints` 读**本域**的缓存桶，不串域。"""
    cb._learning_cache["hints"] = {"stability": {2: [{"label": "稳评历史问题", "count": 3}]}}
    cb._learning_cache["updated"] = {"stability": 1e18, "reclamation": 1e18}  # 视为新鲜，跳过刷新
    assert "稳评历史问题" in cb._get_cached_learning_hints(2, "stability")
    assert cb._get_cached_learning_hints(2, "reclamation") == ""


# ═══════════════════════════════════════════════════════════════
# 5. 工具回合按域取值
# ═══════════════════════════════════════════════════════════════

def test_tool_round_schema_stability_full_and_reclamation_own_tools():
    """稳评工具表＝全量五件（逐字不变）；复垦＝复垦自己那几件，不含稳评专有器物。"""
    from app.agent.tools import ALL_TOOLS_SCHEMA
    assert catalog.tool_round_schema("stability") is ALL_TOOLS_SCHEMA

    rec = catalog.tool_round_schema("reclamation")
    names = [f["function"]["name"] for f in rec]
    assert names == ["retrieve_rag", "parse_kd_table",
                     "calc_earthwork", "calc_investment", "evaluate_suitability"]
    # 稳评专有器物（扣分制评分 / 公众·部门问卷）不得进复垦工具表
    assert "calc_risk_scoring" not in names
    assert "query_survey" not in names
    assert "query_dept_survey" not in names


def test_tool_round_runs_for_reclamation_with_reclamation_identity():
    """复垦域：有自己的工具表 → 发起工具回合，且决策调用用的是复垦身份（非稳评）。"""
    import app.agent.agents.chapters.reclamation_chapter_base as rcb
    a = rcb.ReclamationChapterAgentBase()
    a.chapter_number = 6
    captured = {}

    class _L:
        async def chat_with_reasoning(self, **kwargs):
            captured["system"] = kwargs.get("system", "")
            captured["tools"] = [t["function"]["name"] for t in (kwargs.get("tools") or [])]
            return {"content": "无需工具", "reasoning": "", "tool_calls": []}

    a._llm = _L()
    a._emit_thinking = _noop_async
    out = asyncio.run(a._maybe_run_tool_round({}, "USER", "SYS"))
    assert out == []
    assert "复垦" in captured["system"] and "稳评" not in captured["system"]
    assert "calc_earthwork" in captured["tools"] and "calc_risk_scoring" not in captured["tools"]


async def _noop_async(*args, **kwargs):
    return None

