# -*- coding: utf-8 -*-
"""模板按「章 → 小节」切片（docx / PDF）与多模板同章参考注入。

业主口径（2026-11-02）：「都要切，每章节每小节。」
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.template_section_library import (      # noqa: E402
    _merge_split_headings, section_excerpts, split_docx_sections,
    supplemental_block,
)


def _make_docx(tmp_path, with_subsections=False):
    from docx import Document
    d = Document()
    d.add_paragraph("1 前言", style="Heading 1")
    d.add_paragraph("1.1 编制背景及过程", style="Heading 2")
    d.add_paragraph("背景正文内容。")
    d.add_paragraph("1.2 复垦方案摘要", style="Heading 2")
    d.add_paragraph("摘要正文内容。")
    d.add_paragraph("4 土地复垦方向可行性分析", style="Heading 1")
    if with_subsections:
        d.add_paragraph("4.1 土地损毁分析与评定", style="Heading 2")
        d.add_paragraph("4.1.1 土地损毁环节与时序", style="Heading 3")
        d.add_paragraph("子节正文：施工准备与主体工程施工。")
        d.add_paragraph("4.1.2 已损毁土地现状分析", style="Heading 3")
        d.add_paragraph("已损毁正文。")
    p = tmp_path / "tpl.docx"
    d.save(str(p))
    return str(p)


def test_split_docx_sections_by_heading(tmp_path):
    ch = split_docx_sections(_make_docx(tmp_path, with_subsections=True))
    assert ch["1"]["title"] == "前言"
    assert {"1.1", "1.2"} <= set(ch["1"]["sections"])
    assert "背景正文内容" in ch["1"]["sections"]["1.1"]["text"]
    assert "4" in ch


def test_section_excerpts_pull_subsections(tmp_path):
    """一级小节只有标题时，正文要从 4.1.1 / 4.1.2 子树拼出来。"""
    ex = section_excerpts(_make_docx(tmp_path, with_subsections=True), 4)
    assert "4.1" in ex
    assert "子节正文" in ex["4.1"]
    assert "已损毁正文" in ex["4.1"]


def test_merge_split_headings_rejoins_fragments():
    """王元佳苑那类 PDF 的标题是「1 / 前 / 言 / 1.1 / 编制背景及过程」分行。"""
    out = _merge_split_headings(["1", "前", "言", "1.1", "编制背景及过程", "正文"])
    assert out[0] == "1 前 言"
    assert out[1] == "1.1 编制背景及过程"


def test_supplemental_block_empty_for_unknown_domain():
    assert supplemental_block("no_such_domain", 1) == ""
