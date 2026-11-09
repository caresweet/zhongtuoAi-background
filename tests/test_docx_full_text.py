# -*- coding: utf-8 -*-
"""docx 解析要带上表格内容（施工报告里的井场面积/地层就在表格里）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document

from app.services.file_service import FileService


def test_docx_full_text_includes_tables():
    d = Document()
    d.add_paragraph("一、项目概况")
    d.add_paragraph("每井组有两个井场。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "井场"
    t.cell(0, 1).text = "面积（公顷）"
    t.cell(1, 0).text = "ZXK11"
    t.cell(1, 1).text = "1.20"
    text = FileService._docx_full_text(d)
    assert "每井组有两个井场" in text
    assert "ZXK11 | 1.20" in text
