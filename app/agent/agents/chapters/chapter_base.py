"""ChapterAgentBase — common base class for all per-chapter agents.

Each chapter agent:
1. Checks data completeness in think()
2. Retrieves chapter-specific RAG context
3. Generates chapter content via LLM
4. Streams content to frontend via SSE
5. Presents for user confirmation
"""

import asyncio
import logging
import re
from typing import Dict, List, Any, Optional

from ..base_agent import BaseAgent
from ..knowledge_agent import get_knowledge_context_for_chapter
from app.validation.content_guardrails import AI_BUZZWORDS

logger = logging.getLogger(__name__)

# ── Learning hints cache (shared across all chapter agents) ──
_learning_cache = {"hints": {}, "updated": 0}  # hints by chapter_num


def _refresh_learning_cache():
    """Refresh cached learning hints from the learning service."""
    import time as _t
    now = _t.time()
    if now - _learning_cache["updated"] < 300:
        return  # still fresh
    try:
        import asyncio
        from app.services.learning_service import learning_service
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return  # can't run async in sync context
        async def _fetch():
            for ch in range(1, 11):
                hints = await learning_service.build_learning_hints("stability")
                common = await learning_service.get_common_issues("stability", 10)
                ch_issues = [c for c in common if _is_chapter_relevant(c["type"], ch)]
                if ch_issues:
                    _learning_cache["hints"][ch] = ch_issues
        loop.run_until_complete(_fetch())
        _learning_cache["updated"] = now
    except Exception:
        pass


def _is_chapter_relevant(issue_type: str, chapter_num: int) -> bool:
    """Check if an issue type is relevant to a specific chapter."""
    ch_specific = {
        "fabricated_data": [1, 3, 6, 8],  # chapters with numbers
        "data_validity": [3, 6, 8],        # support rate, scores
        "hallucinated_regulation": [2, 4, 9],  # legal references
        "invalid_range": [1, 6, 8],         # area, scores
    }
    if issue_type in ch_specific:
        return chapter_num in ch_specific[issue_type]
    return True  # general issues apply to all


def _get_cached_learning_hints(chapter_num: int) -> str:
    """Get learning hints for a specific chapter from the cache."""
    _refresh_learning_cache()
    ch_hints = _learning_cache["hints"].get(chapter_num, [])
    if not ch_hints:
        return ""
    lines = ["\n## ⚠️ 本章历史常见问题（请务必避免）"]
    for item in ch_hints:
        lines.append(f"- {item['label']}（近30天出现{item['count']}次）")
    return "\n".join(lines) + "\n"


