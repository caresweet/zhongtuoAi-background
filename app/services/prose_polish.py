# -*- coding: utf-8 -*-
"""整章语句润色（只修语病，不改事实）。

用户反馈（2026-11-16）：「很多语句不通顺」。确定性规则只能断长句、并残句，
缺字/词语粘连/多层从句还得让模型顺一遍。约束：
    · 只做语言润色：数字、名称、文号、标题、表格、图占位一律不许动；
    · 改完做**硬校验**（数字多重集、标题集合、表格行数、长度 0.8～1.2），
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


def _numbers(md: str) -> List[str]:
    return _NUM_RE.findall(md or "")


def _headings(md: str) -> set:
    return {h.strip() for h in _HEAD_RE.findall(md or "")}


def _table_rows(md: str) -> int:
    return sum(1 for line in (md or "").splitlines()
               if line.strip().startswith("|"))


def _passes(orig: str, new: str) -> bool:
    if not new or len(new) < len(orig) * 0.8 or len(new) > len(orig) * 1.2:
        return False
    if sorted(_numbers(orig)) != sorted(_numbers(new)):
        return False
    if _headings(orig) != _headings(new):
        return False
    if _table_rows(orig) != _table_rows(new):
        return False
    return True


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
    return str((res or {}).get("content") if isinstance(res, dict) else res or "").strip()


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
            if not _passes(part, text):
                logger.warning(f"[润色] 第{no}章校验不通过（{len(part)} 字块），保留原文")
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
