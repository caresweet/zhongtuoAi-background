"""Workflow Service — bridges the Agent pipeline with SSE streaming.

Active flows:
1. run_master_agent(): process user messages through MasterAgent (LLM chat)
2. run_chapter_by_chapter(): full 10-chapter generation via ChapterOrchestrator
3. process_chapter_feedback(): handle user confirm/revise/skip during generation
"""

import asyncio
import logging
from typing import Dict, Any, Optional, AsyncGenerator

from app.utils.sse import (
    sse_event, sse_message, sse_thinking,
    sse_phase_change, sse_complete, sse_error,
    sse_agent_status,
)

logger = logging.getLogger(__name__)


class WorkflowService:
    """Orchestrates the full report generation workflow with SSE streaming."""

    _ASSEMBLE_TRIGGERS = frozenset([
        "生成报告", "开始生成", "生成文档", "生成word",
        "全部确认", "确认生成", "确定生成",
        "generate report", "assemble",
        # Assistant-style natural language triggers
        "帮我生成报告", "帮我写报告", "帮我做报告",
        "编写报告", "编制报告", "逐章生成",
    ])

    # ═══════════════════════════════════════════════════════════════════
    # Unified Assistant Entry Point
    # ═══════════════════════════════════════════════════════════════════

    async def run_assistant(
        self, session, user_input: str = "", attachments: list = None,
        folder_structure: dict = None,
    ) -> AsyncGenerator[str, None]:
        """Unified intelligent assistant entry point.

        All user messages flow through here → MasterAgent handles intent
        recognition and routes to the appropriate handler (chat, question,
        table construction, generation, etc.).
        """
        async for evt in self.run_master_agent(
            session, user_input=user_input, attachments=attachments,
            folder_structure=folder_structure,
        ):
            yield evt

    # ═══════════════════════════════════════════════════════════════════
    # Master Agent — chat + intent recognition
    # ═══════════════════════════════════════════════════════════════════

    async def run_master_agent(
        self, session, user_input: str = "", attachments: list = None,
        folder_structure: dict = None,
    ) -> AsyncGenerator[str, None]:
        """Process a user message through the Master Agent (LLM chat)."""
        state = session.state
        # 🔴 Store session reference so agents can save to history DB
        state["_report_session"] = session
        attachments = attachments or []

        if folder_structure:
            state["_folder_structure"] = folder_structure

        state["_latest_user_input"] = user_input

        # Analyze uploaded project materials first so generation can depend on
        # normalized facts rather than only raw extracted PDF text.
        if attachments:
            from app.services.material_ingestion_service import material_ingestion_service

            existing_materials = state.setdefault("_project_materials", [])
            existing_paths = {item.get("source_path") for item in existing_materials if isinstance(item, dict)}
            new_paths = [att for att in attachments if isinstance(att, str) and att not in existing_paths]
            if new_paths:
                analyzed = await material_ingestion_service.ingest_many(
                    new_paths,
                    scope="session",
                    domain=state.get("_domain", "stability"),
                    metadata={"session_id": state.get("session_id", "")},
                )
                existing_materials.extend(analyzed)
                state["_project_materials"] = existing_materials
                state["_project_material_facts"] = material_ingestion_service.merge_project_facts(existing_materials)
                state["_project_material_summary"] = material_ingestion_service.summarize_analysis(existing_materials)
                state["_project_material_chunks"] = [
                    {
                        "source_path": item.get("source_path", ""),
                        "source_type": item.get("source_type", "file"),
                        "retrieval_text": item.get("retrieval_text", ""),
                    }
                    for item in existing_materials
                    if item.get("retrieval_text")
                ]
                state["_project_material_sources"] = [
                    {
                        "source_path": item.get("source_path", ""),
                        "source_name": item.get("source_name", ""),
                        "source_type": item.get("source_type", "file"),
                        "status": item.get("status", "completed"),
                    }
                    for item in existing_materials
                ]

        # Pre-extract text from all document types (PDF/DOCX/DOC/TXT)
        for att in attachments or []:
            if not isinstance(att, str):
                continue
            try:
                from app.services.file_service import file_service
                text = None
                att_lower = att.lower()
                if att_lower.endswith(".pdf"):
                    text = file_service.extract_pdf_text(att)
                elif att_lower.endswith((".docx", ".doc")):
                    text = file_service.extract_docx_text(att)
                elif att_lower.endswith((".txt", ".md")):
                    text = file_service.read_text_file(att)

                if text and len(text.strip()) > 50:
                    text_cache = state.setdefault("_pdf_texts", {})
                    text_cache[att] = text[:30000]
                elif att_lower.endswith(".pdf"):
                    ocr_list = state.setdefault("_pdf_need_ocr", [])
                    if att not in ocr_list:
                        ocr_list.append(att)
            except Exception:
                if att.lower().endswith(".pdf"):
                    ocr_list = state.setdefault("_pdf_need_ocr", [])
                    if att not in ocr_list:
                        ocr_list.append(att)

        from app.agent.agents.master import _add_to_history
        _add_to_history(state, "user", user_input)

        # Store attachments in structured_data
        current_step = state.get("current_step", 1)
        step_key = f"step_{current_step}"
        structured = state.get("structured_data", {})
        step_data = structured.get(step_key, {})
        if user_input:
            step_data["user_input"] = user_input
        if attachments:
            step_data["attachments"] = attachments
            step_data["images"] = attachments
        step_data["status"] = "in_progress"
        structured[step_key] = step_data
        state["structured_data"] = structured

        # 🔴 Fast-path: assembly triggers bypass LLM intent recognition
        # to save a round-trip. MasterAgent also handles these via intent system.
        is_assemble = user_input.strip() in self._ASSEMBLE_TRIGGERS
        is_force = "强制生成" in user_input

        if is_assemble or is_force:
            # 🔴 Regex fallback: extract data from user messages
            import re as _re_wf
            filled = state.setdefault("filled_data", {})
            all_user_text = " ".join(
                m.get("content", "") for m in state.get("messages", [])
                if m.get("role") == "user"
            ) + " " + (user_input or "")
            from app.routers.report import _regex_extract_data
            wf_data = _regex_extract_data(all_user_text)
            for k, v in wf_data.items():
                if v and not filled.get(k):
                    filled[k] = v

            # 🔴 Check for REAL data — exclude internal _prefixed fields
            real_filled = {k: v for k, v in state.get("filled_data", {}).items()
                          if not k.startswith("_")}
            has_real_data = (
                len(real_filled) >= 2                      # At least 2 real data fields
                or bool(state.get("_pdf_texts"))            # Or uploaded PDFs
            )
            if not has_real_data:
                yield sse_message(
                    "## ⚠️ 尚未提供足够信息\n\n"
                    "请先上传项目资料（PDF/图片）或提供项目基本信息"
                    "（位置、面积、责任单位等），"
                    "然后输入「生成报告」开始编写。",
                    message_type="warning",
                )
                return

            state["generation_mode"] = "chapter_by_chapter"
            yield sse_thinking("🚀 启动逐章生成流程...")
            async for evt in self.run_chapter_by_chapter(session, start_chapter=1):
                yield evt
            return

        # Get/create MasterAgent
        from app.agent.agents.master import MasterAgent
        from app.services.llm_service import llm_service
        master = MasterAgent(llm_service=llm_service)
        stream_queue = state.get("_stream_queue")

        yield sse_agent_status(agent="MasterAgent", status="thinking",
                               message="主智能体正在分析您的消息...")

        if stream_queue:
            async def _send_heartbeats():
                while True:
                    await asyncio.sleep(5)
                    await stream_queue.put({
                        "event": "thinking",
                        "data": {"content": "⏳ 正在深度分析中，请耐心等待..."},
                    })

            async def _drain_one(timeout: float = 1.0) -> bool:
                """Drain one queued agent event so long-running parsing keeps SSE alive."""
                try:
                    item = await asyncio.wait_for(stream_queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    return False
                evt_type = item.get("event", "message")
                evt_data = item.get("data", {})
                yield_evt = sse_event(evt_type, evt_data)
                if evt_type == "message" and evt_data.get("role") == "agent":
                    content = evt_data.get("content", "")
                    if content:
                        from app.services.report_service import report_service
                        report_service.add_message(
                            session, "agent", content,
                            message_type=evt_data.get("message_type", "text"))
                return yield_evt

            heartbeat_task = asyncio.create_task(_send_heartbeats())
            master_task = asyncio.create_task(master.run(state, stream_queue))

            try:
                while not master_task.done():
                    evt = await _drain_one(timeout=1.0)
                    if evt:
                        yield evt
                await master_task
            except Exception as exc:
                logger.error(f"MasterAgent.run() failed: {exc}", exc_info=True)
                yield sse_message(
                    f"抱歉，处理您的消息时遇到了问题（{type(exc).__name__}: "
                    f"{str(exc)[:150]}）。请重新发送。",
                    message_type="error",
                )
                return
            finally:
                heartbeat_task.cancel()
                if not master_task.done():
                    master_task.cancel()

            # Drain remaining events + persist messages
            while True:
                evt = await _drain_one(timeout=0.01)
                if not evt:
                    break
                yield evt

        yield sse_agent_status(agent="MasterAgent", status="idle",
                               message="等待您的回复...")

        # If MasterAgent flagged chapter generation, start ChapterOrchestrator
        if state.pop("_start_chapter_generation", False):
            yield sse_thinking("🚀 正在启动逐章生成流程...")
            async for evt in self.run_chapter_by_chapter(session, start_chapter=1):
                yield evt

    # ═══════════════════════════════════════════════════════════════════
    # Chapter-by-Chapter Generation
    # ═══════════════════════════════════════════════════════════════════

    async def run_chapter_by_chapter(
        self, session, start_chapter: int = 1,
    ) -> AsyncGenerator[str, None]:
        """Run the full 10-chapter generation pipeline via ChapterOrchestrator."""
        state = session.state
        state["_report_session"] = session  # ensure history persistence can find the session
        state["generation_mode"] = "chapter_by_chapter"
        state["chapter_orchestrator_state"] = "generating"
        state["current_chapter"] = start_chapter
        state["_cancel_requested"] = False  # Reset cancel flag
        state["_skip_analysis"] = True      # Auto-approve chapters (no user interaction)

        stream_queue = asyncio.Queue()

        from app.agent.agents.chapter_orchestrator import ChapterOrchestrator
        from app.services.llm_service import LLMService

        llm = LLMService()
        orchestrator = ChapterOrchestrator(llm_service=llm)

        # 🔴 Store orchestrator reference in state so cancel endpoint can call orchestrator.cancel()
        state["_orchestrator"] = orchestrator

        yield sse_phase_change("chapter_generation", {
            "total_chapters": 10,
            "start_chapter": start_chapter,
            "mode": "chapter_by_chapter",
        })

        orch_task = asyncio.create_task(
            orchestrator.run_full_pipeline(state, stream_queue, start_chapter)
        )
        # 🔴 Store task reference in state so cancel endpoint can cancel it
        state["_orchestrator_task"] = orch_task

        last_event_time = asyncio.get_event_loop().time()

        while not orch_task.done():
            try:
                # 🔴 Check cancel flag before each queue read
                if state.get("_cancel_requested"):
                    orchestrator.cancel()
                    orch_task.cancel()
                    yield sse_thinking("⏹️ 生成已被用户停止")
                    yield sse_event("cancelled", {"message": "生成已取消"})
                    break

                event_data = await asyncio.wait_for(stream_queue.get(), timeout=2.0)
                last_event_time = asyncio.get_event_loop().time()

                event_type = event_data.get("event", "message")
                data = event_data.get("data", {})

                key_events = {
                    "chapter_start", "chapter_stream", "chapter_complete",
                    "chapter_review_prompt", "chapter_confirmed", "chapter_progress",
                    "phase_change", "thinking", "agent_status", "message",
                    "missing_data_prompt", "review_table_start", "review_table_complete",
                    "validation_result", "complete", "collaboration_agent",
                }

                if event_type in key_events:
                    yield sse_event(event_type, data)
                elif event_type == "error":
                    yield sse_error(str(data) if isinstance(data, dict) else data)
                else:
                    yield sse_event(event_type, data)

                if event_type == "complete":
                    break

            except asyncio.TimeoutError:
                if orch_task.done():
                    break
                # 🔴 Check cancel flag on timeout too
                if state.get("_cancel_requested"):
                    orchestrator.cancel()
                    orch_task.cancel()
                    yield sse_thinking("⏹️ 生成已被用户停止")
                    yield sse_event("cancelled", {"message": "生成已取消"})
                    break
                idle_time = asyncio.get_event_loop().time() - last_event_time
                if idle_time > 10:
                    yield sse_thinking("⏳ 等待中...")
                    last_event_time = asyncio.get_event_loop().time()

        # 🔴 Handle cancellation — task might raise CancelledError
        try:
            state = await orch_task
        except asyncio.CancelledError:
            logger.info("ChapterOrchestrator task cancelled by user")
            state["chapter_orchestrator_state"] = "cancelled"
            yield sse_event("cancelled", {"message": "生成已被用户取消"})
        except Exception as e:
            logger.error(f"ChapterOrchestrator failed: {e}")
            yield sse_error(f"章节生成流程出错：{e}", recoverable=False)
            return

        # Clean up state references
        state.pop("_orchestrator", None)
        state.pop("_orchestrator_task", None)

        session.state = state

    # ═══════════════════════════════════════════════════════════════════
    # Chapter Feedback
    # ═══════════════════════════════════════════════════════════════════

    async def process_chapter_feedback(
        self, session, chapter: int, action: str, revision_text: str = "",
    ) -> Dict[str, Any]:
        """Process user feedback (approve/revise/skip) during chapter generation."""
        state = session.state

        if state.get("generation_mode") != "chapter_by_chapter":
            return {"success": False, "message": "当前不在逐章生成模式中"}

        if action in ("approve", "revise", "skip"):
            state["user_action"] = action
            state["chapter_feedback"] = revision_text if action == "revise" else ""
            event = state.get("_action_event")
            if isinstance(event, asyncio.Event):
                event.set()
        else:
            return {"success": False, "message": f"未知操作：{action}"}

        session.state = state
        return {
            "success": True,
            "message": f"第{chapter}章操作已接收：{action}",
            "chapter": chapter, "action": action,
        }

    def get_chapter_status(self, session, chapter: int) -> Dict[str, Any]:
        """Get status and content of a specific chapter."""
        state = session.state
        chapters = state.get("chapters", {})
        ch_data = chapters.get(chapter, {})

        if not isinstance(ch_data, dict):
            return {"chapter": chapter, "status": "pending", "title": "", "markdown": ""}

        from app.agent.state import CHAPTER_DEFINITIONS
        ch_def = CHAPTER_DEFINITIONS.get(chapter, {})

        return {
            "chapter": chapter,
            "title": ch_data.get("title", ch_def.get("title", "")),
            "status": ch_data.get("status", "pending"),
            "markdown": ch_data.get("markdown", ""),
            "tables": ch_data.get("tables", []),
            "sources": ch_data.get("rag_sources", []),
            "revision_history": ch_data.get("revision_history", []),
            "confirmed_at": ch_data.get("confirmed_at"),
        }

    def get_all_chapters_status(self, session) -> Dict[str, Any]:
        """Get status of all 10 chapters."""
        state = session.state
        result = {}
        for ch_num in range(1, 11):
            result[ch_num] = self.get_chapter_status(session, ch_num)

        return {
            "session_id": state.get("session_id", ""),
            "generation_mode": state.get("generation_mode", "chat"),
            "current_chapter": state.get("current_chapter", 1),
            "chapters": result,
        }

    @staticmethod
    def _format_folder_structure(tree: dict, indent: int = 0) -> str:
        """Format folder tree structure into readable text for the LLM."""
        lines = []
        prefix = "  " * indent
        name = tree.get("name", "")
        files = tree.get("files", [])
        folders = tree.get("folders", [])
        if indent == 0:
            lines.append(f"📂 用户上传了文件夹「{name}」：")
        else:
            lines.append(f"{prefix}📁 {name}/")
        for f in files:
            fname = f.get("name", "")
            fsize = f.get("size", 0)
            ftype = f.get("type", "file")
            icon = {"pdf": "📄", "image": "🖼️"}.get(ftype, "📎")
            size_str = f"{fsize/1024:.1f}KB" if fsize > 1024 else ""
            lines.append(f"{prefix}  {icon} {fname} ({size_str})" if size_str else f"{prefix}  {icon} {fname}")
        for sub in folders:
            lines.append(WorkflowService._format_folder_structure(sub, indent + 1))
        if indent == 0:
            def _count(n):
                return len(n.get("files", [])) + sum(_count(s) for s in n.get("folders", []))
            lines.append(f"\n共 {_count(tree)} 个文件。")
        return "\n".join(lines)


    @staticmethod
    def _sync_step_progress(state: dict) -> None:
        """Sync step progress from state — no-op (frontend polls status API)."""
        pass


workflow_service = WorkflowService()
