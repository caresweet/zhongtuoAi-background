"""第1章「编制说明」装配 agent —— 结构照搬 + agent 扩写。

设计取舍见 `app/services/chapter1_composer.py`：结构（（一）（二）（三）+ 条目名）
由语料库锁定，agent 只补写每条的展开段，避免模型改写或省略条目名称行。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..base_agent import BaseAgent

logger = logging.getLogger(__name__)

STATUS = "composed_with_reference"


class Chapter1ComposerAgent(BaseAgent):
    """第1章：语料库骨架 + agent 扩写。"""

    name = "Chapter1ComposerAgent"
    description = "编制说明：结构照搬知识库案卷，正文由 agent 扩写"

    def __init__(self, chapter_number: int = 1, llm_service=None):
        super().__init__(llm_service=llm_service)
        self.chapter_number = 1
        self.covered_steps = [1]
        try:
            from app.agent.chapter_definitions import get_chapter_title
            self.chapter_title = get_chapter_title(1)
        except Exception:
            self.chapter_title = "编制说明"

    async def think(self, state: dict) -> Dict[str, Any]:
        from app.services import chapter1_composer as C1
        preamble, blocks = C1.build_blocks((state or {}).get("filled_data") or {})
        n_items = sum(len(it) for _, it in blocks)
        return {
            "summary": f"第1章「{self.chapter_title}」：三块照搬 + 前言补写（{n_items} 条原文）",
            "steps": ["📖 取知识库案卷编制说明骨架（（一）（二）（三）体例）",
                      f"📄 前言 + 三块全部照搬原文（零 LLM）",
                      "🔗 确定性拼接（结构不被改写）"],
            "actions": [{"type": "compose_chapter1"}],
            "blocks": blocks, "preamble": preamble,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """🔴 只扩写**前言**（引中办发〔2021〕11号那块）；三块（一）（二）（三）原文照搬。"""
        from app.services import chapter1_composer as C1
        filled = (state or {}).get("filled_data") or {}
        preamble, blocks = C1.build_blocks(filled)
        if not blocks:
            return {"chapter": 1, "title": self.chapter_title, "markdown": "", "gaps": []}
        extra = await C1.enrich_preamble(preamble, filled, self._llm)
        md = C1.compose(preamble, blocks, extra)
        return {
            "chapter": 1, "title": self.chapter_title, "markdown": md,
            "enriched": len(extra), "gaps": [],
        }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        issues = []
        md = result.get("markdown") or ""
        if not md.strip():
            issues.append("第1章内容为空")
        for kw in ("（一）稳评理由", "（二）稳评目的", "（三）稳评原则"):
            if kw not in md:
                issues.append(f"缺少结构块「{kw}」")
        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        chapters = state.setdefault("chapters", {})
        chapters[1] = {"markdown": result.get("markdown", ""),
                       "title": result.get("title") or self.chapter_title,
                       "status": STATUS}
        state["chapters"] = chapters
        gen = state.setdefault("generated_sections", {})
        gen["chapter_1"] = {"markdown": result.get("markdown", ""),
                            "title": result.get("title") or self.chapter_title}
        return state