class ChapterAgentBase(BaseAgent):
    """Base class for per-chapter generation agents.

    Subclasses define:
    - chapter_number: int (1-10)
    - chapter_title: str
    - rag_query_extra: str — additional RAG query terms beyond CHAPTER_QUERY_TEMPLATES
    - required_data_keys: List[str] — keys in filled_data/state needed for this chapter
    - key_tables: List[str] — table names this chapter should generate
    """

    chapter_number: int = 0
    chapter_title: str = ""
    rag_query_extra: str = ""
    required_data_keys: List[str] = []
    key_tables: List[str] = []

    # ---- Public API ----

    async def think(self, state: dict) -> Dict[str, Any]:
        """Check data availability and retrieve RAG context for this chapter."""
        missing = self._check_missing_data(state)

        # Build user data summary for display
        user_data = self._collect_user_data(state)

        # RAG retrieval
        rag_context = {}
        try:
            rag_context = await self._retrieve_rag(state)
        except Exception as e:
            logger.warning(f"Chapter {self.chapter_number} RAG retrieval failed: {e}")

        steps = [
            f"📋 检查第{self.chapter_number}章所需数据...",
            f"📊 已收集数据: {len(user_data)} 项",
        ]
        if missing:
            steps.append(f"⚠️ 缺失数据: {len(missing)} 项 — {', '.join(missing[:5])}")
        else:
            steps.append("✅ 数据完整，开始生成")

        if rag_context.get("sources"):
            steps.append(f"🔍 RAG检索到 {len(rag_context['sources'])} 条相关知识")

        return {
            "summary": f"第{self.chapter_number}章「{self.chapter_title}」— 数据检查完成",
            "steps": steps,
            "actions": [
                {"type": "generate_chapter", "chapter": self.chapter_number},
            ],
            "missing_data": missing,
            "rag_context": rag_context,
            "user_data": user_data,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Generate chapter content using LLM + RAG + user data.

        If state has _use_custom_prompt, use the master orchestrator's custom prompt.
        """
        missing = plan.get("missing_data", [])
        if missing:
            # Log warning but continue generating with available data
            logger.warning(
                f"Chapter {self.chapter_number}: missing data keys {missing}, "
                "generating with available data + placeholders"
            )
            if self._stream_queue:
                await self._stream_queue.put({
                    "event": "thinking",
                    "data": {"content": f"⚠️ 第{self.chapter_number}章部分数据缺失({', '.join(missing[:3])})，将使用【待补充】标注"},
                })

        rag_context = plan.get("rag_context", {})
        user_data = plan.get("user_data", {})

        # Send chapter_start event
        await self._emit_chapter_start()

        rag_sources = rag_context.get("sources", [])
        data_count = len(user_data) if isinstance(user_data, dict) else 0
        await self._emit_thinking(
            f"第{self.chapter_number}章「{self.chapter_title}」开始生成，"
            f"参考 {len(rag_sources)} 条法规范例、{data_count} 项用户数据"
        )

        # 🔴 Master orchestrator custom prompt — overrides agent's built-in prompt
        if state.get("_use_custom_prompt") and state.get("_custom_prompt"):
            system_prompt = "你是江苏众拓项目代理咨询有限公司的资深稳评工程师。"
            user_prompt = state["_custom_prompt"]
        else:
            system_prompt, user_prompt = self._build_llm_prompt(state, rag_context, user_data)

        # 🔴 真实地名/文号硬约束（无论用哪套 prompt 都注入，防止 LLM 编造地名）
        try:
            filled = state.get("filled_data", {})
            loc = filled.get("location") or filled.get("land_location") or ""
            doc_ref = filled.get("doc_reference") or filled.get("project_name") or ""
            constraint_parts = []
            if loc and not str(loc).startswith("【"):
                constraint_parts.append(f"- 项目位置唯一真实值：{loc}。报告中所有位置/坐落/社区/街道必须用这个，禁止编造任何其他地名")
            if doc_ref and not str(doc_ref).startswith("【"):
                constraint_parts.append(f"- 项目文号唯一真实值：{doc_ref}。报告中所有征地文号必须用这个，禁止使用其他文号")
            if constraint_parts:
                constraint_text = "\n🔒 项目真实数据约束（必须严格遵守，不得编造或替换）：\n" + "\n".join(constraint_parts) + "\n"
                # 追加到 user_prompt 末尾（最近原则，LLM 更重视）
                user_prompt = user_prompt + "\n\n" + constraint_text
        except Exception:
            pass

        # 🔴 Inject Few-Shot template example for format alignment (respects style)
        from app.agent.report_styles import get_few_shot
        style_name = state.get("_report_style") or state.get("report_style") or "jinhu"
        few_shot = get_few_shot(self.chapter_number, style_name)
        if few_shot:
            system_prompt = system_prompt + few_shot

        # 🔴 Inject chapter-specific image info into prompt
        image_note = self._build_chapter_image_note(state)
        if image_note:
            user_prompt = user_prompt + "\n\n" + image_note

        # 🔴 Inject table skeleton format instructions into EVERY chapter agent's prompt
        # This ensures per-chapter agent overrides of _build_llm_prompt still get table format
        table_instruction = self._build_table_skeleton_instruction(state)
        if table_instruction:
            user_prompt = user_prompt + "\n\n" + table_instruction

        # 🔴 专家蒸馏 skill 提示（追加到末尾，增强指令强度）
        # 无论用 _custom_prompt 还是默认 prompt，都在最后强注入专家反馈
        try:
            from app.services.master_orchestrator import _get_expert_skill_hints
            skill_hints = _get_expert_skill_hints(self.chapter_number)
            if skill_hints:
                user_prompt = user_prompt + "\n\n" + skill_hints
        except Exception:
            pass

        if not self._llm or getattr(self, '_use_template', False):
            # Template-based content — use RAG context to enrich templates
            content = self._fallback_content(state)
            # 🔴 Enrich template with RAG-retrieved regulation references
            enriched = self._enrich_with_rag(content, rag_context, user_data)
            tables = self._extract_tables(enriched)
            await self._emit_chapter_complete(enriched, tables,
                rag_context.get("sources", []) if isinstance(rag_context, dict) else [])
            return {
                "status": "generated",
                "markdown": enriched,
                "tables": tables,
                "sources": rag_context.get("sources", []) if isinstance(rag_context, dict) else [],
            }

        # 🔴 Retry loop for LLM calls (shared client can fail under parallel load)
        max_retries = 3
        last_error = None
        content = ""
        reasoning = ""

        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    await self._emit_thinking(f"正在生成第{self.chapter_number}章「{self.chapter_title}」...")
                else:
                    await self._emit_thinking(f"🔄 第{self.chapter_number}章重试({attempt+1}/{max_retries})...")
                    await asyncio.sleep(3 + attempt * 3)  # 3s, 6s, 9s backoff for API rate limits

                # 🔴 Guidance — agent decides structure based on professional experience
                writing_guide = (
                    "\n\n## 要求\n"
                    "1. 你是一位资深稳评工程师，根据专业知识决定本章的结构和内容深度\n"
                    "2. 数据用「材料3：本项目数据」中提供的，没有的不编造\n"
                    "3. 地名/村名/社区名必须来自项目数据，禁止使用LLM内部知识中的地名\n"
                    "4. 写短句，像老工程师写报告，不要AI翻译腔\n"
                    "5. 不用「第一/第二/第三」排比，不用「具有重要/切实/有力」等套词\n"
                    "6. ⚠️ 章节结构自由决定（不需要固定小节数），根据内容需要自然组织\n"
                    "7. ⚠️ 「材料5：本章可用图片」中如有图片，必须在正文对应位置插入标记\n"
                    "8. 表格只在必要时使用，不要为填模板而加表\n"
                )
                system_prompt = system_prompt + writing_guide

                result = await asyncio.wait_for(
                    self._llm.chat_with_reasoning(
                        messages=[{"role": "user", "content": user_prompt}],
                        system=system_prompt,
                        max_tokens=8000,
                        temperature=0.4,  # Higher temp for more natural variation
                    ),
                    timeout=150.0,
                )
                content = result.get("content", "")
                reasoning = result.get("reasoning", "")
                if content and len(content.strip()) >= 100:
                    break
                last_error = Exception(f"Short response ({len(content)} chars)")
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Chapter {self.chapter_number} attempt {attempt+1} failed: "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < max_retries - 1:
                    continue

        # If all retries failed, use fallback
        if not content or len(content.strip()) < 100:
            logger.error(f"Chapter {self.chapter_number} failed after {max_retries} attempts: {last_error}")
            fallback = self._fallback_content(state)
            tables = self._extract_tables(fallback)
            await self._emit_chapter_complete(fallback, tables, [])
            return {
                "status": "generated",
                "markdown": fallback,
                "tables": tables,
                "sources": [],
                "error": str(last_error) if last_error else "Unknown",
            }

        # Success path
        if reasoning:
            await self._emit_thinking(reasoning[:500])

        content = self._clean_content(content)
        # 🔴 金湖模板：不注入系统表格，LLM生成markdown表格即可
        tables = self._extract_tables(content)
        sources = rag_context.get("sources", [])

        word_count = len(content.replace("\n", "").replace(" ", ""))
        await self._emit_thinking(
            f"第{self.chapter_number}章生成完成：{word_count}字、{len(tables)}个表格"
        )

        await self._emit_chapter_complete(content, tables, sources)

        return {
            "status": "generated",
            "markdown": content,
            "tables": tables,
            "sources": sources,
        }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """Validate chapter content quality."""
        issues = []

        if result.get("status") == "missing_data":
            issues.append(f"第{self.chapter_number}章: 数据缺失，无法生成")
            return issues

        markdown = result.get("markdown", "")
        if not markdown:
            issues.append(f"第{self.chapter_number}章: 生成内容为空")
        elif len(markdown) < 100:
            issues.append(f"第{self.chapter_number}章: 内容过短（{len(markdown)}字）")

        # Check for placeholder markers
        placeholder_count = markdown.count("【待补充】")
        if placeholder_count > 3:
            issues.append(f"第{self.chapter_number}章: 包含{placeholder_count}个待补充标记")

        # Check for required key phrases per chapter
        key_phrases = self._get_key_phrases()
        for phrase in key_phrases:
            if phrase not in markdown:
                issues.append(f"第{self.chapter_number}章: 缺少关键词「{phrase}」")

        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """Write generated content into state. Preserves missing_data status."""
        chapters = state.setdefault("chapters", {})

        if self.chapter_number not in chapters:
            chapters[self.chapter_number] = {}

        chapter = chapters[self.chapter_number]
        chapter["number"] = self.chapter_number
        chapter["title"] = self.chapter_title

        # 🔴 Preserve the status from act() — "missing_data" vs "generated" vs "review"
        result_status = result.get("status", "review")
        if result_status == "missing_data":
            chapter["status"] = "missing_data"
            chapter["missing"] = result.get("missing", [])
            chapter["markdown"] = ""  # No content when data is missing
            return state

        chapter["markdown"] = result.get("markdown", "")
        chapter["tables"] = result.get("tables", [])
        chapter["rag_sources"] = result.get("sources", [])
        chapter["status"] = "review"

        # Store in generated_sections for cross-chapter reference
        generated = state.setdefault("generated_sections", {})
        section_key = f"chapter_{self.chapter_number}"
        generated[section_key] = {
            "title": self.chapter_title,
            "markdown": result.get("markdown", ""),
            "tables": result.get("tables", []),
        }

        return state

    # ---- Subclass Override Hooks ----

    def _build_chapter_image_note(self, state: dict) -> str:
        """Build image placement instructions for this chapter.

        Uses auto-built chapter image map from assembler + DataAnalysisAgent results.
        """
        # First check DataAnalysisAgent results
        chapter_images = state.get("_chapter_images", {})
        ch_key = str(self.chapter_number)
        ch_imgs = list(chapter_images.get(ch_key, []))

        # Fall back to auto-built chapter image map (from _get_session_images)
        if not ch_imgs:
            ch_map = state.get("_chapter_image_map", {})
            ch_imgs = ch_map.get(self.chapter_number, [])

        if not ch_imgs:
            return ""

        lines = [
            "## 📸 本章必须插入的图片",
            f"以下 {len(ch_imgs)} 张图片属于本章，必须在正文中引用：",
        ]
        ch = self.chapter_number
        for i, img in enumerate(ch_imgs, 1):
            if isinstance(img, dict):
                path = img.get("path", "")
                fname = path.rsplit("/", 1)[-1] if "/" in path else path
                caption = img.get("caption", fname)
                lines.append(f"{i}. `![图{ch}-{i} {caption}]` → 文件: {fname}")
            else:
                fname = str(img).rsplit("/", 1)[-1] if "/" in str(img) else str(img)
                lines.append(f"{i}. `![图{ch}-{i} {fname}]`")

        lines.append(f"\n🔴 在正文中必须插入以上图片标记（如 `![图{ch}-1 xxx]`），每个标记独占一行，放在相关段落之后。")
        return "\n".join(lines)

    def _enrich_with_rag(self, content: str, rag_context: dict, user_data: dict) -> str:
        """Enrich template content with RAG-retrieved knowledge references.

        Injects relevant regulation excerpts, methodology details, and case examples
        from the knowledge base into the template output. Only adds references that
        are actually relevant to the chapter content — no hallucination.
        """
        if not rag_context or not isinstance(rag_context, dict):
            return content

        import re as _re_enrich

        # Extract RAG context strings
        regulation = rag_context.get("regulation_context", "")
        chapter_ctx = rag_context.get("chapter_context", "")
        example = rag_context.get("example_context", "")

        # Build enrichment block: only add specific, useful references
        enrich_lines = []
        if regulation and len(regulation) > 50:
            # Extract key regulation references (lines with 第X条 or standard numbers)
            key_refs = _re_enrich.findall(r'《[^》]+》第[^条]+条|DB32[^，。\n]{5,30}', regulation)
            if key_refs:
                unique_refs = list(dict.fromkeys(key_refs))[:5]  # dedup, max 5
                enrich_lines.append("\n\n### 相关法规依据\n")
                for ref in unique_refs:
                    enrich_lines.append(f"- {ref}")

        if example and len(example) > 100:
            # Add a short methodology note if available
            method_match = _re_enrich.search(r'(评估方法|调查方式|评分标准)[：:]\s*(.+?)(?:\n|$)', example, _re_enrich.DOTALL)
            if method_match:
                enrich_lines.append(f"\n> 参考方法：{method_match.group(2).strip()[:200]}")

        if enrich_lines:
            # Append enrichment at the end of the chapter content
            content = content.rstrip() + "\n" + "\n".join(enrich_lines) + "\n"

        return content

    def _build_llm_prompt(
        self,
        state: dict,
        rag_context: Dict[str, Any],
        user_data: Dict[str, Any],
    ) -> tuple:
        """Build (system_prompt, user_prompt) for LLM generation.

        Subclasses override to provide chapter-specific prompts.
        Returns (system_prompt: str, user_prompt: str).
        """
        system_prompt = self._default_system_prompt(state)
        user_prompt = self._default_user_prompt(state, rag_context, user_data)
        return system_prompt, user_prompt

    def _fallback_content(self, state: dict) -> str:
        """Template-based fallback when LLM is unavailable.

        Subclasses override to provide chapter-specific fallback content.
        """
        return (
            f"## 第{self._chapter_num_cn()}章 {self.chapter_title}\n\n"
            f"【待生成：LLM服务不可用，请重试或手动补充本章内容】\n\n"
        )

    def _get_key_phrases(self) -> List[str]:
        """Key phrases that should appear in this chapter.

        Subclasses override to provide chapter-specific quality checks.
        """
        return []

    # ---- Internal Helpers ----

    def _chapter_num_cn(self) -> str:
        """Convert chapter number to Chinese numeral."""
        cn = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
        return cn[self.chapter_number] if self.chapter_number <= 10 else str(self.chapter_number)

    async def _retrieve_rag(self, state: dict) -> Dict[str, Any]:
        """Retrieve chapter-specific RAG context from knowledge base.

        Uses shared RAG service singleton to avoid per-agent client creation.
        KnowledgeAgent results are preferred if already cached.
        """
        # Use KnowledgeAgent cache if available (avoids duplicate RAG queries)
        kb_cache = state.get("_knowledge_cache", {})
        if self.chapter_number in kb_cache:
            cached = kb_cache[self.chapter_number]
            return {
                "chapter_context": cached.get("regulation_context", ""),
                "example_context": cached.get("example_fragments", ""),
                "local_regulation_context": cached.get("format_template", ""),
                "sources": [],
            }

        try:
            from app.rag.rag_service import rag_service
            from app.agent.chapter_definitions import get_chapter_rag_query

            session_id = state.get("session_id", "")
            project_context = state.get("project_context", "")

            base_query = get_chapter_rag_query(self.chapter_number)
            if self.rag_query_extra:
                base_query += " " + self.rag_query_extra

            result = await rag_service.retrieve_for_chapter(
                chapter_number=self.chapter_number,
                session_id=session_id,
                project_context=project_context + " " + base_query,
                n_results=20,
                domain=state.get("_domain") or state.get("_conversation_domain"),
            )

            # Cache in state
            rag_cache = state.setdefault("_rag_cache", {})
            rag_cache[self.chapter_number] = result

            return result

        except Exception as e:
            logger.warning(f"Chapter {self.chapter_number} RAG failed: {e}")
            return {}

    def _collect_user_data(self, state: dict) -> Dict[str, Any]:
        """Extract relevant user-provided data for this chapter from all sources.

        Handles the mismatch between MasterAgent's placeholder-key storage
        (e.g., highlight_5_0 → "淮安市洪泽区人民政府") and ChapterAgent's
        human-readable key expectations (e.g., "org_name").
        """
        data = {}

        # From filled_data (may use placeholder keys OR human keys)
        filled = state.get("filled_data", {})

        # From structured_data (typed step data)
        structured = state.get("structured_data", {})

        # From PDF texts
        pdf_texts = state.get("_pdf_texts", {})
        pdf_contents = state.get("_pdf_contents", [])

        # 🔴 From chapter-specific context (set by ChapterOrchestrator from outline)
        chapter_context = state.get("_chapter_context", {})
        if chapter_context:
            for k, v in chapter_context.items():
                if k not in data or not data[k]:
                    data[k] = v

        # From project context
        project_context = state.get("project_context", "")

        # From previous chapters' generated content
        generated = state.get("generated_sections", {})

        # ---- Strategy 1: Direct key lookup ----
        for key, value in filled.items():
            data[key] = value

        # ---- Strategy 2: Smart extraction from placeholder-keyed values ----
        # Map common display names to expected data keys
        # The MasterAgent fills data under placeholder keys but the values
        # contain the actual data. We scan all values for known patterns.
        filled_values = list(filled.values())
        filled_text = " ".join(str(v) for v in filled_values if v)

        # Try to extract known data types from any filled_data value
        extraction_rules = [
            # (key_name, regex_pattern, extraction_group)
            ("org_name", [
                r'(?:淮安|南京|南通|苏州|无锡|常州|镇江|扬州|泰州|盐城|徐州|连云港|宿迁|金湖|盱眙|洪泽)\S{0,20}(?:人民政府|管理委员会|街道办事处)',
                r'(?:责任单位|决策单位|征收主体)[：:]\s*(\S{2,30})',
            ]),
            ("implement_unit", [
                r'江苏众拓项目代理咨询有限公司',
            ]),
            ("location", [
                r'(?:位于|坐落|位置)[：:]?\s*(.{5,80}?)(?:。|，|\n|$)',
                r'(?:淮安|南京|南通|苏州)\S{0,10}(?:区|县|市)\S{0,10}(?:街道|镇|乡)\S{0,10}(?:社区|村)',
            ]),
            ("area_m2", [
                r'(\d{4,7})\s*(?:平方米|㎡)',
            ]),
            ("area_mu", [
                r'(\d{2,4}\.?\d*)\s*亩',
            ]),
            ("land_use", [
                r'(?:用途|地类)[：:]\s*(\S{2,20})',
                r'(商业服务业设施用地|工业用地|住宅用地|公共管理与公共服务用地|交通运输用地)',
            ]),
            ("funding", [
                r'(?:资金|投资|补偿)[：:]{0,1}\s*(\d[\d.,，]+)\s*(?:万元|亿元)',
            ]),
            ("total_samples", [
                r'(?:调查|问卷|走访).{0,10}(\d+)\s*(?:人|户|份)',
            ]),
            ("support_rate", [
                r'(\d{2,3}\.?\d*)\s*%\s*(?:支持|赞成|同意)',
            ]),
            # 🔴 New: detailed PDF extraction fields
            ("household_count", [
                r'(?:涉及|共涉及)\s*(\d+)\s*(?:户|农户)',
                r'(\d+)\s*(?:户|农户)',
            ]),
            ("population_count", [
                r'(?:涉及|被征地)\s*(\d+)\s*(?:人|人口|名)',
                r'(\d+)\s*(?:人|人口)',
            ]),
            ("compensation_standard", [
                r'(?:补偿标准|综合补偿)[^\d]*(\d[\d,.]*)\s*(?:元|万元)',
            ]),
            ("total_compensation", [
                r'(?:总费用|总补偿|征地费用|补偿总额)[^\d]*(\d[\d,.]*)\s*(?:元|万元)',
            ]),
            ("resettlement_subsidy", [
                r'(?:安置补助|安置费)[^\d]*(\d[\d,.]*)\s*(?:元|万元)',
            ]),
            ("social_security_fund", [
                r'(?:社会保障|社保)[^\d]*(\d[\d,.]*)\s*(?:元|万元)',
            ]),
        ]

        for key_name, patterns in extraction_rules:
            if key_name in data and data[key_name]:
                continue  # Already have this data
            for pattern in patterns:
                match = re.search(pattern, filled_text)
                if match:
                    # Use group(1) if exists, otherwise group(0)
                    try:
                        data[key_name] = match.group(1) if match.lastindex else match.group(0)
                    except (IndexError, AttributeError):
                        data[key_name] = match.group(0)
                    break

        # ---- Strategy 3: Collect structured data ----
        # All known field names that chapter agents may request
        _known_fields = {
            "org_name", "implement_unit", "location", "area_m2", "area_mu",
            "land_use", "funding", "total_samples", "support_rate",
            "support_count", "oppose_count", "stakeholder_demands",
            "sample_coverage_rate", "survey_method", "household_count",
            "population_count", "compensation_standard", "total_compensation",
            "doc_reference", "report_title", "red_line_map", "earliest_date",
            "legal_basis_docs", "neutral_count", "conditional_support_count",
        }
        for step_key, step_data in structured.items():
            if isinstance(step_data, dict):
                for k, v in step_data.items():
                    if k not in ("images", "attachments", "user_input_raw", "_files_processed"):
                        data[f"{step_key}.{k}"] = v
                        # Expose ALL known fields at top-level for chapter agents
                        if k in _known_fields:
                            if k not in data or not data[k]:
                                data[k] = v

        # ---- Strategy 4: PDF text ----
        if project_context:
            data["project_context"] = project_context

        all_pdf_text = "\n".join(pdf_texts.values()) if pdf_texts else ""
        for pc in pdf_contents:
            all_pdf_text += f"\n【{pc.get('name', 'PDF')}】\n{pc.get('text', '')}"
        if all_pdf_text:
            # Clean garbled text from PDF extraction
            cleaned = self._clean_document_text(all_pdf_text)
            data["pdf_raw_text"] = cleaned[:30000]

        # ---- Strategy 5: Report title and session ----
        data["report_title"] = state.get("report_title", "")
        data["session_id"] = state.get("session_id", "")

        # ---- Strategy 6: Previous chapters ----
        prev_chapters = {}
        for ch_num in range(1, self.chapter_number):
            ch_key = f"chapter_{ch_num}"
            if ch_key in generated:
                prev_chapters[ch_num] = generated[ch_key].get("markdown", "")[:1000]
        if prev_chapters:
            data["_previous_chapters"] = prev_chapters

        # 🔴 Strategy 7: Extracted PDF tables and images from material ingestion
        facts = state.get("_project_material_facts", {})
        summary = state.get("_project_material_summary", {})
        materials = state.get("_project_materials", [])

        # Collect extracted tables (structured data from PDFs)
        extracted_tables = facts.get("_extracted_tables", [])
        if not extracted_tables:
            extracted_tables = summary.get("facts", {}).get("_extracted_tables", [])
        if not extracted_tables:
            for m in materials:
                if isinstance(m, dict):
                    meta = m.get("metadata", {})
                    if isinstance(meta, dict):
                        tbls = meta.get("extracted_tables", [])
                        if tbls:
                            extracted_tables.extend(tbls)

        if extracted_tables:
            data["_extracted_tables"] = extracted_tables

        # Collect extracted images from PDFs
        extracted_images = facts.get("_extracted_images", [])
        if not extracted_images:
            extracted_images = summary.get("facts", {}).get("_extracted_images", [])
        if not extracted_images:
            for m in materials:
                if isinstance(m, dict):
                    meta = m.get("metadata", {})
                    if isinstance(meta, dict):
                        imgs = meta.get("extracted_images", [])
                        if imgs:
                            extracted_images.extend(imgs)
        if extracted_images:
            data["_extracted_images"] = extracted_images

        # 🔴 Strategy 8: Extract/derive survey statistics
        survey_stats = self._extract_survey_stats(data.get("pdf_raw_text", ""), filled)
        # Fallback: derive from project facts if PDF has no extractable text
        if not survey_stats:
            survey_stats = self._derive_survey_from_facts(filled)
        if survey_stats:
            data["survey_stats"] = survey_stats
            for k, v in survey_stats.items():
                if k not in data or not data[k]:
                    data[k] = v

        return data

    @staticmethod
    def _derive_survey_from_facts(filled: dict) -> dict:
        """Derive survey statistics from known project facts when OCR isn't available.

        🔴 ONLY uses explicitly provided data (household_count, support_rate from filled_data).
        Does NOT fabricate or estimate any percentages or distributions.
        """
        stats = {}
        total = int(filled.get("household_count", 0) or 0)
        support_rate_str = filled.get("support_rate", "")

        # Parse support_rate
        support_rate = 0.0
        if support_rate_str:
            try:
                support_rate = float(str(support_rate_str).replace('%', ''))
            except (ValueError, TypeError):
                pass

        if total > 0:
            stats["total_surveys"] = str(total)
        if support_rate > 0 and total > 0:
            support = int(total * support_rate / 100)
            oppose = total - support
            stats["support_count"] = str(support)
            stats["support_rate"] = f"{support_rate:.1f}%"
            stats["oppose_count"] = str(oppose)
            stats["oppose_rate"] = f"{100 - support_rate:.1f}%"
        # 🔴 NO fabrication: don't estimate know_rate, conditional_support, etc.

        return stats

    @staticmethod
    def _extract_survey_stats(pdf_text: str, filled: dict) -> dict:
        """Extract survey statistics from PDF text content.

        Looks for patterns like:
        - 共发放问卷XXX份，回收XXX份
        - 支持XX人，占XX%
        - 反对XX人，占XX%
        """
        import re
        stats = {}

        # Total surveys
        m = re.search(r'(?:发放|回收|共).{0,5}问卷.{0,5}(\d+)\s*(?:份|张)', pdf_text)
        if m:
            stats["total_samples"] = m.group(1)
        m = re.search(r'(\d+)\s*(?:份|张).{0,3}问卷', pdf_text)
        if m and "total_samples" not in stats:
            stats["total_samples"] = m.group(1)

        # Support count/rate
        m = re.search(r'支持.{0,5}(\d+)\s*(?:人|户|份)', pdf_text)
        if m:
            stats["support_count"] = m.group(1)
        m = re.search(r'(?:支持率|支持).{0,3}(\d+\.?\d*)\s*%', pdf_text)
        if m:
            stats["support_rate"] = m.group(1)
        m = re.search(r'(\d+\.?\d*)\s*%[\s，。]*支持', pdf_text)
        if m and "support_rate" not in stats:
            stats["support_rate"] = m.group(1)

        # Oppose count
        m = re.search(r'反对.{0,5}(\d+)\s*(?:人|户|份)', pdf_text)
        if m:
            stats["oppose_count"] = m.group(1)

        # Neutral/conditional support
        m = re.search(r'(?:条件支持|有条件支持|中立|无所谓).{0,5}(\d+)\s*(?:人|户|份)', pdf_text)
        if m:
            stats["neutral_count"] = m.group(1)

        # 座谈会 attendees
        m = re.search(r'(?:座谈|会议).{0,5}参加.{0,3}(\d+)\s*(?:人|名)', pdf_text)
        if m:
            stats["meeting_attendees"] = m.group(1)

        # 公示时间
        m = re.search(r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日).{0,10}(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)', pdf_text)
        if m:
            stats["announcement_period"] = f"{m.group(1)}至{m.group(2)}"

        # Stakeholder demands
        demands = re.findall(r'(?:诉求|要求|希望|建议|意见)[：:]\s*(.{10,100}?)(?:[。；;]|\n)', pdf_text)
        if demands:
            stats["stakeholder_demands"] = "；".join(demands[:3])

        return stats

    def _check_missing_data(self, state: dict) -> List[str]:
        """Check which required data keys are missing or empty."""
        user_data = self._collect_user_data(state)
        missing = []

        for key in self.required_data_keys:
            value = user_data.get(key, "")
            if not value or (isinstance(value, str) and not value.strip()):
                missing.append(key)

        return missing

    @staticmethod
    def _clean_document_text(text: str) -> str:
        """Remove garbled/binary characters from extracted document text."""
        import re
        # Remove non-printable characters except common whitespace
        text = re.sub(r'[^\x20-\x7E一-鿿　-〿＀-￯\n\r\t]', '', text)
        # Remove lines that are mostly garbled (>50% non-CJK non-ASCII)
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            total = len(line)
            if total == 0:
                cleaned_lines.append(line)
                continue
            cjk_ascii = sum(1 for c in line if ('一' <= c <= '鿿') or ('\x20' <= c <= '\x7E'))
            if cjk_ascii / max(total, 1) > 0.4:
                cleaned_lines.append(line)
        text = '\n'.join(cleaned_lines)
        # Collapse excessive whitespace
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        text = re.sub(r' {3,}', '  ', text)
        return text

    def _clean_content(self, content: str) -> str:
        """Clean LLM output: strip garbled text, source annotations, placeholders, fix table formatting, etc."""
        import re

        # Strip garbled/binary characters first
        content = re.sub(r'[^\x20-\x7E一-鿿　-〿＀-￯\n\r\t]', '', content)
        # Strip source annotations like 【数据来源：XXX】
        content = re.sub(r'【数据来源[：:][^】]*】', '', content)
        content = re.sub(r'\[数据来源[：:][^\]]*\]', '', content)
        # Strip 【待补充】 and similar placeholders
        content = re.sub(r'【待补充[^】]*】', '', content)
        content = re.sub(r'\[待补充[^\]]*\]', '', content)
        # Clean up empty table cells
        content = re.sub(r'\|\s*\|\s*', '| — |', content)
        content = re.sub(r'：\s*\n', '：—\n', content)

        # Strip JSON blocks
        content = re.sub(r'```json\s*[\s\S]*?```', '', content)
        content = re.sub(r'```\s*', '', content)

        # 🔴 Fix garbled single-line tables
        content = self._fix_garbled_tables(content)

        # 🔴 Remove unresolved image markers (keep caption text only)
        content = re.sub(r'!\[([^\]]*)\](?:\([^)]*\))?', r'[\1]', content)

        # 🔴🔴 Force-replace known LLM hallucinated community names with correct project data
        # LLM often uses its internal knowledge of Huai'an geography to "fill in" community names
        content = self._fix_hallucinated_names(content)

        # Strip AI meta-text aggressively — remove ALL self-referential AI fluff
        meta_patterns = [
            r'好的[，,]?\s*作?为?稳评报告.*?(?:\n|$)',
            r'好的[，,]?\s*根据您.*?(?:\n|$)',
            r'好的[，,]?\s*遵照您.*?(?:\n|$)',
            r'好的[，,]?\s*我将.*?(?:\n|$)',
            r'以下是为您.*?(?:\n|$)',
            r'^为您撰写.*$',
            r'^>.*$',  # Blockquote-style
            r'通过系统识别[，,]?[^。]*[。]',
            r'依据指令要求[，,]?[^。]*[。]',
            r'本次评估综合运用[^。]*?[：—]',
            r'^注[：:].*$',  # Note/annotation lines
            r'^说明[：:].*$',
            r'仿照.*?模板.*?撰写.*?(?:\n|$)',
            r'^\*{3,}.*$',  # Separator lines
            r'^---+\s*$',  # HR lines
        ]
        for pat in meta_patterns:
            content = re.sub(pat, '', content, flags=re.MULTILINE)

        return content.strip()

    def _extract_tables(self, markdown: str) -> List[Dict[str, Any]]:
        """Extract table data from markdown content."""
        import re
        tables = []

        # Find markdown tables (| ... | ... |)
        table_pattern = r'(\|.+\|(?:\n\|.+\|)+)'
        matches = re.findall(table_pattern, markdown)

        for i, match in enumerate(matches):
            lines = match.strip().split('\n')
            if len(lines) >= 2:
                # Parse header
                headers = [h.strip() for h in lines[0].split('|') if h.strip()]
                # Parse rows (skip separator line)
                rows = []
                for line in lines[2:]:
                    cells = [c.strip() for c in line.split('|') if c.strip()]
                    if cells:
                        rows.append(cells)

                tables.append({
                    "index": i,
                    "headers": headers,
                    "rows": rows,
                    "row_count": len(rows),
                })

        return tables

    async def _emit_chapter_start(self) -> None:
        """Emit chapter_start SSE event with streaming content."""
        if not self._stream_queue:
            return

        await self._stream_queue.put({
            "event": "chapter_start",
            "data": {
                "chapter": self.chapter_number,
                "title": self.chapter_title,
            },
        })

        # Stream chapter content in chunks for real-time display
        # The actual streaming is done by the caller via chapter_stream events
        # Here we just emit the start signal

    async def _emit_chapter_complete(
        self,
        markdown: str,
        tables: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
    ) -> None:
        """Emit chapter_complete SSE event."""
        if not self._stream_queue:
            return

        # Stream the content in chunks for frontend chapter_stream handling
        chunk_size = 200
        for i in range(0, len(markdown), chunk_size):
            await self._stream_queue.put({
                "event": "chapter_stream",
                "data": {
                    "chapter": self.chapter_number,
                    "delta": markdown[i:i + chunk_size],
                },
            })

        await self._stream_queue.put({
            "event": "chapter_complete",
            "data": {
                "chapter": self.chapter_number,
                "title": self.chapter_title,
                "markdown": markdown,
                "tables": tables,
                "sources": sources,
            },
        })

    # ---- Default Prompt Templates ----

    def _default_system_prompt(self, state: dict) -> str:
        """Default system prompt for chapter generation — 去AI化风格."""
        project_name = state.get("report_title", "本项目")

        kb_ctx = get_knowledge_context_for_chapter(state, self.chapter_number)
        kb_spec = kb_ctx.get("structure_spec", {})
        kb_constraints = kb_ctx.get("writing_constraints", {})

        sections_list = kb_constraints.get("sections", [])
        required_tables = kb_constraints.get("required_tables", [])
        min_w = kb_constraints.get("min_words", 600)
        max_w = kb_constraints.get("max_words", 3500)

        from app.agent.agents.knowledge_agent import TEMPLATE_TABLE_SKELETONS
        has_skeletons = bool(TEMPLATE_TABLE_SKELETONS.get(self.chapter_number, {}))

        skeleton_rule = ""
        if has_skeletons:
            skeleton_rule = (
                "\n## 🔒 表格填充规则\n"
                "表格列分为三类：🔒固定列(照抄模板) / ✏️数据列(根据项目数据填入) / 📐骨架列(行号标签)。\n"
                "固定列一字不改，数据列缺失填【待补充】。\n"
            )

        try:
            from app.agent.template_headings import format_headings_for_prompt
            heading_constraint = format_headings_for_prompt(self.chapter_number)
        except Exception:
            heading_constraint = ""

        return (
            f"你是江苏众拓项目代理咨询有限公司的稳评工程师，在淮安做了8年征地稳评。\n"
            f"根据你的专业经验撰写第{self._chapter_num_cn()}章「{self.chapter_title}」。\n\n"
            f"## 核心原则\n"
            f"- 根据DB32/T4013-2021规范和你8年的实战经验来组织内容\n"
            f"- 结构自由决定，不需要固定小节数，内容说到位就停\n"
            f"- 所有地名只用：淮安市洪泽区朱坝街道三圩社区二组、三组、六组\n\n"
            f"## ⛔ 禁止\n"
            f"- 禁用词：{'、'.join(AI_BUZZWORDS)}\n"
            f"- 禁止「第一/第二/第三」工整排比\n"
            f"- 禁止编造数据，用户没提供的写【待补充】\n"
            f"- 禁止AI翻译腔，像真人工程师写报告\n\n"
            f"## ✅ 鼓励\n"
            f"- 段落长短错落，短句穿插长段\n"
            f"- 基层过渡语：「结合入户走访」「根据社区书记反馈」\n"
            f"- 结论适度模糊：「大概率」「初步判断」「需重点关注」\n"
            f"- 政策引用精简，只列本章直接相关的\n"
            f"- 实施单位：江苏众拓项目代理咨询有限公司\n"
            f"- 表格用 markdown 语法写，有数据支撑才写，缺数据写【待补充】\n"
            f"{skeleton_rule}\n"
        )

    def _default_user_prompt(
        self,
        state: dict,
        rag_context: Dict[str, Any],
        user_data: Dict[str, Any],
    ) -> str:
        """Default user prompt for chapter generation."""
        # RAG context
        chapter_ctx = rag_context.get("chapter_context", "")
        example_ctx = rag_context.get("example_context", "")
        local_ctx = rag_context.get("local_regulation_context", "")

        # Dynamic few-shot from similar historical reports
        from app.services.few_shot_service import get_cached_few_shot
        dynamic_examples = get_cached_few_shot(self.chapter_number)

        # User data summary
        data_summary = ""
        for key, value in user_data.items():
            if key.startswith("_"):
                continue
            val_str = str(value)
            if len(val_str) > 3000:
                val_str = val_str[:3000] + "..."
            data_summary += f"- **{key}**: {val_str}\n"

        # Previous chapters — extract key facts, not just raw markdown
        prev_chapters = user_data.get("_previous_chapters", {})
        prev_summary = ""
        if prev_chapters:
            prev_summary = "## 前序章节关键信息\n"
            for ch_num, ch_content in prev_chapters.items():
                facts = self._extract_key_facts(str(ch_content))
                prev_summary += f"### 第{self._num_to_cn(ch_num)}章: {facts}\n"
            prev_summary += "\n"

        # 🔴 Build the table format enforcement (at END for recency bias)
        from app.agent.agents.knowledge_agent import TEMPLATE_TABLE_SKELETONS
        table_enforcement = ""
        skeletons = TEMPLATE_TABLE_SKELETONS.get(self.chapter_number, {})
        if skeletons:
            table_names = list(skeletons.keys())
            table_enforcement = (
                f"\n## ⛔ 表格格式强制要求（最重要！放在最后强调！）\n"
                f"本章必须生成以下表格：{', '.join(table_names)}\n"
                f"**绝对禁止使用2列键值对格式（如 | 项目 | 内容 |）！**\n"
                f"每张表必须按照「🔒 模板表格骨架」中指定的列数生成：\n"
            )
            for tname, tskel in skeletons.items():
                cols = tskel.get("columns", [])
                ncols = len(cols)
                fill_types = tskel.get("fill_types", {})
                table_enforcement += f"- **{tname}**: 必须{ncols}列 — {' | '.join(cols)}\n"
            table_enforcement += "\n**如果你输出2列表格，系统将判定为不合格，需要重新生成！**\n"

        # 🔴 Template content reuse — extract matching sections from historical report
        template_content = ""
        try:
            from app.agent.template_content import template_content_provider
            template_content = template_content_provider.build_template_prompt(
                self.chapter_number, state.get("filled_data", {})
            )
        except Exception:
            pass

        seed_ctx = self._build_seed_context()
        template_content = ""
        try:
            from app.agent.template_content import template_content_provider
            template_content = template_content_provider.build_template_prompt(
                self.chapter_number, state.get("filled_data", {})
            )
        except Exception:
            pass

        return (
            f"## 写作材料（按优先级排列）\n\n"
            f"### 材料1：DB32/T4013-2021规范 + 优秀范文（最重要，优先参考）\n"
            f"{example_ctx[:8000] if example_ctx else '（无）'}\n"
            f"{template_content}\n"
            f"{dynamic_examples}\n\n"
            f"### 材料2：法规标准\n"
            f"{local_ctx[:3000] if local_ctx else ''}\n"
            f"{chapter_ctx[:2000]}\n\n"
            f"### 材料3：本项目数据（用户提供，权威数据）\n"
            f"项目名称：{state.get('report_title', '未指定')}\n"
            f"位置：{state.get('project_context', '')[:300]}\n"
            f"{data_summary}\n"
            f"⛔ 涉及村组只允许：三圩社区二组、三组、六组。禁止出现任何其他社区名/村名。\n"
            f"🔒 用户提供的公告/勘测报告中的数据是权威数据，禁止编造或修改。\n"
            f"   如无数据写\"以正式公告为准\"，不要编造具体数字。\n"
            f"{prev_summary}\n"
            f"### 材料5：本章可用图片\n"
            f"{state.get('_chapter_image_guide', '（无图片）')}\n"
            f"### 材料4：原始文件\n"
            f"{user_data.get('pdf_raw_text', '')[:6000]}\n\n"
            f"---\n"
            f"## 写作指令\n"
            f"以材料1为内容参照、材料2为法规依据、材料3为数据来源、材料4为原始凭证。\n\n"
            f"### 风格要点\n"
            f"1. 模仿材料1的行文节奏，但把里面的项目数据换成本项目的\n"
            f"2. 段落长短不要求均匀，有1-2句短段，也有5-6句长段\n"
            f"3. 法规引用自然嵌入论述，不单独罗列法条清单\n"
            f"4. 数据后面简单分析一两句就行，不用每次都长篇展开\n"
            f"5. 公文语体但不要写得像论文，要有基层工作痕迹\n"
            f"6. 每段开头方式多样化：有时候用「根据...」有时候直接陈述事实\n"
            f"7. 不要写「总-分-总」结构——开头直接说事，结尾不用总结句\n"
            f"8. ⛔ 禁止：据调查显示、经综合分析、通过系统识别、依据指令要求 等机器翻译腔\n"
            f"9. ⛔ 禁止「综上所述」「整体而言」「总体来看」等总结套话\n"
            f"10. 缺失数据用【待补充】，不要编造\n"
            f"{_get_cached_learning_hints(self.chapter_number)}"
            f"{table_enforcement}"
        )

    @staticmethod
    def _build_seed_context() -> str:
        """Load relevant seed data for injection into chapter prompts."""
        try:
            from pathlib import Path
            seed_dir = Path(__file__).parent.parent.parent.parent.parent / "seed_data"
            parts = []

            # DB32/T 4937-2024 — land acquisition specific standard
            f = seed_dir / "db32_4937_2024_land_acquisition.md"
            if f.exists():
                parts.append(f.read_text(encoding='utf-8')[:3000])

            # National guideline 428
            f = seed_dir / "national_guideline_428.md"
            if f.exists():
                parts.append(f.read_text(encoding='utf-8')[:3000])

            # Professional methodology
            f = seed_dir / "professional_methodology.md"
            if f.exists():
                parts.append(f.read_text(encoding='utf-8')[:2000])

            # Case study templates
            f = seed_dir / "case_study_templates.md"
            if f.exists():
                parts.append(f.read_text(encoding='utf-8')[:2000])

            # Social risk theory
            f = seed_dir / "social_risk_theory.md"
            if f.exists():
                parts.append(f.read_text(encoding='utf-8')[:1500])

            combined = "\n\n".join(parts)
            return combined[:12000] if combined.strip() else ""
        except Exception:
            return ""

    @staticmethod
    def _build_local_regulation_context(state: dict, rag_context: dict) -> str:
        """Build comprehensive local regulation context for prompt injection.

        Always includes DB32/T4013-2021 + 江苏省征地补偿办法 if available.
        Falls back to RAG-retrieved local context, then to seed data.
        """
        parts = []

        # 1. Try RAG-retrieved local regulations
        local_ctx = rag_context.get("local_regulation_context", "")
        if local_ctx and len(local_ctx) > 100:
            parts.append(local_ctx[:8000])

        # 2. Always inject key standards from seed data if not in RAG
        try:
            from pathlib import Path
            seed_dir = Path(__file__).parent.parent.parent.parent.parent / "seed_data"
            # DB32/T4013-2021 — the core scoring standard
            db32_path = seed_dir / "db32_t4013_2021.md"
            if db32_path.exists():
                db32_text = db32_path.read_text(encoding='utf-8')
                if "DB32/T4013" not in (local_ctx or ""):
                    parts.append(f"## DB32/T4013-2021 第三方社会稳定风险评估规范\n{db32_text[:6000]}")
            # Stability assessment guideline
            guide_path = seed_dir / "stability_assessment_guideline.md"
            if guide_path.exists():
                guide_text = guide_path.read_text(encoding='utf-8')
                if len(guide_text) > 100:
                    parts.append(f"## 社会稳定风险评估指南\n{guide_text[:4000]}")
            # Emergency response law
            er_path = seed_dir / "emergency_response_law.md"
            if er_path.exists() and "突发事件" not in str(parts):
                er_text = er_path.read_text(encoding='utf-8')
                parts.append(f"## 突发事件应对法\n{er_text[:2000]}")
        except Exception:
            pass

        # 3. Always inject land management law key articles
        try:
            from pathlib import Path
            seed_dir = Path(__file__).parent.parent.parent.parent.parent / "seed_data"
            lm_path = seed_dir / "land_management_law.md"
            if lm_path.exists():
                lm_text = lm_path.read_text(encoding='utf-8')
                # Only include key articles (45-48 cover land acquisition)
                parts.append(f"## 土地管理法（征收相关条款）\n{lm_text[:3000]}")
        except Exception:
            pass

        combined = "\n\n".join(parts)
        if not combined.strip():
            return "（未检索到地区规范，请按DB32/T4013-2021标准撰写）"
        return combined

    @staticmethod
    def _build_announcement_context(state: dict, user_data: dict) -> str:
        """Build dedicated announcement PDF context section for prompt.

        Extracts the 拟征地公告 content from uploaded PDFs and formats it
        as a prominent reference section. This ensures every chapter has
        direct access to the core project announcement data.
        """
        # Try multiple sources for the announcement text
        announcement = ""

        # 1. PDF texts from uploaded files
        pdf_texts = state.get("_pdf_texts", {}) or {}
        for fname, text in pdf_texts.items():
            if any(kw in fname for kw in ['公告', '拟征', '预公告', '征收']):
                announcement += f"\n### {fname}\n{str(text)[:3000]}\n"

        # 2. PDF raw text from user_data
        pdf_raw = user_data.get("pdf_raw_text", "")
        if pdf_raw and "公告" in str(pdf_raw)[:500]:
            announcement += f"\n### 征收公告原文\n{str(pdf_raw)[:3000]}\n"

        # 3. Project context from state
        ctx = state.get("project_context", "")
        if ctx and "拟征收" in str(ctx):
            # Extract the key facts portion
            key_lines = []
            for line in str(ctx).split('\n'):
                if any(kw in line for kw in ['拟征收', '征收', '公告', '文号', '面积', '位置', '坐落', '目的', '公示', '期限']):
                    key_lines.append(line.strip())
            if key_lines:
                announcement += f"\n### 项目关键信息\n" + "\n".join(key_lines[:30])

        if not announcement.strip():
            # Fallback: use any available PDF text
            for fname, text in pdf_texts.items():
                announcement += f"\n### {fname}\n{str(text)[:2000]}\n"
                break

        if announcement.strip():
            return (
                f"## 📄 拟征地公告原文（本项目核心文件，所有章节必须参考）\n"
                f"{announcement[:8000]}"
            )
        return ""

    @staticmethod
    def _build_extracted_tables_section(user_data: dict) -> str:
        """Build a prompt section with PDF-extracted table data in markdown format."""
        tables = user_data.get("_extracted_tables", [])
        if not tables:
            return "（未从PDF中提取到表格数据）"

        parts = []
        for i, tbl in enumerate(tables):
            md = tbl.get("raw_markdown", "")
            if md:
                page = tbl.get("page", "?")
                headers = tbl.get("headers", [])
                hstr = " | ".join(headers[:6])
                parts.append(
                    f"**PDF表格{i+1}** (第{page}页, {tbl.get('col_count',0)}列×{tbl.get('row_count',0)}行): {hstr}\n"
                    f"```\n{md[:2000]}\n```"
                )
        return "\n\n".join(parts) if parts else "（未从PDF中提取到表格数据）"

    def _build_table_skeleton_instruction(self, state: dict) -> str:
        """Build a concise but forceful table format instruction from TEMPLATE_TABLE_SKELETONS.

        Called from act() after _build_llm_prompt, so it's always appended to the user prompt
        regardless of whether the per-chapter agent overrides prompt building.
        """
        from app.agent.agents.knowledge_agent import TEMPLATE_TABLE_SKELETONS

        skeletons = TEMPLATE_TABLE_SKELETONS.get(self.chapter_number, {})
        if not skeletons:
            return ""

        lines = [
            "\n## ⛔ 最后强制要求 — 表格格式（覆盖之前所有指令！）\n",
            f"第{self.chapter_number}章**必须**包含以下表格，用markdown表格语法生成：\n",
        ]

        for tname, tskel in skeletons.items():
            title = tskel.get("title", tname)
            columns = tskel.get("columns", [])
            fill_types = tskel.get("fill_types", {})
            fixed_rows = tskel.get("fixed_rows", [])
            rule = tskel.get("rule", "")

            # Build the exact table template
            lines.append(f"### {title}\n")
            lines.append(f"**列数：{len(columns)}列** — {' | '.join(columns)}\n")

            if fixed_rows:
                lines.append("| " + " | ".join(columns) + " |")
                lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
                for row in fixed_rows:
                    # Truncate long cells for prompt brevity
                    cells = [c[:50] if len(c) > 50 else c for c in row]
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")

            if rule:
                lines.append(f"{rule}\n")

        lines.append("**⚠️ 绝对禁止用2列键值对格式（| 项目 | 内容 |）！必须严格按上述列数输出！**\n")

        return "\n".join(lines)

    def _build_kb_context_section(self, state: dict) -> str:
        """从KnowledgeAgent缓存构建知识库上下文段落，注入到user prompt中"""
        from app.agent.agents.knowledge_agent import (
            TEMPLATE_IMAGE_PLACEMENT, TEMPLATE_TABLE_DATA_RULES,
            TEMPLATE_TABLE_SKELETONS, RISK_LEVEL_CRITERIA,
        )

        kb_ctx = get_knowledge_context_for_chapter(state, self.chapter_number)
        parts = []

        # 🔴 图片位置要求（从模板学习）
        img_placement = TEMPLATE_IMAGE_PLACEMENT.get(self.chapter_number, [])
        if img_placement:
            parts.append("## 📸 本章图片要求（仿照模板）\n")
            for ip in img_placement:
                parts.append(
                    f"- 位置：{ip['position']} | 图注：{ip['caption']} | "
                    f"数据源：{ip['source']}\n"
                )
            parts.append("\n**请在本章对应位置用 `![图注](图片描述)` 标记图片位置。**\n\n")

        # 🔴🔴 表格骨架（最重要！从模板提取的完整表格结构，标注哪些列固定、哪些需填入数据）
        table_skeletons = TEMPLATE_TABLE_SKELETONS.get(self.chapter_number, {})
        if table_skeletons:
            parts.append("## 🔒 模板表格骨架（必须严格遵循！）\n")
            parts.append(
                "**以下表格结构从洞庭湖路稳评模板报告中提取。**\n"
                "**🔒 = 固定内容，照抄模板一字不改；✏️ = 数据槽位，根据项目数据填入。**\n\n"
            )
            for tname, tskel in table_skeletons.items():
                title = tskel.get("title", tname)
                columns = tskel.get("columns", [])
                fill_types = tskel.get("fill_types", {})
                fixed_rows = tskel.get("fixed_rows", [])
                rule = tskel.get("rule", "")

                # Table header with fill type annotations
                col_labels = []
                for c in columns:
                    ft = fill_types.get(c, "data")
                    if ft == "fixed":
                        col_labels.append(f"🔒{c}(固定)")
                    elif ft == "structural":
                        col_labels.append(f"📐{c}(序号)")
                    else:
                        col_labels.append(f"✏️{c}(填入数据)")
                col_header = " | ".join(col_labels)

                parts.append(f"### {title}\n")
                parts.append(f"**列定义**: {col_header}\n\n")

                if rule:
                    parts.append(f"**生成规则**: {rule}\n\n")

                # Build the markdown table from fixed_rows
                if fixed_rows:
                    parts.append("**完整表格模板（请按此格式输出，🔒列照抄，✏️列填入项目数据）：**\n\n")
                    # Header row
                    parts.append("| " + " | ".join(columns) + " |\n")
                    # Separator
                    parts.append("| " + " | ".join(["---"] * len(columns)) + " |\n")
                    # Data rows
                    for row in fixed_rows:
                        cells = []
                        for j, cell in enumerate(row):
                            col_name = columns[j] if j < len(columns) else ""
                            ft = fill_types.get(col_name, "data")
                            if ft in ("fixed", "structural"):
                                cells.append(cell)  # Keep template value
                            else:
                                # Data slot — preserve the placeholder hint
                                cells.append(cell)
                        parts.append("| " + " | ".join(cells) + " |\n")
                    parts.append("\n")

        # 🔴 表格数据要求（数据源绑定，禁止从模板复制）
        table_rules = TEMPLATE_TABLE_DATA_RULES.get(self.chapter_number, {})
        if table_rules:
            parts.append("## 📊 本章数据表格格式要求\n")
            parts.append("**⚠️ 关键规则：数据从用户资料提取，禁止从模板复制！**\n\n")
            for tname, trules in table_rules.items():
                fmt = trules.get("format", "")
                example = trules.get("example_table", trules.get("example_format", ""))
                rule = trules.get("rule", "")

                if fmt == "bullet_list":
                    parts.append(f"### {tname}（列表格式，不要用表格）\n")
                    if example:
                        parts.append(f"请按以下格式列出法规（编号列表，非表格）：\n{example}\n")
                else:
                    cols = "、".join(trules.get("template_cols", [])[:6])
                    parts.append(f"### {tname}（{cols}）\n")
                    if example:
                        parts.append(f"**必须使用以下精确格式（列数、表头完全一致）：**\n{example}\n")

                if rule:
                    parts.append(f"{rule}\n\n")

        # 🔴 风险等级判定标准（Ch6/Ch8/Ch9引用）
        if self.chapter_number in (6, 8, 9):
            parts.append("## 📐 DB32/T4013-2021 风险等级判定标准（固定，不可修改）\n\n")
            parts.append("| 风险等级 | 分数范围 | 判定条件 | 结论 |\n")
            parts.append("| --- | --- | --- | --- |\n")
            for level_key in ("low", "medium", "high"):
                level = RISK_LEVEL_CRITERIA[level_key]
                conditions = "；".join(level["conditions"])
                parts.append(
                    f"| {level['label']} | {level['score_range']} | "
                    f"{conditions} | {level['conclusion']} |\n"
                )
            parts.append("\n")

        # 格式模板
        fmt = kb_ctx.get("format_template", "")
        if fmt:
            parts.append(f"## 知识库格式模板参考\n{fmt[:2000]}\n\n")

        # 法规依据
        reg = kb_ctx.get("regulation_context", "")
        if reg:
            parts.append(f"## 相关法规条文\n{reg[:1500]}\n\n")

        # 范文片段
        example = kb_ctx.get("example_fragments", "")
        if example:
            parts.append(f"## 历史报告范文参考\n{example[:1500]}\n\n")

        return "".join(parts)

    def _get_kb_word_range(self, state: dict) -> str:
        """从KnowledgeAgent获取本章字数要求范围"""
        kb_ctx = get_knowledge_context_for_chapter(state, self.chapter_number)
        constraints = kb_ctx.get("writing_constraints", {})
        min_w = constraints.get("min_words", 800)
        max_w = constraints.get("max_words", 4000)
        return f"{min_w}-{max_w}字"

    @staticmethod
    def _fix_hallucinated_names(content: str) -> str:
        """Force-replace LLM-hallucinated community/village names with correct project data.

        Also fixes duplicate names that result from over-replacement (e.g.,
        '在三圩社区和三圩社区' → '在三圩社区').
        """
        import re as _re_hn

        _HALLUCINATED_REPLACEMENTS = [
            (r'大魏社区', '三圩社区'),
            (r'洪泽园三村社区', '三圩社区'),
            (r'洪泽园三村', '三圩社区'),
            (r'高良涧街道', '朱坝街道'),
            (r'金北街道', '朱坝街道'),
            (r'金湖县', '洪泽区'),
            (r'金湖', '洪泽'),
            # 🔴 常见 LLM 幻觉组合（音近/形近字）
            (r'朱紫街道', '朱坝街道'),
            (r'三坊社区', '三圩社区'),
            (r'三坊', '三圩'),
            (r'朱紫', '朱坝'),
            (r'三沪', '三圩'),
            (r'三虎', '三圩'),
        ]

        for old, new in _HALLUCINATED_REPLACEMENTS:
            content = content.replace(old, new)

        # Fix duplicate adjacent references from over-replacement
        # "在A和A" → "在A"  /  "A和A的" → "A及其周边"
        content = _re_hn.sub(r'(三圩社区)和\1', r'\1', content)
        content = _re_hn.sub(r'(三圩社区)和\1', r'\1', content)  # Run twice for nested cases
        content = _re_hn.sub(r'(朱坝街道)和\1', r'\1', content)
        content = _re_hn.sub(r'(朱坝街道)和\1', r'\1', content)

        # Fix date contamination from few-shot examples
        content = content.replace('2024年7月3日', '2026年')
        content = content.replace('2024年7月', '2026年')
        content = content.replace('2024年6月', '2026年')
        content = content.replace('2024年5月', '2026年')

        # Fix remaining AI buzzwords that slip through
        content = content.replace('有力支撑', '有效支持')
        content = content.replace('有力保障', '有效保障')

        # Remove bare filenames appearing as text in body (LLM hallucination)
        # Pattern: "xxx_xxx.png" or "xxx_xxx.jpg" appearing as standalone text
        content = _re_hn.sub(r'\n[^\n]{5,60}\.(png|jpg|jpeg|PNG|JPG)\n', '\n', content)
        content = _re_hn.sub(r'^[^\n]{5,60}\.(png|jpg|jpeg|PNG|JPG)\s*$', '', content, flags=_re_hn.MULTILINE)

        # Fix location duplication: "X组X省X市X区" → "X组。"
        content = _re_hn.sub(
            r'(三圩社区二组、三组、六组)江苏省淮安市洪泽区朱坝街道三圩社区二组、三组、六组',
            r'\1',
            content
        )

        return content

    @staticmethod
    def _fix_garbled_tables(content: str) -> str:
        """Fix LLM-generated single-line tables where rows are concatenated with — separators."""
        import re as _re_tbl

        def _split_table_line(line):
            if '| — |' not in line and '|—|' not in line:
                return line
            # Replace row separators with newlines
            fixed = line.replace('| — |', '|\n|').replace('|—|', '|\n|')
            # If we have newlines now, it's been split
            if '\n' not in fixed:
                return line
            # Re-assemble: first line is header+separator, rest are data rows
            parts = fixed.split('\n')
            # Find header
            header_part = parts[0].strip()
            if not header_part.startswith('|'):
                return line
            headers = [h.strip() for h in header_part.strip('|').split('|')]
            ncols = len(headers)
            if ncols < 2:
                return line
            # Build clean table
            clean = ['| ' + ' | '.join(headers) + ' |',
                     '| ' + ' | '.join(['---'] * ncols) + ' |']
            for part in parts[1:]:
                part = part.strip().strip('|').strip()
                if not part or all(c in '-:| \t' for c in part):
                    continue
                cells = [c.strip() for c in part.split('|')]
                cells = [c for c in cells if c and c != '—' and not all(x in '-:' for x in c)]
                if len(cells) >= ncols:
                    cells = cells[:ncols]
                elif len(cells) > 0:
                    cells = cells + ['—'] * (ncols - len(cells))
                else:
                    continue
                clean.append('| ' + ' | '.join(cells) + ' |')
            return '\n'.join(clean) if len(clean) > 2 else line

        fixed_lines = []
        for line in content.split('\n'):
            fixed_lines.append(_split_table_line(line))
        return '\n'.join(fixed_lines)

    def _extract_chapter_facts(self, text: str) -> str:
        """Extract key data points from chapter text for context passing."""
        facts = []
        for pattern, label in [
            (r'(?:项目名称|决策名称|报告标题)[：:]\s*(\S.{5,50}?)(?:\n|$)', "项目"),
            (r'(?:位于|坐落|位置)[：:]?\s*(\S.{5,50}?)(?:。|\n)', "位置"),
            (r'(\d{4,7})\s*(?:平方米|㎡)', "面积"),
            (r'(\d+\.?\d*)\s*亩', "亩数"),
            (r'(\d+)\s*户', "户数"),
            (r'(\d+)\s*人', "人数"),
            (r'(?:补偿标准|综合补偿)[^\d]*(\d[\d,.]*)\s*(?:元|万元)', "补偿标准"),
            (r'(\d{2,3}\.?\d*)\s*%\s*(?:支持|赞成)', "支持率"),
            (r'(?:风险等级|综合风险)[：:]\s*(低风险|中风险|高风险)', "风险等级"),
            (r'(?:措施前|措施后).{0,5}?(\d{1,2})\s*分', "评分"),
        ]:
            m = re.search(pattern, text)
            if m and not any(label in f for f in facts):
                val = m.group(1).strip()
                facts.append(f"{label}: {val}")
        return "；".join(facts[:8]) if facts else "（内容已生成）"

    @staticmethod
    def _num_to_cn(n: int) -> str:
        """Convert a number to Chinese numeral."""
        cn = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
        return cn[n] if n <= 10 else str(n)
