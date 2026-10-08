"""日期字段的**唯一规则**（业主 2026-09-24 定）：

    材料里有 → 直接用材料的，**不问、不判断、不修改**；
    材料里没有 → 问用户填写，**以用户填的为准**。

🔴 背景：这条规则推翻了项目里三处旧逻辑
    ① 「数据核对」弹窗专门把材料识别出的日期挑出来让用户"确认"；
    ② 「数据核对」的工作流先后校验，把倒挂的日期挑出来问用户；
    ③ `quality_review_agent` 里**写死**的一套日期/年份改写
       （`12天→7天`、`2026年6月→2026年4月`、`2024/2025/2027年→2026年`）——
       那是给另一个项目写的，会把当前项目 6月/9月 的日期**自动改成 4 月**。
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
_QR = BACKEND / "app/agent/agents/quality_review_agent.py"
_FLOW = BACKEND / "app/services/report_workflow.py"


def test_quality_review_has_no_hardcoded_date_rewrite():
    """质量审核不得写死日期/年份改写（会把用户材料里的日期改掉）。"""
    src = _QR.read_text(encoding="utf-8")
    for banned in ("YEAR_REPLACEMENTS", "date_error", "应为2026"):
        assert banned not in src, f"`{banned}` 又回来了：它会把用户资料里的日期改掉"
    # 具体那几条写死的替换
    for banned in ("'12天'", "'2026年6月'", "4月13日至4月24日"):
        assert banned not in src, f"写死的日期替换 `{banned}` 又回来了"


def test_workflow_has_no_date_order_interrogation():
    """「数据核对」不得再对日期做先后校验并问用户。"""
    src = _FLOW.read_text(encoding="utf-8")
    assert "_date_pairs" not in src, "日期先后校验又回来了 —— 按规则不应再判断"
    assert "时间线倒挂" not in src


def test_material_dates_are_not_asked_back():
    """**核心**：材料里已经抽到的日期，不再出现在待问列表里。"""
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    import asyncio

    from app.services.report_workflow import node_data_inquiry

    filled = {
        "project_name": "某土地征收决策",
        "notice_date": "2026-06-03",
        "survey_date": "2026-06-18",
        "symposium_date": "2026-06-18",
    }

    g = StateGraph(dict)
    g.add_node("inquiry", node_data_inquiry)
    g.set_entry_point("inquiry")
    g.add_edge("inquiry", END)
    app = g.compile(checkpointer=MemorySaver())

    captured = {}

    async def _go():
        cfg = {"configurable": {"thread_id": "t_material_dates"}}
        async for ev in app.astream({"filled_data": dict(filled)}, cfg):
            if "__interrupt__" in ev:
                it = ev["__interrupt__"]
                payload = getattr(it[0], "value", {}) if isinstance(it, (list, tuple)) and it else {}
                captured.update(payload or {})

    asyncio.run(_go())

    # 🔴 先确认弹窗**确实触发了** —— 否则下面的断言会「因为什么都没问」而空转通过
    assert captured.get("type") == "data_inquiry", "弹窗没触发，本用例失去意义"
    fields = captured.get("missing_fields") or []
    assert fields, "弹窗触发了却没问任何字段"
    asked = {f.get("key") for f in fields}
    for k in ("notice_date", "survey_date", "symposium_date"):
        assert k not in asked, f"材料里已经有 {k}，不该再问用户"
    # 真正缺的字段仍然应该问（不能因为改了日期规则把整个弹窗也关掉）
    assert asked, "缺失字段一个都没问"


def test_missing_date_is_asked():
    """材料里没有 → 要问（由用户填，以其为准）。"""
    from app.services.report_workflow import node_data_inquiry  # noqa: F401  （导入即校验可导入）
    from app.agent.chapter_definitions import FIELD_META
    # `notice_date` 等日期字段在 FIELD_META 里有登记，弹窗才显示得出中文标签
    for k in ("notice_date", "survey_date", "symposium_date", "plan_date"):
        assert k in FIELD_META, f"{k} 未在 FIELD_META 登记，弹窗会显示成英文 key"


def test_frontend_skip_still_sends_sentinel():
    """跳过仍要送哨兵（空 resume 会让 LangGraph 死循环）。"""
    p = BACKEND.parent / "frontend/src/views/AssistantView.vue"
    if not p.exists():
        return
    body = p.read_text(encoding="utf-8")
    i = body.find("function skipMissing")
    assert i > 0 and "__skip__" in body[i:i + 900]
