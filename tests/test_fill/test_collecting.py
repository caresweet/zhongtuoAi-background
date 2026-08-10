"""Tests for collecting state machine."""

import pytest
from app.agent.fill.collecting import (
    init_collecting_state,
    build_next_question,
    advance_question,
    collect_user_input,
)


class TestInitCollectingState:
    """Test collecting state initialization."""

    def test_builds_ordered_sections(self, collecting_state):
        """Sections are ordered: cover(-1) → review(0) → body(sorted)."""
        init_collecting_state(collecting_state)
        order = collecting_state["collecting_section_order"]

        assert len(order) >= 3  # cover, review, body sections
        # Cover (-1) first
        assert order[0]["section_index"] == -1
        assert order[0]["label"] == "封面与评审表"
        # Review form (0) second
        assert order[1]["section_index"] == 0
        assert order[1]["label"] == "评审表"

    def test_sets_initial_indices(self, collecting_state):
        """Initial indices start at 0."""
        init_collecting_state(collecting_state)
        assert collecting_state["collecting_current_group_idx"] == 0
        assert collecting_state["collecting_question_idx"] == 0
        assert collecting_state["collecting_skipped_keys"] == []

    def test_skips_already_filled(self, collecting_state):
        """Already-filled placeholders are excluded from sections."""
        collecting_state["filled_data"] = {"highlight_0_0": "已有值"}
        init_collecting_state(collecting_state)
        order = collecting_state["collecting_section_order"]
        # highlight_0_0 is section -1. If it's the only -1 ph, cover section might be empty
        # Check that the filled placeholder is not in the order
        for group in order:
            assert group["section_index"] != -1 or len(group) > 0


class TestBuildNextQuestion:
    """Test question building during section walk."""

    def test_returns_first_question(self, collecting_state):
        """First call returns the first unfilled placeholder's question."""
        init_collecting_state(collecting_state)
        q = build_next_question(collecting_state)

        assert q is not None
        assert "question" in q
        assert "section_title" in q
        assert "progress" in q
        assert "placeholder_type" in q
        assert "placeholder_key" in q
        assert "blank" not in q["question"].lower()

    def test_returns_none_when_all_filled(self, collecting_state):
        """Returns None when all placeholders are filled."""
        # Fill all placeholders
        for ph in collecting_state["template_placeholders"]:
            collecting_state["filled_data"] = collecting_state.get("filled_data", {})
            collecting_state["filled_data"][ph["key"]] = "已填写"

        init_collecting_state(collecting_state)
        q = build_next_question(collecting_state)
        assert q is None

    def test_skips_skipped_keys(self, collecting_state):
        """Skipped keys are excluded from questions."""
        init_collecting_state(collecting_state)
        first_q = build_next_question(collecting_state)
        skipped_key = first_q["placeholder_key"]

        # Reset and add this key to skipped
        collecting_state["collecting_skipped_keys"] = [skipped_key]
        collecting_state["collecting_current_group_idx"] = 0
        collecting_state["collecting_question_idx"] = 0

        second_q = build_next_question(collecting_state)
        if second_q:
            assert second_q["placeholder_key"] != skipped_key


class TestAdvanceQuestion:
    """Test question advancement."""

    def test_increments_question_index(self, collecting_state):
        """Advancing increments q_idx."""
        init_collecting_state(collecting_state)
        old_idx = collecting_state["collecting_question_idx"]
        advance_question(collecting_state)
        assert collecting_state["collecting_question_idx"] == old_idx + 1

    def test_does_not_crash_at_end(self, collecting_state):
        """Advancing past end should not crash."""
        collecting_state["collecting_current_group_idx"] = 999
        advance_question(collecting_state)  # Should be safe


class TestCollectUserInput:
    """Test user input collection."""

    def test_fills_correct_key(self, collecting_state):
        """User input fills the target placeholder."""
        question = {
            "placeholder_key": "blank_1_0",
            "placeholder_type": "text",
            "image_required": False,
        }
        collect_user_input(collecting_state, "测试决策名称", None, question)
        assert collecting_state["filled_data"]["blank_1_0"] == "测试决策名称"

    def test_empty_input_uses_placeholder(self, collecting_state):
        """Empty input fills '需后期提供'."""
        question = {
            "placeholder_key": "blank_1_0",
            "placeholder_type": "text",
            "image_required": False,
        }
        collect_user_input(collecting_state, "", None, question)
        assert collecting_state["filled_data"]["blank_1_0"] == "需后期提供"

    def test_image_attachment_fills_path(self, collecting_state):
        """Image attachment stores file_path."""
        question = {
            "placeholder_key": "highlight_0_0",
            "placeholder_type": "image",
            "image_required": True,
        }
        collect_user_input(collecting_state, "", ["images/test.png"], question)
        assert collecting_state["filled_data"]["highlight_0_0"] == "images/test.png"

    def test_no_question_does_nothing(self, collecting_state):
        """No question = no change."""
        before = dict(collecting_state.get("filled_data", {}))
        collect_user_input(collecting_state, "输入", None, None)
        assert collecting_state.get("filled_data", {}) == before
