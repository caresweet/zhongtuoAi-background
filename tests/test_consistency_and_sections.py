# -*- coding: utf-8 -*-
"""跨章一致性与逐小节润色。"""

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.consistency_check import check_and_fix          # noqa: E402
from app.services.prose_polish import polish_section_text        # noqa: E402


def test_labeled_numbers_unified_to_fact_card():
    chapters = {
        1: {"markdown": "本项目征收面积168.5亩，涉及户数46户，发放问卷46份，支持率100.0%。"},
        6: {"markdown": "征收面积为180亩，涉及户数50户，发放问卷40份，支持率90.0%。"},
    }
    filled = {"area_mu": "168.5", "household_count": "46",
              "total_samples": "46", "support_rate": "100.0"}
    issues, fixes = check_and_fix(chapters, filled, "stability")
    md6 = chapters[6]["markdown"]
    assert "180亩" not in md6 and "168.5亩" in md6
    assert "50户" not in md6 and "46户" in md6
    assert "40份" not in md6 and "46份" in md6
    assert "90.0%" not in md6 and "100.0%" in md6
    assert fixes and issues


def test_boundary_majority_fix():
    good = "东至运河堤顶路，南至规划纬三路，西至现状农田，北至马头干渠"
    bad = "东至运河堤顶路，南至现状农田，西至规划纬五路，北至马头干渠"
    chapters = {
        4: {"markdown": f"四至为{good}。"},
        6: {"markdown": f"四至为{good}。"},
        10: {"markdown": f"四至为{bad}。"},
    }
    issues, fixes = check_and_fix(chapters, {}, "stability")
    assert bad not in chapters[10]["markdown"]
    assert good in chapters[10]["markdown"]
    assert any("四至" in i["message"] for i in issues)


def test_notice_period_majority_fix():
    a = "公告期为2026年8月8日至2026年8月14日，共7天"
    b = "公告期为2026年8月7日至2026年8月14日，共8天"
    chapters = {
        6: {"markdown": a + "。"}, 7: {"markdown": a + "。"},
        10: {"markdown": b + "。"},
    }
    check_and_fix(chapters, {}, "stability")
    assert b not in chapters[10]["markdown"]
    assert a in chapters[10]["markdown"]


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    async def chat_with_reasoning(self, **kwargs):
        return {"content": self.text}


def test_section_polish_only_touches_flagged_section():
    s1 = ("## 1.1 背景\n\n本项目切实保障了群众利益，同时充分体现了科学决策和民主决策的"
          "要求，并且进一步提升了政府公信力和群众满意度，形成了良好局面。")
    s2 = "## 1.2 范围\n\n范围明确，边界清楚。"
    md = s1 + "\n\n" + s2
    polished_s1 = ("## 1.1 背景\n\n本项目保障了群众利益。决策过程体现了科学与民主。"
                   "政府公信力和群众满意度得到提升，形成了良好局面。")
    out, done = asyncio.run(polish_section_text(md, llm=_FakeLLM(polished_s1)))
    assert "切实保障" not in out
    assert "范围明确，边界清楚。" in out
    assert done and "1.1" in done[0]
