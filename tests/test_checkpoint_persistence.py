# -*- coding: utf-8 -*-
"""HITL 暂停点持久化：服务重启后 resume 还能接着跑。

🔴 线上事故（2026-11-02）：复垦生成中途弹窗，用户点「跳过」后整单失败 ——
   `InvalidUpdateError: Must write to at least one of ['__root__']`。
根因：检查点只在 `MemorySaver`（进程内存），部署/重启后 thread 不存在了。
本用例用最小图模拟「暂停 → 进程重启 → 新 runner resume」。
"""

from __future__ import annotations

import asyncio


def _tiny_interrupt_graph():
    """一个只会中断一次的最小图（不跑 LLM/文件解析）。"""
    from langgraph.graph import StateGraph, END
    from langgraph.types import interrupt

    def node(state):
        val = interrupt({"ask": "x"})
        state["answer"] = val
        return state

    g = StateGraph(dict)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g


def test_sqlite_checkpoint_resumes_after_restart(tmp_path):
    from app.services.report_workflow import ReportWorkflowRunner

    db = tmp_path / "ckpt.db"
    cfg = {"configurable": {"thread_id": "t_restart"}}

    async def go():
        # 第一次进程：跑到 interrupt
        r1 = ReportWorkflowRunner()
        r1.workflow = _tiny_interrupt_graph()
        assert await r1.use_sqlite_checkpoints(str(db))
        seen_interrupt = False
        async for ev in r1.compiled.astream({"a": 1}, cfg):
            if "__interrupt__" in ev:
                seen_interrupt = True
                break
        await r1.close()
        assert seen_interrupt, "图没有在预期处中断"

        # 模拟服务重启：全新 runner + 同一份 SQLite 文件
        from langgraph.types import Command
        r2 = ReportWorkflowRunner()
        r2.workflow = _tiny_interrupt_graph()
        assert await r2.use_sqlite_checkpoints(str(db))
        final = None
        async for ev in r2.compiled.astream(Command(resume={"x": 2}), cfg):
            if "n" in ev:
                final = ev["n"]
        await r2.close()
        return final

    final = asyncio.run(go())
    assert final and final.get("answer") == {"x": 2}


def test_sqlite_checkpoint_falls_back_to_memory(tmp_path):
    """初始化失败不能把服务带崩 —— 回落内存并返回 False。"""
    from app.services.report_workflow import ReportWorkflowRunner

    r = ReportWorkflowRunner()
    ok = asyncio.run(r.use_sqlite_checkpoints(str(tmp_path / "no_such_dir" / "ck.db")))
    assert ok is False
    assert r.compiled is not None
