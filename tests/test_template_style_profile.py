# -*- coding: utf-8 -*-
"""模板版式画像：标题/正文的字体字号之外，对齐、首行缩进、行距也要量出来。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm

from app.services.template_style_profile import load_style_profile


def test_profile_measures_heading_and_body_paragraph_formats(tmp_path):
    d = Document()
    h = d.add_heading("第一章 编制说明", 1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h.paragraph_format.line_spacing = 1.5
    for _ in range(3):
        p = d.add_paragraph("这是一段用于测量正文版式的长句子，应该会被统计到正文样本里。")
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.first_line_indent = Cm(0.74)
        p.paragraph_format.line_spacing = 1.5
    path = tmp_path / "style.docx"
    d.save(str(path))

    prof = load_style_profile(path)
    assert prof["h1"]["align"] == "center"
    assert abs(prof["h1"]["line_spacing"] - 1.5) < 0.01
    assert prof["body"]["align"] == "justify"
    assert abs(prof["body"]["first_indent_pt"] - 21.0) < 1.0   # 0.74cm ≈ 21pt
    assert abs(prof["body"]["line_spacing"] - 1.5) < 0.01
    assert prof["body"]["line_rule"] == "multiple"
