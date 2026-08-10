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

        # 🔴 Per-request client limits (more reliable under parallel load)
        self.limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)

        # ── Circuit breaker state ──
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._circuit_open = False

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

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
    ) -> str:
        """Non-streaming chat completion.

        Args:
            messages: List of {"role": "user|assistant", "content": "..."}.
            system: Optional system prompt.
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
            response_format: Optional {"type": "json_object"} for JSON mode.
            enable_search: Enable DashScope web search for real-time info.

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
        )
        return result.get("content", "")

    async def chat_with_reasoning(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        response_format: Optional[Dict[str, str]] = None,
        enable_search: bool = False,
    ) -> Dict[str, str]:
        """Non-streaming chat that captures both reasoning and content.

        DeepSeek-R1 and similar models return a `reasoning_content` field
        alongside the final `content`. This method captures both.

        Args:
            enable_search: Enable DashScope built-in web search for Qwen models.
                           Adds real-time internet results to the response.

        Returns:
            Dict with keys:
                - "content": The final response text
                - "reasoning": The model's chain-of-thought reasoning (may be empty)
        """
        if not self.is_available:
            return {"content": "", "reasoning": ""}

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
        if enable_search and "dashscope" in self.base_url.lower():
            body["enable_search"] = True
            body["search_options"] = {"search_strategy": "standard"}

        async def _do_request():
            async with httpx.AsyncClient(timeout=self.timeout, limits=self.limits) as client:
                return await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=body,
                )

        data = await self._retry_request(_do_request, label="LLM")
        choices = data.get("choices", [])
        if not choices:
            return {"content": "", "reasoning": ""}
        message = choices[0].get("message", {})
        return {
            "content": message.get("content", ""),
            "reasoning": message.get("reasoning_content", ""),
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

        # Vision model & endpoint are configurable. Defaults fall back to the
        # main text model/endpoint so a single OpenAI multimodal model
        # (e.g. gpt-4o) serves both text and vision.
        from app.config import settings as _s
        vision_model = _s.VISION_MODEL or self.model
        vision_base_url = (_s.VISION_BASE_URL or self.base_url).rstrip("/")
        if vision_base_url.endswith("/anthropic"):
            vision_base_url = vision_base_url[:-9]
        vision_key = _s.VISION_API_KEY or self.api_key

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
