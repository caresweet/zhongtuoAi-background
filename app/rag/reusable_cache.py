"""Reusable-content cache — saves tokens by memoizing domain-general retrievals.

Motivation: much of a report's RAG context is *reusable* across projects in the
same domain — regulation text, the standard assessment procedure, emergency-plan
boilerplate, company qualifications. Re-embedding a query and re-running vector
search for identical (domain, chapter) context on every report wastes both an
embedding call and, downstream, prompt tokens.

This cache keys retrieval results by (domain, chapter_number, query_signature).
The first retrieval populates it; subsequent same-key retrievals return instantly
with no embedding call and no vector search.

Two tiers:
- in-memory LRU (per-process, fast, always on)
- optional SQLite persistence in knowledge_base.db (survives restarts)

Project-specific context (a given plot, village, budget) is NOT cached — only
retrievals explicitly marked reusable, or run through cache_for_chapter which
scopes the key to (domain, chapter) rather than to project text.
"""

import hashlib
import json
import logging
import sqlite3
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_MEMORY_ENTRIES = 256
_DEFAULT_TTL_SEC = 7 * 24 * 3600  # a week; regulation/boilerplate rarely changes


def _signature(text: str) -> str:
    """Short stable hash of a query string (order-independent within a domain)."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


class ReusableCache:
    """Domain/chapter-scoped cache of reusable retrieval results."""

    def __init__(self, db_path: Optional[str] = None, ttl_sec: int = _DEFAULT_TTL_SEC):
        self._mem: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._ttl = ttl_sec
        self._db_path = db_path
        self._hits = 0
        self._misses = 0
        if db_path:
            self._init_db()

    # ── key ──

    @staticmethod
    def make_key(domain: str, chapter: Optional[int], query: str) -> str:
        ch = "-" if chapter is None else str(chapter)
        return f"{domain or 'any'}::{ch}::{_signature(query)}"

    # ── SQLite tier ──

    def _init_db(self):
        try:
            con = sqlite3.connect(self._db_path)
            con.execute(
                "CREATE TABLE IF NOT EXISTS reusable_snippets ("
                "cache_key TEXT PRIMARY KEY, domain TEXT, chapter INTEGER, "
                "payload TEXT, created_at REAL)"
            )
            con.commit()
            con.close()
        except Exception as e:
            logger.warning("reusable cache DB init failed: %s", e)
            self._db_path = None

    def _db_get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self._db_path:
            return None
        try:
            con = sqlite3.connect(self._db_path)
            row = con.execute(
                "SELECT payload, created_at FROM reusable_snippets WHERE cache_key=?",
                (key,),
            ).fetchone()
            con.close()
            if not row:
                return None
            payload, created_at = row
            if self._expired(created_at):
                return None
            return json.loads(payload)
        except Exception as e:
            logger.debug("reusable cache DB get failed: %s", e)
            return None

    def _db_put(self, key: str, domain: str, chapter: Optional[int],
                value: Dict[str, Any], created_at: float):
        if not self._db_path:
            return
        try:
            con = sqlite3.connect(self._db_path)
            con.execute(
                "INSERT OR REPLACE INTO reusable_snippets "
                "(cache_key, domain, chapter, payload, created_at) VALUES (?,?,?,?,?)",
                (key, domain or "", chapter if chapter is not None else -1,
                 json.dumps(value, ensure_ascii=False), created_at),
            )
            con.commit()
            con.close()
        except Exception as e:
            logger.debug("reusable cache DB put failed: %s", e)

    # ── public API ──

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return a cached payload or None. Checks memory then SQLite.

        NOTE: created_at is NOT injected by the runtime clock (Date.now-style
        calls are avoided elsewhere); expiry uses timestamps stamped at put().
        """
        entry = self._mem.get(key)
        if entry is not None:
            if self._expired(entry.get("_ts", 0)):
                self._mem.pop(key, None)
            else:
                self._mem.move_to_end(key)
                self._hits += 1
                return entry["value"]

        db_val = self._db_get(key)
        if db_val is not None:
            # warm memory tier
            self._mem[key] = {"value": db_val, "_ts": time.time()}
            self._mem.move_to_end(key)
            self._hits += 1
            return db_val

        self._misses += 1
        return None

    def put(self, key: str, value: Dict[str, Any],
            domain: str = "", chapter: Optional[int] = None):
        """Store a payload in both tiers."""
        now = time.time()
        self._mem[key] = {"value": value, "_ts": now}
        self._mem.move_to_end(key)
        while len(self._mem) > _MAX_MEMORY_ENTRIES:
            self._mem.popitem(last=False)
        self._db_put(key, domain, chapter, value, now)

    def _expired(self, ts: float) -> bool:
        return self._ttl > 0 and (time.time() - ts) > self._ttl

    @property
    def stats(self) -> Dict[str, int]:
        total = self._hits + self._misses
        rate = round(100 * self._hits / total) if total else 0
        return {"hits": self._hits, "misses": self._misses, "hit_rate_pct": rate,
                "mem_entries": len(self._mem)}

    def clear(self):
        self._mem.clear()
