"""Chapter12Agent — 确定风险等级。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter12Agent(ChapterAgentBase):
    name = "Chapter12Agent"
    description = "生成第12章：确定风险等级"
    covered_steps = [12]
    chapter_number = 12
    chapter_title = "确定风险等级"      # 实际以 chapter_definitions 为准（__init__ 会覆盖）
    rag_query_extra = ""
    required_data_keys = ["project_name"]
    key_tables = []
