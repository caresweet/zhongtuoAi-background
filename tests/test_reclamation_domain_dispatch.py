"""复垦域分发收口 —— 装配器与单章提示词按域取，稳评逐字不变。

背景（本分支要治的病）：域标记 `_domain` 早已贯穿生成（`domain_of_state`），
但两个下游层仍写死稳评：
  · `report_assembler` —— 无条件把**稳评评审表**塞进复垦报告的第 3 章，
    并因此**丢弃复垦第 3 章（项目概况）的正文**；附件章也按稳评第 14 章挂。
  · `master_orchestrator.build_chapter_prompt` —— 把稳评的章结构/字数/表格/口径
    注进**每一章**的提示词，包括复垦章。

修法：两个消费者一律经 `app.agent.chapter_catalog` 按域取值，自己不按域分支。
稳评是默认域，取值逐字等于改动前 —— 本文件的 `*_stability_*` 用例是那条护栏。
"""
from docx import Document

from app.agent import chapter_catalog as cat


# ════════════════════════════════════════════════════════════
# A. chapter_catalog —— 按域取值的唯一收口
# ════════════════════════════════════════════════════════════

def test_reclamation_catalog_values():
    assert cat.all_chapters("reclamation") == list(range(1, 11))
    assert cat.total_chapters("reclamation") == 10
    assert cat.chapter_title("reclamation", 3) == "项目概况"
    assert cat.chapter_kind("reclamation", 3) == "fact"       # 叙述章，不是评审表
    assert cat.chapter_titles("reclamation")[1] == "前言"
    # 🔴 复垦没有评审表章、没有附件章 —— 这是「不塞评审表、不挂稳评附件」的判据
    assert cat.has_review_table("reclamation") is False
    assert cat.appendix_chapter_no("reclamation") is None
    # 字数取自复垦定义里已有的 min/max
    assert cat.target_words("reclamation", 3) == (1500, 2450)


def test_stability_catalog_values_unchanged():
    """稳评（含不传域的默认）逐字等于改动前 —— 稳评护栏。"""
    assert cat.has_review_table("stability") is True
    assert cat.has_review_table(None) is True
    assert cat.appendix_chapter_no("stability") == 14
    assert cat.appendix_chapter_no(None) == 14
    assert cat.chapter_title(None, 3) == cat.chapter_title("stability", 3)
    # 稳评的字数/字段标注委托既有实现，不得出现第二份
    from app.services.master_orchestrator import target_words, stability_key_labels
    for n in range(1, 15):
        assert cat.target_words("stability", n) == target_words(n)
        assert cat.target_words(None, n) == target_words(n)
    assert cat.key_labels("stability") == stability_key_labels()
    # 报告类型名（页眉/文件名用）
    assert cat.report_kind_name("stability") == "社会稳定风险评估报告"
    assert cat.report_kind_name(None) == "社会稳定风险评估报告"
    assert cat.cover_type_line("stability") == "《社会稳定性风险评估》"
    assert cat.cover_fallback_title("stability") == "土地征收决策"


def test_catalog_sections_and_table_ref_by_domain():
    from app.agent.chapter_definitions import format_sections_for_prompt
    # 复垦第 3 章的二级标题是 3.1~3.4，不是稳评评审表的（一）（二）
    rec = cat.sections_for_prompt("reclamation", 3)
    assert "## 3.1 项目简介" in rec and "## 3.4 项目区土地利用状况" in rec
    assert "（一）" not in rec
    # 稳评第 3 章的规范由既有实现给出（逐字不变）
    assert cat.sections_for_prompt("stability", 3) == format_sections_for_prompt(3)
    assert cat.sections_for_prompt(None, 3) == format_sections_for_prompt(3)
    # 复垦第 4 章有结构规定的表；第 3 章没有 → 空串
    assert "临时用地土地利用现状统计表" in cat.table_format_reference("reclamation", 4)
    assert cat.table_format_reference("reclamation", 3) == ""


# ════════════════════════════════════════════════════════════
# B. build_chapter_prompt —— 提示词里的「结构」按域取
# ════════════════════════════════════════════════════════════

_STAB_LEAK_MARKERS = ["社会稳定风险评估", "稳评", "DB32", "征地", "征收", "评审表"]


def _chapter_def(num, title):
    return {"num": num, "title": title, "key_points": ["要点"],
            "data_needed": ["project_name"], "depends_on": [], "argument_note": ""}


def test_stability_prompt_unchanged_by_default_domain():
    """不传 domain == 显式传 stability —— 默认路径逐字不变。"""
    from app.services.master_orchestrator import build_chapter_prompt
    cd = _chapter_def(3, "社会稳定风险评估评审表")
    filled = {"project_name": "某项目", "area_mu": "1.5"}
    a = build_chapter_prompt(cd, filled)
    b = build_chapter_prompt(cd, filled, domain="stability")
    assert a == b
    # 稳评身份首句与评审表章特定要求仍在
    assert "你是社会稳定风险评估报告的资深编写专家。" in a
    assert "社会稳定风险评估评审表" in a


