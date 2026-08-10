"""BiddingOrchestrator — drives per-chapter *thinking* generation of a bidding
document, mirroring the stability ChapterOrchestrator experience.

Flow:
  for each chapter in the report type's outline:
    BiddingChapterAgent.think() → RAG reference + outline
    BiddingChapterAgent.act()   → LLM writes the chapter (streamed via SSE)
    content guardrails check → auto-retry on blocking issues (max 3)
    present for user review → wait for approve/revise/skip
    result written back to state["_bidding_chapters"]
  assemble all chapters → markdown → BiddingDocxGenerator → docx
  persist to history

This does NOT fill a template. Each chapter is written from the user's actual
project data + retrieved reference tone, with regulations chosen to fit the
real project.
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

CHAPTER_REVIEW_TIMEOUT = 600


class BiddingOrchestrator(BaseAgent):
    name = "BiddingOrchestrator"
    description = "招标文件逐章思考生成编排器"

    def __init__(self, llm_service=None):
        super().__init__(llm_service)
        self._cancelled = False
        self._action_event: Optional[asyncio.Event] = None

    def cancel(self):
        self._cancelled = True
        if self._action_event:
            self._action_event.set()

    async def think(self, state: dict) -> Dict[str, Any]:
        return {"summary": "招标文件逐章生成编排", "steps": [], "actions": []}

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    async def run_full_pipeline(
        self, state: dict, stream_queue=None, report_type: str = "tender_response",
    ) -> Dict[str, Any]:
        self._stream_queue = stream_queue
        self._action_event = asyncio.Event()
        state["_action_event"] = self._action_event
        state["generation_mode"] = "chapter_by_chapter"

        from app.agent.bidding_chapters import get_bidding_chapters, get_bidding_type_name
        from .bidding_chapter_agent import BiddingChapterAgent

        type_name = get_bidding_type_name(report_type)

        if report_type == "tender_response" and not state.get("_tender_spec"):
            src = state.get("_bidding_source_text", "")
            if src:
                try:
                    from app.services.tender_spec_analyzer import tender_spec_analyzer
                    await self._emit_thinking("正在解析招标标准文件（所需模块 / 评分 / 项目信息）...")
                    spec = await tender_spec_analyzer.analyze(src)
                    state["_tender_spec"] = spec
                    proj = spec.get("project", {})
                    if proj.get("name"):
                        await self._emit_thinking(
                            f"招标标准解析完成：{proj.get('name')} | 技术重点 {len(spec.get('technical_focus', []))} 项"
                        )
                except Exception as e:
                    logger.error(f"Tender spec analysis failed: {e}")

        # Fixed data retrieval for bidding domain
        try:
            from app.services.fixed_data_service import get_fixed_data_for_domain
            await self._emit_thinking("正在从知识库检索公司固定数据（营业执照、资质证书等）...")
            fixed_data = await get_fixed_data_for_domain("bidding")
            state["_fixed_company_assets"] = fixed_data
            asset_count = len(fixed_data.get("assets", []))
            await self._emit_thinking(f"公司固定数据检索完成：{asset_count}项资产")
        except Exception as e:
            logger.error(f"FixedDataRetrieval failed (non-critical): {e}")

        chapters_def = get_bidding_chapters(report_type)
        total = len(chapters_def)

        await self._emit_thinking(
            f"开始逐模块生成{type_name}（共{total}个模块，每模块生成后可审核修改）..."
        )
        await self._emit("phase_change", {
            "total_chapters": total, "start_chapter": 1, "mode": "chapter_by_chapter",
        })

        state["_bidding_chapters"] = {}
        failed = []

        # Per-module buffer queues — capture SSE events during parallel
        # generation so the frontend replays them sequentially.
        chapter_count = len(chapters_def)
        chapter_buffers: Dict[int, asyncio.Queue] = {
            idx: asyncio.Queue()
            for idx in range(1, chapter_count + 1)
        }

        # Phase A: generate all modules in parallel into private buffers
        await self._emit_thinking(f"正在并行生成全部{total}个模块...")
        for idx in range(1, chapter_count + 1):
            await self._emit("chapter_progress", {
                "current": idx, "total": total, "status": "generating",
            })

        gen_tasks = [
            asyncio.ensure_future(
                self._generate_bidding_module(
                    idx, ch_def, state, report_type,
                    stream_queue=chapter_buffers[idx],
                )
            )
            for idx, ch_def in enumerate(chapters_def, start=1)
        ]
        gen_results = await asyncio.gather(*gen_tasks, return_exceptions=True)

        gen_ok = sum(1 for r in gen_results if r is True)
        gen_fail = sum(1 for r in gen_results if r is not True)
        await self._emit_thinking(
            f"并行生成完成：{gen_ok}/{total}个模块成功" +
            (f"，{gen_fail}个需重试" if gen_fail else "")
        )

        # Phase B: sequential UI playback — drain each module's buffer
        # to the main stream in order, then present for user review.
        for idx, (ch_def, gen_ok) in enumerate(zip(chapters_def, gen_results), start=1):
            if self._cancelled:
                break
            if gen_ok is not True and isinstance(gen_ok, Exception):
                await self._emit_thinking(f"模块{idx}并行生成异常：{gen_ok}")

            await self._drain_chapter_buffer(idx, chapter_buffers)

            await self._emit("chapter_progress", {
                "current": idx, "total": total, "status": "reviewing",
            })

            ok = await self._review_bidding_module(idx, ch_def, state, report_type)
            if not ok:
                failed.append(idx)

            await self._emit("chapter_progress", {
                "current": idx, "total": total, "status": "confirmed",
            })

        # Assemble — renumber parts and insert the 技术方案 chapter header once
        from app.agent.bidding_chapters import get_bidding_chapters
        defs = get_bidding_chapters(report_type)
        chapters = state.get("_bidding_chapters", {})
        parts = []
        part_no = 0
        tech_header_done = False
        _CN = "〇一二三四五六七八九十"
        for idx in sorted(chapters.keys()):
            md = chapters[idx].get("markdown", "")
            if not md.strip():
                continue
            ch_def = defs[idx - 1] if idx - 1 < len(defs) else {}
            is_tech = bool(ch_def.get("tech_group"))
            if is_tech and report_type == "tender_response":
                if not tech_header_done:
                    part_no += 1
                    cn = _CN[part_no] if part_no < len(_CN) else str(part_no)
                    parts.append(f"# 第{cn}部分 技术方案")
                    tech_header_done = True
                parts.append(md.strip())
            else:
                part_no += 1
                cn = _CN[part_no] if part_no < len(_CN) else str(part_no)
                body = re.sub(r"^#\s*第[〇一二三四五六七八九十\d]+部分\s*", f"# 第{cn}部分 ", md.strip(), count=1)
                parts.append(body)
        full_markdown = "\n\n".join(parts)
        state["_bidding_full_markdown"] = full_markdown

        if failed:
            await self._emit_thinking(f"{len(failed)}个模块生成异常：{failed}")
        else:
            await self._emit_thinking("全部模块生成完成")

        # Generate docx + persist
        output_path = ""
        if full_markdown and len(full_markdown) >= 200:
            try:
                output_path = await self._save_docx(state, full_markdown, report_type, type_name)
                if output_path:
                    await self._run_post_assembly_checks(state, output_path)
            except Exception as e:
                logger.error(f"Bidding docx assemble failed: {e}")
                await self._emit_thinking(f"DOCX生成失败：{e}")

        return {
            "status": "completed",
            "report_type": report_type,
            "markdown": full_markdown,
            "output_path": output_path,
            "chapters": total,
            "failed": failed,
        }

    # ═══════════════════════════════════════════════════════════════
    # Chapter Generation + Review (with content guardrails)
    # ═══════════════════════════════════════════════════════════════

    async def _generate_bidding_module(
        self, idx: int, ch_def: dict, state: dict, report_type: str,
        stream_queue: asyncio.Queue = None,
    ) -> bool:
        """Generate one bidding module with content-guardrails auto-retry.

        No user interaction — returns True on success.  When stream_queue is
        provided (parallel phase), events are buffered into it instead of the
        main frontend queue.
        """
        from .bidding_chapter_agent import BiddingChapterAgent

        output_queue = stream_queue or self._stream_queue
        max_attempts = 3
        for attempt in range(max_attempts):
            if self._cancelled:
                return False

            if attempt > 0:
                feedback = state.get("chapter_feedback") or ""
                if feedback:
                    state[f"_revision_feedback_bidding_{idx}"] = feedback

            try:
                agent = BiddingChapterAgent(
                    llm_service=self._llm,
                    report_type=report_type,
                    chapter_index=idx,
                    chapter_def=ch_def,
                )
                await agent.run(state, output_queue)
            except Exception as e:
                logger.error(f"Bidding module {idx} failed: {e}")
                if attempt < max_attempts - 1:
                    continue
                return False

            review = self._run_blocking_content_review(idx, state)
            if not review["passed"]:
                issues_desc = "；".join(i.get("description", "") for i in review["issues"])
                if attempt < max_attempts - 1:
                    state["chapter_feedback"] = issues_desc
                    await self._emit_thinking(f"模块{idx}审核未通过（{issues_desc}），自动重写...")
                    continue
                else:
                    await self._emit_thinking(
                        f"模块{idx}已达最大重试次数，仍存在：{issues_desc}")

            return True

        return True

    async def _review_bidding_module(
        self, idx: int, ch_def: dict, state: dict, report_type: str,
    ) -> bool:
        """Present bidding module for user review with approve/revise/skip."""
        max_rounds = 5
        for round_num in range(max_rounds):
            if self._cancelled:
                return False

            if round_num > 0:
                ok = await self._generate_bidding_module(idx, ch_def, state, report_type)
                if not ok:
                    return False

            await self._present_for_review(idx, ch_def, state)
            action = await self._wait_for_action(state)

            if action == "approve" or action is None:
                chapters = state.get("_bidding_chapters", {})
                if idx in chapters and isinstance(chapters[idx], dict):
                    chapters[idx]["status"] = "approved"
                await self._emit("chapter_confirmed", {"chapter": idx})
                return True
            elif action == "revise":
                await self._emit_thinking(f"根据修改意见重新编写第{idx}模块...")
                continue
            elif action == "skip":
                await self._emit("chapter_confirmed", {"chapter": idx})
                return True
            else:
                return True

        await self._emit_thinking(f"第{idx}模块已达最大修改轮次")
        return True

    async def _drain_chapter_buffer(
        self, idx: int, buffers: Dict[int, asyncio.Queue],
    ) -> None:
        """Replay a module's buffered SSE events to the main stream in order."""
        buffer = buffers.get(idx)
        if buffer is None:
            return
        while not buffer.empty():
            try:
                msg = buffer.get_nowait()
                if self._stream_queue:
                    await self._stream_queue.put(msg)
            except asyncio.QueueEmpty:
                break

    def _run_blocking_content_review(self, idx: int, state: dict) -> Dict[str, Any]:
        """Hard gate for placeholders and colloquial wording."""
        chapters = state.get("_bidding_chapters") or {}
        ch_data = chapters.get(idx, {}) if isinstance(chapters, dict) else {}
        markdown = ch_data.get("markdown", "") if isinstance(ch_data, dict) else ""
        try:
            from app.validation.content_guardrails import find_blocking_issues
            issues = find_blocking_issues(markdown)
        except Exception:
            issues = []
        return {"passed": not issues, "issues": issues}

    async def _present_for_review(self, idx: int, ch_def: dict, state: dict) -> None:
        """Emit chapter_review_prompt so the frontend shows the review card."""
        chapters = state.get("_bidding_chapters") or {}
        ch_data = chapters.get(idx, {})
        if not isinstance(ch_data, dict):
            return
        markdown = ch_data.get("markdown", "")
        word_count = len(markdown.replace("\n", "").replace(" ", ""))
        title = ch_def.get("title", f"第{idx}模块")

        await self._emit("chapter_review_prompt", {
            "chapter": idx,
            "title": title,
            "markdown": markdown,
            "word_count": word_count,
            "table_count": markdown.count("|---"),
            "source_count": 0,
            "is_regeneration": False,
            "message": f"第{idx}模块「{title}」已生成（{word_count}字），请审核。",
        })

        state["chapter_review_pending"] = True
        state["current_chapter"] = idx

    async def _wait_for_action(self, state: dict) -> Optional[str]:
        """Wait for user action using asyncio.Event."""
        event = state.get("_action_event")
        if isinstance(event, asyncio.Event):
            event.clear()
        state["user_action"] = ""

        try:
            if isinstance(event, asyncio.Event):
                await asyncio.wait_for(event.wait(), timeout=CHAPTER_REVIEW_TIMEOUT)
            else:
                waited = 0.0
                while waited < CHAPTER_REVIEW_TIMEOUT:
                    if state.get("user_action"):
                        return state.get("user_action")
                    await asyncio.sleep(1.0)
                    waited += 1.0
        except asyncio.TimeoutError:
            return None

        return state.get("user_action")

    def _signal_action(self, state: dict, action: str) -> None:
        state["user_action"] = action
        event = state.get("_action_event")
        if isinstance(event, asyncio.Event):
            event.set()

    # ═══════════════════════════════════════════════════════════════
    # DOCX Generation + Persist
    # ═══════════════════════════════════════════════════════════════

    async def _save_docx(self, state: dict, markdown: str, report_type: str, type_name: str) -> str:
        from app.services.bidding_docx_generator import BiddingDocxGenerator

        bidding_data = state.get("_bidding_data", {}) or {}
        generator = BiddingDocxGenerator()
        output_path = generator.generate(
            markdown_content=markdown,
            report_type=report_type,
            metadata={
                "session_id": state.get("session_id", ""),
                "project_name": bidding_data.get("bid_project_name", ""),
                "reference": bidding_data.get("bid_reference", ""),
            },
            state=state,
        )

        try:
            state["output_path"] = output_path
            state["status"] = "completed"
            state["report_title"] = bidding_data.get("bid_project_name", "") or type_name
            state["markdown_content"] = markdown
            session = state.get("_report_session")
            if session:
                from app.services.report_service import report_service
                rid = await report_service.persist_report(session)
                if rid:
                    logger.info(f"Bidding report saved to history (id={rid})")
                    await self._emit_thinking("报告已存入历史报告")
        except Exception as e:
            logger.warning(f"Bidding history persist failed: {e}")

        return output_path

    async def _emit(self, event: str, data) -> None:
        if self._stream_queue:
            if isinstance(data, str):
                data = {"content": data}
            await self._stream_queue.put({"event": event, "data": data})

    async def _run_post_assembly_checks(self, state: dict, output_path: str) -> None:
        """Run spec checker after DOCX assembly (bidding)."""
        try:
            from app.validation.spec_checker import check_report_async
            await self._emit_thinking("正在进行格式规范校验...")
            spec_report = await check_report_async(output_path)
            if spec_report:
                state["_spec_check"] = {
                    "score": spec_report.compliance_score,
                    "passed": spec_report.passed,
                    "passed_count": spec_report.passed_count,
                    "total_rules": spec_report.total_rules,
                }
                await self._emit_thinking(
                    f"格式规范校验完成：合规率 {spec_report.compliance_score:.0%}"
                    f"（{spec_report.passed_count}/{spec_report.total_rules}）"
                )
                await self._emit("validation_result", {
                    "passed": spec_report.passed,
                    "summary": f"格式规范合规率 {spec_report.compliance_score:.0%}",
                    "details": state["_spec_check"],
                })
            else:
                await self._emit_thinking("格式规范校验跳过：未找到规范文件")
        except Exception as e:
            logger.warning(f"Bidding spec check failed (non-critical): {e}")
