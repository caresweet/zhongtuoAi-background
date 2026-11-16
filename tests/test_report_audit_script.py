# -*- coding: utf-8 -*-
"""审核脚本（scripts/report_audit.py）的分章与产物冒烟测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document

from scripts.report_audit import _split_chapters, main


def test_split_stability_chapters():
    blocks = [
        ("p", "封面"),
        ("p", "第一章 编制说明"),
        ("p", "正文一"),
        ("tbl", "| 表头 | 值 |"),
        ("p", "第二章 编制依据"),
        ("p", "正文二"),
    ]
    ch = _split_chapters(blocks, "stability")
    assert set(ch) == {0, 1, 2}
    assert "封面" in ch[0]["markdown"]
    assert "正文一" in ch[1]["markdown"]
    assert "| 表头 | 值 |" in ch[1]["markdown"]      # 表格跟着所在章
    assert "正文二" in ch[2]["markdown"]


def test_split_reclamation_chapters_uses_number_heading():
    blocks = [
        ("p", "临时用地土地复垦方案"),
        ("p", "1 前言"),
        ("p", "正文一"),
        ("p", "2 编制总则"),
        ("p", "2.1 编制目的"),
    ]
    ch = _split_chapters(blocks, "reclamation")
    assert 1 in ch and 2 in ch
    assert "2.1 编制目的" in ch[2]["markdown"]
    # 不是章标题的「2.1」不能另起一章
    assert len([k for k in ch if k > 0]) == 2


def test_main_writes_markdown_report(tmp_path, capsys):
    doc = Document()
    doc.add_paragraph("封面")
    for cn, title in (("一", "编制说明"), ("二", "编制依据")):
        doc.add_paragraph(f"第{cn}章 {title}")
        doc.add_paragraph("本项目为测试报告。" * 40)
    report = tmp_path / "测试报告.docx"
    doc.save(str(report))

    out = tmp_path / "审核报告.md"
    rc = main([str(report), "--out", str(out)])
    capsys.readouterr()          # main 会打印整份报告

    text = out.read_text(encoding="utf-8")
    assert "综合评分" in text
    assert "第 1 层：结构完整性" in text
    assert "第 6 层：交付体检" in text
    assert rc in (0, 1)          # 合成报告分数不定，但不崩溃
