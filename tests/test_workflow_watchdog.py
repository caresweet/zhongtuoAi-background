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


# ════════════════════════════════════════════════════════
# 缺口草稿必须**写进正文**，不是只存兜底区
# ════════════════════════════════════════════════════════
# 业主 2026-10-29：「本次生成有 13 处待补充/待核对 —— 这个在生成的时候改不了么？」
# 原因是 AI 拟好的替代句只进了事实卡兜底区，正文那句「以…为准」原样留着。

def test_gap_drafts_replace_hedge_sentences_in_body():
    from app.services.report_workflow import _apply_gap_drafts

    chapters = {6: {"markdown": "第一句。以征收补偿安置方案为准。第三句。"}}
    pending = [
        ("_gap_6_0", 6, {"sentence": "以征收补偿安置方案为准。"}),
        ("_gap_6_1", 6, {"sentence": "这句正文里没有。"}),
    ]
    drafts = {"_gap_6_0": "补偿标准按洪政规〔2026〕1号执行。", "_gap_6_1": "无关草稿"}
    applied, left, resolved = _apply_gap_drafts(chapters, pending, drafts)
    assert (applied, left) == (1, 1)
    assert resolved == {"_gap_6_0"}
    assert "以征收补偿安置方案为准" not in chapters[6]["markdown"]
    assert "洪政规〔2026〕1号" in chapters[6]["markdown"]
