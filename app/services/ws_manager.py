"""WebSocket Connection Manager — replaces SSE for bidirectional chat."""

import asyncio
import json
import logging
from typing import Dict, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections per session."""

    def __init__(self):
        self._connections: Dict[str, WebSocket] = {}
        self._queues: Dict[str, asyncio.Queue] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[session_id] = ws
        self._queues[session_id] = asyncio.Queue()
        logger.info(f"WS connected: {session_id}")

    async def disconnect(self, session_id: str):
        ws = self._connections.pop(session_id, None)
        self._queues.pop(session_id, None)
        if ws:
            try: await ws.close()
            except: pass
        logger.info(f"WS disconnected: {session_id}")

    async def send(self, session_id: str, event: str, data: dict = None):
        """Send an event to a specific session."""
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json({"event": event, "data": data or {}})
            except Exception:
                await self.disconnect(session_id)

    async def broadcast(self, event: str, data: dict = None):
        """Send to all connected sessions."""
        for sid in list(self._connections.keys()):
            await self.send(sid, event, data)

    def is_connected(self, session_id: str) -> bool:
        return session_id in self._connections

    async def stream_text(self, session_id: str, text: str, chunk_size: int = 5, delay: float = 0.01):
        """Stream text in sentence-level chunks to the frontend for smooth typewriter effect.

        Splits on Chinese/English sentence boundaries (。！？\n) so the frontend
        renders complete semantic units, not mid-sentence fragments.
        Falls back to character-based chunking when sentences are very long.
        """
        ws = self._connections.get(session_id)
        if not ws:
            return

        # Split by sentence boundaries: Chinese periods, question/exclamation marks, newlines
        import re
        sentences = re.split(r'(?<=[。！？\n])\s*', text)
        # Filter empty strings and re-join very short fragments
        parts = []
        buf = ""
        for s in sentences:
            if not s:
                continue
            buf += s
            if len(buf) >= 15 or s.endswith('\n'):
                parts.append(buf)
                buf = ""
        if buf:
            parts.append(buf)

        # For very long parts (>80 chars), sub-split them
        final_chunks = []
        for p in parts:
            if len(p) > 80:
                for i in range(0, len(p), 40):
                    final_chunks.append(p[i:i+40])
            else:
                final_chunks.append(p)

        for chunk in final_chunks:
            try:
                await ws.send_json({"event": "stream", "data": {"delta": chunk}})
                await asyncio.sleep(delay)
            except Exception:
                break

    @property
    def active_sessions(self) -> list:
        return list(self._connections.keys())


# Singleton
ws_manager = ConnectionManager()
