"""Chapter7Agent — 分析预测风险源。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter7Agent(ChapterAgentBase):
    name = "Chapter7Agent"
    description = "生成第7章：分析预测风险源"
    covered_steps = [7]
    chapter_number = 7
    chapter_title = "分析预测风险源"
    rag_query_extra = "分析预测风险源 风险因素 量化指标评价体系 初始风险等级表"
    required_data_keys = ["project_name"]
    key_tables = ["表7-4 风险因素初始风险等级表"]


    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """第7章「稳评工作方案」的写作要点。

        新结构下本章为 7.1 指导思想 / 7.2 评估方法 / 7.3 评估内容 /
        7.4 评估程序及步骤 / 7.5 评估时限（过程） / 7.6 工作计划。
        （评分与风险清单分别属第12章、第11章，不在这里注入。）
        """
        filled = state.get("filled_data", {}) or {}
        parts = ["## 📋 本章写作要点（7.1～7.6 逐节写实）"]
        parts.append("- 7.2 评估方法：对照表法 / 实地考察法 / 问卷调查法 / 综合研判法等，逐项说明本项目怎么做。")
        parts.append("- 7.3 评估内容：围绕合法性、合理性、可行性、可控性四方面各自评估什么。")
        parts.append("- 7.4 评估程序及步骤：九个步骤依次写清（明确目的→成立评估组制定方案→收集审阅资料→"
                     "听取各方意见→全面评估论证→确定风险等级→编制报告→专家评审修改→正式提交）。")
        parts.append("- 7.6 工作计划：稳评小组分工 + 调研方案 + 风险预测 + 报告编制审核 + 评审报送。")
        parts.append("")
        # 🔴 时间只能取事实卡真实值；缺则用中性表述，禁止编造日期或工作日天数
        _dates = {k: filled.get(k) for k in
                  ("commission_date", "notice_date", "notice_end_date", "survey_date",
                   "symposium_date", "report_draft_date", "review_date") if filled.get(k)}
        if _dates:
            parts.append("## ⏱ 事实卡中的真实时间（只能用这些，不得另行编造）")
            for k, v in _dates.items():
                parts.append(f"- {k}: {v}")
        else:
            parts.append("🔴 事实卡未提供具体日期 → **不要写任何具体年月或「X 个工作日」**，"
                         "用「按委托约定时间组织实施」这类中性表述。")
        return "\n".join(parts)
