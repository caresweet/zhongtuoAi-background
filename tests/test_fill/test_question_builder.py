"""Tests for question_builder module (format_question, describe_placeholder_location)."""

import pytest
from app.agent.fill.question_builder import (
    format_question,
    describe_placeholder_location,
    build_fill_summary,
    CHAPTER_NAMES,
)


class TestFormatQuestion:
    """Test natural language question generation from placeholder metadata."""

    def test_highlight_with_current_value(self):
        """🟡 highlight with description → shows current value."""
        ph = {
            "display_name": "🟡 金征预告〔2026〕3号",
            "description": "当前值：「金湖县戴楼街道办事处」",
            "expected_type": "text",
            "section_title": "",
        }
        q = format_question(ph, "封面与评审表")
        assert "金湖县戴楼街道办事处" in q
        assert "blank" not in q.lower()
        assert "highlight" not in q.lower()

    def test_blank_with_context(self):
        """「」- Context → asks about context."""
        ph = {
            "display_name": "「」- 1.1决策名称的具体内容...",
            "description": "",
            "expected_type": "text",
            "section_title": "1.1 决策名称",
        }
        q = format_question(ph, "1.1 决策名称")
        assert "决策名称" in q or "1.1" in q
        assert "blank" not in q.lower()

    def test_named_section_blank(self):
        """「SectionRef」- rest → asks about specific section."""
        ph = {
            "display_name": "「1.1决策名称」- 需要填写决策名称...",
            "description": "",
            "expected_type": "text",
            "section_title": "1.1 决策名称",
        }
        q = format_question(ph, "1.1 决策名称")
        assert "决策名称" in q
        assert "blank" not in q.lower()

    def test_form_field(self):
        """📋 form field → asks to fill form field."""
        ph = {
            "display_name": "📋 事 项  名 称：",
            "description": "表单中的事项名称",
            "expected_type": "text",
            "section_title": "",
        }
        q = format_question(ph, "评审表")
        assert "表单" in q
        assert "事项名称" in q

    def test_table_placeholder(self):
        """Table cell → asks to fill table cell."""
        ph = {
            "display_name": "稳评责任单位 - 单位名称",
            "description": "",
            "expected_type": "table",
            "section_title": "",
        }
        q = format_question(ph, "评审表")
        assert "表格" in q
        assert "稳评责任单位" in q
        assert "单位名称" in q

    def test_figure_reference(self):
        """Figure reference → asks to upload image."""
        ph = {
            "display_name": "图3-1 决策评估公示内容",
            "description": "",
            "expected_type": "text",
            "section_title": "3.4 风险调查过程",
        }
        q = format_question(ph, "3.4 风险调查过程")
        assert "上传" in q or "图片" in q

    def test_date_type(self):
        """Date type → asks for date."""
        ph = {
            "display_name": "📋 填表日期：",
            "description": "请填写评估日期",
            "expected_type": "date",
            "section_title": "评审表",
        }
        q = format_question(ph, "评审表")
        assert "日期" in q

    def test_no_technical_keys_exposed(self):
        """All 6 patterns never expose technical keys."""
        test_cases = [
            {"display_name": "「」- 项目具体内容...", "expected_type": "text", "section_title": "", "description": ""},
            {"display_name": "🟡 highlight_78_4 prefill", "expected_type": "text", "section_title": "", "description": "当前值：「测试」"},
            {"display_name": "📋 表单字段名称：", "expected_type": "text", "section_title": "", "description": ""},
            {"display_name": "table_1_2_3 - field", "expected_type": "table", "section_title": "", "description": ""},
            {"display_name": "「placeholder_key」- context", "expected_type": "text", "section_title": "", "description": ""},
            {"display_name": "🟡 项目月份*信息", "expected_type": "text", "section_title": "基本信息", "description": "当前值：「2026年6月」"},
        ]
        for ph in test_cases:
            q = format_question(ph, "测试章节")
            assert "blank" not in q.lower(), f"blank exposed: {q}"
            assert "highlight" not in q.lower(), f"highlight exposed: {q}"
            assert "form_" not in q, f"form_ exposed: {q}"
            assert "wildcard" not in q.lower(), f"wildcard exposed: {q}"


class TestDescribePlaceholderLocation:
    """Test human-readable location descriptions."""

    def test_cover_title(self):
        assert "封面" in describe_placeholder_location({
            "section_index": -1, "paragraph_index": 0, "section_title": "", "display_name": "",
        })

    def test_cover_area(self):
        assert "封面" in describe_placeholder_location({
            "section_index": -1, "paragraph_index": 10, "section_title": "", "display_name": "",
        })

    def test_chapter_with_section(self):
        loc = describe_placeholder_location({
            "section_index": 21, "section_title": "4.1合法性分析", "paragraph_index": 10, "display_name": "",
        })
        assert "第4章" in loc
        # Location should include the chapter name and the sub-section reference
        assert "4.1" in loc or "合法性分析" in loc or "决策综合分析" in loc

    def test_fallback(self):
        loc = describe_placeholder_location({
            "section_index": 99, "section_title": "", "paragraph_index": 5, "display_name": "",
        })
        assert "段落6" in loc


class TestBuildFillSummary:
    """Test fill summary generation."""

    def test_generates_markdown(self, collecting_state):
        state = {"template_placeholders": collecting_state["template_placeholders"], "template_name": "测试模板"}
        filled = {"highlight_0_0": "新标题", "blank_1_0": "新决策名称"}
        summary = build_fill_summary("测试报告", "测试报告决策", filled, state)
        assert "## 📊" in summary
        assert "测试模板" in summary
        assert "变更详情" in summary


class TestChapterNames:
    """Test CHAPTER_NAMES mapping."""

    def test_all_10_chapters(self):
        assert len(CHAPTER_NAMES) == 10
        assert CHAPTER_NAMES[1] == "第1章 拟征收决策基本概况"
        assert CHAPTER_NAMES[10] == "第10章 应急预案"
