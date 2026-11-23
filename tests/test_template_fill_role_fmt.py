# -*- coding: utf-8 -*-
"""模板成稿：AI 改写章节时，正文/小标题各按模板角色套格式。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

from app.services.template_fill_report import merge_ai_sections


def _p(doc, text, size, bold, align=None, ind=None):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    if align is not None:
        p.alignment = align
    if ind is not None:
        p.paragraph_format.first_line_indent = ind
    return p


def _make_template(path):
    d = Document()
    _p(d, "第一章 编制说明", 22, True, WD_ALIGN_PARAGRAPH.CENTER)
    # 章下项目名/副标题，字号比正文大且加粗——旧实现把它们当正文样式源
    _p(d, "某某项目", 14, True, WD_ALIGN_PARAGRAPH.CENTER)
    _p(d, "社会稳定风险评估报告", 14, True, WD_ALIGN_PARAGRAPH.CENTER)
    _p(d, "（一）导语", 12, True)
    _p(d, "为积极配合做好项目立项及实施前的各项工作，确保项目顺利推进。",
       12, False, ind=Cm(1.0))
    _p(d, "1.项目名称", 12, True)
    _p(d, "某某项目土地征收决策", 12, False)
    _p(d, "2.项目相关单位", 12, True)
    _p(d, "稳评责任单位：某某镇人民政府", 12, False)
    _p(d, "第二章 编制依据", 22, True, WD_ALIGN_PARAGRAPH.CENTER)
    _p(d, "依据《重大行政决策程序暂行条例》等规定。", 12, False)
    _p(d, "第三章 附件", 22, True, WD_ALIGN_PARAGRAPH.CENTER)
    _p(d, "附件清单。", 12, False)
    d.save(str(path))


def _make_gen(path):
    d = Document()
    h = d.add_paragraph("第一章 编制说明")
    h.style = d.styles["Heading 1"]
    d.add_paragraph("（一）项目基本情况")
    d.add_paragraph("本项目位于某镇某村，拟征收土地总面积5.0公顷。")
    d.add_paragraph("1. 项目名称")
    d.add_paragraph("某区某号地块土地征收决策。")
    d.add_paragraph("2. 决策主体")
    d.add_paragraph("决策主体为某镇人民政府。")
    d.save(str(path))


def test_merge_body_text_uses_body_format(tmp_path):
    tpl, gen = tmp_path / "tpl.docx", tmp_path / "gen.docx"
    out = tmp_path / "out.docx"
    _make_template(tpl)
    _make_gen(gen)
    merge_ai_sections(template_path=tpl, gen_path=gen,
                      analysis_sections=[{"replace": True, "title": "编制说明"}],
                      out_path=out)

    doc = Document(str(out))
    paras = {p.text.strip(): p for p in doc.paragraphs if p.text.strip()}
    for txt in ("（一）项目基本情况", "1. 项目名称", "2. 决策主体"):
        r = paras[txt].runs[0]
        assert r.font.size.pt == 12, txt
        assert r.font.bold is True, txt
    for txt in ("本项目位于某镇某村，拟征收土地总面积5.0公顷。",
                "某区某号地块土地征收决策。",
                "决策主体为某镇人民政府。"):
        r = paras[txt].runs[0]
        assert r.font.size.pt == 12, txt
        assert r.font.bold is False, txt
