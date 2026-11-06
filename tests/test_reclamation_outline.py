# -*- coding: utf-8 -*-
"""复垦标准大纲（TD/T 1031.1-2011 第六章）与结构核对。"""

from docx import Document

from app.services.reclamation_outline import (
    check_chapters, full_structure, sections_of, standard_chapters, standard_source,
)


def test_outline_has_ten_chapters_and_post_body():
    ch = standard_chapters()
    assert list(ch) == list(range(1, 11))
    assert ch[1] == "前言"
    assert ch[10] == "保障措施"
    full = full_structure()
    assert full[11] == "复垦方案报告表"
    assert full[14] == "附件"
    assert "TD/T 1031.1" in standard_source()


def test_sections_follow_the_regulation():
    assert sections_of(1)[-1] == "1.2.6 生态空间管控情况"
    assert "4.5 水土资源平衡分析" in sections_of(4)
    assert "10.6 土地权属调整方案" in sections_of(10)


def test_check_chapters_reports_missing_and_extra():
    res = check_chapters({1: "前言", 2: "编制总则", 99: "多余的章"},
                         include_post=False)
    assert res["missing"] == list(range(3, 11))
    assert res["extra"] == [99]
    assert res["ok"] is False


def test_expected_structure_uses_outline():
    from app.services.template_rules import expected_structure
    exp = expected_structure("reclamation")
    assert exp[1] == "前言" and exp[14] == "附件"


def test_corpus_profile_checks_outline(tmp_path):
    from app.services.template_corpus_profile import analyze_template_file
    d = Document()
    for n, title in ((1, "前言"), (2, "编制总则"), (3, "项目概况"),
                     (4, "土地复垦方向可行性分析"), (5, "土地复垦质量要求与复垦措施")):
        d.add_heading(f"{n} {title}", level=1)
        d.add_paragraph("正文内容。" * 20)
    p = tmp_path / "sample.docx"
    d.save(str(p))
    prof = analyze_template_file(p, domain="reclamation")
    oc = prof["outline_check"]
    assert oc["missing"] == [6, 7, 8, 9, 10]
    assert oc["ok"] is False
