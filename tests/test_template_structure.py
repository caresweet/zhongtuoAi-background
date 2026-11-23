# -*- coding: utf-8 -*-
"""模板章节结构：解析模板章标题 → 装配后按模板重排/改名/重编表号。"""

from __future__ import annotations

from docx import Document

from app.services.template_structure import (
    Chapter, Plan, apply_plan, extract_plan)


def _h1(doc, text):
    p = doc.add_paragraph(text)
    p.style = doc.styles["Heading 1"]
    return p


def test_extract_plan_from_cn_headings(tmp_path):
    d = Document()
    _h1(d, "第一章 编制说明")
    _h1(d, "第二章 编制依据")
    _h1(d, "第三章 稳评评审报告表")
    _h1(d, "第四章 决策事项基本情况")
    _h1(d, "第五章 突发性事件或不稳定因素处置")
    d.add_paragraph("应对工作预案")   # 标题被拆成两段
    path = tmp_path / "t.docx"
    d.save(str(path))

    plan = extract_plan(path)
    kinds = [c.kind for c in plan.chapters]
    assert kinds[:4] == ["intro", "basis", "review", "overview"]
    assert kinds[4] == "emergency"
    assert plan.chapters[4].title.endswith("应对工作预案")
    assert plan.chapters[2].sys_no == 3


def test_extract_plan_prefers_toc_style(tmp_path):
    d = Document()
    from docx.enum.style import WD_STYLE_TYPE
    d.styles.add_style("toc 1", WD_STYLE_TYPE.PARAGRAPH)
    p = d.add_paragraph("淮安市社会稳定风险评估评审表\t1")
    p.style = d.styles["toc 1"]
    for i, t in enumerate(("1 拟建项目基本情况\t3", "2 分析预测风险源\t8",
                           "3 突发性事件或不稳定因素处置应对工作预案\t9"), 1):
        q = d.add_paragraph(t)
        q.style = d.styles["toc 1"]
    path = tmp_path / "t.docx"
    d.save(str(path))

    plan = extract_plan(path)
    assert [c.kind for c in plan.chapters] == ["review", "overview",
                                               "risk_source", "emergency"]
    assert plan.review_in_front is True


def test_apply_plan_reorders_renames_and_renumbers(tmp_path):
    d = Document()
    _h1(d, "第一章、编制说明")
    d.add_paragraph("正文一")
    _h1(d, "第十一章、分析预测风险源")
    d.add_paragraph("见表11-1。")
    d.add_paragraph("表11-1 风险源清单")
    _h1(d, "第十四章、附件")
    d.add_paragraph("表14-1 位置示意图")
    path = tmp_path / "r.docx"
    d.save(str(path))

    plan = Plan(chapters=[
        Chapter(1, "分析预测风险源", "risk_source", 11),
        Chapter(2, "编制说明", "intro", 1),
    ])
    st = apply_plan(path, plan)
    assert st["applied"] and st["chapters"] == 3   # 模板没列附件也保留附件章

    doc = Document(str(path))
    heads = [p.text for p in doc.paragraphs
             if p.style.name == "Heading 1" and p.text.strip()]
    assert heads == ["第一章、分析预测风险源", "第二章、编制说明", "第三章、附件"]
    texts = [p.text for p in doc.paragraphs]
    assert "表1-1 风险源清单" in texts          # 表号改按新章号
    assert "见表1-1。" in texts
    assert "表3-1 位置示意图" in texts          # 附件章内容保留并重编表号


def test_extract_plan_detects_chapter_title_layout(tmp_path):
    """章标题是「独占一页」还是「和正文同页」，按模板实测识别。"""
    from docx.enum.text import WD_BREAK

    d = Document()
    # 第一章：标题独占一页（正文另起一页）
    p1 = d.add_paragraph("第一章 编制说明")
    p1.paragraph_format.page_break_before = True
    q1 = d.add_paragraph("第一章正文")
    q1.paragraph_format.page_break_before = True
    # 第二章：标题段首分页，正文紧跟在同一页
    p2 = d.add_paragraph()
    p2.add_run().add_break(WD_BREAK.PAGE)
    p2.add_run("第二章 稳评工作方案")
    d.add_paragraph("第二章正文")
    path = tmp_path / "layout.docx"
    d.save(str(path))

    plan = extract_plan(path)
    by_kind = {c.kind: c for c in plan.chapters}
    assert by_kind["intro"].own_page is True
    assert by_kind["intro"].new_page is True
    assert by_kind["plan"].own_page is False      # 标题与正文同页
    assert by_kind["plan"].new_page is True       # 但章仍从新页开始


def test_extract_plan_reads_front_matter_spec(tmp_path):
    """前置件写法（名单标题/岗位分组/证书节标题）按模板识别。"""
    d = Document()
    d.add_paragraph("稳评工作组人员及分工情况")
    d.add_paragraph("1 、稳评负责人")
    d.add_paragraph("张三（总经理）")
    d.add_paragraph("2 、调研工作")
    d.add_paragraph("李四（工程师）")
    d.add_paragraph("3 、风险研判与评估报告编制工作")
    d.add_paragraph("4 、资料与档案工作")
    d.add_paragraph("3.人员培训证书")
    for t in ("第一章 编制说明", "第二章 编制依据", "第三章 拟征收决策基本情况"):
        d.add_paragraph(t)
    path = tmp_path / "front.docx"
    d.save(str(path))

    front = extract_plan(path).front
    assert front["roster_heading"] == "稳评工作组人员及分工情况"
    assert front["roster_groups"] == [
        "1 、稳评负责人", "2 、调研工作",
        "3 、风险研判与评估报告编制工作", "4 、资料与档案工作"]
    assert front["cert_heading"] == "3.人员培训证书"


def test_extract_plan_collects_subsections(tmp_path):
    """章内小节标题（（一）/1.）按层级收集，正文编号列表不算。"""
    d = Document()
    d.add_paragraph("第一章 编制说明")
    d.add_paragraph("（一）稳评理由")
    d.add_paragraph("1.为维护人民群众的合法权益")
    d.add_paragraph("（二）稳评目的")
    d.add_paragraph("1.客观公正性原则")
    for t in ("第二章 编制依据", "第三章 项目基本情况"):
        d.add_paragraph(t)
    path = tmp_path / "sec.docx"
    d.save(str(path))

    plan = extract_plan(path)
    ch = by_kind = {c.kind: c for c in plan.chapters}["intro"]
    lv2 = [s["text"] for s in ch.sections if s["level"] == 2]
    lv3 = [s["text"] for s in ch.sections if s["level"] == 3]
    assert lv2 == ["（一）稳评理由", "（二）稳评目的"]
    assert lv3 == ["1.客观公正性原则"]      # 「为维护…」是正文列表，不算小节


def test_apply_plan_renames_subsections_when_counts_match(tmp_path):
    from docx.enum.text import WD_BREAK

    d = Document()
    _h1(d, "第一章、编制说明")
    d.add_paragraph("（一）旧小节名", style="Heading 2")
    d.add_paragraph("1.旧三级名", style="Heading 3")
    d.add_paragraph("正文")
    path = tmp_path / "rename.docx"
    d.save(str(path))

    plan = Plan(chapters=[Chapter(
        1, "编制说明", "intro", 1,
        sections=[{"level": 2, "text": "（一）模板小节名"},
                  {"level": 3, "text": "1.模板三级名"}])])
    apply_plan(path, plan)
    texts = [p.text for p in Document(str(path)).paragraphs]
    assert "（一）模板小节名" in texts
    assert "1.模板三级名" in texts
