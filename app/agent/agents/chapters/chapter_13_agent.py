"""Chapter13Agent — 防范和化解风险的工作建议。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter13Agent(ChapterAgentBase):
    name = "Chapter13Agent"
    description = "生成第13章：防范和化解风险的工作建议"
    covered_steps = [13]
    chapter_number = 13
    chapter_title = "防范和化解风险的工作建议"      # 实际以 chapter_definitions 为准（__init__ 会覆盖）
    rag_query_extra = ""
    required_data_keys = ["project_name"]
    key_tables = []
