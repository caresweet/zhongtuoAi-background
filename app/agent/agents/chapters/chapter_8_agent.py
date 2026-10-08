"""Chapter8Agent — 提出防范和化解风险的工作建议。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter8Agent(ChapterAgentBase):
    name = "Chapter8Agent"
    description = "生成第8章：提出防范和化解风险的工作建议"
    covered_steps = [8]
    chapter_number = 8
    chapter_title = "提出防范和化解风险的工作建议"
    rag_query_extra = "防范化解措施 工作建议 落实措施后风险等级"
    required_data_keys = ["project_name"]
    key_tables = ["表8-1 稳评风险等级确定量化指标评价体系（措施后）"]


    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """注入措施后评分参考 + 第7章已识别风险。"""
        parts = []
        try:
            from app.services.scoring_service import scoring_service
            filled = state.get("filled_data", {}) or {}
            rep = scoring_service.build_scoring_report(filled, {})
            txt = scoring_service.format_for_llm(rep)
            if txt:
                parts.append("## 🧮 系统评分参考（措施后，分值应低于措施前）")
                parts.append(txt)
        except Exception:
            pass
        prev = (state.get("generated_sections", {}) or {}).get("chapter_7", {}).get("markdown", "")
        if prev:
            parts.append("## ⬆️ 第7章已识别的风险因素（（一）节须逐项对应提出措施，不得遗漏）")
            parts.append(prev[:2000])
        return "\n".join(parts)
