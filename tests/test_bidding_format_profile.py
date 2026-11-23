# -*- coding: utf-8 -*-
"""格式章版式量测：字号/缩进/行距必须从招标文件本身读出来，不能写死。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from app.services.bidding_format_profile import (merged, profile_from_docx,
                                                 role_fmt)


def _para(doc, text, *, size, bold=False, align=None, first_chars=None,
          exact=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    if first_chars:
        p.paragraph_format.first_line_indent = Pt(first_chars * size)
    if exact:
        p.paragraph_format.line_spacing = Pt(exact)
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold
    r.font.name = "宋体"
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    return p


def _make_tender(path):
    d = Document()
    _para(d, "第二章   响应文件格式", size=22, bold=True,
          align=WD_ALIGN_PARAGRAPH.CENTER)
    # 封面（量「正本」右对齐、项目名称 5 字缩进）
    _para(d, "【正/副本】", size=22, align=WD_ALIGN_PARAGRAPH.RIGHT)
    _para(d, "项目名称:", size=18, bold=True, first_chars=5, exact=20)
    _para(d, "项目编号:", size=18, bold=True, first_chars=4.95, exact=20)
    _para(d, "供应商：", size=18, bold=True, first_chars=4.95, exact=20)
    _para(d, "二〇二六年 月 日", size=20, bold=True, first_chars=6.82)
    _para(d, "一、磋商函", size=14, bold=True,
          align=WD_ALIGN_PARAGRAPH.CENTER, exact=26)
    _para(d, "致:（采购人）", size=12, align=WD_ALIGN_PARAGRAPH.JUSTIFY,
          first_chars=2.5, exact=24)
    _para(d, "关于本项目的磋商文件收悉，我们经详细审阅和研究，决定参加磋商。",
          size=12, align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_chars=2.5, exact=24)
    _para(d, "1、按照磋商文件中规定各项要求，我方愿以……提供所需的服务。",
          size=12, first_chars=1, exact=26)
    _para(d, "二、资质审查时供应商必须提交的资格证明文件", size=14, bold=True,
          align=WD_ALIGN_PARAGRAPH.CENTER, exact=22)
    _para(d, "1、供应商提供下列材料之一（加盖供应商公章装订在标书中）:",
          size=12, first_chars=1, exact=26)
    _para(d, "三、人员配备表", size=14, bold=True,
          align=WD_ALIGN_PARAGRAPH.CENTER)
    _para(d, "五、示范格式", size=14, bold=True, first_chars=11.46, exact=22)
    _para(d, "示范格式一", size=14, bold=True, exact=22)
    _para(d, "法定代表人资格证明", size=16, bold=True, first_chars=10,
          exact=32)
    _para(d, "单位名称：", size=12, first_chars=1, exact=36)
    _para(d, "示范格式二", size=14, bold=True, exact=22)
    _para(d, "授权委托书", size=16, bold=True, first_chars=10.45, exact=26)
    _para(d, "致：采购人", size=12, exact=26)
    d.save(str(path))


def _indent_chars(fmt):
    """Word 的 firstLine 是磅、firstLineChars 是字符；统一折算成字符核对。"""
    if fmt.get("first_line_chars"):
        return round(fmt["first_line_chars"], 2)
    if fmt.get("first_line_pt") and fmt.get("size"):
        return round(fmt["first_line_pt"] / fmt["size"], 2)
    return 0


def test_profile_measures_fonts_indents_and_spacing(tmp_path):
    f = tmp_path / "tender.docx"
    _make_tender(f)
    prof = profile_from_docx(f)

    assert prof["chapter_title"]["size"] == 22
    assert prof["chapter_title"]["bold"] is True

    # 章节标题按序号分开取：一、居中 26 磅；五、缩进 11.46 字
    assert prof["h1_by_no"]["一"]["align"] == "center"
    assert prof["h1_by_no"]["一"]["line_exact_pt"] == 26
    assert abs(_indent_chars({**prof["h1_by_no"]["五"], "size": 14}) - 11.46) < 0.01

    # 正文与条目分开：条目 1 字缩进，正文 2.5 字
    assert _indent_chars(prof["section"]["一"]["item"]) == 1
    assert _indent_chars(prof["section"]["一"]["plain"]) == 2.5

    # 示范格式按表名分组：字段行 36 磅、委托书 26 磅
    assert prof["form"]["法定代表人资格证明"]["field"]["line_exact_pt"] == 36
    assert prof["form"]["授权委托书"]["field"]["line_exact_pt"] == 26
    assert prof["form"]["法定代表人资格证明"]["title"]["size"] == 16

    # 封面也能量到
    assert prof["cover"]["copy"]["align"] in ("end", "right")
    assert _indent_chars({**prof["cover"]["name"], "size": 18}) == 5


def test_role_fmt_falls_back_to_measured_body(tmp_path):
    f = tmp_path / "tender.docx"
    _make_tender(f)
    prof = merged(profile_from_docx(f))
    fmt = role_fmt(prof, "一", "plain")
    assert _indent_chars(fmt) == 2.5
    assert fmt["line_exact_pt"] == 24


def test_builder_body_follows_measured_profile():
    """full/xunbi/compact 的正文排版也走同一份量测值。"""
    from docx import Document

    from app.services.bidding_bid_builder import _p, set_format_profile
    try:
        set_format_profile({"body": {"font": "楷体", "size": 14,
                                     "first_line_chars": 2,
                                     "line_exact_pt": 30}})
        d = Document()
        p = _p(d, "测试正文段落，字号和行距都应来自招标文件量测值。")
        assert p.runs[0].font.name == "楷体"
        assert p.runs[0].font.size.pt == 14
        assert p.paragraph_format.line_spacing.pt == 30
        # 首行缩进 = 2 字 × 14 磅
        assert abs(p.paragraph_format.first_line_indent.pt - 28) < 0.6
    finally:
        set_format_profile({})
