"""Checkpoint Manager — SQLite-based state persistence for crash recovery."""

import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Persists session state snapshots to SQLite for crash recovery.

    On server restart, active sessions can be restored from their last checkpoint.
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            from app.config import settings
            db_path = str(settings.DATA_DIR / "checkpoints.db")
        self._db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    phase TEXT DEFAULT '',
                    updated_at TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_updated ON checkpoints(updated_at)")
            conn.commit()

    def save(self, session_id: str, state: dict, phase: str = ""):
        """Save a checkpoint of the session state."""
        try:
            # Only serialize JSON-safe fields
            safe_state = {}
            for k, v in state.items():
                if k.startswith("_"):
                    continue
                try:
                    json.dumps(v)
                    safe_state[k] = v
                except (TypeError, ValueError):
                    safe_state[k] = str(v)[:1000]  # Truncate non-serializable

            msg_count = len(state.get("messages", []))

            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO checkpoints (session_id, state_json, phase, updated_at, message_count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, json.dumps(safe_state, ensure_ascii=False),
                     phase, datetime.now().isoformat(), msg_count)
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Checkpoint save failed for {session_id}: {e}")

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Restore a session from its last checkpoint."""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT state_json, phase, updated_at, message_count FROM checkpoints WHERE session_id=?",
                    (session_id,)
                ).fetchone()
                if row:
                    state = json.loads(row["state_json"])
                    state["_checkpoint_phase"] = row["phase"]
                    state["_checkpoint_time"] = row["updated_at"]
                    logger.info(f"Restored session {session_id}: {row['message_count']} messages, phase={row['phase']}")
                    return state
        except Exception as e:
            logger.warning(f"Checkpoint load failed for {session_id}: {e}")
        return None

    def list_active(self) -> list:
        """List all active session checkpoints."""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                rows = conn.execute(
                    "SELECT session_id, phase, updated_at, message_count FROM checkpoints ORDER BY updated_at DESC LIMIT 50"
                ).fetchall()
                return [{"session_id": r[0], "phase": r[1], "updated_at": r[2], "message_count": r[3]} for r in rows]
        except Exception:
            return []

    def delete(self, session_id: str):
        """Remove a session checkpoint."""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("DELETE FROM checkpoints WHERE session_id=?", (session_id,))
                conn.commit()
        except Exception:
            pass

    def cleanup_old(self, max_age_hours: int = 24):
        """Remove checkpoints older than max_age_hours."""
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("DELETE FROM checkpoints WHERE updated_at < ?", (cutoff,))
                conn.commit()
        except Exception:
            pass


# Singleton
checkpoint_manager = CheckpointManager()
