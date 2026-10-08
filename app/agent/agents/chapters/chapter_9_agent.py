"""Chapter9Agent — 稳评工作报告（确定风险等级）。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter9Agent(ChapterAgentBase):
    name = "Chapter9Agent"
    description = "生成第9章：稳评工作报告（确定风险等级）"
    covered_steps = [9]
    chapter_number = 9
    chapter_title = "稳评工作报告（确定风险等级）"
    rag_query_extra = "稳评工作报告 确定风险等级 结论 建议 签字"
    required_data_keys = ["project_name", "risk_level"]
    key_tables = ["表9-1 决策可能出现的不稳定风险一览表"]


    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """注入前序章节摘要，用于第9章汇总（四性/风险/结论）。"""
        gen = state.get("generated_sections", {}) or {}
        parts = []
        for n, label in ((3, "决策基本情况"), (6, "四性分析"), (7, "风险源"), (8, "防范化解措施")):
            md = (gen.get(f"chapter_{n}") or {}).get("markdown", "")
            if md:
                parts.append(f"## ⬆️ 第{n}章（{label}）摘要——本章须与其结论一致，不得矛盾")
                parts.append(md[:1500])
        try:
            from app.services.scoring_service import scoring_service
            rep = scoring_service.build_scoring_report(state.get("filled_data", {}) or {}, {})
            txt = scoring_service.format_for_llm(rep)
            if txt:
                parts.append("## 🧮 评分参考（（四）结论中的总分与等级须与此一致）")
                parts.append(txt)
        except Exception:
            pass
        return "\n".join(parts)
