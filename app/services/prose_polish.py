# -*- coding: utf-8 -*-
"""整章语句润色（只修语病，不改事实）。

用户反馈（2026-11-16）：「很多语句不通顺」。确定性规则只能断长句、并残句，
缺字/词语粘连/多层从句还得让模型顺一遍。约束：
    · 只做语言润色：数字、名称、文号、标题、表格、图占位一律不许动；
    · 改完做**硬校验**（数字多重集、标题集合、表格行数、长度 0.6～1.5），
      任何一项不通过就丢弃，保留原文；
    · 只处理体检有问题的章（长句/段首雷同/套话），不整本重写。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_HEAD_RE = re.compile(r"^#{1,4}\s+.+$", re.M)


def _numbers(md: str) -> List[float]:
    """数字归一成 float 多重集（2.6800 与 2.68 视为同一个数）。"""
    out: List[float] = []
    for x in _NUM_RE.findall(md or ""):
        try:
            out.append(float(x))
        except ValueError:
            pass
    return sorted(out)


def _headings(md: str) -> set:
    """只比标题文字（忽略 # 级别与空白差异）。"""
    return {re.sub(r'^#+\s*', '', h).strip() for h in _HEAD_RE.findall(md or "")}


def _table_rows(md: str) -> int:
    return sum(1 for line in (md or "").splitlines()
               if line.strip().startswith("|"))


def _check(orig: str, new: str) -> str:
    """返回不通过原因；空串 = 通过。"""
    if not new:
        return "空输出"
    if len(new) < len(orig) * 0.6 or len(new) > len(orig) * 1.5:
        return f"长度异常 {len(orig)}→{len(new)}"
    if sorted(_numbers(orig)) != sorted(_numbers(new)):
        return "数字被改动"
    if _headings(orig) != _headings(new):
        return "标题被改动"
    if _table_rows(orig) != _table_rows(new):
        return "表格行数被改动"
    return ""


def _passes(orig: str, new: str) -> bool:
    return not _check(orig, new)


def _needs_polish(text: str) -> bool:
    """该小节是否需要模型顺一遍（长句/AI 套词/段首雷同/标点重复）。"""
    if re.search(r"([，。；：、])\1+", text):
        return True
    for sent in re.split(r"[。！？；;]", text):
        if len(sent.strip()) > 80:
            return True
    try:
        from app.validation.content_guardrails import AI_BUZZWORD_PATTERNS
        for item in AI_BUZZWORD_PATTERNS:
            pat = item[0] if isinstance(item, (tuple, list)) else item
            if re.search(pat, text):
                return True
    except Exception:
        pass
    opens = [p.strip()[:4] for p in text.splitlines()
             if p.strip() and not p.strip().startswith(("#", "|", "!["))]
    if any(opens.count(o) >= 3 for o in set(opens)):
        return True
    return False


async def polish_section_text(md: str, llm=None, *, timeout: float = 120.0,
                              max_sections: int = 10) -> tuple[str, List[str]]:
    """逐小节顺语句：先做确定性归一，再对有问题的小节做模型润色。

    Returns: `(新正文, 被模型润色的小节标题列表)`。
    """
    if not md:
        return md, []
    from app.validation.content_guardrails import (
        merge_broken_sentences, split_long_sentences)

    parts = re.split(r"(?m)(?=^##\s)", md)
    out: List[str] = []
    polished: List[str] = []
    for part in parts:
        body = part
        body, _n1 = split_long_sentences(body, 80)
        body, _n2 = merge_broken_sentences(body)
        if llm is not None and len(body) >= 50 and _needs_polish(body) \
                and len(polished) < max_sections:
            title = ""
            m = re.match(r"^##\s+(.+)$", body, re.M)
            if m:
                title = m.group(1).strip()
            try:
                new = await _polish_text(llm, body, _SECTION_SYSTEM, timeout)
            except Exception as e:
                logger.warning(f"[小节润色] {title or '本节'}失败: {e}")
                new = ""
            if new and not _check(body, new):
                body = new
                polished.append(title or f"第{len(polished)+1}节")
        out.append(body)
    return "".join(out), polished


