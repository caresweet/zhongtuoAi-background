"""ReferenceChapterAgent —— 参照件原文照搬适配器。

业主口径：
    「第 1、2、4、5、7 章沿用参照件原文，只替换地区/项目信息。」

把「取参照件原文 + 字面替换事实卡」包成既有的 `run(state, queue)` 契约，
两条编排路径（ChapterOrchestrator / LangGraph report_workflow）无需改动即可分派到它。

🔴 **零 LLM**：全部是字面替换，不调模型。编排层靠 `status="reference_verbatim"`
识别并跳过质量门/重写（否则「字数不足/表后缺分析」会触发重写，照搬失效）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..base_agent import BaseAgent

logger = logging.getLogger(__name__)

# 编排层用这个状态位判定「本章出自参照件轨道，跳过质量门与重写」
STATUS = "reference_verbatim"        # 逐字照搬（零 LLM）
STATUS_GUIDED = "reference_guided"   # 参照件出骨架，正文由模型重写

_REFERENCE_STATUSES = (STATUS, STATUS_GUIDED)


class ReferenceChapterAgent(BaseAgent):
    """按参照件生成一章。

    默认走「模型改写」：参照件只出骨架（标题 / 插图 / 系统表标记 / 法规白名单），
    正文行文由模型结合 RAG 与事实卡重写（业主口径「除项目事实外全交给模型」）。
    模型不可用 / 超时 / 结构损坏 → 自动回退逐字照搬稿。
    """

    name = "ReferenceChapterAgent"
    description = "沿用人工成品原文，只替换地区/项目信息"

    def __init__(self, chapter_number: int, llm_service=None, ref_id: str = None):
        super().__init__(llm_service=llm_service)
        self.chapter_number = int(chapter_number)
        self._ref_id = ref_id
        self.name = f"ReferenceChapterAgent(ch{self.chapter_number})"
        self.covered_steps = [self.chapter_number]
        try:
            from app.agent.chapter_definitions import get_chapter_title
            self.chapter_title = get_chapter_title(self.chapter_number)
        except Exception:
            self.chapter_title = f"第{self.chapter_number}章"

    # ── think ────────────────────────────────────────────────
    async def think(self, state: dict) -> Dict[str, Any]:
        from app.config import settings
        from app.services.reference_corpus import get_reference_chapter_text
        raw = get_reference_chapter_text(self.chapter_number, self._ref_id)
        guided = bool(getattr(settings, "REFERENCE_LLM_REWRITE", False))
        steps = [
            f"📖 取参照件第{self.chapter_number}章原文（{len(raw)} 字）",
            "🔁 字面替换地区/项目信息",
            "🧹 剔除参照件项目类型专属条目",
        ]
        steps.append("✍️ 模型按骨架 + RAG + 事实卡重写正文" if guided
                     else "（照搬模式：零 LLM）")
        steps.append("🚫 校验编造痕（数字/人名/文号/法规）")
        return {
            "summary": f"第{self.chapter_number}章「{self.chapter_title}」"
                       f"{'参照件骨架 + 模型改写' if guided else '参照件原文照搬'}",
            "steps": steps,
            "actions": [{"type": "reference_guided" if guided else "reference_verbatim",
                         "chapter": self.chapter_number}],
            "has_corpus": bool(raw),
        }

    # ── act ──────────────────────────────────────────────────
    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.reference_corpus import render_reference_markdown
        filled = (state or {}).get("filled_data") or {}
        res = render_reference_markdown(self.chapter_number, filled, self._ref_id)
        out = {
            "chapter": self.chapter_number,
            "title": self.chapter_title,
            "markdown": res.markdown,
            "filled_count": res.filled_count,
            "removed_count": res.removed_count,
            "gaps": res.gaps,
            "leaks": res.leaks,
            "guided": False,
            "issues": [],
            "fallback": "",
        }

        # 🔴 模型改写（业主口径「除项目事实外全交给模型」）：
        #    参照件只保留骨架，正文行文交模型结合 RAG + 事实卡重写。
        from app.config import settings
        if not getattr(settings, "REFERENCE_LLM_REWRITE", False):
            return out

        from app.services.reference_corpus import get_reference_chapter_text
        from app.services.reference_llm import retrieve_rag_text, rewrite_chapter_prose
        rag_text = await retrieve_rag_text(state, self.chapter_number)
        r = await rewrite_chapter_prose(
            self.chapter_number, self.chapter_title, res.markdown, filled, self._llm,
            reference_text=get_reference_chapter_text(self.chapter_number, self._ref_id),
            rag_text=rag_text,
            state=state,
        )
        out["markdown"] = r.get("markdown") or res.markdown
        out["issues"] = r.get("issues") or []
        out["fallback"] = r.get("fallback") or ""
        out["guided"] = not out["fallback"]
        if out["fallback"]:
            logger.warning(f"[参照件] 第{self.chapter_number}章改写未成，"
                           f"回退照搬稿：{out['fallback']}")
        else:
            logger.info(f"[参照件] 第{self.chapter_number}章模型改写完成"
                        f"（{len(out['markdown'])} 字，可疑 {len(out['issues'])} 项）")
        return out

    # ── validate ─────────────────────────────────────────────
    async def validate(self, result: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        md = result.get("markdown") or ""
        if not md.strip():
            issues.append("参照件原文为空（语料未生成？）")
        # 🔴 泄漏 = 参照件的项目专有词没替换干净，属硬错误
        for w in (result.get("leaks") or []):
            issues.append(f"残留参照件专有词「{w}」（替换未做干净）")
        # 未闭合的占位符
        import re as _re
        left = _re.findall(r'\{\{\w+\}\}', md)
        if left:
            issues.append(f"未替换的占位符 {len(left)} 处：{left[:5]}")
        for g in (result.get("gaps") or []):
            if g.get("severity") == "error":
                issues.append(f"缺口[{g.get('reason')}] {g.get('field','')}")
        # 🔴 模型改写后的「编造痕」：硬违规（手机号/非事实卡文号/白名单外法规）必须报
        for it in (result.get("issues") or []):
            if it.get("severity") == "error":
                issues.append(f"改写校验[{it.get('kind')}] {it.get('detail', '')}")
        return issues

    # ── update_state ─────────────────────────────────────────
    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        ch = self.chapter_number
        guided = bool(result.get("guided"))
        chapters = state.setdefault("chapters", {})
        chapters[ch] = {
            "markdown": result.get("markdown", ""),
            "title": result.get("title") or self.chapter_title,
            # 🔴 编排层据此跳过质量门与重写
            "status": STATUS_GUIDED if guided else STATUS,
            "verbatim": not guided,
            "llm_rewritten": guided,
        }
        state["chapters"] = chapters

        gen = state.setdefault("generated_sections", {})
        gen[f"chapter_{ch}"] = {"markdown": result.get("markdown", ""),
                                "title": result.get("title") or self.chapter_title,
                                "verbatim": not guided}
        state["generated_sections"] = gen

        gaps = list(result.get("gaps") or [])
        # 改写校验的警告项也留档，供审核环节查看
        gaps += [{"field": it.get("kind", ""), "reason": "llm_rewrite_" + str(it.get("kind")),
                  "severity": it.get("severity", "warn"), "detail": it.get("detail", "")}
                 for it in (result.get("issues") or [])]
        if gaps:
            acc = state.setdefault("_chapter_gaps", {})
            acc[str(ch)] = gaps
            state["_chapter_gaps"] = acc
        return state


def is_verbatim_status(ch_data) -> bool:
    """编排层判定「本章出自参照件轨道」→ 应跳过质量门与自动重写。

    参照件轨道的字数/表格分布与「LLM 自由写」的预期不同（如第2章只有法规清单、
    第5章 5000 字预案），质量门会把它们判为「字数不足/表后缺分析」并触发重写，
    重写后就脱离了参照件轨道。故编排层必须据此放行。
    """
    return isinstance(ch_data, dict) and ch_data.get("status") in _REFERENCE_STATUSES
