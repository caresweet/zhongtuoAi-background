# -*- coding: utf-8 -*-
"""测试稿数据替换：旧项目数据 → 该地区虚构测试数据。"""

from __future__ import annotations

from docx import Document

from app.services.test_doc_makeup import apply_test_facts


def test_apply_test_facts_replaces_project_data():
    d = Document()
    d.add_paragraph(
        "拟征收土地总面积80000.0平方米，涉及32户，共发放问卷60份，"
        "项目社会稳定风险评估总分为0分。")
    d.add_paragraph("本项目资金由洪泽区财政统筹。")
    d.add_paragraph("2026年8月发布征收土地预公告。")

    apply_test_facts(d, "淮安区")
    text = "\n".join(p.text for p in d.paragraphs)

    assert "76200平方米" in text
    assert "102户" in text
    assert "96份" in text
    assert "总分为83.5分" in text
    assert "淮安区财政统筹" in text      # 区县错配归到本项目区
    assert "2027年" in text
    assert "80000" not in text


def test_apply_test_facts_replaces_header():
    d = Document()
    d.add_paragraph("正文")
    h = d.sections[0].header
    h.is_linked_to_previous = False
    h.paragraphs[0].add_run("白马湖农场1号风电项目社会稳定性风险评估")

    apply_test_facts(d, "淮安区")

    assert "白马湖农场3号风电项目" in h.paragraphs[0].text
    assert "1号风电项目" not in h.paragraphs[0].text
