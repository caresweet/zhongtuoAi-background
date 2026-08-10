"""Report service — manages generation sessions with persistent memory."""
import json
import os
import uuid
import logging
from pathlib import Path
from typing import Dict, Optional, AsyncIterator, Any
from datetime import datetime

from fastapi import HTTPException

from app.models.history import Report, ConversationMessage
from app.config import settings

logger = logging.getLogger(__name__)


class ReportSession:
    """Holds the state for a single report generation session."""

    def __init__(
        self,
        session_id: str,
        template_id: int,
        template_name: str,
        template_path: str,
        state: Dict[str, Any],
    ):
        self.session_id = session_id
        self.template_id = template_id
        self.template_name = template_name
        self.template_path = template_path
        self.state = state
        self.created_at = datetime.now()
        self.last_activity = datetime.now()

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "template_id": self.template_id,
            "state": {k: v for k, v in self.state.items() if not k.startswith("_")},
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }


class ReportService:
    """Manages report generation sessions with persistent memory.

    Memory layers:
    - In-memory dict: fast access during active session
    - Filesystem JSON: survives server restart (storage/sessions.json)
    - ChromaDB vector: semantic long-term knowledge (via existing RAG)
    - SQLite DB: completed report archives (history_reports.db)
    """

    # Session lifecycle limits
    _MAX_SESSIONS = 200
    _SESSION_MAX_AGE_HOURS = 24

    def __init__(self):
        self._sessions: Dict[str, ReportSession] = {}
        self._storage_path = Path(settings.STORAGE_DIR) if hasattr(settings, 'STORAGE_DIR') else Path('storage')
        self._sessions_file = self._storage_path / "sessions.json"
        self._load_sessions()
        self._expire_old_sessions()

    def _load_sessions(self):
        """Restore sessions from filesystem after server restart."""
        try:
            if self._sessions_file.exists():
                with open(self._sessions_file, 'r') as f:
                    data = json.load(f)
                for sdata in data.get("sessions", []):
                    sid = sdata.get("session_id")
                    if sid:
                        state = sdata.get("state", {})
                        state["session_id"] = sid
                        session = ReportSession(
                            session_id=sid,
                            template_id=sdata.get("template_id", 0),
                            template_name=sdata.get("template_name", ""),
                            template_path=sdata.get("template_path", ""),
                            state=state,
                        )
                        self._sessions[sid] = session
                logger.info(f"Restored {len(self._sessions)} sessions from disk")
        except Exception as e:
            logger.warning(f"Failed to load sessions: {e}")

    def _save_sessions(self):
        """Persist all active sessions to filesystem."""
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            sessions_data = {
                "updated_at": datetime.now().isoformat(),
                "sessions": [s.to_dict() for s in self._sessions.values()],
            }
            with open(self._sessions_file, 'w') as f:
                json.dump(sessions_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save sessions: {e}")

    def _expire_old_sessions(self):
        """Remove sessions older than _SESSION_MAX_AGE_HOURS and enforce max count.

        Oldest sessions are evicted first when the total exceeds _MAX_SESSIONS.
        Called on startup and before each new session creation.
        """
        from datetime import timedelta
        now = datetime.now()
        cutoff = now - timedelta(hours=self._SESSION_MAX_AGE_HOURS)
        expired = []

        for sid, s in self._sessions.items():
            if s.last_activity < cutoff:
                expired.append(sid)

        for sid in expired:
            self.remove_session(sid)

        if expired:
            logger.info(f"Expired {len(expired)} old sessions (>{self._SESSION_MAX_AGE_HOURS}h)")

        # Enforce max session count — evict least recently active
        if len(self._sessions) > self._MAX_SESSIONS:
            sorted_sessions = sorted(
                self._sessions.items(), key=lambda x: x[1].last_activity
            )
            overflow = len(self._sessions) - self._MAX_SESSIONS
            for sid, _ in sorted_sessions[:overflow]:
                self.remove_session(sid)
            if overflow > 0:
                logger.info(f"Evicted {overflow} oldest sessions (max={self._MAX_SESSIONS})")

    def create_session(
        self,
        template_id: int,
        template_name: str,
        template_path: str,
        initial_message: str,
    ) -> ReportSession:
        """Create a new generation session."""
        # Cleanup old sessions before creating new one
        self._expire_old_sessions()

        session_id = uuid.uuid4().hex

        state = {
            "session_id": session_id,
            "template_id": template_id,
            "template_path": template_path,
            "report_title": "",
            "sections": [],
            "total_sections": 0,
            "current_section_index": 0,
            "total_placeholders": 0,
            "filled_placeholders": 0,
            "messages": [],
            "status": "created",
            "error_message": None,
            "retry_count": 0,
            "filled_data": {},
            "output_path": None,
            "report_id": None,
        }

        session = ReportSession(
            session_id=session_id,
            template_id=template_id,
            template_name=template_name,
            template_path=template_path,
            state=state,
        )

        self._sessions[session_id] = session
        self._save_sessions()
        return session

    def get_session(self, session_id: str) -> ReportSession:
        """Get an existing session by ID."""
        session = self._sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")
        session.last_activity = datetime.now()
        return session

    def remove_session(self, session_id: str):
        """Remove a session (on cancel or completion)."""
        session = self._sessions.pop(session_id, None)
        if session:
            uploaded = session.state.pop("_uploaded_files", [])
            if uploaded:
                from app.services.file_service import file_service
                for fpath in uploaded:
                    try:
                        file_service.delete_file(fpath)
                    except Exception:
                        pass
            self._save_sessions()
            logger.info(f"Cleaned up {len(uploaded)} files from removed session {session_id}")

    def add_message(self, session: ReportSession, role: str, content: str, **kwargs):
        """Add a message to the session's conversation state."""
        message = {
            "role": role,
            "content": content,
            "message_type": kwargs.get("message_type", "text"),
            "metadata": kwargs.get("metadata", {}),
            "timestamp": datetime.now().isoformat(),
        }
        if "messages" not in session.state:
            session.state["messages"] = []
        session.state["messages"].append(message)
        session.last_activity = datetime.now()

        # Trim state to prevent unbounded growth
        self._trim_state(session.state)

        self._save_sessions()
        # Auto-checkpoint on each message
        try:
            from app.services.checkpoint import checkpoint_manager
            checkpoint_manager.save(session.session_id, session.state, session.state.get("chapter_orchestrator_state", ""))
        except Exception:
            pass

    @staticmethod
    def _trim_state(state: dict) -> None:
        """Trim large fields in state to prevent unbounded memory growth.

        Applied after each message is added. Limits:
        - messages: last 60 (30 conversation rounds)
        - agent_log: last 100 entries
        - Cleaned large internal fields after generation completes
        """
        messages = state.get("messages", [])
        if len(messages) > 60:
            state["messages"] = messages[-60:]

        agent_log = state.get("agent_log", [])
        if len(agent_log) > 100:
            state["agent_log"] = agent_log[-100:]

        # After generation completes, drop large raw-text caches
        if state.get("status") in ("completed", "cancelled"):
            for key in ("_pdf_texts", "_pdf_raw_text", "_pdf_need_ocr",
                        "_project_material_chunks", "_enhanced_context"):
                state.pop(key, None)

    def get_conversation(self, session: ReportSession) -> list:
        """Get all conversation messages for a session."""
        return session.state.get("messages", [])

    async def save_report_to_db(
        self, db, session: ReportSession, file_service
    ) -> Report:
        """Persist the completed report to history_reports.db."""
        from app.models.history import Report, ConversationMessage

        duration = int((datetime.now() - session.created_at).total_seconds())

        report = Report(
            title=session.state.get("report_title", "未命名报告"),
            session_id=session.session_id,
            template_id=session.template_id,
            template_name=session.template_name,
            status=session.state.get("status", "completed"),
            report_file_path=session.state.get("output_path", ""),
            filled_data_json=json.dumps(session.state.get("filled_data", {}), ensure_ascii=False),
            section_progress_json=json.dumps(
                session.state.get("sections", []), ensure_ascii=False
            ),
            conversation_json=json.dumps(
                session.state.get("messages", []), ensure_ascii=False
            ),
            generation_duration_sec=duration,
            completed_at=datetime.now(),
        )
        db.add(report)
        await db.flush()
        await db.refresh(report)

        # Save individual conversation messages
        for msg in session.state.get("messages", []):
            conv_msg = ConversationMessage(
                report_id=report.id,
                session_id=session.session_id,
                role=msg.get("role", "system"),
                content=msg.get("content", ""),
                message_type=msg.get("message_type", "text"),
                metadata_json=json.dumps(msg.get("metadata", {}), ensure_ascii=False),
            )
            db.add(conv_msg)

        await db.flush()
        return report


    async def persist_report(self, session: ReportSession, file_service=None) -> Optional[int]:
        """Save (or update) the report to history_reports.db, managing its own DB session.

        Safe to call from agent/orchestrator code that has no FastAPI-injected
        db. Upserts by session_id so re-running generation for the same session
        updates the existing row instead of duplicating it. Returns the report id.
        """
        from sqlalchemy import select
        from app.database.history_db import async_session as history_session
        from app.models.history import Report, ConversationMessage

        try:
            async with history_session() as db:
                existing = (
                    await db.execute(
                        select(Report).where(Report.session_id == session.session_id)
                    )
                ).scalar_one_or_none()

                duration = int((datetime.now() - session.created_at).total_seconds())
                title = session.state.get("report_title") or session.state.get("title") or "未命名报告"
                status = session.state.get("status", "completed")
                output_path = session.state.get("output_path", "")

                if existing:
                    existing.title = title
                    existing.status = status
                    existing.report_file_path = output_path
                    existing.markdown_content = session.state.get("markdown_content", existing.markdown_content)
                    existing.generation_duration_sec = duration
                    existing.completed_at = datetime.now()
                    report_id = existing.id
                else:
                    report = Report(
                        title=title,
                        session_id=session.session_id,
                        template_id=getattr(session, "template_id", None),
                        template_name=getattr(session, "template_name", None),
                        status=status,
                        report_file_path=output_path,
                        markdown_content=session.state.get("markdown_content", ""),
                        filled_data_json=json.dumps(session.state.get("filled_data", {}), ensure_ascii=False),
                        section_progress_json=json.dumps(session.state.get("sections", []), ensure_ascii=False),
                        conversation_json=json.dumps(session.state.get("messages", []), ensure_ascii=False),
                        generation_duration_sec=duration,
                        completed_at=datetime.now(),
                    )
                    db.add(report)
                    await db.flush()
                    report_id = report.id

                await db.commit()
                return report_id
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"persist_report failed: {e}", exc_info=True)
            return None


report_service = ReportService()