_SECTION_SYSTEM = (
    "你是中文公文校对。只改语病、缺字、词语粘连、断句和重复表达。"
    "禁止改动：数字、金额、面积、日期、单位名称、人名、文号、法规名称、"
    "各级标题、表格内容、图占位。同章相邻段落不要用相同的四字开头。"
    "保持原 markdown 结构，输出完整正文，不要解释、不要加前后缀。"
)


async def _polish_text(llm, md: str, system: str, timeout: float) -> str:
    """润色一段正文；优先关闭思考链（推理模型会把预算耗在 reasoning 上）。"""
    msgs = [{"role": "user", "content": "请润色下面这份报告的章节正文，只修语言问题：\n\n" + md}]
    budget = min(12000, max(2000, len(md) * 2))
    try:
        res = await asyncio.wait_for(
            llm.chat_with_reasoning(messages=msgs, system=system,
                                    max_tokens=budget, temperature=0.2,
                                    disable_thinking=True),
            timeout=timeout)
    except AttributeError:
        res = await asyncio.wait_for(
            llm.chat(messages=msgs, system=system,
                     max_tokens=budget, temperature=0.2),
            timeout=timeout)
    text = str((res or {}).get("content") if isinstance(res, dict) else res or "").strip()
    # 去掉代码围栏与「好的，以下是…」前言；只保留从第一个标题开始的正文
    text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text).strip()
    m = re.search(r"(?m)^#{1,4}\s+", text)
    if m and m.start() > 0:
        text = text[m.start():]
    return text.strip()


def _chunks(md: str, limit: int = 7000) -> List[str]:
    """按 `##` 小节切块；单块仍超限就按段落硬切。"""
    if len(md) <= limit:
        return [md]
    parts, cur = [], ""
    for seg in re.split(r"(?m)(?=^##\s)", md):
        if cur and len(cur) + len(seg) > limit:
            parts.append(cur)
            cur = seg
        else:
            cur += seg
    if cur:
        parts.append(cur)
    out: List[str] = []
    for p in parts:
        if len(p) <= limit * 1.5:
            out.append(p)
            continue
        for i in range(0, len(p), limit):
            out.append(p[i:i + limit])
    return out


async def polish_chapters(chapters: Dict[Any, Dict[str, Any]], llm=None,
                          *, max_chapters: int = 5, timeout: float = 180.0) -> List[int]:
    """对体检有问题的章做语言润色，返回被润色的章号。"""
    if not chapters:
        return []
    if llm is None:
        from app.services.llm_service import LLMService
        llm = LLMService()
    try:
        from app.services.delivery_audit import audit_chapters
        audit = audit_chapters(chapters)
        flagged = {int(it.get("chapter") or 0) for it in (audit.get("items") or [])}
    except Exception:
        flagged = set()

    targets = [int(k) for k in chapters
               if isinstance(k, (int, str)) and str(k).isdigit()
               and int(k) in flagged and (chapters.get(k) or {}).get("markdown")]
    targets = targets[:max_chapters]
    done: List[int] = []

    system = (
        "你是中文公文校对。只改语病、缺字、词语粘连、断句和重复表达。"
        "禁止改动：数字、金额、面积、日期、单位名称、人名、文号、法规名称、"
        "各级标题、表格内容、图占位。保持原 markdown 结构，输出完整正文，"
        "不要解释、不要加前后缀。"
    )
    for no in targets:
        md = str(chapters[no].get("markdown") or "")
        if len(md) < 80:
            continue
        new_parts: List[str] = []
        ok = True
        for part in _chunks(md):
            try:
                text = await _polish_text(llm, part, system, timeout)
            except Exception as e:
                logger.warning(f"[润色] 第{no}章失败: {e}")
                ok = False
                break
            _why = _check(part, text)
            if _why:
                logger.warning(f"[润色] 第{no}章未采用：{_why}（{len(part)} 字块）")
                ok = False
                break
            new_parts.append(text)
        if not ok:
            continue
        text = "".join(new_parts)
        chapters[no]["markdown"] = text
        done.append(no)
        logger.info(f"[润色] 第{no}章已润色（{len(md)} → {len(text)} 字）")
    return done
