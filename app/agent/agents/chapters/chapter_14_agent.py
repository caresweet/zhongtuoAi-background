"""Chapter14Agent — 附件。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter14Agent(ChapterAgentBase):
    name = "Chapter14Agent"
    description = "生成第14章：附件"
    covered_steps = [14]
    chapter_number = 14
    chapter_title = "附件"      # 实际以 chapter_definitions 为准（__init__ 会覆盖）
    rag_query_extra = ""
    required_data_keys = ["project_name"]
    key_tables = []
