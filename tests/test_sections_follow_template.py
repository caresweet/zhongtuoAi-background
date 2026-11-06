# -*- coding: utf-8 -*-
"""复垦章节结构规范必须以**模板小节**为准，而不是代码里的旧骨架。

业主 2026-11-04：「你和人工写出来的报告对比，我总觉得你还是用的以前的报告大纲」。
根因：`sections_for_prompt` 之前只给 `RECLAMATION_CHAPTER_DEFINITIONS.sections`
（4.1~4.7 / 5.1~5.5），模板里的 4.1.1~4.1.4、4.2.1~4.2.5 根本没进提示词。
"""

from pathlib import Path

import pytest

from app.agent import chapter_catalog as cat

TPL = Path(__file__).resolve().parent.parent / "storage" / "templates" / \
    "user_95946f347730449c8716ef49c2a5be41.docx"

pytestmark = pytest.mark.skipif(not TPL.exists(), reason="测试模板不在仓库里")


def test_chapter4_sections_follow_template():
    spec = cat.sections_for_prompt("reclamation", 4, str(TPL))
    assert "## 4.1 土地损毁分析与评定" in spec
    assert "### 4.1.1 土地损毁环节与时序" in spec
    assert "### 4.2.2 占用永久基本农田、生态保护红线和生态管控区情况" in spec
    assert "## 4.6 复垦的目标任务" in spec
    # 旧代码骨架里有、模板里没有的「4.6 生态保护红线…」不能出现
    assert "## 4.6 生态保护红线" not in spec
    # 前导点已清掉
    assert "4.5.1 土资源平衡分析" in spec and ".土资源平衡分析" not in spec


def test_chapter5_does_not_invent_sub_sections():
    spec = cat.sections_for_prompt("reclamation", 5, str(TPL))
    assert "## 5.1 土地复垦质量要求" in spec
    assert "### 5.1.1 复垦质量要求" in spec
    # 模型曾经自造的 5.1.2~5.1.6（清理/平整/剥覆/灌溉/道路标准）不应作为结构给出
    assert "5.1.2" not in spec
    assert "5.1.6" not in spec


def test_fallback_without_template_uses_code_sections():
    spec = cat.sections_for_prompt("reclamation", 5, "")
    assert "## 5.1 土地复垦质量要求" in spec
    assert "## 5.5 管护措施" in spec


def test_stability_unchanged():
    a = cat.sections_for_prompt("stability", 4, "")
    b = cat.sections_for_prompt("stability", 4, str(TPL))
    assert a == b


def test_unlisted_headings_become_lead_lines_and_titles_follow_template():
    from app.services.report_assembler import align_headings_to_template
    md = ("## 3.1 项目简介\n\n正文。\n\n"
          "#### 3.1.5 项目建设内容：\n\n正文甲。\n\n"
          "### 5.1.1 清理工程标准\n\n正文乙。\n\n"
          "## 3.2 项目区自然概况\n")
    allowed = {"3.1": "项目简介", "3.2": "项目区自然概况",
               "5.1.1": "复垦质量要求"}
    out, n = align_headings_to_template(md, allowed)
    assert n >= 2
    assert "## 3.1 项目简介" in out and "## 3.2 项目区自然概况" in out
    assert "5）项目建设内容" in out
    assert "#### 3.1.5" not in out
    # 编号在模板里、标题写错 → 改回模板标题
    assert "### 5.1.1 复垦质量要求" in out
    assert "清理工程标准" not in out


def test_deep_heading_with_template_title_is_promoted():
    from app.services.report_assembler import align_headings_to_template
    md = "#### 4.6.1 复垦的目标任务\n\n正文。\n"
    allowed = {"4.6": "复垦的目标任务"}
    out, n = align_headings_to_template(md, allowed)
    assert "4.6 复垦的目标任务" in out
    assert "4.6.1" not in out


def test_template_table_4_5_is_copied_with_merged_cells():
    from docx import Document as _D
    from docx.oxml.ns import qn
    from app.services.report_assembler import ReportAssembler
    a = ReportAssembler.__new__(ReportAssembler)
    a._domain = "reclamation"
    a._session_state = {"template_path": str(TPL)}
    a._rendered_captions = set()
    doc = _D()
    ok = a._insert_template_table(
        doc, "表4-5 土地适宜性评价指标体系", ("一级影响因子", "三级影响因子"))
    assert ok
    assert len(doc.tables) == 1
    tb = doc.tables[0]
    assert len(tb.columns) == 9 and len(tb.rows) == 35
    # 模板自带纵向合并（同类项合并）
    vm = tb.rows[1].cells[0]._tc.tcPr.find(qn('w:vMerge'))
    assert vm is not None
