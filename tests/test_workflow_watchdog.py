"""生成任务的僵尸/卡死兜底（2026-10-29 线上事故）。

现象：用户在线上点「开始生成」后界面一直转圈，15 小时后只能手动取消。
根因：后台生成任务是 asyncio 任务，**服务重启/异常退出后就没了**，但会话状态里
`_workflow_running` 还是 True —— `/workflow/status` 一直回「生成中」，前端无限轮询。

修法：
1. `_has_live_bg_task()` 判断本进程里还有没有存活任务；没有就判中断（置 error）；
2. 心跳 `_workflow_heartbeat`：任务活着但 30 分钟没任何进度 → 也判卡死。
"""

import asyncio


class _FakeSession:
    def __init__(self, state):
        self.state = state


def test_no_bg_tasks_means_dead():
    from app.routers.report import _has_live_bg_task

    assert _has_live_bg_task(_FakeSession({})) is False
    assert _has_live_bg_task(_FakeSession({"_bg_tasks": []})) is False


def test_finished_task_is_dead():
    from app.routers.report import _has_live_bg_task

    async def _run():
        t = asyncio.create_task(asyncio.sleep(0))
        await t
        return t

    done_task = asyncio.run(_run())
    assert _has_live_bg_task(_FakeSession({"_bg_tasks": [done_task]})) is False


def test_live_task_is_alive():
    from app.routers.report import _has_live_bg_task

    async def _run():
        t = asyncio.create_task(asyncio.sleep(5))
        alive = _has_live_bg_task(_FakeSession({"_bg_tasks": [t]}))
        t.cancel()
        return alive

    assert asyncio.run(_run()) is True
