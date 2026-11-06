# -*- coding: utf-8 -*-
"""上传模板 → 识别报告类型（替代「分析需求」）。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.routers.report import (      # noqa: E402
    _detect_template_domain, _template_title,
)
from app.schemas.report import StartGenerationRequest   # noqa: E402


def test_detect_reclamation_beats_stability_keywords():
    text = "土地复垦方案报告书\n临时用地复垦\n社会稳定风险分析"
    assert _detect_template_domain(text, "模板.docx") == "reclamation"


def test_detect_stability():
    assert _detect_template_domain("社会稳定风险评估报告", "x.docx") == "stability"


def test_detect_bidding():
    assert _detect_template_domain("技术标投标文件", "x.docx") == "bidding"


def test_template_title_prefers_report_line():
    text = "某某公司\n江苏盐业井神三井组临时用地土地复垦方案\n2026年8月"
    assert _template_title(text, "模板.docx").startswith("江苏盐业井神三井组")


def test_start_request_accepts_template_path():
    r = StartGenerationRequest(initial_message="生成报告", template_path="templates/user_x.docx")
    assert r.template_path.endswith(".docx")
