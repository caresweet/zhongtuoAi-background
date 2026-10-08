"""ReclamationChapter1Agent — 复垦第 1 章：前言。"""

from .reclamation_chapter_base import ReclamationChapterAgentBase


class ReclamationChapter1Agent(ReclamationChapterAgentBase):
    name = "ReclamationChapter1Agent"
    description = "生成复垦第1章：前言（编制背景及过程、复垦方案摘要）"
    covered_steps = [1]
    chapter_number = 1
    chapter_title = "前言"
    rag_query_extra = "编制背景 复垦方案摘要 服务年限 各类土地面积 土地损毁情况 复垦目标 投资情况"
    required_data_keys = ["project_name", "build_unit", "location", "service_years"]
    key_tables = []
