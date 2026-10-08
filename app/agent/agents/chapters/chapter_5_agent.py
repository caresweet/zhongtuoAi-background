"""Chapter5Agent — 各利益相关者对决策事项的反映。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter5Agent(ChapterAgentBase):
    name = "Chapter5Agent"
    description = "生成第5章：各利益相关者对决策事项的反映"
    covered_steps = [5]
    chapter_number = 5
    chapter_title = "各利益相关者对决策事项的反映"
    rag_query_extra = "利益相关者 风险调查 问卷统计 座谈会 各方意见 诉求"
    required_data_keys = ["project_name", "total_samples"]
    key_tables = ["表5-1 问卷调查统计汇总表", "表5-2 单位问卷调查统计汇总表"]


    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """注入问卷统计（表5-1/5-2）与座谈信息。"""
        filled = state.get("filled_data", {}) or {}
        parts = ["## 📊 本章事实卡（问卷/座谈）"]
        parts.append("🔴 （六）节的问卷份数、支持率必须与下列数值一致，禁止推算、禁止用「户数」冒充问卷数。")
        for k, label in (("total_samples", "个人问卷发放/有效数"),
                         ("dept_survey_count", "单位问卷发放/有效数"),
                         ("support_count", "支持人数"), ("oppose_count", "反对人数"),
                         ("support_rate", "支持率"), ("oppose_rate", "反对率"),
                         ("awareness_rate", "知晓率"),
                         ("household_count", "涉及户数（仅用于（二）调查对象，不得当作问卷数）"),
                         ("symposium_attendees", "座谈会参会人数"),
                         ("symposium_date", "座谈会时间"),
                         ("symposium_location", "座谈会地点")):
            v = filled.get(k)
            if v:
                parts.append(f"- {label}：{v}")
        return "\n".join(parts)
