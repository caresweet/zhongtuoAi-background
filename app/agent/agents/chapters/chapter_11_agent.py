"""Chapter11Agent — 附件。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter11Agent(ChapterAgentBase):
    name = "Chapter11Agent"
    description = "生成第11章：附件"
    covered_steps = [11]
    chapter_number = 11
    chapter_title = "附件"
    rag_query_extra = "附件 拟征地位置示意图 拟征地公告 勘测定界报告 签到表 问卷 专家评审"
    required_data_keys = ["project_name"]
    key_tables = []

    def _chapter_extra_context(self, state, rag_context, user_data) -> str:
        """🔴 本章**不出表格**（参照件模板第十一章「分析预测风险源」通篇无表）。

        「风险因素初始风险等级表」按模板属**第12章 表12-4**。本章抢着写，
        报告里就会冒出两张同名表（业主：「重名不正确吧」）。
        风险源一律文字逐项叙述。
        """
        return ("## 🔒 本章体例约束（按参照件模板）\n"
                "- **本章不输出任何表格**：风险源逐项用文字叙述（风险描述 → 风险分析 → 风险程度）。\n"
                "- 「风险因素初始风险等级表」属**第12章 表12-4**，本章不得出现该表、"
                "也不得写整块的「风险等级」清单。\n")

