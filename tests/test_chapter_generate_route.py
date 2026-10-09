# -*- coding: utf-8 -*-
"""「生成本章」按钮（POST /generate/{sid}/chapter/generate）回归护栏。

该端点的逐章生成路径曾**整条死掉**：它调用的 `orchestrator.generate_single_chapter(...)`
根本不存在（真实方法是带下划线的 `_generate_single_chapter`），而且把 **SSE 事件队列**
当成了第三个参数 —— 真实签名是
`_generate_single_chapter(ch_num, state, chapter_data_packages)`，
第三个参数是**本章数据包 dict（键=章号）**，事件则走**编排器自身的** `self._stream_queue`
（由 `run_full_pipeline` 挂载）。原实现还把方法返回的 **bool** 当成 state 覆盖了
`session.state`，等于一键把会话状态换成 True/False。

本测试直接调端点**函数**（不走 HTTP，故不经过依赖注入，须自传 `user`），
打桩 `report_service.get_session_for_user` 与 `ChapterOrchestrator._generate_single_chapter`，
断言：走的是真方法、第三参数是数据包 dict（非 Queue）、编排器队列已挂上且事件确实流出、
`session.state` 未被 bool 覆盖、章节内容就地写回。

不触网、不碰真实库。
"""
import asyncio


class _FakeSession:
    def __init__(self, state):
        self.state = state


class _FakeUser:
    id = 101
    role = "user"
    is_superuser = False
    is_active = True
    company_id = 1


_FAKE_USER = _FakeUser()


def _drain(response):
    """把 StreamingResponse 的 SSE 分片整段取出（借此驱动 event_generator）。"""
    async def _go():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks
    return asyncio.run(_go())


def _install_fake_gen(monkeypatch, seen, pkgs):
    """打桩真方法：记录收到的实参，并模拟「边跑边推事件 + 就地写回章节」。"""
    from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

    async def fake_gen(self, ch_num, state_arg, chapter_data_packages):
        seen["ch_num"] = ch_num
        seen["state_is_session_state"] = state_arg is pkgs["_state"]
        seen["third_arg"] = chapter_data_packages
        seen["queue_type"] = type(self._stream_queue).__name__
        await self._emit("chapter_start", {"chapter": ch_num})
        await self._emit("chapter_stream", {"chapter": ch_num, "content": "正文"})
        state_arg.setdefault("chapters", {})[ch_num] = {
            "markdown": "正文", "status": "approved",
        }
        return True

    monkeypatch.setattr(ChapterOrchestrator, "_generate_single_chapter", fake_gen)


def test_generate_chapter_calls_real_method_with_data_packages(monkeypatch):
    """三处关键断言：真方法名 / 第三参数是数据包 dict / 事件经编排器队列流出。"""
    from app.routers.report import report_service, generate_chapter

    state = {"chapters": {}, "_chapter_data_packages": {3: {"quality": "ok", "missing": []}}}
    monkeypatch.setattr(report_service, "get_session_for_user",
                        lambda sid, user: _FakeSession(state))

    seen = {}
    pkgs = {"_state": state}
    _install_fake_gen(monkeypatch, seen, pkgs)

    resp = asyncio.run(generate_chapter("fake-sid", {"chapter": 3}, _FAKE_USER))
    body = "".join(_drain(resp))

    # 1) 真方法被调用，第三参数是 state 里那份「本章数据包」dict —— 绝不是事件队列
    assert seen["ch_num"] == 3
    assert seen["state_is_session_state"] is True
    assert seen["third_arg"] is state["_chapter_data_packages"]
    assert isinstance(seen["third_arg"], dict)
    assert not isinstance(seen["third_arg"], asyncio.Queue)

    # 2) 编排器自身的 _stream_queue 已挂上，事件确实推到了 SSE 流
    assert seen["queue_type"] == "Queue"
    assert "event: chapter_start" in body
    assert "event: chapter_stream" in body

    # 3) session.state 没被方法返回的 bool 覆盖，章节内容就地写回
    assert isinstance(state, dict)
    assert state["chapters"][3]["status"] == "approved"


def test_generate_chapter_defaults_to_empty_packages_when_absent(monkeypatch):
    """会话尚无 `_chapter_data_packages` 时，第三参数回落空 dict（与重写调用点同源）。"""
    from app.routers.report import report_service, generate_chapter

    state = {"chapters": {}}  # 无 _chapter_data_packages
    monkeypatch.setattr(report_service, "get_session_for_user",
                        lambda sid, user: _FakeSession(state))

    seen = {}
    _install_fake_gen(monkeypatch, seen, {"_state": state})

    resp = asyncio.run(generate_chapter("fake-sid", {"chapter": 5}, _FAKE_USER))
    _drain(resp)

    assert seen["third_arg"] == {}
    assert isinstance(seen["third_arg"], dict)


def test_generate_chapter_surfaces_task_error(monkeypatch):
    """生成任务抛异常时，错误经 SSE `error` 事件吐给前端（不静默吞掉）。"""
    from app.routers.report import report_service, generate_chapter
    from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

    state = {"chapters": {}}
    monkeypatch.setattr(report_service, "get_session_for_user",
                        lambda sid, user: _FakeSession(state))

    async def boom(self, ch_num, state_arg, chapter_data_packages):
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(ChapterOrchestrator, "_generate_single_chapter", boom)

    resp = asyncio.run(generate_chapter("fake-sid", {"chapter": 1}, _FAKE_USER))
    body = "".join(_drain(resp))

    assert "event: error" in body
    assert "模型炸了" in body


def test_orchestrator_exposes_only_underscore_single_chapter():
    """名词护栏：真方法必是 `_generate_single_chapter`；无公开 `generate_single_chapter`。"""
    from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

    assert hasattr(ChapterOrchestrator, "_generate_single_chapter")
    assert not hasattr(ChapterOrchestrator, "generate_single_chapter")
