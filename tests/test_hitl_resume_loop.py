"""回归：HITL 弹窗「跳过」不能死循环。

🔴 事故（2026-09-22 端到端实测）
────────────────────────────────────────────────────────────────────────
用户点「跳过」→ **同一个弹窗无限重弹**，整条生成卡死。

排查绕了三层弯路（都记在下面，免得后人重走）：

  ① 以为「节点里没给跳过的字段打 `_confirmed_`」→ 补了标记，没用；
  ② 以为「中断时工作流状态没同步回 session」→ 补了同步，没用；
  ③ 以为「`interrupt()` 抛出时节点内的写入会丢」→ 改到 router 里打标记，还是没用。

**真正的原因在 LangGraph**：`Command(resume={})` —— **空的 resume 值**被它当成
「没有恢复值」，于是 resume 时 `interrupt()` **再次抛出**，节点永远走不到中断
之后的代码。离线最小复现：

    resume={}                  → ❌ 又中断，死循环
    resume={'x': 1}            → ✅ 正常继续
    resume={'__skip__': True}  → ✅ 正常继续

而前端「跳过」按钮送的**正是 `{}`**（`AssistantView.skipMissing`）。

① ② ③ 之所以「看起来没生效」，是因为**节点在 resume 时根本没往下跑** ——
中断之后的所有代码（包括打标记）从未执行。诊断时「代码写了但行为没变」时，
先确认那段代码到底有没有被执行，再去查它为什么没生效。
"""

from __future__ import annotations

import asyncio

import pytest


def _run_resume(resume_value, filled: dict):
    """在最小 LangGraph 图里跑一次「中断 → 用给定值 resume」。

    Returns: (是否又中断, 最终 filled_data)
    """
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from app.services.report_workflow import node_data_inquiry

    g = StateGraph(dict)
    g.add_node("inquiry", node_data_inquiry)
    g.set_entry_point("inquiry")
    g.add_edge("inquiry", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": f"t_{abs(hash(str(resume_value)))}"}}

    async def _go():
        async for _ in app.astream({"filled_data": dict(filled)}, cfg):
            pass
        again = False
        async for ev in app.astream(Command(resume=resume_value), cfg):
            if "__interrupt__" in ev:
                again = True
        final = (app.get_state(cfg).values or {}).get("filled_data") or {}
        return again, final

    return asyncio.run(_go())


def _state_with_missing_dates():
    """造一个**确实缺**日期字段的 state —— 「数据核对」弹窗会为它中断。

    🔴 以前这里造的是「材料已抽到、来源=material」的值，靠旧判据
       （`field_sources.unconfirmed()` = 有值且非用户填）把弹窗勾出来。
       那个判据已经改了：**材料抽到的值不再问用户**（业主：「资料里有公示日期
       就不要再弹窗问我」）。所以载体换成「真的没有值」——弹窗照旧会弹，
       这几条用例要验的「空 resume 会死循环」不受影响。
    """
    return {"project_name": "某土地征收决策"}


class TestEmptyResumeDoesNotLoop:
    """🔴 空 resume 值会死循环 —— 这是 LangGraph 的行为，只能靠**不送空值**绕开。"""

    def test_empty_resume_loops(self):
        """钉住病因：空字典 resume 必然再中断。LangGraph 若哪天修了，这条会红 —— 那是好事。"""
        again, _ = _run_resume({}, _state_with_missing_dates())
        assert again, ("LangGraph 对 `Command(resume={})` 的处理变了：现在不再死循环。"
                       "可以简化掉哨兵逻辑。")

    def test_sentinel_resume_continues(self):
        """✅ 哨兵值能正常恢复，而且**中断之后的标记代码真的会执行**。"""
        filled = _state_with_missing_dates()
        again, final = _run_resume({"__skip__": True}, filled)
        assert not again, "哨兵值仍然死循环"
        assert final.get("_confirmed_notice_date"), "跳过的字段没打上 `_confirmed_`"
        assert final.get("_confirmed_survey_date")

    def test_filled_resume_continues(self):
        """用户真填了值 —— 当然要能正常继续。"""
        again, final = _run_resume({"notice_date": "2026-06-04"}, _state_with_missing_dates())
        assert not again
        assert final.get("notice_date") == "2026-06-04"


class TestRouterGuardsEmptyResume:
    """调用方送空对象时，router 必须兜一个哨兵 —— 不能指望每个调用方都记得。"""

    def test_router_replaces_empty_with_sentinel(self):
        import inspect
        from app.routers import report as R
        src = inspect.getsource(R.resume_workflow)
        assert "__skip__" in src, "router 没有对空 resume 值兜底"
        assert "user_responses = {" in src or "user_responses={" in src

    def test_frontend_skip_sends_sentinel(self):
        """前端「跳过」按钮不能再送 `{}`。"""
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "frontend/src/views/AssistantView.vue"
        if not p.exists():
            pytest.skip("前端不在本仓库布局内")
        src = p.read_text(encoding="utf-8")
        i = src.find("function skipMissing")
        assert i > 0, "找不到 skipMissing"
        body = src[i:i + 900]
        assert "__skip__" in body, "skipMissing 仍送空对象，会触发 LangGraph 死循环"
