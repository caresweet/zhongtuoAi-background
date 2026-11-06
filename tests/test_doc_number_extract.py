# -*- coding: utf-8 -*-
"""项目文号自动识别：能从材料里拿到就不弹窗问用户。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.doc_number_extract import from_text   # noqa: E402


def test_policy_doc_no_inside_book_title_is_not_project_doc():
    txt = "依据《自然资源部关于规范临时用地管理的通知》（自然资规〔2019〕1号）办理。"
    assert from_text(txt) == ""


def test_project_approval_doc_no_is_found():
    txt = "本项目临时用地经淮安市自然资源和规划局批复，批复文号为淮自然资〔2026〕12号。"
    assert from_text(txt) == "淮自然资〔2026〕12号"


def test_prefers_approval_over_policy_in_same_text():
    txt = ("依据《自然资源部关于规范临时用地管理的通知》（自然资规〔2019〕1号），"
           "本项目临时用地备案文号为苏自然资函〔2025〕8号。")
    assert from_text(txt) == "苏自然资函〔2025〕8号"
