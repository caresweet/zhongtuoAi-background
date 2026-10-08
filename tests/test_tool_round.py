"""Chapter agent tool-call round tests — tool results injected into prompt before generation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.agents.chapters.chapter_base import ChapterAgentBase


class _DummyLLM:
    """Mock LLM: first call returns a tool_call (calc_risk_scoring), subsequent return text."""

    def __init__(self):
        self.call_count = 0

    async def chat_with_reasoning(self, **kwargs):
        self.call_count += 1
        if "tools" in kwargs and kwargs["tools"]:
            return {
                "content": "",
                "reasoning": "",
                "tool_calls": [
                    {"name": "calc_risk_scoring", "arguments": {"support_rate": 95}}
                ],
            }
        return {"content": "正文内容", "reasoning": "", "tool_calls": []}


class _DummyAgent(ChapterAgentBase):
    name = "dummy"
    description = "dummy"
    covered_steps = []
    chapter_number = 6
    chapter_title = "措施前风险等级研判"


def _make_agent():
    llm = _DummyLLM()
    agent = _DummyAgent(llm_service=llm)
    return agent, llm


def test_tool_round_returns_scoring_result():
    """工具回合调用评分工具，返回真实评分文本（不依赖网络）。"""
    agent, llm = _make_agent()
    with patch.object(agent, "_emit_thinking", AsyncMock()):
        state = {"filled_data": {"support_rate": "95"}, "session_id": "t"}
        results = asyncio.run(agent._maybe_run_tool_round(
            state, user_prompt="写正文", system_prompt="sys"
        ))
    assert results, "应返回工具结果"
    joined = "".join(results)
    assert "扣分" in joined or "pre_total" in joined or "风险等级" in joined


def test_tool_round_empty_when_llm_returns_no_tools():
    """LLM 不调工具时返回空列表（直接走正文生成）。"""

    class NoToolLLM:
        async def chat_with_reasoning(self, **kwargs):
            return {"content": "无需工具", "reasoning": "", "tool_calls": []}

    agent = _DummyAgent(llm_service=NoToolLLM())
    with patch.object(agent, "_emit_thinking", AsyncMock()):
        state = {"filled_data": {}, "session_id": "t"}
        results = asyncio.run(agent._maybe_run_tool_round(
            state, user_prompt="写正文", system_prompt="sys"
        ))
    assert results == []


def test_tool_round_disabled_by_switch():
    """开关关闭时不执行工具回合。"""

    class FailingLLM:
        async def chat_with_reasoning(self, **kwargs):
            raise AssertionError("不应调用 LLM")

    agent = _DummyAgent(llm_service=FailingLLM())
    with patch("app.config.settings.ENABLE_AGENT_TOOLCALL", False):
        with patch.object(agent, "_emit_thinking", AsyncMock()):
            state = {"filled_data": {}, "session_id": "t"}
            results = asyncio.run(agent._maybe_run_tool_round(
                state, user_prompt="写正文", system_prompt="sys"
            ))
    assert results == []


def test_act_injects_tool_results_into_prompt():
    """act() 走工具回合后，工具结果注入 user_prompt，再生成正文。"""
    # 捕获传给 LLM 的 user_prompt，断言含工具补充数据标记
    captured = {}

    class CaptureLLM:
        async def chat_with_reasoning(self, **kwargs):
            msgs = kwargs.get("messages", [])
            captured["user_text"] = msgs[0]["content"] if msgs else ""
            return {"content": "第6章正文内容较长……" * 20, "reasoning": "", "tool_calls": []}

    agent = _DummyAgent(llm_service=CaptureLLM())
    agent._stream_queue = None

    # 直接 patch _maybe_run_tool_round 返回固定工具结果，验证注入逻辑
    with patch.object(agent, "_maybe_run_tool_round", AsyncMock(return_value=["【评分】措施前扣分合计 12 分"])), \
         patch.object(agent, "_emit_chapter_start", AsyncMock()), \
         patch.object(agent, "_emit_thinking", AsyncMock()), \
         patch.object(agent, "_build_llm_prompt",
                      MagicMock(return_value=("sys", "写正文：需要评分"))):
        state = {"filled_data": {"support_rate": "95"}, "session_id": "t"}
        # 直接调 act()（跳过 run() 的 think/validate 编排）
        result = asyncio.run(agent.act(state, {"missing_data": [], "rag_context": {},
                                               "user_data": {}}))

    assert result.get("status") == "generated"
    assert "工具补充数据" in captured["user_text"], "工具结果应注入 user_prompt"
    assert "【评分】措施前扣分合计 12 分" in captured["user_text"]