# -*- coding: utf-8 -*-
"""模板分析的项目既定规则：解析、判域、章结构对账。"""

from docx import Document

from app.services.template_rules import (
    check_structure, domain_terms, load_rules, rules_block, rules_for,
    score_domains,
)


def test_rules_file_parses_both_domains():
    data = load_rules()
    assert "reclamation" in data and "stability" in data
    rec = data["reclamation"]
    assert rec["global"], "复垦应有全章通用规则"
    assert rec["chapters"].get(4), "复垦第4章应有专属规则"
    assert rec["structure"].get(14) == "附件"
    assert "表土剥离" in rec["terms"]


def test_rules_for_filters_by_chapter():
    all_rules = rules_for("reclamation")
    ch4 = rules_for("reclamation", 4)
    assert len(ch4) > len(all_rules)
    assert any("表4-11" in r for r in ch4)
    # 第5章的规则不能串到第4章
    assert not any("路线方案设计" in r for r in ch4)
    assert "第4章" not in rules_block("reclamation", 4).split("\n")[0]


def test_rules_block_empty_for_unknown_domain():
    assert rules_block("bidding", 1) == ""
    assert domain_terms("bidding") == []


def test_score_domains_by_structure_words():
    rec, s1 = score_domains("本项目复垦方向为水田，表土剥离厚度 0.3m，损毁程度重度")
    assert rec == "reclamation" and s1 >= 2
    st, s2 = score_domains("本项目社会稳定风险等级为低风险，利益相关者问卷 120 份")
    assert st == "stability" and s2 >= 2
    assert score_domains("一份普通合同") == ("", 0)


def _make_doc(path, chapters, style="num"):
    d = Document()
    for no, title in chapters:
        text = f"{no} {title}" if style == "num" else f"第{no}章 {title}"
        d.add_heading(text, level=1)
        d.add_paragraph("正文内容。")
    d.save(str(path))


def test_check_structure_reports_missing_and_extra(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_doc(p, [(1, "前言"), (2, "编制总则"), (99, "多余的一章")])
    res = check_structure(p, "reclamation")
    assert res["missing"] == [3, 4, 5, 6, 7, 8, 9, 10]
    # 11–14 是系统渲染部分，模板没有不算缺陷
    assert res["missing_parts"] == [11, 12, 13, 14]
    assert res["extra"] == [99]
    assert res["ok"] is False


def test_check_structure_accepts_chinese_chapter_headings(tmp_path):
    p = tmp_path / "tpl.docx"
    _make_doc(p, [(1, "前言"), (2, "编制总则")], style="cn")
    res = check_structure(p, "reclamation")
    assert [x["no"] for x in res["found"]] == [1, 2]


def test_check_structure_ignores_non_docx(tmp_path):
    p = tmp_path / "tpl.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert check_structure(p, "reclamation") == {}
