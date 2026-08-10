"""Redis-backed session store for multi-worker deployments.

Replaces the in-memory dict in report_service.py when REDIS_URL is configured.
Fallback to in-memory store when Redis is unavailable (dev mode).
"""

import json
import logging
import time
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory store limits — prevent unbounded growth in fallback mode
_MAX_INMEMORY_ENTRIES = 500
_DEFAULT_TTL_SEC = 86400  # 24 hours


class SessionStore:
    """Abstract session store interface."""

    async def get(self, session_id: str) -> Optional[dict]:
        raise NotImplementedError

    async def set(self, session_id: str, data: dict, ttl: int = 86400) -> None:
        raise NotImplementedError

    async def delete(self, session_id: str) -> None:
        raise NotImplementedError

    async def exists(self, session_id: str) -> bool:
        raise NotImplementedError

    async def keys(self, pattern: str = "*") -> list[str]:
        raise NotImplementedError


class InMemorySessionStore(SessionStore):
    """Simple in-memory session store (dev fallback) with LRU + TTL eviction.

    Limits:
    - Max 500 entries — least recently used are evicted first
    - TTL of 24 hours — expired entries are lazily removed on access
    """

    def __init__(self):
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()

    def _evict_expired(self):
        """Lazy eviction: remove all expired entries."""
        now = time.time()
        expired = [
            k for k, (_, ts) in self._store.items()
            if now - ts > _DEFAULT_TTL_SEC
        ]
        for k in expired:
            self._store.pop(k, None)
        if expired:
            logger.debug(f"Evicted {len(expired)} expired sessions from memory store")

    def _evict_lru(self):
        """Evict oldest entries when capacity exceeded."""
        while len(self._store) > _MAX_INMEMORY_ENTRIES:
            key, _ = self._store.popitem(last=False)
            logger.debug(f"LRU evicted session: {key}")

    async def get(self, session_id: str) -> Optional[dict]:
        self._evict_expired()
        entry = self._store.get(session_id)
        if entry is None:
            return None
        data, ts = entry
        if time.time() - ts > _DEFAULT_TTL_SEC:
            self._store.pop(session_id, None)
            return None
        # Move to end (most recently used)
        self._store.move_to_end(session_id)
        return data

    async def set(self, session_id: str, data: dict, ttl: int = 86400) -> None:
        self._evict_expired()
        self._store[session_id] = (data, time.time())
        self._store.move_to_end(session_id)
        self._evict_lru()

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    async def exists(self, session_id: str) -> bool:
        self._evict_expired()
        entry = self._store.get(session_id)
        if entry is None:
            return False
        _, ts = entry
        if time.time() - ts > _DEFAULT_TTL_SEC:
            self._store.pop(session_id, None)
            return False
        return True

    async def keys(self, pattern: str = "*") -> list[str]:
        self._evict_expired()
        return list(self._store.keys())


class RedisSessionStore(SessionStore):
    """Redis-backed session store with TTL support."""

    def __init__(self, redis_url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
        )
        self._prefix = "session:"

    async def get(self, session_id: str) -> Optional[dict]:
        try:
            raw = await self._redis.get(f"{self._prefix}{session_id}")
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Redis get failed for {session_id}: {e}")
        return None

    async def set(self, session_id: str, data: dict, ttl: int = 86400) -> None:
        try:
            raw = json.dumps(data, ensure_ascii=False, default=str)
            await self._redis.setex(f"{self._prefix}{session_id}", ttl, raw)
        except Exception as e:
            logger.warning(f"Redis set failed for {session_id}: {e}")

    async def delete(self, session_id: str) -> None:
        try:
            await self._redis.delete(f"{self._prefix}{session_id}")
        except Exception as e:
            logger.warning(f"Redis delete failed for {session_id}: {e}")

    async def exists(self, session_id: str) -> bool:
        try:
            return await self._redis.exists(f"{self._prefix}{session_id}") > 0
        except Exception:
            return False

    async def keys(self, pattern: str = "*") -> list[str]:
        try:
            raw = await self._redis.keys(f"{self._prefix}{pattern}")
            prefix_len = len(self._prefix)
            return [k[prefix_len:] for k in raw]
        except Exception:
            return []


# Singleton — lazy init on first use
_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        from app.config import settings
        redis_url = settings.REDIS_URL
        if redis_url:
            try:
                _store = RedisSessionStore(redis_url)
                logger.info(f"Session store: Redis ({redis_url})")
            except Exception as e:
                logger.warning(f"Redis unavailable ({e}), falling back to in-memory store")
                _store = InMemorySessionStore()
        else:
            logger.info("Session store: InMemory (no REDIS_URL configured)")
            _store = InMemorySessionStore()
    return _store
