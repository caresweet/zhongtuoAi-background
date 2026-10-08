"""Chapter1Agent — 编制说明。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter1Agent(ChapterAgentBase):
    name = "Chapter1Agent"
    description = "生成第1章：编制说明"
    covered_steps = [1]
    chapter_number = 1
    chapter_title = "编制说明"
    rag_query_extra = "编制说明 稳评理由 稳评目的 稳评原则"
    required_data_keys = ["project_name"]
    key_tables = []

