"""缺口补写 —— **只对白名单小节**用 AI 补写，且死死约束在事实卡与法规白名单内。

设计原则（用户要求「模板填空为主，AI 只补缺口」）：
- 模板里已有的固定话术、项目数据 → 一律不归 AI 管
- AI 只写「模板没有、又必须有的分析性内容」，例如四性分析、防范化解措施、应急预案正文
- AI **不得**产出任何数字/日期/文号/单位名/区划名 —— 这些只能来自事实卡
- 写完立刻自检：编造数据 / 编造法规 / AI 套话 → 不过就退回中性兜底并记缺口
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 允许 AI 写的小节（章号 → 小节关键词）──────────────────────
# 其余小节一律不改：模板有什么就是什么。
AI_WRITABLE_SECTIONS: Dict[int, List[str]] = {
    5:  ["总则", "指导思想", "工作原则", "组织领导", "职责任务", "工作流程",
         "预警机制", "应急响应", "信息报送", "后期处置", "保障措施", "奖惩"],  # 第5章 应急预案
    10: ["合法性分析", "合理性分析", "可行性分析", "可控性分析"],           # 第10章 四性分析
    11: ["风险"],                                                        # 第11章 分析预测风险源
    13: ["措施", "建议"],                                                # 第13章 防范和化解风险的工作建议
}

# 🔴 **由 AI 自主创建**的小节：模板里的示例文字整段弃用，完全按本项目事实重写。
#    用户明确要求：第10章「四性分析」整章由 LLM 依事实卡撰写（模板套话不适用具体项目）。
AI_AUTHORED_SECTIONS: Dict[int, List[str]] = {
    10: ["合法性分析", "合理性分析", "可行性分析", "可控性分析"],
}

# 分析类段落字数上限（防止 AI 灌水）
_MAX_CHARS = {5: 400, 10: 500, 11: 500, 13: 500}


@dataclass
class GapContext:
    """补写一段所需的全部约束上下文。"""
    chapter: int
    section_title: str
    section_points: List[str] = field(default_factory=list)
    project_card: str = ""
    regulation_whitelist: str = ""
    filled: Dict[str, Any] = field(default_factory=dict)   # 自检用（find_fabricated_data 需 dict）
    style_excerpt: str = ""          # 模板同节正文（仅作风格参照）


class AIGapWriter:
    """受约束的缺口补写器。"""

    def __init__(self, llm_service=None):
        self._llm = llm_service

    # ── 对外接口 ──────────────────────────────────────────────

    def is_writable(self, chapter_no: int, section_title: str) -> bool:
        """该小节是否属于「允许 AI 补写」范围。"""
        kws = AI_WRITABLE_SECTIONS.get(int(chapter_no))
        if not kws:
            return False
        t = str(section_title or "")
        return any(k in t for k in kws)

    def is_ai_authored(self, chapter_no: int, section_title: str) -> bool:
        """该小节是否**由 AI 自主创建**（模板示例文字整段弃用）。"""
        kws = AI_AUTHORED_SECTIONS.get(int(chapter_no))
        if not kws:
            return False
        t = str(section_title or "")
        return any(k in t for k in kws)

    async def write_section(self, chapter_no: int, section_title: str,
                            state: dict, template_excerpt: str = "") -> Optional[str]:
        """为「AI 自主创建」的小节写整段正文。

        Args:
            chapter_no: 章号。
            section_title: 小节标题（如「（二）决策合理性分析」）。
            state: 工作流状态（取 filled_data）。
            template_excerpt: 模板里该节的示例文字（仅作风格参照）。

        Returns:
            合格正文；None = 未通过自检或 LLM 不可用（调用方保留模板原文）。
        """
        if not self._llm or not self.is_ai_authored(chapter_no, section_title):
            return None
        filled = (state or {}).get("filled_data") or {}

        try:
            from app.services.master_orchestrator import (
                build_project_card, build_regulation_whitelist)
            from app.agent.chapter_definitions import get_chapter_section_specs
        except Exception:
            return None

        points: List[str] = []
        for s in get_chapter_section_specs(chapter_no):
            if str(s.get("title", "")).lstrip("（(一二三四五六七八九十）) ") in section_title or \
                    section_title in str(s.get("title", "")):
                points = s.get("points", []) or []
                break

        ctx = GapContext(
            chapter=chapter_no,
            section_title=section_title,
            section_points=points,
            project_card=build_project_card(filled),
            regulation_whitelist=build_regulation_whitelist(filled),
            style_excerpt=template_excerpt,
            filled=filled,
        )
        text = await self.write_gap(ctx)
        # write_gap 的 is_writable 门槛更严，这里再放行「自主创建」小节
        if text:
            return text
        return await self._write_forced(ctx)

    async def _write_forced(self, ctx: GapContext) -> Optional[str]:
        """跳过 writable 白名单，直接按 ctx 生成（供 AI 自主创建小节用）。"""
        if not self._llm:
            return None
        try:
            resp = await self._llm.chat(
                messages=[{"role": "user", "content": self._build_prompt(ctx)}],
                max_tokens=3000, temperature=0.2,
            )
            text = self._clean(str(resp or ""))
        except Exception as e:
            logger.warning(f"自主创建小节失败 ch{ctx.chapter}「{ctx.section_title}」: {e}")
            return None
        ok, why = self._self_check(text, ctx)
        if not ok:
            logger.warning(f"自主创建未通过自检（{why}）：ch{ctx.chapter}「{ctx.section_title}」")
            return None
        return text

    async def write_gap(self, ctx: GapContext) -> Optional[str]:
        """补写一个小节；失败或不合格返回 None（调用方回落中性兜底）。

        Returns:
            合格正文；None = 未通过自检或 LLM 不可用。
        """
        if not self._llm:
            return None
        if not self.is_writable(ctx.chapter, ctx.section_title):
            return None

        prompt = self._build_prompt(ctx)
        try:
            resp = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000, temperature=0.2,
            )
            text = self._clean(str(resp or ""))
        except Exception as e:
            logger.warning(f"缺口补写失败 ch{ctx.chapter}「{ctx.section_title}」: {e}")
            return None

        ok, why = self._self_check(text, ctx)
        if not ok:
            logger.warning(f"缺口补写未通过自检（{why}），回落中性兜底：ch{ctx.chapter}「{ctx.section_title}」")
            return None
        return text

    # ── 内部 ─────────────────────────────────────────────────

    def _build_prompt(self, ctx: GapContext) -> str:
        points = "\n".join(f"- {p}" for p in (ctx.section_points or [])) or "（无特别要求）"
        style = (ctx.style_excerpt or "").strip()[:800]
        return f"""你是稳评报告编写专家。请补写报告第{ctx.chapter}章的「{ctx.section_title}」小节。

