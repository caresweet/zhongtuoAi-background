# -*- coding: utf-8 -*-
"""整章语句润色：只改语言，数字/标题/表格不一致必须丢弃。"""

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.prose_polish import polish_chapters, _passes  # noqa: E402


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    async def chat(self, **kwargs):
        return {"content": self.text}


def _chapter(text):
    return {1: {"markdown": text}}


def test_polish_keeps_facts_and_splits_sentences():
    orig = ("# 编制说明\n\n## 1.1 背景\n\n"
            "本项目切实保障了群众利益，同时充分体现了科学决策和民主决策的"
            "要求，并且进一步提升了政府公信力和群众满意度，形成了良好局面。")
    polished = ("# 编制说明\n\n## 1.1 背景\n\n"
                "本项目保障了群众利益。决策过程体现了科学与民主。"
                "政府公信力和群众满意度得到提升，形成了良好局面。")
    assert _passes(orig, polished)
    chapters = _chapter(orig)
    done = asyncio.run(polish_chapters(chapters, llm=_FakeLLM(polished)))
    assert done == [1]
    assert "切实保障" not in chapters[1]["markdown"]


def test_polish_rejects_changed_numbers():
    orig = "# 章\n\n项目面积 2.6800 公顷，投资 57.48 万元。切实保障群众利益。"
    bad = "# 章\n\n项目面积 2.68 公顷，投资 60 万元。保障群众利益。"
    assert not _passes(orig, bad)
    chapters = _chapter(orig)
    done = asyncio.run(polish_chapters(chapters, llm=_FakeLLM(bad)))
    assert done == []
    assert chapters[1]["markdown"] == orig
