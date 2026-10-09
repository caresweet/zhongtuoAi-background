"""三个调用点都必须把 state 里的 _domain 传给章节工厂。

源码级断言（inspect.getsource）比反射更稳：这两处是异步编排链路，
构造完整 state 成本很高，而回归风险恰恰在「忘了传」——
写死的 "_domain": "stability" 只要还剩一处，源码断言就会红。
"""
import inspect

from app.agent.agents import chapter_orchestrator
from app.services import report_workflow


def test_orchestrator_passes_domain():
    src = inspect.getsource(chapter_orchestrator)
    # 两处调用都要带 domain=
    assert src.count("get_chapter_agent(") >= 2
    assert "domain=" in src


def test_workflow_passes_domain_and_does_not_hardcode_stability():
    src = inspect.getsource(report_workflow)
    assert "get_chapter_agent(" in src
    assert "domain=" in src
    # 写死的 "_domain": "stability" 必须消失（改由 state 决定）
    assert '"_domain": "stability"' not in src


class _FakeCompiled:
    """假图：只截获交给 LangGraph 的起点状态，不真的跑节点（不触网、不调 LLM）。

    回归靶心是「起点把域带上了没有」——`start()` 里那段从 existing_state 拷贝
    手挑 key 的循环曾漏掉 `_domain`/`_conversation_domain`，于是整条工作流按
    `domain_of_state(state)` 取域时一律回落 "stability"：复垦会话顶着稳评 14 章
    大纲跑（第1章「编制说明」而非复垦的「前言」）。用假图把起点原样留下断言。
    """
    def __init__(self):
        self.seen_base = None

    async def astream(self, base, config):
        self.seen_base = base
        if False:  # pragma: no cover —— 空异步生成器（不产出任何事件）
            yield {}


def test_start_carries_domain_into_graph_state():
    """复垦会话的 _domain / _conversation_domain 必须进入图起点状态。"""
    import asyncio
    from app.services.report_workflow import ReportWorkflowRunner

    runner = ReportWorkflowRunner()
    fake = _FakeCompiled()
    runner.compiled = fake  # 短路真实图，只验状态构建

    session_state = {"_domain": "reclamation", "_conversation_domain": "reclamation",
                     "_pdf_raw_text": "x", "filled_data": {"project_name": "甲"}}
    out = asyncio.run(runner.start("sid", existing_state=session_state))

    assert fake.seen_base["_domain"] == "reclamation"
    assert fake.seen_base["_conversation_domain"] == "reclamation"
    assert out["_domain"] == "reclamation"
    # 顺带确认：图起点取域结果确实是复垦 —— 这正是 outline/章节工厂实际读到的值
    from app.domains import domain_of_state
    assert domain_of_state(out) == "reclamation"