def test_reclamation_prompt_carries_reclamation_structure_only():
    """复垦提示词：含复垦章结构/字数，不含任何稳评结构/口径。"""
    from app.services.master_orchestrator import build_chapter_prompt
    from app.agent.reclamation_definitions import (
        RECLAMATION_ALL_CHAPTERS, RECLAMATION_CHAPTER_DEFINITIONS,
        get_reclamation_chapter_title)

    for n in RECLAMATION_ALL_CHAPTERS:
        cd = _chapter_def(n, get_reclamation_chapter_title(n))
        p = build_chapter_prompt(cd, {"project_name": "某复垦项目"}, domain="reclamation")
        # 身份是复垦的
        assert "土地复垦方案编制专家" in p
        # 复垦的结构（二级标题）在、稳评的评审表章特定要求不在
        for s in RECLAMATION_CHAPTER_DEFINITIONS[n]["sections"]:
            assert f"## {s}" in p, f"第{n}章缺小节 {s}"
        # 🔴 一字都不许带稳评的结构/口径
        for m in _STAB_LEAK_MARKERS:
            assert m not in p, f"第{n}章复垦提示词混入稳评标记「{m}」"
        # 字数是复垦区间（复垦第3章上限 2450，稳评第3章是 2000）
        lo, hi = cat.target_words("reclamation", n)
        assert f"本章必须达到{lo}字以上" in p
        assert f"本章不要超过 {hi} 字" in p


def test_reclamation_prompt_ch3_is_a_narrative_not_review_table():
    """复垦第 3 章提示词必须让它写「项目概况」正文，而不是稳评评审表。"""
    from app.services.master_orchestrator import build_chapter_prompt
    p = build_chapter_prompt(_chapter_def(3, "项目概况"), {"project_name": "X"},
                             domain="reclamation")
    assert "## 3.1 项目简介" in p
    assert "3.4 项目区土地利用状况" in p


# ════════════════════════════════════════════════════════════
# C. 装配器 —— 端到端渲染（用 tmp 目录，不碰真实 storage / 库）
# ════════════════════════════════════════════════════════════

def _render(tmp_path, domain, chapters, filled):
    from app.services.report_assembler import ReportAssembler
    state = {"session_id": "t", "_domain": domain,
             "chapters": chapters, "filled_data": filled}
    a = ReportAssembler(storage_dir=tmp_path)
    out = a.assemble(state)
    doc = Document(str(tmp_path / "generated" / out.split("/")[-1]))
    text_parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            text_parts.append(" | ".join(c.text for c in row.cells))
    for sec in doc.sections:
        for p in sec.header.paragraphs:
            text_parts.append("HDR:" + p.text)
    return out, "\n".join(text_parts)


def test_reclamation_render_writes_ch3_and_has_no_review_table(tmp_path):
    """复垦：第3章正文写出来；没有评审表；封面/页眉/文件名按复垦。"""
    out, text = _render(tmp_path, "reclamation",
                        {3: {"markdown": "# 项目概况\n\n## 3.1 项目简介\n\n"
                                            "本项目为测试复垦项目RECLAMATIONBODY。"}},
                        {"project_name": "测试复垦项目", "doc_reference": "苏复垦〔2026〕1号"})
    assert "RECLAMATIONBODY" in text                 # 第3章正文没被丢弃
    assert "评审表" not in text                       # 没塞稳评评审表
    assert "社会稳定性风险评估" not in text           # 封面副标题不是稳评
    assert "社会稳定风险评估" not in text
    assert "附件清单" not in text                     # 没有稳评第14章附件
    assert "土地复垦方案" in out                      # 文件名按复垦
    assert "HDR:测试复垦项目土地复垦方案" in text


def test_stability_render_keeps_review_table_and_discards_ch3_llm_body(tmp_path):
    """稳评：第3章仍是评审表（LLM 正文被丢弃）；封面是稳评。"""
    out, text = _render(tmp_path, "stability",
                        {3: {"markdown": "# 社会稳定风险评估评审表\n\nLLMBODY"},
                         4: {"markdown": "# 第4章\n\n## 4.1 合法性分析\n\nSTABILITYBODY"}},
                        {"project_name": "测试稳评项目", "doc_reference": "苏政发〔2026〕1号"})
    assert "评审表" in text                           # 评审表路径照走
    assert "LLMBODY" not in text                      # 第3章 LLM 正文照旧被丢弃
    assert "STABILITYBODY" in text                    # 其它章正文照写
    assert "《社会稳定性风险评估》" in text           # 封面副标题未变
    assert "社会稳定风险评估报告" in out              # 文件名未变
    assert "附件清单" in text                         # 第14章附件照挂


def test_reclamation_chapter_and_appendix_decisions_are_domain_gated():
    """决策点（不渲染）：装配器按域决定有无评审表路径、有无附件章。"""
    assert cat.has_review_table("reclamation") is False
    assert cat.has_review_table("stability") is True
    assert cat.appendix_chapter_no("reclamation") is None
    assert cat.appendix_chapter_no("stability") == 14


# ════════════════════════════════════════════════════════════
# D. 编排层把域透传给提示词构造器（少了这一行，复垦仍拿稳评提示词）
# ════════════════════════════════════════════════════════════

def test_workflow_generate_passes_domain_to_prompt_builder():
    import inspect
    from app.services import report_workflow
    src = inspect.getsource(report_workflow)
    assert "build_chapter_prompt(" in src
    # 调用点必须带上 domain=_domain（域在 node_chapter_generate 里已解析）
    call = src.index("prompt = build_chapter_prompt(")
    assert "domain=_domain" in src[call:call + 400]
