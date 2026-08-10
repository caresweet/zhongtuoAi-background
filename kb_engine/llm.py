"""llm.py — DashScope (OpenAI 兼容) LLM 客户端

使用 httpx 异步调用，与项目现有 llm_service 配置一致。
"""

import os
import json
import asyncio
from typing import Dict, List, Optional

import httpx

_API_KEY = os.environ.get("LLM_API_KEY", "")
_BASE_URL = os.environ.get("LLM_BASE_URL", "") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
_MODEL = os.environ.get("LLM_MODEL", "") or "qwen3-max"
_VISION_MODEL = os.environ.get("VISION_MODEL", "") or "qwen-vl-max"
_VISION_KEY = os.environ.get("VISION_API_KEY", "") or _API_KEY
_VISION_BASE = os.environ.get("VISION_BASE_URL", "") or _BASE_URL


class LLMClient:
    """轻量 LLM 客户端，支持文本对话与视觉 OCR。"""

    def __init__(self):
        self.api_key = _API_KEY
        self.base_url = _BASE_URL.rstrip("/")
        self.model = _MODEL
        self.timeout = httpx.Timeout(180.0, connect=15.0, read=150.0)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def chat(self, messages: List[Dict], system: Optional[str] = None,
                   max_tokens: int = 4096, temperature: float = 0.4) -> str:
        """非流式文本对话。"""
        if not self.available:
            raise RuntimeError("LLM API Key 未配置")
        api_msgs = []
        if system:
            api_msgs.append({"role": "system", "content": system})
        api_msgs.extend(messages)
        body = {
            "model": self.model,
            "messages": api_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json=body,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"LLM API {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"] if choices else ""

    async def chat_json(self, messages: List[Dict], system: Optional[str] = None,
                        max_tokens: int = 4096, temperature: float = 0.3) -> dict:
        """要求 JSON 输出的对话，自动解析。"""
        if system:
            system = system.rstrip() + "\n\n请仅返回合法 JSON，不要包含 markdown 代码块标记。"
        else:
            system = "请仅返回合法 JSON，不要包含 markdown 代码块标记。"
        text = await self.chat(messages, system=system, max_tokens=max_tokens, temperature=temperature)
        return _parse_json_loose(text)

    async def vision(self, prompt: str, image_b64: str,
                     mime_type: str = "image/png", max_tokens: int = 2048) -> str:
        """视觉模型：图片理解 / OCR。"""
        base = _VISION_BASE.rstrip("/")
        key = _VISION_KEY or self.api_key
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
            ],
        }]
        body = {"model": _VISION_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                json=body,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Vision API {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"] if choices else ""


def _parse_json_loose(text: str) -> dict:
    """宽松解析 LLM 返回的 JSON（去除 markdown 包裹、提取首个 { ... }）。"""
    if not text:
        return {}
    t = text.strip()
    # 去 markdown 代码块
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 提取首个 { 到最后一个 }
    s = t.find("{")
    e = t.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except json.JSONDecodeError:
            pass
    return {"_raw": text}
