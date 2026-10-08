"""ChapterWriterAgent —— 章节编写适配器（template_fill 管线）。

替代旧的 `chapters/chapter_*.py`（LLM 自由写）。本适配器只做一件事：
把「模板填空 + 缺口补写 + 规则校验」包成既有的 `run(state, queue)` 契约，
使两条编排路径（ChapterOrchestrator / LangGraph report_workflow）无需改动即可切换。

内部一律委托 `ChapterComposer`（无副作用、幂等、不依赖章序）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ChapterWriterAgent(BaseAgent):
    """按模板填空生成一章。"""

    name = "ChapterWriterAgent"
    description = "按人工模板填空生成章节（模板填空为主，AI 只补缺口）"

    def __init__(self, chapter_number: int, llm_service=None):
        super().__init__(llm_service=llm_service)
        self.chapter_number = int(chapter_number)
        self.name = f"ChapterWriterAgent(ch{self.chapter_number})"
        self.covered_steps = [self.chapter_number]
        # 章标题从单一事实源取，避免各处写死
        try:
            from app.agent.chapter_definitions import get_chapter_title
            self.chapter_title = get_chapter_title(self.chapter_number)
        except Exception:
            self.chapter_title = f"第{self.chapter_number}章"

    # ── think ────────────────────────────────────────────────
    async def think(self, state: dict) -> Dict[str, Any]:
        from app.services.chapter_composer import ChapterComposer
        filled = (state or {}).get("filled_data") or {}
        block = ChapterComposer._template_block(self.chapter_number)
        steps = [
            f"📐 取第{self.chapter_number}章模板底稿（{len(block)} 字）",
            "🔍 按标注表匹配槽位",
            "✍️ 填事实卡数据（填不上进缺口清单）",
            "✅ 跑规则校验（阈值 / 异常值 / 内容）",
        ]
        return {
            "summary": f"第{self.chapter_number}章「{self.chapter_title}」模板填空",
            "steps": steps,
            "actions": [{"type": "template_fill", "chapter": self.chapter_number}],
            "has_template": bool(block),
            "filled_keys": len([k for k in filled if not k.startswith("_")]),
        }

    # ── act ──────────────────────────────────────────────────
    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.chapter_composer import ChapterComposer
        composer = ChapterComposer(llm_service=self._llm)
        result = composer.compose_chapter(self.chapter_number, state)
        return {
            "chapter": self.chapter_number,
            "title": self.chapter_title,
            "markdown": result.markdown,
            "filled_count": result.filled_count,
            "gaps": result.gaps,
        }

    # ── validate ─────────────────────────────────────────────
    async def validate(self, result: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        try:
            from app.validation.content_rule_checker import check_text
            md = result.get("markdown") or ""
            for it in check_text(md):
                issues.append(f"{it.get('metric')}: {it.get('message')}")
        except Exception as e:
            logger.warning(f"内容规则校验失败 ch{self.chapter_number}: {e}")

        for g in (result.get("gaps") or []):
            if g.get("severity") == "error":
                issues.append(f"缺口[{g.get('reason')}] {g.get('detail','')}")
        if not result.get("markdown", "").strip():
            issues.append("本章内容为空（模板缺失且无兜底骨架）")
        return issues

    # ── update_state ─────────────────────────────────────────
    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        ch = self.chapter_number
        chapters = state.setdefault("chapters", {})
        chapters[ch] = {
            "markdown": result.get("markdown", ""),
            "title": result.get("title") or self.chapter_title,
            "status": "template_filled",
        }
        state["chapters"] = chapters

        gen = state.setdefault("generated_sections", {})
        gen[f"chapter_{ch}"] = {"markdown": result.get("markdown", ""),
                                "title": result.get("title") or self.chapter_title}
        state["generated_sections"] = gen

        # 缺口清单（供前端弹窗 / Word 批注）
        if result.get("gaps"):
            gaps = state.setdefault("_chapter_gaps", {})
            gaps[str(ch)] = result["gaps"]
            state["_chapter_gaps"] = gaps
        return state
