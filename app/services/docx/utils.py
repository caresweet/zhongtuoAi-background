"""Shared utilities and data structures for docx operations."""

import re
from typing import List, Optional
from dataclasses import dataclass, field


# Regex patterns for detecting placeholders in docx text
PLACEHOLDER_PATTERNS = [
    (r'\{\{(\w+)\}\}', "double_brace"),           # {{project_name}}
    (r'\{\{([^}]+)\}\}', "double_brace_cn"),      # {{项目名称}}
    (r'[［【]([^］】]+)[］】]', "bracket_cn"),     # 【项目名称】
    (r'<([^>]+)>', "angle_bracket"),              # <project_name>
    (r'_{3,}', "underscore_blank"),               # ________
]


@dataclass
class SectionInfo:
    """Represents a document section (heading + its content)."""
    index: int
    title: str
    level: int  # heading level 1-6
    start_para_index: int
    end_para_index: Optional[int] = None
    placeholders: List["PlaceholderInfo"] = field(default_factory=list)


@dataclass
class PlaceholderInfo:
    """Represents a single placeholder in the document."""
    key: str
    display_name: str
    section_index: int
    section_title: str
    paragraph_index: int
    run_index: Optional[int] = None
    expected_type: str = "text"
    description: str = ""
    is_required: bool = True
    original_text: str = ""  # Full text of the run containing the placeholder
    placeholder_pattern: str = ""  # Actual pattern matched, e.g. "{{project_name}}"
