"""Chapter2Agent — 编制依据。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter2Agent(ChapterAgentBase):
    name = "Chapter2Agent"
    description = "生成第2章：编制依据"
    covered_steps = [2]
    chapter_number = 2
    chapter_title = "编制依据"
    rag_query_extra = "编制依据 法律法规 评估依据 决策相关文件"
    required_data_keys = ["project_name"]
    key_tables = []


    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """注入「可引用法规清单」——按项目所在地区从配置选取，绝不允许编造法规。"""
        parts = []
        try:
            from app.services.master_orchestrator import build_regulation_whitelist
            wl = build_regulation_whitelist(state.get("filled_data", {}) or {})
            if wl:
                parts.append(wl)
                parts.append(
                    "🔴 本章法规条目**只能从上面清单中选取**，按「国家法律 → 行政法规 → 省级法规 → "
                    "省级政策 → 市级文件」的顺序排列，每条写全称并加书名号，有文号的补文号。"
                    "清单之外的一律不写，绝不编造文号。"
                )
        except Exception:
            pass
        return "\n".join(parts)
