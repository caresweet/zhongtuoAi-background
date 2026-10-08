"""ReclamationChapter2Agent — 复垦第 2 章：编制总则。"""

from .reclamation_chapter_base import ReclamationChapterAgentBase


class ReclamationChapter2Agent(ReclamationChapterAgentBase):
    name = "ReclamationChapter2Agent"
    description = "生成复垦第2章：编制总则（目的、原则、依据）"
    covered_steps = [2]
    chapter_number = 2
    chapter_title = "编制总则"
    rag_query_extra = "编制目的 编制原则 编制依据 法律法规 政策文件 标准规范"
    required_data_keys = ["project_name"]
    key_tables = []
