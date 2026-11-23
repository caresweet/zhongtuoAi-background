# -*- coding: utf-8 -*-
"""复垦专家批注修补（2026-11-19）的确定性规则。"""

from __future__ import annotations

from docx import Document

from app.services.reclamation_content_repairs import (
    fix_expert_sentences, fix_heading_numbers, fix_list_numbering)


def test_heading_number_follows_current_second_level():
    md = ("## 1.1 复垦方案摘要\n\n### 1.2.1 复垦方案服务年限\n\n"
          "### 1.2.2 方案涉及的各类土地面积\n")
    out, n = fix_heading_numbers(md)
    assert "### 1.1.1 复垦方案服务年限" in out
    assert "### 1.1.2 方案涉及的各类土地面积" in out
    assert n >= 2


def test_list_numbering_renumbers_gaps_across_blank_lines():
    md = "（1）甲；\n\n（3）乙；\n\n（4）丙；\n"
    out, n = fix_list_numbering(md)
    assert "（1）甲" in out and "（2）乙" in out and "（3）丙" in out
    assert n == 2


def test_expert_sentences():
    md = ("在工程施淮安市自然资源和规划局淮安分局指定专人。"
          "每年12月31淮安市自然资源和规划局淮安分局报告。"
          "尽量少占用永久基本农田。综合分值均高于90分。")
    out, n = fix_expert_sentences(md)
    assert "在施工过程中，由淮安市" in out
    assert "每年12月31日前向淮安市" in out
    assert "不占用永久基本农田" in out
    assert "综合分值均在80分以上" in out
    assert n == 4


def test_add_land_codes_and_audit(tmp_path):
    from app.services.reclamation_expert_fixes import (
        _add_land_codes, audit_reclamation_docx)

    d = Document()
    d.add_paragraph("1 前言")
    p = d.add_paragraph("1.1 编制背景及过程")
    p.style = d.styles["Heading 2"]
    d.add_paragraph("项目单位：某单位")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "地类名称及编码"
    t.cell(0, 1).text = "面积（hm²）"
    t.cell(1, 0).text = "旱地"
    t.cell(1, 1).text = "1.4000"
    path = tmp_path / "r.docx"
    d.save(str(path))

    assert _add_land_codes(Document(str(path))) == 1
    d2 = Document(str(path))
    _add_land_codes(d2)
    d2.save(str(path))
    assert "旱地（0103）" in d2.tables[0].cell(1, 0).text
    # 缺目录页码/8.2/8.3 等应被审计报出，但不要因无关项崩
    issues = audit_reclamation_docx(path)
    assert any("第8章缺 8.2" in x for x in issues)
