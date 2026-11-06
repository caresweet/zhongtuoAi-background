# -*- coding: utf-8 -*-
"""「空泛判断」检测的精度：事实/目的陈述不能算缺口。

🔴 线上 2026-11-03：复垦报告交付体检列出 13 处「只有结论、没写依据」，
   大半是误报 —— 功能说明、气候事实、单位能力、前言套话都被当成了缺口，
   弹窗还会问用户「补个数字」。这些句子补数字反而不对。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.gap_detector import detect_hollow_claims   # noqa: E402

# 线上误报原句（截图里那 13 处的代表）
_FALSE_POSITIVES = [
    "计算复垦工程量、提出复垦工程的投资概算，为土地复垦工程的实施提供资金使用的依据。",
    "推进农业现代化，促进生态文明建设等都具有重要意义。",
    "汛期降水集中，雨热同期，有利于农作物生长。",
    "该公司作为建设单位，具备相应的资金保障能力和工程管理能力，能够按本方案落实复垦费用和各项复垦措施。",
    "办公区为管理人员办公和工作提供场所。",
]

# 真·空泛判断（没有数字/文号/法规名，应该被标出来）
_TRUE_POSITIVES = [
    "该项目实施有利于完善周边基础设施配套。",
    "项目总体实施难度相对可控。",
    "该报告切实保障了群众知情权。",
    "本方案与当地土地利用规划相匹配。",
    "项目建设有利于提升区域形象。",
]


def test_factual_and_purpose_sentences_are_not_gaps():
    for sent in _FALSE_POSITIVES:
        assert detect_hollow_claims(sent, 3) == [], f"误报: {sent}"


def test_evaluative_claims_without_evidence_are_still_flagged():
    for sent in _TRUE_POSITIVES:
        hits = detect_hollow_claims(sent, 3)
        assert hits, f"漏报: {sent}"
        assert hits[0]["reason"] == "hollow_claim"


def test_claim_with_number_or_policy_is_not_flagged():
    sent = "该项目实施有利于完善周边基础设施配套，涉及临时用地2.3公顷。"
    assert detect_hollow_claims(sent, 3) == []

    sent2 = "项目总体实施难度相对可控，符合《土地复垦条例》要求。"
    assert detect_hollow_claims(sent2, 3) == []