## 🚫 铁律（违反即作废）
1. **不得出现任何具体数字/日期/文号/单位名/区划名**——事实卡里没有的一律不写、不猜、不编。
2. **法规名称与文号只能引用下方白名单**，其余一律不写。
3. 不写空话套话（如「各单位应高度重视」），不写「本章从…」「综上所述」这类说明性废话。
4. 只写本节内容，不要重复其它章节已写过的数据。

## 📋 本项目事实卡（唯一可用事实源）
{ctx.project_card or '（无）'}

{ctx.regulation_whitelist or ''}

## 📐 本节要点
{points}

## ✍️ 行文风格参照（**只学语气与句式，其中数据一律不得照抄**）
{style or '（无参照）'}

请直接输出小节正文，不要标题、不要解释、不要 markdown 代码块。字数不超过 {_MAX_CHARS.get(ctx.chapter, 400)} 字。"""

    @staticmethod
    def _clean(text: str) -> str:
        t = str(text or "").strip()
        t = re.sub(r'^```[a-z]*\n?', '', t)
        t = re.sub(r'\n?```$', '', t)
        t = re.sub(r'^\s*#{1,6}\s*', '', t, flags=re.M)
        return t.strip()

    def _self_check(self, text: str, ctx: GapContext) -> tuple:
        """自检：编造数据 / 编造法规 / AI 套话 / 空。"""
        if not text or len(text) < 30:
            return False, "内容过短"
        try:
            from app.validation.content_guardrails import (
                find_fabricated_data, find_hallucinated_regulations, AI_BUZZWORDS,
            )
            if find_hallucinated_regulations(text):
                return False, "编造法规"
            fab = find_fabricated_data(text, ctx.filled or {}, "")
            if fab:
                return False, "编造数据"
            if any(w in text for w in AI_BUZZWORDS):
                return False, "AI 套话"
            if re.search(r'本章(?:从|将|主要)?[^。]{0,20}不再(?:重复)?罗列', text):
                return False, "无意义说明"
        except Exception as e:
            logger.warning(f"补写自检异常（放行）: {e}")
        return True, "ok"
