"""ReclamationChapter3Agent — 复垦第 3 章：项目概况。"""

from .reclamation_chapter_base import ReclamationChapterAgentBase


class ReclamationChapter3Agent(ReclamationChapterAgentBase):
    name = "ReclamationChapter3Agent"
    description = "生成复垦第3章：项目概况（项目简介、自然概况、社会经济、土地利用状况）"
    covered_steps = [3]
    chapter_number = 3
    chapter_title = "项目概况"
    rag_query_extra = "项目简介 地理位置 气候 地貌 土壤 水文 地质 社会经济 土地利用"
    required_data_keys = ["project_name", "location", "land_use"]
    key_tables = []
