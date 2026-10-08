"""Chapter6Agent — 决策事项合法性、合理性、可行性、可控性分析。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter6Agent(ChapterAgentBase):
    name = "Chapter6Agent"
    description = "生成第6章：决策事项合法性、合理性、可行性、可控性分析"
    covered_steps = [6]
    chapter_number = 6
    chapter_title = "决策事项合法性、合理性、可行性、可控性分析"
    rag_query_extra = "合法性 合理性 可行性 可控性 分析"
    required_data_keys = ["project_name", "doc_reference"]
    key_tables = []

