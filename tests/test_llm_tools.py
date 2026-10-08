"""LLM tools (function calling) tests — 请求体带 tools、响应解析 tool_calls。

Uses asyncio.run() directly (no async pytest plugin dependency).

🔴 这里要**同时**模拟两条传输路径：
    · 带 `tools` → 非流式 `client.post`（tool_calls 不适合流式拼碎片）；
    · 不带 `tools` → **流式** `client.stream` + `aiter_lines`（SSE）。
    非流式那条为什么改成流式，见 `llm_service._stream_choices` 的说明：
    httpx 的 `read` 超时对非流式响应是「整段生成」的上限（实测 180 秒），
    长章节会被整段掐断重来。
"""

import asyncio
import json
import httpx
from unittest.mock import patch


# ═══════════════════════════════════════════════════════════════
# 假 httpx：post（非流式） + stream（SSE）
# ═══════════════════════════════════════════════════════════════

def _payload_for(body: dict) -> dict:
    """按请求体决定假响应：带 tools 回 tool_calls，否则回普通正文。"""
    if body.get("tools"):
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "retrieve_rag",
                             "arguments": '{"query": "土地管理法"}'},
            }],
        }}]}
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": "这是正文", "reasoning_content": ""}}]}


class _FakeResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    @property
    def text(self):
        return json.dumps(self._data)


class _FakeStreamResponse:
    """把同一份 `data` 摊成 SSE 行，模拟 `resp.aiter_lines()`。"""
    status_code = 200

    def __init__(self, data):
        self._data = data

    async def aread(self):
        return b""

    async def aiter_lines(self):
        ch = (self._data.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        if msg.get("reasoning_content"):
            yield "data: " + json.dumps(
                {"choices": [{"delta": {"reasoning_content": msg["reasoning_content"]}}]})
        if msg.get("content"):
            yield "data: " + json.dumps(
                {"choices": [{"delta": {"content": msg["content"]}}]})
        yield "data: " + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": ch.get("finish_reason") or "stop"}]})
        yield "data: [DONE]"


class _FakeStreamCtx:
    def __init__(self, data):
        self._resp = _FakeStreamResponse(data)

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


TOOLS = [{
    "type": "function",
    "function": {"name": "retrieve_rag", "description": "RAG检索",
                 "parameters": {"type": "object"}},
}]


def _make_client(captured: dict):
    """一个既能当非流式、也能当流式的假 AsyncClient。"""
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            captured["body"] = kw.get("json", {})
            return _FakeResponse(_payload_for(captured["body"]))

        def stream(self, method, url, **kw):
            captured["body"] = kw.get("json", {})
            return _FakeStreamCtx(_payload_for(captured["body"]))

    return FakeClient


def _run_chat(tools=None):
    from app.services import llm_service as ls
    svc = ls.LLMService()
    svc.api_key = "test"                                  # is_available = True
    svc.base_url = "https://api.deepseek.com"             # 关思考开关的 provider 门禁
    captured = {}
    with patch.object(httpx, "AsyncClient", _make_client(captured)):
        res = asyncio.run(svc.chat_with_reasoning(
            messages=[{"role": "user", "content": "hi"}],
            system="sys", tools=tools,
        ))
    return res, captured.get("body", {})


# ═══════════════════════════════════════════════════════════════
# 用例
# ═══════════════════════════════════════════════════════════════

def test_request_body_contains_tools():
    _, body = _run_chat(TOOLS)
    assert "tools" in body
    assert body["tools"] == TOOLS


def test_response_parses_tool_calls():
    res, _ = _run_chat(TOOLS)
    assert res.get("tool_calls") == [{"name": "retrieve_rag",
                                      "arguments": {"query": "土地管理法"}}]


def test_no_tools_no_tools_field():
    """无 tools 时请求体不含 tools 字段。"""
    _, body = _run_chat(None)
    assert "tools" not in body


def test_plain_content_when_no_tool_call():
    """模型普通回复时返回 content，无 tool_calls。"""
    res, _ = _run_chat(None)
    assert res["content"] == "这是正文"
    assert res.get("tool_calls") == []


def test_streaming_path_requests_stream_flag():
    """无 tools 的请求**必须**带 `stream: true`。

    漏了这一个字段，服务端会按普通请求返回一整个 JSON（不是 SSE）——
    `aiter_lines` 只吐出一行、且不以 `data: ` 开头，content 恒为空。实测踩过。
    """
    _, body = _run_chat(None)
    assert body.get("stream") is True


def test_non_streaming_path_when_tools_present():
    """带 tools 时**不**走流式（tool_calls 流式要按 index 拼碎片，复杂且易错）。"""
    _, body = _run_chat(TOOLS)
    assert "stream" not in body


# ═══════════════════════════════════════════════════════════════
# 推理模型保护：空转 vs 真截断，处理方式**相反**
# ═══════════════════════════════════════════════════════════════

def _fake_with_sequence(payloads: list, captured: dict):
    """每次请求依次吐 `payloads` 里的一项；用尽后重复最后一项。"""
    state = {"i": 0}

    def _next_payload(body):
        i = min(state["i"], len(payloads) - 1)
        state["i"] += 1
        captured.setdefault("calls", []).append(dict(body))
        return payloads[i]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, **kw):
            return _FakeStreamCtx(_next_payload(kw.get("json", {})))

        async def post(self, url, **kw):
            return _FakeResponse(_next_payload(kw.get("json", {})))

    return FakeClient


