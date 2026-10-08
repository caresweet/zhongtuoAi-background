"""Chapter10Agent — 突发性事件或不稳定因素处置应对工作预案。"""

from typing import Any, Dict

from .chapter_base import ChapterAgentBase


class Chapter10Agent(ChapterAgentBase):
    name = "Chapter10Agent"
    description = "生成第10章：突发性事件或不稳定因素处置应对工作预案"
    covered_steps = [10]
    chapter_number = 10
    chapter_title = "突发性事件或不稳定因素处置应对工作预案"
    rag_query_extra = "突发性事件 不稳定因素 处置应对工作预案 组织领导 预警预防 现场处置 舆情处置"
    required_data_keys = ["project_name"]
    key_tables = []

