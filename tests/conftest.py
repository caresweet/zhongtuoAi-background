"""Shared test fixtures for the report generation system."""

import os
import sys
import pytest
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.state import create_initial_state


# ---- Paths ----

@pytest.fixture
def project_root():
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def template_path():
    """Path to the real empty template for integration tests."""
    p = Path(__file__).resolve().parent.parent / "storage/templates/65439de459274f44b2a623c6002be2ea.docx"
    if p.exists():
        return str(p)
    return None


@pytest.fixture
def example_path():
    """Path to the best completed example for diff comparison."""
    p = Path(__file__).resolve().parent.parent / "storage/generated/b5a55e9774a64a82853c2bef0e6846e4.docx"
    if p.exists():
        return str(p)
    return None


# ---- State fixtures ----

@pytest.fixture
def empty_state():
    """Create a fresh initial state."""
    import uuid
    return create_initial_state(str(uuid.uuid4()))


@pytest.fixture
def collecting_state(empty_state):
    """State with template placeholders for collecting tests."""
    state = dict(empty_state)
    state["template_placeholders"] = [
        {
            "key": "highlight_0_0",
            "display_name": "🟡 金征预告〔2026〕3号",
            "section_index": -1,
            "section_title": "",
            "paragraph_index": 0,
            "expected_type": "text",
            "description": "当前值：「金征预告〔2026〕3号」",
            "matched_text": "金征预告〔2026〕3号",
        },
        {
            "key": "blank_1_0",
            "display_name": "「」- 1.1决策名称...",
            "section_index": 3,
            "section_title": "1.1 决策名称",
            "paragraph_index": 1,
            "expected_type": "text",
            "description": "",
        },
        {
            "key": "blank_2_0",
            "display_name": "「」- 1.2决策单位...",
            "section_index": 4,
            "section_title": "1.2 决策单位",
            "paragraph_index": 2,
            "expected_type": "text",
            "description": "",
        },
        {
            "key": "form_date_0",
            "display_name": "📋 填表日期：",
            "section_index": 0,
            "section_title": "",
            "paragraph_index": -1,
            "expected_type": "date",
            "description": "表单中的填表日期",
        },
        {
            "key": "table_0_0_0",
            "display_name": "稳评责任单位 - 单位名称",
            "section_index": -1,
            "section_title": "",
            "paragraph_index": -1,
            "expected_type": "table",
            "description": "",
        },
        {
            "key": "blank_ch4_0",
            "display_name": "「」- 4.1合法性分析...",
            "section_index": 21,
            "section_title": "4.1 合法性分析",
            "paragraph_index": 10,
            "expected_type": "text",
            "description": "",
        },
    ]
    state["filled_data"] = {}
    return state


# ---- Agent fixtures ----

@pytest.fixture
def mock_llm_service():
    """Mock LLM service for unit tests."""
    class MockLLM:
        async def chat(self, prompt: str) -> str:
            return f"Mock response for: {prompt[:50]}..."
    return MockLLM()
