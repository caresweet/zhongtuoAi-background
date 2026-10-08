"""Unified LLM service — wraps OpenAI-compatible API (DashScope/DeepSeek/etc.).

Abstracts away SDK differences and provides a consistent interface for
both streaming and non-streaming LLM calls used throughout the agent.

Includes:
- Exponential backoff retry (3 attempts, 1s→2s→4s)
- Circuit breaker (opens after 5 consecutive failures, resets after 30s)
- Proper httpx.AsyncClient lifecycle management
"""

import os
import json
import asyncio
import time
import logging
from typing import List, Dict, Any, Optional, AsyncIterator
import httpx

logger = logging.getLogger(__name__)

# ── Retry / Circuit Breaker Configuration ──────────────────────────────────
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0       # seconds — doubles each retry
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_CIRCUIT_BREAKER_THRESHOLD = 5   # consecutive failures before opening
_CIRCUIT_BREAKER_COOLDOWN = 30.0  # seconds before half-open attempt


class LLMService:
    """Unified LLM client supporting OpenAI-compatible APIs.

    Configured via environment/.env:
    - LLM_BASE_URL: API base URL (default: DashScope)
    - LLM_API_KEY: API key
    - LLM_MODEL: Model name (default: qwen-max / Qwen3-Max)
    """

    def __init__(self):
        from app.config import settings

        self.api_key = settings.ANTHROPIC_API_KEY

        # Use LLM_MODEL from .env, default to deepseek-chat
        import os as _os
        self.model = _os.environ.get("FINETUNED_MODEL") or settings.LLM_MODEL or "deepseek-chat"

        # Base URL from config, fallback to DeepSeek official API
        base_url = settings.LLM_BASE_URL or settings.ANTHROPIC_BASE_URL or ""
        if not base_url:
            base_url = "https://api.deepseek.com/v1"

        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/anthropic"):
            self.base_url = self.base_url[:-9]

        self.timeout = httpx.Timeout(300.0, connect=15.0, read=180.0)

        # 🔴 **流式**请求专用超时：`read` 在流式下的含义是「两个 chunk 之间的间隔」，
        #    不再是「整段生成」的上限 —— 只要模型持续吐字就不会被掐断。
        #    所以这里刻意**不设总时长上限**（第一个参数传 None），
        #    真正的上限交给业务侧的外层 `asyncio.wait_for`。
        #    见 `chat_with_reasoning` / `_stream_choices` 的说明。
        self.timeout_stream = httpx.Timeout(None, connect=15.0, read=180.0,
                                            write=30.0, pool=15.0)

        # 🔴 Per-request client limits (more reliable under parallel load)
        self.limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)

        # ── Circuit breaker state ──
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._circuit_open = False

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def _supports_thinking_toggle(self) -> bool:
        """本 provider 是否支持 `thinking={"type":"disabled"}` 关思考。

        🔴 只在**实测过**的 provider 上返回 True。发一个对方不认的字段，
           多半直接 400 —— 那是非重试错误，会砸在「空转恢复」这条路上
           （越需要兜底越炸）。未知 provider 返回 False，调用方退化为放大预算。
        """
        return "deepseek" in (self.base_url or "").lower()

    # ── Circuit breaker helpers ──────────────────────────────────────────

    def _check_circuit(self):
        """Raise if circuit breaker is open (too many consecutive failures)."""
        if self._circuit_open:
            elapsed = time.time() - self._last_failure_time
            if elapsed > _CIRCUIT_BREAKER_COOLDOWN:
                # Half-open: allow one request through
                self._circuit_open = False
                logger.info("LLM circuit breaker: half-open, allowing probe request")
            else:
                raise RuntimeError(
                    f"LLM circuit breaker OPEN — too many failures, "
                    f"retry in {_CIRCUIT_BREAKER_COOLDOWN - elapsed:.0f}s"
                )

    def _record_success(self):
        """Reset failure count on success — circuit stays closed."""
        if self._failure_count > 0 or self._circuit_open:
            logger.info(f"LLM circuit breaker: reset after success ({self._failure_count} prior failures)")
        self._failure_count = 0
        self._circuit_open = False

    def _record_failure(self, status_code: int = 0):
        """Increment failure count; open circuit if threshold reached."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= _CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open = True
            logger.error(
                f"LLM circuit breaker OPEN after {self._failure_count} consecutive failures"
            )

    def _is_retryable(self, status_code: int, exc: Exception = None) -> bool:
        """Determine if an error is transient and worth retrying."""
        if status_code in _RETRYABLE_STATUSES:
            return True
        if status_code == 0:
            # Network-level error (timeout, connection reset, DNS)
            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                                httpx.RemoteProtocolError, httpx.NetworkError)):
                return True
        return False

    # ── Retry wrapper ────────────────────────────────────────────────────

    async def _retry_request(self, request_fn, label: str = "LLM") -> dict:
        """Execute request_fn() with exponential backoff retry + circuit breaker.

        Args:
            request_fn: Async callable that returns httpx.Response.
            label: Human-readable label for log messages.

        Returns:
            Parsed JSON response dict.

        Raises:
            RuntimeError: After exhausting retries or circuit breaker open.
        """
        last_error = None
        for attempt in range(_MAX_RETRIES):
            self._check_circuit()
            try:
                response = await request_fn()
                if response.status_code == 200:
                    self._record_success()
                    return response.json()
                elif self._is_retryable(response.status_code):
                    self._record_failure(response.status_code)
                    last_error = RuntimeError(
                        f"{label} API error {response.status_code}: {response.text[:300]}"
                    )
                else:
                    # Non-retryable (400, 401, 403, etc.) — fail fast
                    raise RuntimeError(
                        f"{label} API error {response.status_code}: {response.text[:500]}"
                    )
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.NetworkError) as e:
                self._record_failure()
                last_error = RuntimeError(f"{label} network error: {e}")
            except RuntimeError:
                raise  # Re-raise non-retryable errors immediately

            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"{label} attempt {attempt + 1}/{_MAX_RETRIES} failed, "
                    f"retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)

        raise last_error or RuntimeError(f"{label} request failed after {_MAX_RETRIES} attempts")

    # ── Chat API ─────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        response_format: Optional[Dict[str, str]] = None,
        enable_search: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Non-streaming chat completion.

        Args:
            messages: List of {"role": "user|assistant", "content": "..."}.
            system: Optional system prompt.
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
            response_format: Optional {"type": "json_object"} for JSON mode.
            enable_search: Enable DashScope web search for real-time info.
            tools: Optional OpenAI-format tool definitions (function calling).

        Returns:
            Generated text content.
        """
        result = await self.chat_with_reasoning(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            enable_search=enable_search,
            tools=tools,
        )
        return result.get("content", "")

    async def _stream_choices(self, body: dict) -> list:
        """流式发请求，把增量**累加**回 `choices[0].message` 的形状。

        🔴 为什么从非流式改成流式
            httpx 的 `read` 超时对**非流式**响应而言，是「整段生成」的上限 ——
            实测 `read=180.0`，于是任何超过 180 秒的章节生成都会被 `ReadTimeout`
            掐断、整段重来；而外层 `asyncio.wait_for(timeout=600.0)` 根本轮不到生效
            （内层先生效）。两层预算自相矛盾，实际按 180 秒执行。
            改成流式后 `read` 变成「两个 chunk 之间的间隔」上限 ——
            只要模型持续吐字就不会断，真正的上限回到业务侧的外层 `wait_for`。
        """
        last_error = None
        # 🔴 `stream` 必须在这里补上：本函数的 body 由 `chat_with_reasoning` 构造，
        #    那份 body 是**非流式**的。漏了这一个字段，服务端会按普通请求返回
        #    一整个 JSON（不是 SSE）——`aiter_lines` 只吐出一行、且不以 `data: ` 开头，
        #    结果 content 恒为空。实测踩过。
        body = dict(body, stream=True)
        for attempt in range(_MAX_RETRIES):
            self._check_circuit()
            try:
                parts_c: List[str] = []
                parts_r: List[str] = []
                finish = None
                async with httpx.AsyncClient(timeout=self.timeout_stream,
                                             limits=self.limits) as client:
                    async with client.stream(
                        "POST", f"{self.base_url}/chat/completions",
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {self.api_key}"},
                        json=body,
                    ) as resp:
                        if resp.status_code != 200:
                            raw = await resp.aread()
                            msg = (f"LLM API error {resp.status_code}: "
                                   f"{raw[:500].decode('utf-8', 'replace')}")
                            if not self._is_retryable(resp.status_code):
                                raise RuntimeError(msg)   # 400/401/403 → 立刻失败
                            self._record_failure(resp.status_code)
                            last_error = RuntimeError(msg)
                        else:
                            self._record_success()
                            async for line in resp.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                s = line[6:].strip()
                                if s == "[DONE]":
                                    break
                                try:
                                    d = json.loads(s)
                                except json.JSONDecodeError:
                                    continue
                                chs = d.get("choices") or []
                                if not chs:
                                    continue
                                ch = chs[0]
                                if ch.get("finish_reason"):
                                    finish = ch["finish_reason"]
                                delta = ch.get("delta") or {}
                                if delta.get("reasoning_content"):
                                    parts_r.append(delta["reasoning_content"])
                                if delta.get("content"):
                                    parts_c.append(delta["content"])
                            return [{
                                "finish_reason": finish,
                                "message": {"content": "".join(parts_c),
                                            "reasoning_content": "".join(parts_r)},
                            }]
            except RuntimeError:
                raise                                  # 非重试类错误直接上抛
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.NetworkError) as e:
                self._record_failure()
                last_error = RuntimeError(f"LLM 流式网络错误: {e}")

            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(f"LLM 流式 attempt {attempt + 1}/{_MAX_RETRIES} 失败，"
                               f"{delay:.1f}s 后重试")
                await asyncio.sleep(delay)

        raise last_error or RuntimeError(f"LLM 流式请求失败（{_MAX_RETRIES} 次）")

    async def chat_with_reasoning(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        response_format: Optional[Dict[str, str]] = None,
        enable_search: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        disable_thinking: bool = False,
    ) -> Dict[str, Any]:
        """Chat that captures both reasoning and content（**流式**发、整段返回）。

        DeepSeek / Qwen 等推理模型会返回 `reasoning_content`；本方法把两路增量
        分别累加后一次性返回，调用方拿到的形状与非流式一致。

        When `tools` is provided (OpenAI-compatible function calling), the
        response may include `tool_calls` instead of final content. The returned
        dict then contains `tool_calls`:
            {"content": str, "reasoning": str, "tool_calls": [{"name", "arguments(dict)"}, ...]}

        Args:
            enable_search: Enable DashScope built-in web search for Qwen models.
            tools: Optional list of OpenAI tool definitions (function calling).
            disable_thinking: 关闭模型的思考链（仅**空转重试**路径使用，见下）。
                实测 `thinking={"type":"disabled"}` 让同一句话的 reasoning 从
                249 字降到 0 字、用量 153→24 token。

        Returns:
            Dict with keys: "content" / "reasoning" / "tool_calls"
        """
        if not self.is_available:
            return {"content": "", "reasoning": "", "tool_calls": []}

        # Build the messages list with system prompt first
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        body = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            body["response_format"] = response_format
        if tools:
            body["tools"] = tools
        if enable_search and "dashscope" in self.base_url.lower():
            body["enable_search"] = True
            body["search_options"] = {"search_strategy": "standard"}
        # 🔴 关思考：`enable_thinking=False` 实测**无效**（reasoning 只从 249 字降到
        #    206 字），`reasoning_effort=low` 只降到 177 字；只有这个形式真正清零。
        #    ⚠️ 只在**实测支持**的 provider 上发：未知 provider 可能直接 400，
        #       而非 200 是非重试错误 —— 会砸在恢复路径上（越需要兜底越炸）。
        if disable_thinking and self._supports_thinking_toggle():
            body["thinking"] = {"type": "disabled"}

        if tools:
            # 🔴 带 tools 时仍走非流式：流式 tool_calls 要按 index 拼碎片，复杂且易错，
            #    而工具调用的回复本来就很短，不存在 180 秒读超时的问题。
            async def _do_request():
                async with httpx.AsyncClient(timeout=self.timeout,
                                             limits=self.limits) as client:
                    return await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {self.api_key}"},
                        json=body,
                    )

            data = await self._retry_request(_do_request, label="LLM")
            choices = data.get("choices", [])
        else:
            choices = await self._stream_choices(body)

        if not choices:
            return {"content": "", "reasoning": "", "tool_calls": []}
        message = choices[0].get("message", {})

        # 🔴 推理模型保护 —— 分两种情况，处理方式**相反**：
        #
        #   ① **模型空转**：`finish_reason=length` 但 `content` 一个字都没有。
        #      预算全烧在推理上。这时**放大预算只会更糟** —— 推理预算越大越容易烧光，
        #      再叠上读超时，整段白跑。实测第6章就是这么烧掉 897 秒（三次调用）。
        #      正解是**关掉思考**、预算保持不变重试。
        #
        #   ② **真被截断**：有正文但没写完（曾出现第2章只产出 582 字节、句中断开）。
        #      这时才应该放大预算重试。
        _content = str(message.get("content") or "").strip()
        _reasoning = str(message.get("reasoning_content") or "")
        _finish = choices[0].get("finish_reason")
        _BUDGET_CAP = 32000
        if not tools and _finish == "length" and max_tokens < _BUDGET_CAP:
            _can_toggle = self._supports_thinking_toggle()
            if not _content and not disable_thinking and _can_toggle:
                logger.warning(
                    f"LLM 空转（content=0 字，reasoning={len(_reasoning)} 字，"
                    f"max_tokens={max_tokens}）→ **关闭思考**重试（预算不变）")
                return await self.chat_with_reasoning(
                    messages=messages, system=system, max_tokens=max_tokens,
                    temperature=temperature, response_format=response_format,
                    enable_search=enable_search, tools=tools,
                    disable_thinking=True,
                )
            if _content or (not _content and not _can_toggle):
                # ① 真被截断（有正文没写完）→ 放大预算；
                # ② 空转但本 provider 不支持关思考 → 退回旧策略（放大预算），
                #    总比拿同样的参数再跑一遍强。
                _new_budget = min(max(max_tokens * 2, 16000), _BUDGET_CAP)
                logger.warning(
                    f"LLM 输出被截断（finish_reason=length，max_tokens={max_tokens}，"
                    f"content={len(_content)} 字）→ 放大预算至 {_new_budget} 重试")
                return await self.chat_with_reasoning(
                    messages=messages, system=system,
                    max_tokens=_new_budget,
                    temperature=temperature, response_format=response_format,
                    enable_search=enable_search, tools=tools,
                )
            # 关过思考还是空 → 不再递归（避免死循环），交调用方重试

        # 🔴 解析 tool_calls（OpenAI 兼容 function calling）
        tool_calls = []
        raw_tool_calls = message.get("tool_calls") or []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({"name": name, "arguments": args})

        return {
            "content": message.get("content", ""),
            "reasoning": message.get("reasoning_content", ""),
            "tool_calls": tool_calls,
        }

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        enable_search: bool = False,
    ) -> AsyncIterator[Dict[str, str]]:
        """Streaming chat completion — yields typed deltas.

        Each yielded dict has:
            {"type": "reasoning" | "content", "delta": "text chunk"}

        DeepSeek-R1 and similar models stream reasoning_content first,
        then content. This method preserves both streams separately
        so the frontend can display reasoning as a collapsible block.

        Args:
            messages: List of {"role": "user|assistant", "content": "..."}.
            system: Optional system prompt.
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
            enable_search: Enable DashScope web search for real-time info.

        Yields:
            Dicts with "type" and "delta" keys.
        """
        if not self.is_available:
            yield {"type": "content", "delta": ""}
            return

        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        body = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if enable_search and "dashscope" in self.base_url.lower():
            body["enable_search"] = True
            body["search_options"] = {"search_strategy": "standard"}

        # 🔴 Fix: use async with for proper cleanup even on generator interruption
        async with httpx.AsyncClient(timeout=self.timeout, limits=self.limits) as client:
            try:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        text = await response.aread()
                        self._record_failure(response.status_code)
                        raise RuntimeError(
                            f"LLM stream error {response.status_code}: {text[:500]}"
                        )
                    self._record_success()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    reasoning = delta.get("reasoning_content", "")
                                    if reasoning:
                                        yield {"type": "reasoning", "delta": reasoning}
                                    content = delta.get("content", "")
                                    if content:
                                        yield {"type": "content", "delta": content}
                            except json.JSONDecodeError:
                                continue
            except GeneratorExit:
                # Generator was closed externally — clean up gracefully
                pass

    async def chat_with_image(
        self,
        text: str,
        image_base64: str,
        media_type: str = "image/png",
        system: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> str:
        """Chat with an image (multi-modal vision).

        Uses OpenAI-compatible vision format.

        Args:
            text: Text prompt.
            image_base64: Base64-encoded image data.
            media_type: Image MIME type.
            system: Optional system prompt.
            max_tokens: Max output tokens.

        Returns:
            Generated text response.
        """
        if not self.is_available:
            return "{}"

        from app.config import settings as _s
        vision_model = _s.VISION_MODEL or self.model
        vision_base_url = (_s.VISION_BASE_URL or self.base_url).rstrip("/")
        if vision_base_url.endswith("/anthropic"):
            vision_base_url = vision_base_url[:-9]
        vision_key = _s.VISION_API_KEY or self.api_key

        # 🔴 DeepSeek 官方视觉 API 用「message.image 数组」格式（非 OpenAI content[].image_url）
        #     已验证：model=deepseek-v4-flash-vision-exp，image=[{"type":..,"base64":..}] 被接受。
        #     其他 OpenAI 兼容网关（如 aigw.telecomjs.com）仍需 content[].image_url 格式。
        is_deepseek_vision = "api.deepseek.com" in vision_base_url
        if is_deepseek_vision:
            img_type = (media_type.split("/")[-1] if media_type and "/" in media_type else "png").lower()
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            # 🔴 DeepSeek image 元素用 `data` 字段承载 base64（非 base64 字段）
            messages.append({
                "role": "user",
                "image": [{"type": img_type, "data": image_base64}],
                "content": text,
            })
        else:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_base64}",
                        },
                    },
                ],
            })

        async def _do_request():
            async with httpx.AsyncClient(timeout=self.timeout, limits=self.limits) as client:
                return await client.post(
                    f"{vision_base_url}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {vision_key}",
                    },
                    json={
                        "model": vision_model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                    },
                )

        data = await self._retry_request(_do_request, label="Vision")
        choices = data.get("choices", [])
        if choices and choices[0].get("message", {}).get("content"):
            return choices[0]["message"]["content"]
        return "{}"


# Singleton
llm_service = LLMService()
