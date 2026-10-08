"""复垦生成路径按复垦的 10 章结构建大纲；稳评路径逐字段不变。

背景：`report_workflow` 原先硬编码稳评的 14 章（`chapter_definitions`）。
复垦章号 1–10 与稳评重合，但结构不同 —— 不改的话复垦第 3 章（项目概况）
会被当成稳评的评审表（table）跳过。域分发收口在 `app.agent.chapter_catalog`。
"""
import asyncio

from app.agent.chapter_catalog import all_chapters, chapter_kind, standard_outline
from app.services.report_workflow import _standard_outline, node_outline_check


def test_reclamation_outline_is_ten_reclamation_chapters():
    outline = standard_outline("reclamation")
    assert [c["chapter_no"] for c in outline] == [str(n) for n in range(1, 11)]
    assert outline[2]["title"] == "项目概况"
    # 🔴 复垦第 3 章是叙述章（fact）；按稳评取会得到 "table"（评审表）而被跳过。
    assert chapter_kind("reclamation", 3) == "fact"
    assert all_chapters("reclamation") == list(range(1, 11))


def test_stability_outline_unchanged():
    """稳评路径（含不传域的默认值）逐字段不变 —— 这是稳评的护栏。"""
    outline = standard_outline("stability")
    assert [c["chapter_no"] for c in outline] == [str(n) for n in range(1, 15)]
    assert outline[2]["kind"] == "table"          # 稳评第 3 章仍是评审表
    assert chapter_kind("stability", 3) == "table"
    # 默认（不传域）等于稳评 —— 老调用点（scripts/gen_*.py）行为不变
    assert _standard_outline() == _standard_outline("stability")
    assert all_chapters(None) == list(range(1, 15))
    assert chapter_kind(None, 3) == "table"


def test_outline_check_keeps_reclamation_structure():
    """复垦的 10 章大纲经 outline_check 不得被重置成稳评的 14 章。"""
    state = {"_domain": "reclamation",
             "outline_list": [{"chapter_no": str(n), "title": f"第{n}章"}
                              for n in range(1, 11)],
             "filled_data": {}}
    out = asyncio.run(node_outline_check(state))
    assert [c["chapter_no"] for c in out["outline_list"]] == [str(n) for n in range(1, 11)]


def test_outline_check_resets_stability_shaped_outline_under_reclamation():
    """喂一份稳评形状（14 章）的大纲给复垦会话 → 重置成复垦的 10 章。

    这正是修复前复垦走到的那条路：`nums != ALL_CHAPTERS` 把复垦重置成稳评结构。
    """
    state = {"_domain": "reclamation",
             "outline_list": [{"chapter_no": str(n)} for n in range(1, 15)],
             "filled_data": {}}
    out = asyncio.run(node_outline_check(state))
    assert [c["chapter_no"] for c in out["outline_list"]] == [str(n) for n in range(1, 11)]
