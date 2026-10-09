"""ReclamationGenericChapterAgent —— 无专属 agent 的复垦章的通用实现。

第 4/6/8/9 章暂无专属 agent（一期只做到前 3 章），但编排层拿不到 agent 会**跳过该章**，
成稿就缺章。标题、RAG 检索词、表格清单、字数区间**全部从复垦章节目录取**
（`reclamation_definitions`），因此不必为每章各写一个类。

与专属章（第 1/2/3 章）相比，通用章只是没有各章定制的提示词/取数逻辑；
生成契约、RAG 检索、质量门完全一致。
"""

from __future__ import annotations

from .reclamation_chapter_base import ReclamationChapterAgentBase


class ReclamationGenericChapterAgent(ReclamationChapterAgentBase):
    name = "ReclamationGenericChapterAgent"
    description = "生成复垦章（通用：标题/检索词/表清单取自复垦章节目录）"

    def __init__(self, chapter_number: int, llm_service=None):
        # 🔴 必须在 super().__init__ 之前设好 —— 基类 __init__ 会用它去查
        #    标题与检索词（查的是复垦目录）。
        self.chapter_number = int(chapter_number)
        super().__init__(llm_service=llm_service)
        self.name = f"ReclamationGenericChapterAgent(ch{self.chapter_number})"
        self.covered_steps = [self.chapter_number]
        try:
            from app.agent.reclamation_definitions import (
                RECLAMATION_CHAPTER_DEFINITIONS)
            d = RECLAMATION_CHAPTER_DEFINITIONS.get(self.chapter_number) or {}
            self.key_tables = list(d.get("key_tables") or [])
        except Exception:
            pass
