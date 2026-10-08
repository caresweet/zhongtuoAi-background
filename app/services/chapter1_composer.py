"""第1章「编制说明」装配 —— 结构照搬 + agent 扩写。

业主口径：
    「编制说明的内容再让agent多写一些。」
    同时要求体例锁定为「（一）稳评理由 → 1. 2. 3.」三块。

为什么不直接让 LLM 整章生成：实测模型反复改写/省略条目名称行
（把「1.为维护人民群众的合法权益」写成句子开头或整行删掉），
结构与知识库案卷对不上。故改为**结构由语料库锁定、内容由 agent 补足**：

    ① **三块（一）（二）（三）及其下条目：原文照搬，一字不改**；
    ② **只有前言（引中办发〔2021〕11号那块）由 agent 补写**——
       业主口径：「我让你添加内容的模块是根据中共中央那块的内容」；
    ③ 确定性拼接 —— 前言原文 + agent 补写的前言段 + 三块原文。

🔴 此前把扩写加在三块里，导致与参照件原文差距很大（条目被改写）。
   业主明确：**加内容的只有前言，三块保持照搬**。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# 三块的固定顺序（也是二级标题文本）
BLOCKS = ("（一）稳评理由", "（二）稳评目的", "（三）稳评原则")

# 🔴 是否让 agent 补写前言。业主口径（2026-09-16）：**先不要补写**——
#    「字数也要跟模板差不多」：参照件第1章 848 字，而三块照搬原文已占 728 字，
#    再加补写就超出一倍。设为 True 可恢复「前言补写」（会走 LLM）。
PREAMBLE_ENRICH_ENABLED = False


def _parse_skeleton(md: str) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """把语料库的第1章 markdown 解析成 [(块标题, [(条目名, 原说明段)])]。

    语料形态：
        ### （一）稳评理由
        1.为维护人民群众的合法权益
        说明段…
        2.…
    """
    out: List[Tuple[str, List[Tuple[str, str]]]] = []
    cur_block, cur_items, cur_label, cur_body = None, [], None, []

    def _flush_item():
        nonlocal cur_label, cur_body
        if cur_block is not None and cur_label:
            cur_items.append((cur_label, "\n".join(x for x in cur_body if x.strip()).strip()))
        cur_label, cur_body = None, []

    def _flush_block():
        nonlocal cur_items
        _flush_item()
        if cur_block is not None and cur_items:
            out.append((cur_block, cur_items))
        cur_items = []

    for line in md.split("\n"):
        s = line.strip()
        if not s:
            continue
        m = re.match(r'^#{2,4}\s*(（[一二三四五六七八九十]+）\s*\S.*)$', s)
        if m:
            _flush_block()
            cur_block = m.group(1).strip()
            continue
        if cur_block is None:
            continue
        if re.match(r'^\d+[.、]\s*\S', s):
            _flush_item()
            cur_label = s
            continue
        cur_body.append(s)
    _flush_block()
    return out


def skeleton_text() -> str:
    """返回语料库第1章的骨架 markdown（未替换占位符）。"""
    from app.services.reference_corpus import get_reference_chapter_text
    return get_reference_chapter_text(1)


def build_blocks(filled: Dict[str, Any]):
    """取语料库骨架并完成占位符替换 → (前言, [(块标题, [(条目名, 原说明段)])])。"""
    from app.services.reference_corpus import render_reference_markdown
    md = render_reference_markdown(1, filled).markdown
    # 前言 = 第一个二级标题之前的所有内容（受托背景/依据/致谢）
    m = re.search(r'^#{2,4}\s*（', md, flags=re.MULTILINE)
    preamble = md[:m.start()].strip() if m else ""
    return preamble, _parse_skeleton(md)


# ── agent 扩写 ────────────────────────────────────────────

def _enrich_prompt(preamble: str, filled: Dict[str, Any], style_ref: str = "") -> str:
    """构造**前言补写**提示词。

    🔴 业主口径：要加内容的只有前言（引中办发〔2021〕11号那块），
    三块（一）（二）（三）一律照搬原文、不得改写。
    🔴 实测补 3 段会**角度重复**（都在讲「稳评的意义」「工作程序」），
    故这里限定只补 **2 段**并**指定各自角度**，且明示原文已写过什么。
    """
    lines = [
        "你在为一篇《社会稳定风险评估报告》「第一章 编制说明」的**前言**补写内容。",
        "",
        "## 前言已有内容（**这些已写过，不要重复**）",
        "① 开展本项评估的政策依据；② 受托背景（受谁委托、承担什么）；"
        "③ 评估工作组的调查方式（资料收集/现场踏勘/问卷调查/座谈走访/风险分析研判）；④ 致谢。",
        "",
        "## 已定的前言原文（保持不动）",
        preamble.strip()[:900] if preamble.strip() else "（暂无）",
        "",
        "## 只补写 1 段（**不要多写**）",
        "角度：**社会稳定风险评估的制度定位与政策沿革**"
        "（作为重大决策的前置程序与刚性约束、中央与省市近年来的部署要求、"
        "「应评尽评、源头预防」的工作导向、评估结论的运用与责任约束）。",
        "",
        "## 硬性要求",
        "1. **不要写本项目的具体数据**（位置、面积、村组名、问卷份数等一律不写）——"
        "那些属于第6章「项目基本情况」。",
        "2. 不要重复「前言已有内容」里的四点；两段之间也不要有相同的句子或意思。",
        "3. **只写 1 段，180～240 字**，通用公文表述，语气与体例与原文一致、接在其后读起来连续。"
        "\n4. 全文（含照搬的三块）总字数要与参照件第1章相当，**不要靠堆段数把篇幅撑大**。",
    ]
    if style_ref.strip():
        lines += ["", "## 知识库同类案卷的行文参照（学措辞，**不要照抄其中的项目名/数据**）",
                  style_ref.strip()[:2000]]
    lines += [
        "",
        "## 输出要求",
        '严格输出 JSON：{"1": "这一段"}，**只有 1 个键**。',
        "只输出 JSON，不要解释、不要代码块围栏。",
    ]
    return "\n".join(lines)


def _is_near_duplicate(new: str, existing: List[str], n: int = 8, thresh: float = 0.18) -> bool:
    """近重复判定：与任一已有段落共用 ≥thresh 的 n-gram 即判重复。

    实测补写段之间/与原文之间会出现整句雷同（都在讲「稳评的意义」「工作程序」），
    光靠提示词约束不住，这里做一道确定性兜底。
    """
    import re as _re
    def sh(t):
        t = _re.sub(r'\s+', '', t)
        return {t[i:i+n] for i in range(max(0, len(t)-n))}
    A = sh(new)
    if not A:
        return True
    for e in existing:
        B = sh(e)
        if not B:
            continue
        if len(A & B) / min(len(A), len(B)) >= thresh:
            return True
    return False


async def _style_reference(filled: Dict[str, Any]) -> str:
    """RAG 取回知识库同类案卷的编制说明类文字，作为行文参照。"""
    try:
        from app.rag.rag_service import rag_service
        from app.agent.chapter_definitions import get_chapter_rag_query
        r = await rag_service.retrieve_for_chapter(
            chapter_number=1, session_id="",
            project_context=get_chapter_rag_query(1), n_results=10, domain="stability")
        if not isinstance(r, dict):
            return ""
        return "\n\n".join(str(r.get(k) or "") for k in
                            ("chapter_context", "local_regulation_context", "example_context")).strip()
    except Exception as e:
        logger.warning(f"第1章行文参照检索失败: {e}")
        return ""


async def enrich_preamble(preamble: str, filled: Dict[str, Any], llm) -> List[str]:
    """让 agent 为**前言**补写若干段，返回段落列表。失败返回 []。

    🔴 由 `PREAMBLE_ENRICH_ENABLED` 控制；关闭时直接返回 []（纯照搬，零 LLM）。
    """
    if not PREAMBLE_ENRICH_ENABLED or llm is None:
        return []
    try:
        style_ref = await _style_reference(filled)
        prompt = _enrich_prompt(preamble, filled, style_ref)
        # 🔴 **字数上限** —— 第1章走的是第三条生成路径（本 composer），
        #    既不过 `build_chapter_prompt` 也不过 `build_rewrite_prompt`。
        #    实测 1019 字（目标 900）。三条路径共用 `target_words()` 一个来源。
        try:
            from app.services.master_orchestrator import target_words
            _lo, _hi = target_words(1)
            prompt += (f"\n\n## 篇幅\n🔴 本章 **{_lo}–{_hi} 字**。"
                       f"超过 {_hi} 字要删 —— 同一件事不换说法重复说、不堆套话、"
                       f"不写没有事实的收尾句。写完数一下，超了就删。")
        except Exception:
            pass
        text = await llm.chat_with_reasoning(
            messages=[{"role": "user", "content": prompt}],
            system="你是资深稳评工程师，只输出严格 JSON。",
            max_tokens=6000, temperature=0.4,
        )
        raw = text.get("content", "") if isinstance(text, dict) else str(text)
        m = re.search(r'\{[\s\S]*\}', raw or "")
        if not m:
            logger.warning("第1章前言补写未返回 JSON")
            return []
        data = json.loads(m.group(0))
    except Exception as e:
        logger.warning(f"第1章前言补写失败: {e}")
        return []
    cand = []
    for k in sorted((data or {}).keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
        v = data[k]
        if isinstance(v, str) and v.strip():
            cand.append(v.strip())
    # 🔴 去重兜底：与已有前言或已采纳段落近重复的一律丢弃，最多保留 2 段
    out: List[str] = [preamble] if preamble.strip() else []
    kept: List[str] = []
    for c in cand:
        if len(kept) >= 1:
            break
        if _is_near_duplicate(c, out):
            logger.info("第1章前言补写段与已有内容近重复，已丢弃")
            continue
        kept.append(c); out.append(c)
    return kept


def compose(preamble: str, blocks, extra_paras: List[str]) -> str:
    """确定性拼接：前言原文 + agent 补写的前言段 + 三块**原文照搬**。"""
    out = []
    if preamble.strip():
        out.append(preamble.strip())
    for p in (extra_paras or []):
        if p.strip():
            out.append(p.strip())
    for bname, items in blocks:
        out.append(f"### {bname}")
        for label, body in items:
            out.append(label)
            if body:
                out.append(body)
    return "\n\n".join(out)
