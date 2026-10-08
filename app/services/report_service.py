"""Report service — manages generation sessions with persistent memory."""
import json
import uuid
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from datetime import datetime

from fastapi import HTTPException

from app.models.history import Report, ConversationMessage
from app.config import settings

logger = logging.getLogger(__name__)


def _ensure_company_ctx(session: "ReportSession") -> None:
    """保证会话 state 里有 `_company_id`（老会话没有，靠这条兜底补上）。

    🔴 放在**取会话的唯一入口**里，而不是散在 30 个端点各写一遍 ——
       漏一处那份报告的公司信息就会走成「待填写」（或更糟：回落成我们自己）。
    """
    try:
        if not session.state.get("_company_id") and session.company_id:
            session.state["_company_id"] = session.company_id
    except Exception:
        pass


class ReportSession:
    """Holds the state for a single report generation session."""

    def __init__(
        self,
        session_id: str,
        template_id: int,
        template_name: str,
        template_path: str,
        state: Dict[str, Any],
        user_id: str = "",
        company_id: int = None,
    ):
        self.session_id = session_id
        self.template_id = template_id
        self.template_name = template_name
        self.template_path = template_path
        self.state = state
        # 🔴 归属必须在**建会话时**记下，不能等到后台任务里去取 ——
        #    `start_workflow` 的 `asyncio.create_task` 在请求返回后才跑，
        #    那时已经没有 Request / 依赖注入上下文，拿不到当前用户。
        self.user_id = str(user_id) if user_id else ""
        self.company_id = company_id
        self.created_at = datetime.now()
        self.last_activity = datetime.now()

    # 🔴 落盘时**必须保留的下划线字段**。
    #    原来 `to_dict()` 一律丢弃 `_` 开头的键，而上传材料清单恰恰存在
    #    `state["_uploaded_files"]` —— 于是后端每次重启（开发用 `uvicorn --reload`，
    #    改任何一行代码都会重启）恢复出来的会话就是**没有材料的空壳**：
    #    图库、附件、人员证书全空，表现得就像「刚上传的数据不见了」。
    _PERSIST_KEYS = (
        "_uploaded_files",          # 上传材料的路径 + 原始文件名（图库/附件/证书全靠它）
        "_image_original_names",    # 原文件名 → 存储名，附件分类要用
        "_classified_images",       # 视觉分类结果（公示/现场/座谈），重跑代价高
        "_missing_fields",          # 待补充字段弹窗的状态
        "_extracted_tables",        # 勘测定界提取表（表6-1 等）
        "_project_materials",       # 材料解析结果（避免每次重启都重跑一遍解析）
        "_chapter_gaps",            # 缺口清单（遁词检测 + 缺数据）→ 前端「待补充 N 项」
    )

    def to_dict(self):
        return {
            "session_id": self.session_id,
            # 🔴 user_id / company_id 是**顶层字段**，不经过下面那个 `_PERSIST_KEYS`
            #    过滤（那个过滤只作用于 `state` 子字典）。漏在这里写 → 重启后
            #    归属丢失，报告会被判成「无主」。改这个 dict 时务必成对改
            #    `_load_sessions` 的还原逻辑。
            "user_id": self.user_id,
            "company_id": self.company_id,
            "template_id": self.template_id,
            # 🔴 这两个原先**只读不写** —— `_load_sessions` 里 `sdata.get("template_name")`
            #    永远拿不到值，重启后会话就丢了模板名/路径。这里补齐，两边成对。
            "template_name": self.template_name,
            "template_path": self.template_path,
            "state": {k: v for k, v in self.state.items()
                      if not k.startswith("_") or k in self._PERSIST_KEYS},
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
                            user_id=sdata.get("user_id", ""),
                            company_id=sdata.get("company_id"),
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
        user_id: str = "",
        company_id: int = None,
    ) -> ReportSession:
        """Create a new generation session.

        `user_id` / `company_id` 是报告的**归属**，由路由层从当前登录用户传入；
        不传（脚本、测试）则留空，报告归为「无主」，只有管理员看得到。
        """
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
            user_id=user_id,
            company_id=company_id,
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

    def get_session_for_user(self, session_id: str, user) -> ReportSession:
        """按会话 id 取会话，并**校验归属**。

        🔴 为什么必须有这个
            `/reports/generate/{session_id}/*` 有 30 多个端点，原先一律
            `get_session(sid)` —— **猜到 session_id 就能读别人的会话**
            （材料、填写数据、生成的正文全在里面）。这里统一按归属过滤：
            非本人且非管理员 → 一律 404（用 404 而不是 403：不泄露「这个 id 存在」）。

            无主的旧会话（`user_id` 为空，脚本/历史遗留）只有管理员能取。
        """
        from app.deps import is_admin

        session = self._sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")
        if is_admin(user):
            session.last_activity = datetime.now()
            _ensure_company_ctx(session)
            return session
        if not session.user_id or session.user_id != str(getattr(user, "id", "")):
            raise HTTPException(status_code=404, detail="会话不存在或已过期")
        session.last_activity = datetime.now()
        _ensure_company_ctx(session)
        return session

    def remove_session(self, session_id: str):
        """Remove a session (on cancel or completion)."""
        session = self._sessions.pop(session_id, None)
        if session:
            # 仅移除内存会话 —— 用户上传原件保留在 storage（作为项目资料存档），
            # 需要删除时由「删除项目资料」等显式操作完成，绝不在取消/移除会话时自动删文件
            self._save_sessions()
            logger.info(f"Removed session {session_id}; user uploads kept on disk")

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
                    # 🔴 更新分支**必须也写 filled_data_json** —— 原来只有新建分支写，
                    #    于是同一会话二次生成后，库里的 filled_data 还停在第一次那份，
                    #    脚本按 session 取数时拿到的是旧值。
                    existing.filled_data_json = json.dumps(
                        session.state.get("filled_data", {}), ensure_ascii=False)
                    existing.conversation_json = json.dumps(
                        session.state.get("messages", []), ensure_ascii=False)
                    existing.generation_duration_sec = duration
                    existing.completed_at = datetime.now()
                    # 🔴 归属只在**还没有**的时候补 —— 更新不该把已有归属改掉
                    #    （草稿可能先由 archive_filled_data 建、后由这里更新）
                    if not existing.user_id or existing.user_id == "default":
                        existing.user_id = session.user_id or existing.user_id
                    if existing.company_id is None:
                        existing.company_id = session.company_id
                    report_id = existing.id
                else:
                    report = Report(
                        title=title,
                        session_id=session.session_id,
                        # 🔴 报告归属：不写的话所有行都是默认值 "default"，
                        #    「每个账号只看自己的报告」就无从实现
                        user_id=session.user_id or "default",
                        company_id=session.company_id,
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


    async def archive_filled_data(self, session: "ReportSession") -> bool:
        """把会话的 `filled_data` **立即**归档到 history_reports.db（不等生成完成）。

        🔴 为什么需要：会话只有 24 小时生命（`_SESSION_MAX_AGE_HOURS`），用户在弹窗里
           填的日期/单位/电话如果只活在会话里，过期即丢；而原来只有「生成完成」才写库。
           改成**填完就落库** —— 会话过期后脚本/界面按 session_id 照样读得到。

        只 upsert `filled_data_json`，不碰状态、不要求生成完成，幂等可反复调用。
        """
        from sqlalchemy import select
        from app.database.history_db import async_session as history_session
        from app.models.history import Report

        try:
            fd = (session.state or {}).get("filled_data") or {}
            if not fd:
                return False
            async with history_session() as db:
                existing = (
                    await db.execute(select(Report).where(Report.session_id == session.session_id))
                ).scalar_one_or_none()
                if existing:
                    existing.filled_data_json = json.dumps(fd, ensure_ascii=False)
                    # 🔴 同上：只在**还没有**归属时补，不覆盖已有归属
                    if not existing.user_id or existing.user_id == "default":
                        existing.user_id = session.user_id or existing.user_id
                    if existing.company_id is None:
                        existing.company_id = session.company_id
                else:
                    db.add(Report(
                        title=session.state.get("report_title") or "未命名报告",
                        session_id=session.session_id,
                        user_id=session.user_id or "default",
                        company_id=session.company_id,
                        template_id=getattr(session, "template_id", None),
                        template_name=getattr(session, "template_name", None),
                        status=session.state.get("status", "created"),
                        filled_data_json=json.dumps(fd, ensure_ascii=False),
                        conversation_json=json.dumps(session.state.get("messages", []), ensure_ascii=False),
                    ))
                await db.commit()
            return True
        except Exception as e:
            logger.error(f"archive_filled_data failed: {e}", exc_info=True)
            return False


report_service = ReportService()