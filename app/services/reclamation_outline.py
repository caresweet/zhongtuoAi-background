# -*- coding: utf-8 -*-
"""复垦报告的标准大纲 —— **TD/T 1031.1-2011《土地复垦方案编制规程 第1部分：通则》
第六章「土地复垦方案编制内容」**。

业主口径（2026-11-04）：「识别《土地复垦编制规程2011--1-通则》第六章土地复垦方案
编制内容，就是文档的大纲」；同时明确：「这个只是查漏补缺，因为模板中也有很多不是
参照这个写的，这个只是兜底方案」。

所以本模块的定位是**兜底基准**，不是强制结构：

- 模板/范文自己的章结构优先（模板怎么写就怎么用）；
- 模板没覆盖到、或结构明显残缺时，按本大纲补章/补节（查漏补缺）；
- 上传分析里的「结构差异」只作提示，不判模板不合格。`

⚠️ 规程正文不在仓库里，条目标题按规程第六章与本项目既有章节定义整理；
   如拿到规程原文有出入，以原文为准改 JSON，不改代码。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FILE = Path(__file__).resolve().parents[1] / "content_rules" / "reclamation_outline_td1031_1.json"


@lru_cache(maxsize=1)
def load_outline() -> Dict[str, Any]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[标准大纲] 读取失败 {_FILE}: {e}")
        return {}


def standard_source() -> str:
    d = load_outline()
    return f"{d.get('standard', '')} {d.get('clause', '')}".strip()


def standard_chapters() -> Dict[int, str]:
    """正文 1..10 章 → 章名。"""
    return {int(c["no"]): str(c.get("title") or "")
            for c in (load_outline().get("chapters") or [])}


def post_body_parts() -> Dict[int, str]:
    """正文之后的部分（报告表/请示/图件/附件）。"""
    return {int(c["no"]): str(c.get("title") or "")
            for c in (load_outline().get("post_body") or [])}


def full_structure() -> Dict[int, str]:
    out = standard_chapters()
    out.update(post_body_parts())
    return out


def sections_of(chapter_no: int) -> List[str]:
    for c in (load_outline().get("chapters") or []):
        if int(c.get("no") or 0) == int(chapter_no):
            return [str(s) for s in (c.get("sections") or [])]
    return []


def outline_text(include_post: bool = True) -> str:
    """纯文本大纲（提示词/界面展示用）。"""
    lines = [f"标准大纲：{standard_source()}"]
    for c in (load_outline().get("chapters") or []):
        lines.append(f"{c['no']} {c.get('title') or ''}")
        for s in (c.get("sections") or []):
            lines.append(f"  {s}")
    if include_post:
        for c in (load_outline().get("post_body") or []):
            lines.append(f"{c['no']} {c.get('title') or ''}")
    return "\n".join(lines)


def check_chapters(found: Dict[int, str], include_post: bool = True) -> Dict[str, Any]:
    """模板/范文的章结构 vs 标准大纲（只比章号，标题仅展示）。

    `include_post=False` 只比正文 1..10 章（范文通常没有报告表/附件部分）。
    """
    expected = full_structure() if include_post else standard_chapters()
    if not expected:
        return {}
    missing = sorted(set(expected) - set(found or {}))
    extra = sorted(set(int(k) for k in (found or {})) - set(expected))
    return {
        "standard": standard_source(),
        "role": "fallback",     # 只作查漏补缺，不判模板不合格
        "missing": missing,
        "extra": extra,
        "ok": not missing and not extra,
    }
