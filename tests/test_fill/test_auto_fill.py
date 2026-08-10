"""Tests for auto_fill_title_placeholders."""

import pytest
from app.agent.fill.auto_fill import auto_fill_title_placeholders


class TestAutoFillTitlePlaceholders:
    """Test unified auto-fill logic."""

    def test_fills_title_placeholder(self, collecting_state):
        """Title pattern matching fills the right placeholder."""
        filled = auto_fill_title_placeholders(
            placeholder_map=collecting_state["template_placeholders"],
            filled_data={},
            report_title="测试预告〔2026〕5号（测试项目）",
            decision_name="测试预告〔2026〕5号（测试项目）决策",
        )

        # highlight_0_0 has matched_text "金征预告〔2026〕3号" → matches title pattern
        assert "highlight_0_0" in filled
        assert filled["highlight_0_0"] == "测试预告〔2026〕5号（测试项目）"

    def test_fills_decision_name_placeholder(self, collecting_state):
        """决策名称 pattern fills the right placeholder."""
        filled = auto_fill_title_placeholders(
            placeholder_map=collecting_state["template_placeholders"],
            filled_data={},
            report_title="测试项目",
            decision_name="测试项目决策",
        )

        # blank_1_0 has section_title "1.1 决策名称" → fills decision_name
        assert "blank_1_0" in filled
        assert filled["blank_1_0"] == "测试项目决策"

    def test_skips_already_filled(self, collecting_state):
        """Already-filled keys are not overwritten."""
        existing = {"highlight_0_0": "原始值"}
        filled = auto_fill_title_placeholders(
            placeholder_map=collecting_state["template_placeholders"],
            filled_data=existing,
            report_title="新标题",
            decision_name="新决策",
        )

        # Already-filled key should keep its original value
        assert filled["highlight_0_0"] == "原始值"

    def test_returns_mutated_dict(self, collecting_state):
        """Returns the same dict object (mutated in-place)."""
        data = {}
        result = auto_fill_title_placeholders(
            placeholder_map=collecting_state["template_placeholders"],
            filled_data=data,
            report_title="测试",
            decision_name="测试决策",
        )
        assert result is data  # Same object


class TestAutoFillEdgeCases:
    """Edge cases for auto-fill."""

    def test_empty_placeholder_map(self):
        """Empty placeholder map returns unchanged."""
        result = auto_fill_title_placeholders(
            placeholder_map=[],
            filled_data={},
            report_title="测试",
        )
        assert result == {}

    def test_auto_derives_decision_name(self):
        """If decision_name not provided, auto-derives from title."""
        result = auto_fill_title_placeholders(
            placeholder_map=[{
                "key": "test",
                "display_name": "1.1 决策名称",
                "section_title": "1.1 决策名称",
                "paragraph_index": 1,
                "section_index": 3,
            }],
            filled_data={},
            report_title="项目X",
        )
        assert result["test"] == "项目X决策"
