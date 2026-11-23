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
