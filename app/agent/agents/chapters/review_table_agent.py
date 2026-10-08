"""ReviewTableAgent — 稳评评审报告表（**不编号的前置部分，不是正文章节**）。

依据江苏省统一制式与人工模板《金湖稳评报告（空）.docx》：
评审表是封面后独立的一页表格（22 行主表 + 填表说明），**不写「第一章」**，不占章号。

职责：
1. 收集评审表所需的字段（责任单位负责人/职务/联系人/电话、决策概况 7 条、
   各方意见、主要风险因素、降低风险措施、结论）——缺失项交由工作流向用户补问；
2. 渲染江苏省统一制式表（`review_table_service.build_review_table_docx`）。
"""

from typing import Any, Dict, List

from .chapter_base import ChapterAgentBase


class ReviewTableAgent(ChapterAgentBase):
    """评审表 agent（独立于 11 个正文章节 agent）。"""

    name = "ReviewTableAgent"
    description = "生成「社会稳定风险评估评审表」（不编号前置部分）"
    covered_steps = [0]
    # 不占正文号：用 0 表示「前置部分」
    chapter_number = 0
    chapter_title = "社会稳定风险评估评审表"
    rag_query_extra = "稳评评审表 江苏省统一制式 决策事项名称 稳评责任单位 稳评实施单位 评估结论"
    required_data_keys = ["project_name", "org_name"]
    key_tables = ["社会稳定风险评估评审表"]

    # 评审表需要用户补齐的字段（负责单位侧）
    REQUIRED_FIELDS: List[tuple] = [
        ("org_leader", "责任单位负责人", "text", None, 20, "张三"),
        ("org_leader_title", "负责人职务", "text", None, 20, "主任"),
        ("org_contact", "责任单位联系人", "text", None, 20, "李四"),
        ("org_phone", "责任单位联系电话", "phone", r'^1[3-9]\d{9}$', 11, "13800000000"),
    ]

    def render_into(self, doc, filled_data: dict, chapters: dict = None, extracted: dict = None):
        """把评审表渲染到给定 docx 文档上（供组装器直接调用）。"""
        from app.services.review_table_service import build_review_table_docx
        build_review_table_docx(doc, filled_data or {}, chapters or {}, extracted)

    def build_markdown(self, filled_data: dict, chapters: dict = None, extracted: dict = None) -> str:
        """评审表的 markdown 概览（供预览/审核用）。"""
        from app.services.review_table_service import _build_review_data
        d = _build_review_data(filled_data or {}, chapters or {}, extracted)
        return "\n".join([
            f"决策事项名称：{d['name']}",
            f"稳评责任单位：{d['org']}",
            f"稳评实施单位：{d['impl_unit']}",
            *d["overview"],
            f"稳评实施单位结论：{d['conclusion']}",
        ])

    def _chapter_extra_context(self, state: dict, rag_context: Dict[str, Any],
                               user_data: Dict[str, Any]) -> str:
        return ""
