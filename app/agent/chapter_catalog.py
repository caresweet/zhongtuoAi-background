# -*- coding: utf-8 -*-
"""按域取「章目录」—— report_workflow 的确定性大纲需要按域取章集合/章标题/章类型。

稳评（stability）走 `app.agent.chapter_definitions`（14 章②，行为与历史一致）；
复垦（reclamation）走 `app.agent.reclamation_definitions`（10 章）。
两套定义同构，差异只在这里收口，避免 `report_workflow` 里长出一串
`if domain == ...` 分支。

🔴 默认域（stability）的取值逐字等于改动前的实现 —— 这是「稳评行为不变」的依据。
"""

from typing import Any, Dict, List

_RECLAMATION = "reclamation"


def all_chapters(domain: str | None) -> List[int]:
    """该域的章号列表（按顺序）。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_ALL_CHAPTERS
        return list(RECLAMATION_ALL_CHAPTERS)
    from app.agent.chapter_definitions import ALL_CHAPTERS
    return list(ALL_CHAPTERS)


def total_chapters(domain: str | None) -> int:
    """该域的总章数。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_TOTAL_CHAPTERS
        return RECLAMATION_TOTAL_CHAPTERS
    from app.agent.chapter_definitions import TOTAL_CHAPTERS
    return TOTAL_CHAPTERS


def chapter_title(domain: str | None, chapter_number: int) -> str:
    """章标题。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import get_reclamation_chapter_title
        return get_reclamation_chapter_title(chapter_number)
    from app.agent.chapter_definitions import get_chapter_title
    return get_chapter_title(chapter_number)


def chapter_kind(domain: str | None, chapter_number: int) -> str:
    """章类型（fact/table/template/attachment…）。"""
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import get_reclamation_chapter_kind
        return get_reclamation_chapter_kind(chapter_number)
    from app.agent.chapter_definitions import get_chapter_kind
    return get_chapter_kind(chapter_number)


def standard_outline(domain: str | None) -> List[Dict[str, Any]]:
    """按域生成确定性大纲。

    稳评：逐字段等于改动前 `report_workflow._standard_outline` 的实现
    （含 `get_chapter_data_requirements` 推出的依赖数据与依赖章）。
    复垦：复垦章节目录没有「依赖数据/依赖章」概念，故这两项留空。
    """
    if domain == _RECLAMATION:
        from app.agent.reclamation_definitions import RECLAMATION_CHAPTER_DEFINITIONS
        definitions = RECLAMATION_CHAPTER_DEFINITIONS
        outline: List[Dict[str, Any]] = []
        for num in all_chapters(domain):
            d = definitions.get(num, {})
            outline.append({
                "chapter_no": str(num),
                "title": d.get("title", f"第{num}章"),
                "depend_on_data": [],
                "need_spec_tags": [s.split(" ", 1)[-1] for s in d.get("sections", [])],
                "depends_on": [],
                "argument_note": "",
                "kind": chapter_kind(domain, num),
                "raw_content": None, "review_score": None, "review_msg": None,
                "retry_count": 0, "status": "pending",
            })
        return outline

    from app.agent.chapter_definitions import (
        CHAPTER_DEFINITIONS, get_chapter_data_requirements,
    )
    outline = []
    for num in all_chapters(domain):
        d = CHAPTER_DEFINITIONS.get(num, {})
        req = get_chapter_data_requirements(num)
        deps = [k for k, _n, *_r in req.get("required", [])] + \
               [k for k, _n in req.get("recommended", [])]
        outline.append({
            "chapter_no": str(num),
            "title": d.get("title", f"第{num}章"),
            "depend_on_data": deps,
            "need_spec_tags": [s.split(" ", 1)[-1] for s in d.get("sections", [])],
            "depends_on": req.get("depends_on_chapters", []),
            "argument_note": "",
            "kind": chapter_kind(domain, num),
            "raw_content": None, "review_score": None, "review_msg": None,
            "retry_count": 0, "status": "pending",
        })
    return outline
