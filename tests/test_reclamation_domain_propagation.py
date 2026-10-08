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