def _run_seq(payloads, max_tokens=8000):
    from app.services import llm_service as ls
    svc = ls.LLMService()
    svc.api_key = "test"
    svc.base_url = "https://api.deepseek.com"
    captured = {}
    with patch.object(httpx, "AsyncClient", _fake_with_sequence(payloads, captured)):
        res = asyncio.run(svc.chat_with_reasoning(
            messages=[{"role": "user", "content": "hi"}], max_tokens=max_tokens))
    return res, captured


_EMPTY_LEN = {"choices": [{"finish_reason": "length",
                           "message": {"content": "", "reasoning_content": "想了很多但没写"}}]}
_OK = {"choices": [{"finish_reason": "stop",
                    "message": {"content": "正文" * 60, "reasoning_content": ""}}]}


def test_idle_spin_retries_without_growing_budget():
    """🔴 **空转**（content=0 且 finish=length）→ 关掉思考重试，**预算不变**。

    放大预算只会更糟：推理预算更大 → 更容易烧光 → 再叠上读超时，整段白跑。
    实测第6章就是这么烧掉 897 秒（三次调用），评审表那 222 秒里也有两次。
    """
    _, cap = _run_seq([_EMPTY_LEN, _OK], max_tokens=1500)
    assert len(cap["calls"]) == 2, "空转后应当只重试一次"
    assert cap["calls"][0].get("thinking") is None, "第一次不该关思考"
    assert cap["calls"][1].get("thinking") == {"type": "disabled"}, "重试必须关思考"
    assert cap["calls"][1]["max_tokens"] == 1500, "空转重试**不该**放大预算"


def test_real_truncation_grows_budget():
    """**真截断**（有正文但没写完）→ 才放大预算。"""
    partial = {"choices": [{"finish_reason": "length",
                            "message": {"content": "写了半截的正文" * 20,
                                        "reasoning_content": ""}}]}
    _, cap = _run_seq([partial, _OK], max_tokens=8000)
    assert len(cap["calls"]) == 2
    assert cap["calls"][1]["max_tokens"] == 16000, "真截断应当放大预算"
    assert cap["calls"][1].get("thinking") is None, "真截断不该关思考"


def test_idle_spin_does_not_recurse_forever():
    """关过思考还是空 → 不再递归，交调用方重试（避免死循环）。"""
    res, cap = _run_seq([_EMPTY_LEN], max_tokens=1500)
    assert len(cap["calls"]) == 2, f"最多重试一次，实际 {len(cap['calls'])} 次"
    assert res["content"] == ""


def test_unknown_provider_falls_back_to_growing_budget():
    """provider 不支持关思考时，空转**退回放大预算**（而不是拿同样参数再跑一遍）。"""
    from app.services import llm_service as ls
    svc = ls.LLMService()
    svc.api_key = "test"
    svc.base_url = "https://api.unknown-vendor.example/v1"
    captured = {}
    with patch.object(httpx, "AsyncClient", _fake_with_sequence([_EMPTY_LEN, _OK], captured)):
        asyncio.run(svc.chat_with_reasoning(
            messages=[{"role": "user", "content": "hi"}], max_tokens=1500))
    assert len(captured["calls"]) == 2
    assert "thinking" not in captured["calls"][1], "未知 provider 不该发 thinking 字段"
    assert captured["calls"][1]["max_tokens"] == 16000, "应退回放大预算"
