"""复垦章节 agent 基类。

与稳评基类 `ChapterAgentBase` 的唯一差别：标题与 RAG 检索词取自复垦的章节目录。

⚠️ 必须在此**重新覆盖两个属性**，不是只覆盖标题：基类的 `__init__`
（chapter_base.py:114-118）会从**稳评的** `chapter_definitions` 同时写入
`chapter_title` 与 `rag_query_extra` ——

    self.chapter_title   = get_chapter_title(n)        # 稳评标题
    self.rag_query_extra = get_chapter_rag_query(n)    # 稳评的 RAG 检索词

`get_chapter_rag_query` 的兜底是 `f"社会稳定风险评估报告 第{n}章"`。
**只补标题、不补检索词**，复垦第 4 章的检索词就会变成「社会稳定风险评估报告 第4章」，
**把稳评的参考素材拉进复垦章节** —— 不报错，只出错。
"""

from .chapter_base import ChapterAgentBase


class ReclamationChapterAgentBase(ChapterAgentBase):
    """复垦章节 agent 基类（标题与检索词均以复垦章节目录为准）。"""

    def __init__(self, llm_service=None):
        super().__init__(llm_service=llm_service)
        from app.agent.reclamation_definitions import (
            get_reclamation_chapter_rag_query, get_reclamation_chapter_title)

        if self.chapter_number:
            self.chapter_title = get_reclamation_chapter_title(self.chapter_number)
            self.rag_query_extra = get_reclamation_chapter_rag_query(self.chapter_number)
