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
from typing import Dict, List, Any

from ..base_agent import BaseAgent
from ..knowledge_agent import get_knowledge_context_for_chapter
from app.validation.content_guardrails import AI_BUZZWORDS
from app.agent.chapter_definitions import ALL_CHAPTERS, FANWEN_STYLE_GUIDE

logger = logging.getLogger(__name__)


def _safe_int(v, default: int = 0) -> int:
    """字段值 → int；`【待补充】` / 空 / 非数字一律回落默认值。

    🔴 占位符被直接 `int()` 会抛 ValueError，把整条链打断。
    """
    import re as _re
    t = _re.sub(r'[^\d.\-]', '', str(v if v is not None else ""))
    try:
        return int(float(t))
    except (TypeError, ValueError):
        return int(default)

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
            for ch in ALL_CHAPTERS:
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
    # 章号 = 参照件十四章号：6 项目基本情况 / 9 各利益相关者 / 12 确定风险等级
    #                    2 编制依据 / 10 四性分析 / 13 工作建议
    ch_specific = {
        "fabricated_data": [6, 9, 12],          # 有具体数字的章
        "data_validity": [9, 12],               # 支持率/评分
        "hallucinated_regulation": [2, 10, 13], # 法规引用
        "invalid_range": [6, 12],               # 面积/评分
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

    def __init__(self, llm_service=None):
        super().__init__(llm_service=llm_service)
        # 🔴 标题与 RAG 检索词一律以 chapter_definitions（唯一事实源）为准，
        #    覆盖子类里可能已过时的类属性，避免章节结构变更后各处写死走样。
        try:
            from app.agent.chapter_definitions import (
                get_chapter_title, get_chapter_rag_query)
            if self.chapter_number:
                self.chapter_title = get_chapter_title(self.chapter_number)
                self.rag_query_extra = get_chapter_rag_query(self.chapter_number)
        except Exception:
            pass

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
            # 🔴 不写死公司名：外部用户出报告时，提示词里的公司名会被模型抄进正文。
            #    实施单位一律取自事实卡（已按本报告所属公司填好）。
            system_prompt = "你是一位资深稳评工程师。"
            user_prompt = state["_custom_prompt"]
            # 🔴 自定义 prompt 路径此前会**丢掉 think() 检索到的 RAG** ——
            #    LLM 只能凭记忆写，于是编造法规名称/规划名称/同类项目。
            #    这里把检索到的真实素材追加进去，并明确「只能用它」。
            _rag_block = self._format_rag_for_prompt(rag_context)
            if _rag_block:
                user_prompt = user_prompt + "\n\n" + _rag_block
                await self._emit_thinking(
                    f"🔍 已注入 RAG 检索素材（{len(rag_sources)} 条），约束 LLM 不得编造"
                )
            # 🔴 章节体例约束**必须在这条路上也注入**：主编排给的 custom prompt 会整段
            #    覆盖 agent 自带提示词，不补的话「本章不出表」「须含表4-2/表4-3」这类约束
            #    只在 UI 路径生效，`scripts/gen_full_report.py` 生成的全量报告完全不受约束
            #    —— 两套路径分叉的老毛病。
            try:
                _extra = self._chapter_extra_context(state, rag_context, user_data)
                if _extra:
                    system_prompt = system_prompt + "\n\n" + _extra
            except Exception:
                pass
        else:
            system_prompt, user_prompt = self._build_llm_prompt(state, rag_context, user_data)

        # 🔴 标准小标题约束（无论哪套 prompt 都注入，保证章节内小标题结构稳定）
        try:
            from app.agent.template_headings import format_headings_for_prompt
            heading_hint = format_headings_for_prompt(self.chapter_number)
            if heading_hint and heading_hint not in user_prompt:
                user_prompt = user_prompt + "\n\n" + heading_hint
        except Exception:
            pass

        # 🔴 真实地名/文号/时间硬约束（无论用哪套 prompt 都注入，防止 LLM 编造）
        try:
            filled = state.get("filled_data", {})
            # 🔴 清洗视觉分析 dict 混入的 location（{'general_area': ...} 等），
            # 防止把结构化机器字段当"唯一真实值"注入导致正文出现 JSON 残留。
            loc = self._sanitize_filled_value(
                filled.get("location") or filled.get("land_location") or ""
            )
            doc_ref = filled.get("doc_reference") or filled.get("project_name") or ""
            constraint_parts = []
            if loc and not str(loc).startswith("【"):
                constraint_parts.append(f"- 项目位置唯一真实值：{loc}。报告中所有位置/坐落/社区/街道必须用这个，禁止编造任何其他地名")
            if doc_ref and not str(doc_ref).startswith("【"):
                constraint_parts.append(
                    f"- 项目文号唯一真实值：{doc_ref}。报告中所有征地文号必须用这个，禁止使用其他文号\n"
                    f"- 🚫 参考范文里出现的任何其他文号（如洪拟征告〔2026〕X号、清拟征告、金征预告等）都是**别的项目的**，一律忽略，绝不写入本报告\n"
                    f"- 如发现范文里带其他文号，把它当示例格式参考，但文号一律用「{doc_ref}」"
                )
            # 🔴 时间硬约束：报告年度为2026年，评估时间应在公告发布之后（2026年），禁止出现2024年等错误年份
            constraint_parts.append("- 报告年度为2026年。本项目的公告/预公告发布时间为2026年，评估工作时间应为2026年。禁止出现2024年、2025年等错误年份作为评估启动时间")
            # 🔴 社区背景描述约束：禁止编造社区的地理/历史/发展/水文等背景（如"地处淮河流域""紧挨城区""城镇化进程加快"等）
            constraint_parts.append("- 禁止编造社区或街道的背景描述（地理位置、水系、历史沿革、城镇化发展、周边设施等）。如果项目资料里没有这些信息，就直接写事实或标注【待补充】，不要用内部知识补充虚构的社区背景")

            # 🔴 权威风险等级约束（系统计算一次，各章统一引用，禁止自创/矛盾）
            ra = state.get("_risk_assessment") or {}
            if ra and self.chapter_number in (5, 6, 8, 9):
                level = ra.get("level", "低风险")
                pre = ra.get("pre_total", "")
                post = ra.get("post_total", "")
                if self.chapter_number == 5:
                    constraint_parts.append(
                        f"- 🔴 本章综合风险等级判定必须为「{level}」（措施前{pre}分，系统已按量化评分体系统一计算）。"
                        f"识别出的单因素风险可以是低/较低/一般/较高/高，但『综合风险等级』表述必须写「{level}」，"
                        f"禁止写『一般偏中』『中风险』『较高』等与权威值矛盾的等级"
                    )
                elif self.chapter_number in (8, 9):
                    constraint_parts.append(
                        f"- 🔴 本章风险等级表述必须为「{level}」（措施前{pre}分、措施后{post}分，系统计算）。"
                        f"结论处禁止写『中风险』『高风险』『一般偏中』等与权威值矛盾的等级；"
                        f"如需说明措施前后变化，表述为「措施前{pre}分、落实措施后{post}分，均为{level}」"
                    )
                else:
                    constraint_parts.append(
                        f"- 风险等级按系统计算值「{level}」（措施前{pre}分、措施后{post}分），不得写矛盾等级"
                    )
            if constraint_parts:
                constraint_text = "\n🔒 项目真实数据约束（必须严格遵守，不得编造或替换）：\n" + "\n".join(constraint_parts) + "\n"
                # 追加到 user_prompt 末尾（最近原则，LLM 更重视）
                user_prompt = user_prompt + "\n\n" + constraint_text
        except Exception:
            pass

        # 🔴 文风约束：以范文为蓝本重写、去 AI 痕迹。
        #    放在这里（而不是 `_format_rag_for_prompt`）是因为 act() 有**两条分支**：
        #      · `_use_custom_prompt` → `_format_rag_for_prompt`；
        #      · 否则 → `_build_llm_prompt`。
        #    只有本处是两条分支的**公共下游**，放这里才能保证每一章都带上。
        if FANWEN_STYLE_GUIDE.strip() not in user_prompt:
            user_prompt = user_prompt + "\n\n" + FANWEN_STYLE_GUIDE

        # 🔴 Inject Few-Shot template example for format alignment (respects style)
        from app.agent.report_styles import get_few_shot
        style_name = state.get("_report_style") or state.get("report_style") or "jinhu"
        few_shot = get_few_shot(self.chapter_number, style_name)
        # 🔴 few-shot 前置专家 skill 提示（避免范文覆盖 skill 要求）
        try:
            from app.services.few_shot_service import get_few_shot_with_skills
            few_shot = get_few_shot_with_skills(self.chapter_number)
        except Exception:
            pass
        if few_shot:
            system_prompt = system_prompt + few_shot

        # 🔴 图片统一由组装器按图库分类插入；不再向 prompt 注入任何图片信息/标记要求。

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

        # 🔴 工具预检索回合：agent 可按需自主调用后端能力（RAG/勘测定界/评分）
        # 把工具结果作为「真实数据补充」注入 user_prompt，再进入正文生成。
        # 失败/关闭时完全回退到现有纯文本路径，不改变生成行为。
        try:
            tool_results = await self._maybe_run_tool_round(state, user_prompt, system_prompt)
            if tool_results:
                user_prompt = user_prompt + "\n\n## 🔧 工具补充数据（真实，可直接引用）\n" + "\n".join(tool_results)
                await self._emit_thinking(f"已通过工具获取补充数据（{len(tool_results)} 项），用于本章生成")
        except Exception as _te:
            logger.warning(f"Tool-call round skipped for ch{self.chapter_number}: {type(_te).__name__}: {_te}")

        # 🔴 Retry loop for LLM calls (shared client can fail under parallel load)
        # ⚠️ `settings` 必须**局部 import** —— 模块级 `from app.config import settings`
        #    会成环（app.config → database.engine → logging_config → 本模块）。
        from app.config import settings as _cfg

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
                    "1. 你是一位资深稳评工程师，根据专业知识决定本章每个小节下的内容深度\n"
                    "2. 数据用「材料3：本项目数据」中提供的，没有的不编造\n"
                    "3. 地名/村名/社区名必须来自项目数据，禁止使用LLM内部知识中的地名\n"
                    "4. 写短句，像老工程师写报告，不要AI翻译腔\n"
                    "5. 不用「第一/第二/第三」排比，不用「具有重要/切实/有力」等套词\n"
                    "6. 🔴 子标题结构：严格用下方「本章结构规范」/「本章必须使用的子标题」给出的编号标题"
                    "（如 4.1 合法性分析、6.1 量化评分指标体系）。禁止自创数字编号标题，"
                    "禁止用「六、」「八、」中文序号标题；更细的分点用（一）（二）（三）写在正文里，不用编号标题。\n"
                    "7. 表格只在必要时使用，不要为填模板而加表\n"
                )
                system_prompt = system_prompt + writing_guide

                result = await asyncio.wait_for(
                    self._llm.chat_with_reasoning(
                        messages=[{"role": "user", "content": user_prompt}],
                        system=system_prompt,
                        max_tokens=8000,
                        temperature=0.4,  # Higher temp for more natural variation
                        # 🔴 关思考：推理模型会把预算烧在 reasoning_content 上，
                        #    正文一个字都出不来（详见 config.CHAPTER_DISABLE_THINKING）。
                        disable_thinking=_cfg.CHAPTER_DISABLE_THINKING,
                    ),
                    # 🔴 单次预算 8000 token 的中文正文，按 DeepSeek 的吐字速度要
                    #    200～400s；若被 finish_reason=length 截断，服务内部还会放大到
                    #    16000 再重试一次 —— 150s 连第一次都跑不完。
                    #    实测第 8 章（稳评方法与过程，四大节、8000+ 字）连续两轮
                    #    3 次尝试全部 TimeoutError，只能回退上一轮正文。
                    timeout=600.0,
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
        # 🔴 整章重复兜底（LLM 偶发输出两遍）
        content = self._dedup_repeated_blocks(content)
        # 🔴 三级标题规范化（LLM 屡次退回加粗写法，提示约束不住，用后处理兜底）
        content = self._apply_section_subs(content)
        # 🔴 按参照件模板**通篇无表**的章节：确定性删表（提示词约束不住时的兜底）。
        #    第十一章「分析预测风险源」模板里一张表都没有，而「风险因素初始风险等级表」
        #    属**第十二章 表12-4**。本章若也写一张，报告里就冒出两张同名表。
        if self.chapter_number in self.NO_TABLE_CHAPTERS:
            content = self._strip_markdown_tables(content)
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

        # 🔴 地类/权属面积重复检测：同一大数值（5-7位）在正文出现≥4次，
        # 疑似 LLM 把总面积（如326342）套用到每个地类/权属上 → 触发重写
        if self.chapter_number == 4:
            import re as _re_area
            from collections import Counter
            area_nums = _re_area.findall(r'(?<!\d)(\d{5,7})(?=\s*(?:㎡|平方米|亩|m²))', markdown)
            for num, cnt in Counter(area_nums).items():
                if cnt >= 4:
                    issues.append(
                        f"第4章: 数字{num}重复出现{cnt}次，疑似把总面积套用到多个地类/权属，"
                        f"必须与第6章 表6-1 的真实数据核对（表中逐地块/逐权属列出），不得每项都写总面积"
                    )
                    break

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

        默认实现完全由「章节结构规范」（chapter_definitions.CHAPTER_SECTION_SPECS）
        驱动：标题、小节、内容要点、数据要求、图片规格全部来自单一事实源，
        不再在各章 agent 里重复写死提示词。

        子类只需覆盖 `_chapter_extra_context()` 注入本章特有的补充上下文
        （如评分参考、勘测定界表、问卷统计表）。
        Returns (system_prompt: str, user_prompt: str).
        """
        system_prompt = self._default_system_prompt(state)
        extra = self._chapter_extra_context(state, rag_context, user_data)
        if extra:
            system_prompt = f"{system_prompt}\n\n{extra}"
        user_prompt = self._default_user_prompt(state, rag_context, user_data)
        return system_prompt, user_prompt

    def _chapter_extra_context(
        self,
        state: dict,
        rag_context: Dict[str, Any],
        user_data: Dict[str, Any],
    ) -> str:
        """本章特有的补充上下文（默认无）。子类按需覆盖。"""
        return ""

    # ═══════════════════════════════════════════════════════════════
    # Agent Tool-Call 回合（按需调用后端能力）
    # ═══════════════════════════════════════════════════════════════
    async def _maybe_run_tool_round(
        self,
        state: dict,
        user_prompt: str,
        system_prompt: str,
    ) -> List[str]:
        """Agent 工具预检索回合：LLM 自主决定是否调用工具补充数据。

        仅在开关开启、LLM 支持 tools、且本章不是纯模板模式时执行。
        返回工具执行结果文本列表（空列表 = 未调用工具或关闭）。

        工具结果只作为 prompt 增补注入，不写入 state，失败时回退不中断。
        """
        if getattr(self, "_use_template", False) or not self._llm:
            return []
        try:
            from app.config import settings
            if not getattr(settings, "ENABLE_AGENT_TOOLCALL", True):
                return []
        except Exception:
            return []

        # 只对需要数据的章节尝试（可调参：默认全部开启）
        try:
            from app.agent.tools import ALL_TOOLS_SCHEMA, execute_tool_calls

            # 组装简短的工具决策 prompt（避免把超大 user_prompt 重复发给 LLM）
            decision_prompt = self._build_tool_decision_prompt(state, user_prompt)

            resp = await asyncio.wait_for(
                self._llm.chat_with_reasoning(
                    messages=[{"role": "user", "content": decision_prompt}],
                    system="你是一个稳评报告助手。判断是否需要调用工具获取真实数据。"
                           "需要时输出 tool call；不需要则直接简短回复'无需工具'。",
                    max_tokens=512,
                    temperature=0.2,
                    tools=ALL_TOOLS_SCHEMA,
                ),
                timeout=60.0,
            )
        except Exception as e:
            logger.debug(f"Tool-call round init failed (fallback to plain text): {e}")
            return []

        tool_calls = resp.get("tool_calls") or []
        if not tool_calls:
            return []

        # 执行工具并上报事件
        await self._emit_thinking(
            f"🔧 调用工具: " + ", ".join(tc.get("name", "") for tc in tool_calls)
        )
        context = self._build_tool_context(state)
        results = await execute_tool_calls(tool_calls, context)
        return [r.get("content", "") for r in results if r.get("content")]

    def _build_tool_decision_prompt(self, state: dict, user_prompt: str) -> str:
        """组装工具决策 prompt：给 LLM 提供项目关键信息 + 按章节引导相关工具。"""
        filled = state.get("filled_data", {}) or {}
        proj = {
            "project_name": filled.get("project_name", ""),
            "location": filled.get("location", ""),
            "area_mu": filled.get("area_mu", ""),
            "land_use": filled.get("land_use", ""),
            "doc_reference": filled.get("doc_reference", ""),
        }
        proj_text = "\n".join(f"- {k}: {v}" for k, v in proj.items() if v)

        # 🔴 按章节给与最相关的工具引导（避免无关工具的干扰调用）
        ch = self.chapter_number
        available = "可用工具：\n"
        if ch in (1, 4):
            available += "- parse_kd_table：获取地块/土地分类面积真实数据（第6章 表6-1），本表内容高度相关\n"
        if ch == 3:
            available += "- query_survey：获取公众问卷调查统计（身份/年龄/职业/了解/态度/诉求方式人数）\n"
            available += "- query_dept_survey：获取部门/单位问卷调查统计\n"
        if ch in (6, 8):
            available += "- calc_risk_scoring：获取风险评分（扣分制）与风险等级\n"
        else:
            available += "- calc_risk_scoring：如需风险评分数据可调用\n"
        available += "- retrieve_rag：正文需引用具体法规/规范/范文条文时调用"
        if ch == 3:
            available += "（quote法规依据、调查规范）"
        available += "\n- parse_kd_table：如需地块/面积数据可调用\n"

        return (
            f"当前项目信息：\n{proj_text or '（暂无）'}\n\n"
            f"你将生成报告第 {ch} 章。判断是否需要调用工具获取真实数据：\n{available}\n"
            f"优先调用与你本章内容最相关的工具；若需要法规依据，可另调 retrieve_rag。\n"
            f"如无需调用工具，直接回复'无需工具'（不要生成正文）。"
        )

    def _build_tool_context(self, state: dict) -> dict:
        """构造工具执行所需的项目上下文（session_id、勘测定界 pdf 路径等）。"""
        filled = state.get("filled_data", {}) or {}
        uploaded = state.get("_uploaded_files", []) or []
        paths = []
        for f in uploaded:
            if isinstance(f, dict):
                p = f.get("path", f.get("file_path", ""))
            else:
                p = str(f)
            if p:
                paths.append(str(p))
        # 本批上传的勘测定界报告（从文件名识别）
        survey_report_path = ""
        for p in paths:
            if "勘测" in p or "界" in p:
                survey_report_path = p
                break
        return {
            "session_id": state.get("session_id", "default"),
            "domain": "stability",
            "file_paths": paths,
            "survey_report_path": survey_report_path,
            "facts": {
                "project_name": filled.get("project_name", ""),
                "support_rate": filled.get("support_rate", ""),
                "location": filled.get("location", ""),
            },
            # 🔴 供「问卷/部门意见」工具使用的完整统计与生成缓存
            "survey_analysis": (state.get("generated_sections", {}) or {}).get("survey_analysis", {}) or {},
            "generated_sections": state.get("generated_sections", {}) or {},
            "structured_data": state.get("structured_data", {}) or {},
            # 报告相关字段（供多工具查询）
            "filled": filled,
        }

    def _fallback_content(self, state: dict) -> str:
        """LLM 不可用时的兜底内容 —— **按本章结构规范输出骨架**（不写死任何章节）。

        与旧版「只给一句待生成」不同：这里按 chapter_definitions 的二级标题
        逐节输出标题 + 中性说明，保证降级产物的**结构**仍与案卷一致，
        组装出的目录/层级不塌，人工补写时可直接在各节下续写。
        """
        lines = [f"## 第{self._chapter_num_cn()}章 {self.chapter_title}", ""]
        try:
            from app.agent.chapter_definitions import (
                get_chapter_section_specs, get_chapter_kind)
            specs = get_chapter_section_specs(self.chapter_number)
            if specs:
                for sec in specs:
                    lines.append(f"### {sec.get('title', '')}")
                    points = sec.get("points") or []
                    note = points[0] if points else ""
                    lines.append(f"（本节待补充：{note}）" if note else "（本节待补充）")
                    lines.append("")
            elif get_chapter_kind(self.chapter_number) in ("table", "attachment"):
                lines.append("（本章由系统按模板生成，无需 LLM 撰写）")
            else:
                lines.append("【待生成：LLM服务不可用，请重试或手动补充本章内容】")
        except Exception:
            lines.append("【待生成：LLM服务不可用，请重试或手动补充本章内容】")
        return "\n".join(lines) + "\n"

    def _get_key_phrases(self) -> List[str]:
        """Key phrases that should appear in this chapter.

        Subclasses override to provide chapter-specific quality checks.
        """
        return []

    # ---- Internal Helpers ----

    @staticmethod
    def _sanitize_filled_value(value: Any) -> str:
        """清洗 filled_data 中可能混入的结构化数据（视觉分析 dict/JSON）。

        背景：image_analyzer 的视觉返回有时被整段写入 filled_data['location']，
        形如 "{'general_area': '疑似城乡结合部…', 'visual_landmarks': …}"，
        被当作"唯一真实值"注入 prompt 后，LLM 会原样抄进报告正文。

        规则：
        - dict/JSON 形态 → 返回空串（不可作为位置正文，交由 field_validate 用 PDF 原文修复）
        - 普通文本 → 原样返回
        """
        if value is None:
            return ""
        s = str(value).strip()
        if not s:
            return ""
        # Python dict 字面量或 JSON 对象
        if s.startswith("{") or s.startswith("[{"):
            return ""
        return s

    def _chapter_num_cn(self) -> str:
        """Convert chapter number to Chinese numeral（支持 1~99，12 章结构需要「十一」「十二」）。"""
        n = int(self.chapter_number)
        if n <= 10:
            return ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"][n]
        if n < 20:
            return "十" + ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"][n - 10]
        return f"{n // 10}十" + (["", "一", "二", "三", "四", "五", "六", "七", "八", "九"][n % 10] if n % 10 else "")

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

            # 🔴 四性分析专线：第4章 4.2 / 第10章整章都要写四性分析，
            #    主类型取回的是评分表碎片，四性范文排不上来 → LLM 只能凭记忆编法规名。
            #    这里单独从 kb_four_dimension 取同类土地征收案卷的四性原文。
            if self.chapter_number in (4, 10):
                _fd = await self._retrieve_four_dimension(state)
                if _fd:
                    result["four_dimension_context"] = _fd

            # Cache in state
            rag_cache = state.setdefault("_rag_cache", {})
            rag_cache[self.chapter_number] = result

            return result

        except Exception as e:
            logger.warning(f"Chapter {self.chapter_number} RAG failed: {e}")
            return {}

    def _dedup_repeated_blocks(self, md: str) -> str:
        """🔴 兜底：LLM 偶发把整章输出两遍（截断后重试 / 结构规范重复注入）。

        以二级标题为界把正文切成「标题块」，**同名块只保留首次出现的那一份**。
        实测第1章曾整章重复（7,375 字 ≈ 2×），本方法可自动收敛。
        注意要按「块」而非按行处理：第二份里可能有第一份没有的标题
        （如第一份被截断在（二），第二份才写到（三））—— 那些块要保留。
        """
        import re as _re
        heads = list(_re.finditer(r'^#{2,4}\s+\S.*$', md, flags=_re.MULTILINE))
        if len(heads) < 4:
            return md

        prefix = md[:heads[0].start()]          # 章标题与前言（第一个二级标题之前）
        blocks = []
        for k, m in enumerate(heads):
            end = heads[k + 1].start() if k + 1 < len(heads) else len(md)
            blocks.append((m.group(0).strip(), md[m.start():end]))

        seen, kept = set(), []
        for title, seg in blocks:
            if title in seen:
                continue                         # 同名块 → 丢弃（保留首次）
            seen.add(title)
            kept.append(seg)
        if len(kept) == len(blocks):
            return md
        return (prefix + "".join(kept)).strip()

    # 按参照件模板**通篇无表**的章节（第十一章 分析预测风险源）。
    # 🔴「风险因素初始风险等级表」按模板属**第十二章 表12-4**；第十一章再写一张，
    #   报告里就是两张同名表（业主：「重名不正确吧」）。
    NO_TABLE_CHAPTERS = {11}

    @staticmethod
    def _strip_markdown_tables(md: str) -> str:
        """删掉 markdown 表格整块（连同紧邻上方的表题行）。

        提示词里已经写了「本章不出表」，但模型屡次照写不误 —— 层级、表名这类
        结构问题必须用确定性后处理兜底，不能指望提示约束。
        """
        lines = (md or "").split("\n")
        out, i = [], 0
        while i < len(lines):
            if lines[i].strip().startswith('|'):
                # 连表题行一起删。🔴 表题与表格之间常夹一个空行，
                #   必须先跳过空行再找表题，否则表题会孤零零留在正文里。
                while out and not out[-1].strip():
                    out.pop()
                while out:
                    _t = out[-1].strip()
                    if _t.startswith('**表') or (_t.startswith('表') and len(_t) < 40):
                        out.pop()
                        while out and not out[-1].strip():
                            out.pop()
                    else:
                        break
                while i < len(lines) and lines[i].strip().startswith('|'):
                    i += 1
                continue
            out.append(lines[i])
            i += 1
        # 删表后可能留下「空壳标题」（原标题底下就只有一张表）→ 一并去掉
        _ls = "\n".join(out).split("\n")
        keep = []
        for k, l in enumerate(_ls):
            if l.strip().startswith('#'):
                _nxt = next((x.strip() for x in _ls[k + 1:] if x.strip()), "")
                if not _nxt or _nxt.startswith('#'):
                    continue
            keep.append(l)
        return re.sub(r'\n{3,}', '\n\n', "\n".join(keep))

    @staticmethod
    def _promote_dynamic_sections(md: str) -> str:
        """动态小节章（第11、13章）把 LLM 自拟的 `N.小节名` 裸行提升为 `### （一）小节名`。

        🔴 影响的是**章节层级与目录**：这两章没有固定小节清单（`dynamic_sections`），
           小节名全由 LLM 自拟，它屡次写成 `1.xxx` 裸行 → 层级掉成正文条目，
           整章在目录里只剩一个章标题（实测第11章 6 节、第13章 6 节全部丢失）。

        判据三条同时满足，**宁可漏升也不误升**：
          ① 该行独占一段（前一行是空行**或标题行** —— 模型常把小节 1 紧贴在章标题下一行）；
          ② 编号**从 1 起连续**递增 —— 正文里的 1. 2. 3. 条目接不上这个序列；
          ③ 行短（≤30 字）且不带句末标点、不以冒号引出（那是在写正文条目）。
        """
        _CN = {i + 1: c for i, c in enumerate("一二三四五六七八九十")}
        lines = (md or "").split("\n")
        out, expect = [], 1
        for i, line in enumerate(lines):
            t = line.strip()
            # 🔴 已开始提升后，再冒出「没编号的 ### 二级标题」（如汇总小节）就按序补号，
            #    否则（一）（二）…里会混进一个光秃秃的二级标题，目录看着就散了。
            if (expect > 1 and re.match(r'^###\s+\S', t)
                    and not re.match(r'^###\s+（[一二三四五六七八九十]+）', t)):
                out.append(f"### （{_CN.get(expect, expect)}）{t[4:].strip()}")
                expect += 1
                continue
            m = re.match(r'^(\d{1,2})[.、]\s*(\S.{1,28})$', t)
            _prev = lines[i - 1].strip() if i else ""
            prev_ok = (i == 0) or (not _prev) or _prev.startswith('#')
            if (m and prev_ok and int(m.group(1)) == expect
                    and not re.search(r'[。；，,;：:]$', t)):
                out.append(f"### （{_CN.get(expect, expect)}）{m.group(2).strip()}")
                expect += 1
                continue
            out.append(line)
        return "\n".join(out) if expect > 1 else md

    def _apply_section_subs(self, md: str) -> str:
        """把 LLM 自创的加粗小标题规范成 `#### X.Y.Z 标题` 三级标题。

        🔴 为什么需要：`chapter_definitions.CHAPTER_SECTION_SUBS` 里定义了三级标题
        （4.2.1 合法性评估、8.4.1 现场公示…），提示词也明确要求用 `####` 输出，
        但 LLM 屡次退回「**（一）合法性评估**」这种加粗写法 —— 提示约束不住，
        必须用确定性后处理兜底，否则目录层级与参照件对不上。
        """
        # 🔴 全报告二级标题体例为「（一）（二）（三）」（业主口径：照参照件 1号）。
        #    LLM 常写成加粗行或 ### 行，这里统一规范成 `### （一）xxx`（Heading 2，进目录）。
        if True:
            # 只有这三块是二级标题；块内的「（一）客观公正性原则」等要落回 `N.` 条目，
            # 否则会被一并提升成二级标题（实测（三）下的四条原则全变成了 Heading 2）。
            _BLOCKS = {"（一）稳评理由", "（二）稳评目的", "（三）稳评原则"}
            _STD = set()
            try:
                from app.agent.chapter_definitions import get_chapter_section_specs
                _STD = {x.get("title", "") for x in get_chapter_section_specs(self.chapter_number)}
            except Exception:
                pass
            _CN2N = {c: i + 1 for i, c in enumerate("一二三四五六七八九十")}
            out = []
            for line in md.split("\n"):
                t = line.strip()
                # 兼容三种写法：加粗行、裸行、以及被 LLM 写成 ### / #### 的标题行
                m = re.match(r'^#{0,4}\s*\*{0,2}\s*([（(][一二三四五六七八九十]+[）)]\s*[^*\s]{1,20})\s*\*{0,2}$', t)
                if not m:
                    out.append(line); continue
                body = m.group(1).strip()
                if body in _BLOCKS or body in _STD:
                    out.append(f"### {body}")          # 标准二级标题 → 保留
                else:
                    # 块内条目（如（一）客观公正性原则）→ 落回 `N.xxx` 正文条目
                    mm = re.match(r'^[（(]([一二三四五六七八九十]+)[）)]\s*(.+)$', body)
                    out.append(f"{_CN2N.get(mm.group(1), 1)}.{mm.group(2).strip()}" if mm else line)
            md = "\n".join(out)

        # 🔴 动态小节章（第11/13章，`sections` 为空、小节名由 LLM 自拟）：
        #    它把顶层小节写成 `1.xxx` 裸行，层级掉成正文条目 → 确定性提升为二级标题。
        try:
            from app.agent.chapter_definitions import get_chapter_section_specs
            if not get_chapter_section_specs(self.chapter_number):
                md = self._promote_dynamic_sections(md)
        except Exception:
            pass

        try:
            from app.agent.chapter_definitions import get_chapter_section_subs
            subs = get_chapter_section_subs(self.chapter_number)
        except Exception:
            return md
        if not subs:
            return md

        # {三级标题正文: 节内序号}，用于把「（一）合法性评估」「1.项目名称」对回来。
        # 🔴 序号**在各二级小节内重排**，输出成 `#### 1.标题` —— 与照搬章
        #    （reference_corpus ①a）同一套三级标题体例，全报告一致。
        #    旧实现只认 `X.Y.Z 标题` 形式的配置项，第6章配置里写的是裸标题
        #    （「项目名称」），于是 `want` 恒为空、三级标题一个都升不出来。
        want = {}
        for _sec, items in (subs or {}).items():
            for _i, full in enumerate(items, 1):
                _t = re.sub(r'^\d+(?:\.\d+){1,2}\s*', '', str(full).strip()).strip()
                if _t:
                    want[_t] = _i

        out = []
        for line in md.split("\n"):
            t = line.strip()
            if not t:
                out.append(line)
                continue
            # 已是标题行（任何编号写法）且正文对得上配置 → 规范成 `#### N.标题`
            _m3 = re.match(r'^#{3,4}\s*\d+(?:\.\d+)*[.、]?\s*(.+)$', t)
            if _m3 and _m3.group(1).strip() in want:
                _k = _m3.group(1).strip()
                out.append(f"#### {want[_k]}.{_k}")
                continue
            # 加粗小标题／裸编号行：「**（一）合法性评估**」「1.项目名称」
            m = re.match(r'^\*\*\s*(?:[（(][一二三四五六七八九十\d]+[）)]\s*|\d+[.、]\s*)?([^*]+?)\s*\*\*$', t)
            if m:
                key = m.group(1).strip()
            elif re.match(r'^\d{1,2}[.、]\s*\S', t):
                key = re.sub(r'^\d{1,2}[.、]\s*', '', t).strip()
            else:
                key = ""
            if key and key in want:
                out.append(f"#### {want[key]}.{key}")
                continue
            out.append(line)
        return "\n".join(out)

    def _format_rag_for_prompt(self, rag_context: dict, max_per_section: int = 6000) -> str:
        """把 RAG 检索结果渲染成 prompt 素材块（供自定义 prompt 路径注入）。

        🔴 为什么必须注入：`_use_custom_prompt` 分支原先只用主编排给的 prompt，
        把 `think()` 检索到的知识库素材整个丢掉。后果是 LLM 凭记忆写，
        编造法规名称、规划名称、同类项目先例 —— 业主明确要求「不要让 LLM 乱编，要结合 RAG」。

        素材按类型分组贴出，并附硬约束：**只能用这里和事实卡里的内容**。
        """
        if not isinstance(rag_context, dict):
            return ""
        _groups = [
            # 🔴 四性分析排最前：第4章 4.2 / 第10章 直接照它的行文与依据写
            ("four_dimension_context", "知识库·同类土地征收案卷「四性分析」原文"),
            ("chapter_context", "知识库·规范条文与同类案卷"),
            ("local_regulation_context", "知识库·地方规范"),
            ("example_context", "知识库·同类项目范例"),
            ("project_context", "本项目上传材料摘录"),
        ]
        parts = []
        for key, label in _groups:
            txt = str(rag_context.get(key) or "").strip()
            if len(txt) < 30:
                continue
            parts.append(f"### 【{label}】\n{txt[:max_per_section]}")
        if not parts:
            return ""

        return (
            "\n## 📚 检索到的真实素材（写本章**只能**依据这些 + 事实卡）\n"
            + "\n\n".join(parts)
            + "\n\n## ⛔ 素材使用铁律\n"
            "- 上面这些是**真实检索结果**：法规名称、文号、规划名称、同类项目先例**只能从里面取**，"
            "一个字都不许自己编（编造法规文号是硬错误）；\n"
            "- 上述素材里**没有提到**的法规、规划、先行项目、机构名称，一律不要写；\n"
            "- 本项目专属数字/名称一律以「🔗 本项目事实卡」为准；两处都没有的，"
            "写「以现状调查确认结果为准」这类规范表述，**不要编一个像样的数字填上**；\n"
            "- 范例（同类项目）只作**行文与结构参考**，其中的项目名/地名/数字**绝不能抄进正文**。"
        )

    async def _retrieve_four_dimension(self, state: dict, n: int = 10) -> str:
        """专线检索「四性分析」范文（`kb_four_dimension`）。

        🔴 为什么要专线：第4章 4.2 与第10章整章都要写四性分析，而
        `CHAPTER_KB_TYPES` 的主类型取回的是**评分表碎片**，四性范文混在几十条里排不上来。
        结果 LLM 只能凭记忆编法规名与规划名 —— 业主明确要求「不要让 LLM 乱编，要结合 RAG」。

        该库里是知识库内的**土地征收案卷**四性分析原文（法律法规相符性/地方规划相符性/
        区域经济发展需要/符合群众根本利益/资金来源可靠…），正是本项目能对号入座的依据。
        """
        try:
            from app.rag.rag_service import rag_service
            from app.agent.chapter_definitions import get_chapter_rag_query
            _ret = rag_service.retriever          # 复用常驻检索器（自带 embedder + vector_store）
            vs = _ret.vector_store
            col = vs.get_or_create_collection("kb_four_dimension")
            if not col.count():
                return ""
            q = f"{get_chapter_rag_query(self.chapter_number)} 合法性 合理性 可行性 可控性 " \
                f"{str(state.get('project_context',''))[:80]}"
            emb = await _ret.embedder.embed_text(q)
            res = vs.query(col, emb, n)
            docs = (res.get("documents") or [[]])[0] if isinstance(res, dict) else []
            metas = (res.get("metadatas") or [[]])[0] if isinstance(res, dict) else []
            out = []
            for d, m in zip(docs, metas or [None] * len(docs)):
                sec = str((m or {}).get("section_title", "") or "")
                out.append(f"【{sec}】\n{d}" if sec else str(d))
            return "\n\n---\n\n".join(out)
        except Exception as e:
            logger.warning(f"Chapter {self.chapter_number} 四性分析检索失败: {e}")
            return ""

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
            # 🔴 location 若混入视觉分析 dict/JSON，清洗为纯文本（防正文出现 {'general_area': ...}）
            if key in ("location", "land_location"):
                value = self._sanitize_filled_value(value)
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
                r'[一-龥]{2,6}(?:人民政府|管理委员会|街道办事处)',
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

        🔴 问卷总数只取 total_samples/survey_total_count，严禁用 household_count（涉及户数≠问卷数）。
        支持率/反对率取真实字段，未提供则不推导，禁止把「户数」当「问卷数」换算人数。
        """
        stats = {}
        # 问卷总数：优先核验值 survey_total_count，其次 total_samples；不回退到 household_count
        try:
            total = _safe_int(filled.get("survey_total_count") or filled.get("total_samples"))
        except (TypeError, ValueError):
            total = 0
        support_rate_str = str(filled.get("support_rate", "") or "").replace('%', '').strip()
        oppose_rate_str = str(filled.get("oppose_rate", "") or "").replace('%', '').strip()

        # Parse rates
        support_rate = 0.0
        oppose_rate = None
        if support_rate_str:
            try:
                support_rate = float(support_rate_str)
            except (ValueError, TypeError):
                support_rate = 0.0
        if oppose_rate_str:
            try:
                oppose_rate = float(oppose_rate_str)
            except (ValueError, TypeError):
                oppose_rate = None

        if total > 0:
            stats["total_surveys"] = str(total)
        if support_rate > 0 and total > 0:
            support = int(total * support_rate / 100)
            oppose = oppose_rate if oppose_rate is not None else max(0, 100 - support_rate)
            stats["support_count"] = str(support)
            stats["support_rate"] = f"{support_rate:.1f}%"
            stats["oppose_count"] = str(int(total * oppose / 100))
            stats["oppose_rate"] = f"{oppose:.1f}%"
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

        # 🔴 清理一级标题下的游离正文：所有内容必须归属二级标题
        # 结构：`## 第X章 XXX` 后若有直接正文（非标题行），且之后还有二级标题 → 删除该游离正文
        content = self._remove_h1_free_text(content)

        # 🔴 修复标题编号混乱：自创数字三级标题→（一）（二）；中文序号章重复标题→删除
        content = self._normalize_headings(content)

        # 🔴 兜底清理旧44分制残留（评分=扣分值，无"满分44分"概念）
        content = re.sub(r'[（(]?\s*满分\s*44\s*分\s*[）)]?', '', content)
        content = re.sub(r'总分\s*44\s*分', '', content)
        content = re.sub(r'44\s*分制', '扣分制', content)
        content = content.replace('满分44分', '').replace('总分44分', '')

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
        # 🔴 不要往「以冒号结尾的行」后面补破折号——那是早期占位符时代的遗留，
        #    会产出「拟征收地块基本情况如下表所示：—」这种正文脏字符。
        content = re.sub(r'：\s*—\s*\n', '：\n', content)

        # Strip JSON blocks
        content = re.sub(r'```json\s*[\s\S]*?```', '', content)
        content = re.sub(r'```\s*', '', content)

        # 🔴 正文不写「界址点数」——表格（第6章 表6-1）里已经有了，写在正文纯属冗余。
        #    注意只删**正文**；表格单元格里的「界址点数」列不受影响（本函数不处理表内）。
        content = self._strip_boundary_point_mentions(content)

        # 🔴 标准号规范化：模型常把连字符写丢（DB32/T 4013-2021 → DB32/T 40132021），
        #    也常写成全角／长破折号。统一回「DB32/T 4013-2021」这种写法。
        content = re.sub(
            r'(DB\s?32\s*/\s*T\s*)(\d{3,5})\s*[-—–－‐]?\s*(\d{4})',
            lambda m: f"{m.group(1).replace(' ', '').upper()} {m.group(2)}-{m.group(3)}",
            content,
        )
        # 其他常见标准号：GB/T、JGJ、苏政发 等形如 XXXX 1234-2020
        content = re.sub(
            r'\b(GB\s?/\s?T|JGJ|CJJ|SL|TD\s?/\s?T)\s*(\d{3,5})\s*[-—–－‐]?\s*(\d{4})\b',
            lambda m: f"{m.group(1).replace(' ', '')} {m.group(2)}-{m.group(3)}",
            content,
        )

        # 🔴 Fix garbled single-line tables
        content = self._fix_garbled_tables(content)

        # 🔴 表格末尾被吞进来的解读句 → 拆出来单独成段
        content = self._split_table_trailing_prose(content)

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

    def _remove_h1_free_text(self, markdown: str) -> str:
        """删除一级标题（## 第X章）下的游离正文。

        规则：一级标题行后紧跟的正文段落（非标题、非空行），若之后还有二级标题
        （### 或 ## 或 数字.数字），则该正文视为"游离内容"，删除之。
        所有正文必须归属到二级标题下。
        """
        import re as _re
        lines = markdown.split('\n')
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # 判断是否一级标题：## 第X章
            is_h1 = bool(_re.match(r'^#+\s*第[一二三四五六七八九十\d]+章', stripped))
            if is_h1:
                result.append(line)
                i += 1
                # 收集一级标题后紧跟的游离正文行（直到遇到下一个标题）
                free_lines = []
                while i < len(lines):
                    s = lines[i].strip()
                    # 遇到标题（## 或 ### 或 数字.数字 或 第X章）→ 停止
                    if _re.match(r'^#{1,3}\s', s) or _re.match(r'^\d+\.\d+\s', s) or _re.match(r'^第[一二三四五六七八九十\d]+章', s):
                        break
                    if s:  # 非空行 = 游离正文
                        free_lines.append(lines[i])
                    i += 1
                # 若游离正文后确实有二级标题（free_lines 非空且后续有标题），则丢弃游离正文
                # 判断是否还有后续标题
                has_next_heading = any(
                    _re.match(r'^#{1,3}\s', lines[j].strip()) or _re.match(r'^\d+\.\d+\s', lines[j].strip())
                    for j in range(i, len(lines))
                )
                if free_lines and has_next_heading:
                    # 丢弃游离正文（不 append free_lines）
                    pass
                else:
                    # 一级标题后没有二级标题（整章无小节）→ 保留游离正文（否则会丢内容）
                    result.extend(free_lines)
                continue
            result.append(line)
            i += 1
        return '\n'.join(result)

    def _normalize_headings(self, markdown: str) -> str:
        """修复 LLM 标题编号混乱。

        规则（与模板/规范对齐）：
        1. 标准二级标题（如 `### 4.1 合法性分析`，与 template_headings.CHAPTER_HEADINGS
           完全一致）→ 保留原样。
        2. 中文序号章内重复标题（如 `## 六、风险等级`、`### 八、…`）→ 删除
           （章节标题由组装器统一生成 `# 第X章 …`）。
        3. 其余自创标题（撞号三级标题如 `4.1 决策主体与权限`、层数不一的
           `4.1.2`/`4.2`/`4.3` 混用）→ 统一改写为正文小点 `**（一）（二）（三）**`，
           与模板「（一）（二）」分点风格一致。
        """
        import re as _re
        # 🔴 小节名**按项目动态生成**的章（如第11章分析预测风险源、第13章工作建议），
        #    没有固定标题表可比对，归一化会把它们的小节标题全部改写成正文小点
        #    → 章节结构塌掉。这类章直接放行。
        try:
            from app.agent.chapter_definitions import has_dynamic_sections
            if has_dynamic_sections(self.chapter_number):
                return markdown
        except Exception:
            pass
        try:
            from app.agent.template_headings import CHAPTER_HEADINGS
        except Exception:
            return markdown

        # 标准二级标题全文本集合：{"4.1 合法性分析", "6.1 量化评分指标体系", ...}
        std_heading_texts = set()
        for headings in CHAPTER_HEADINGS.values():
            for h in headings:
                std_heading_texts.add(h.strip())

        cn_num = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                  "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

        lines = markdown.split('\n')
        out = []
        sub_counter = 0
        inside_std_section = False

        for line in lines:
            s = line.strip()
            m = _re.match(r'^#{1,4}\s*(.+)$', s)
            if not m:
                out.append(line)
                continue
            heading = m.group(1).strip().strip('*').strip()
            if not heading:
                out.append(line)
                continue

            # 标准二级标题 → 保留，重置（一）（二）计数
            if heading in std_heading_texts:
                out.append(line)
                inside_std_section = True
                sub_counter = 0
                continue

            # 中文序号章内重复标题（六、/七、/八、…）→ 删除（非章节标题本身）
            if _re.match(r'^[一二三四五六七八九十]+、', heading) and '章' not in heading:
                continue

            # 自创标题 → 改写为（N）正文小点（文档顺序递增）
            if inside_std_section or _re.match(r'^\d+\.\d+(\.\d+)?\s', heading):
                sub_counter += 1
                cn = cn_num[sub_counter] if sub_counter < len(cn_num) else str(sub_counter)
                title = _re.sub(r'^[\d\.]+\s*', '', heading).strip()
                # 🔴 去掉标题自带的中文序号，避免与方法生成的序号叠加成「（一）（一）」
                title = _re.sub(r'^[（(][一二三四五六七八九十]+[）)]\s*', '', title).strip()
                out.append(f"**（{cn}）{title}**")
                continue

            # 其他（如「一、国家法律」等固定节）→ 原样保留
            out.append(line)

        return '\n'.join(out)

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

        # 🔴 实施单位取**本报告所属公司**（字段校验阶段已按公司填好）；
        #    取不到就明确告诉模型「不要写具体单位名」——
        #    以前这里写死「实施单位：江苏众拓…」，模型会把我们的名字抄进外部用户的报告。
        _impl_unit_hint = str(((state.get("filled_data") or {}).get("implement_unit")) or "").strip() \
            or "（本报告未提供实施单位，相关位置写「待填写」，不得写入任何具体公司名）"

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

        # 🔴 章节二级标题结构规范（含每节内容要点/数据/图片规格）
        try:
            from app.agent.chapter_definitions import format_sections_for_prompt
            section_spec = format_sections_for_prompt(self.chapter_number)
        except Exception:
            section_spec = ""

        # 🔴 本章「人工模板底稿」：从公司标准模板切出本章对应文本，作为结构与行文基准
        try:
            from app.services.template_chapter_chunker import build_template_block_for_prompt
            template_block = build_template_block_for_prompt(self.chapter_number)
        except Exception:
            template_block = ""

        return (
            f"你是一位资深稳评工程师，长期做土地征收社会稳定风险评估。\n"
            f"根据你的专业经验撰写第{self._chapter_num_cn()}章「{self.chapter_title}」。\n\n"
            f"## 核心原则\n"
            f"- 根据DB32/T4013-2021规范和你8年的实战经验来组织内容\n"
            f"- 🔴 严格按下方「本章结构规范」的二级标题逐一撰写，禁止新增/删减/合并/改名标题\n"
            f"- 🔴 每个二级标题下必须有实质内容，禁止标题后空白\n"
            f"- 🔴 所有地名只能用「可用数据 / 事实卡」里的真实地名，禁止使用任何其他地名\n\n"
            f"## ⛔ 禁止\n"
            f"- 禁用词：{'、'.join(AI_BUZZWORDS)}\n"
            f"- 禁止「第一/第二/第三」工整排比\n"
            f"- 禁止编造数据，用户没提供的写规范话术（如「以现状调查确认结果为准」）\n"
            f"- 禁止AI翻译腔，像真人工程师写报告\n\n"
            f"## 🔴 数据归位铁律（本报告各章数据各归其位，不得越界、不得重复）\n"
            f"- 项目概况类数据（征收面积/地类面积/四至/户数）只在第4章「决策事项基本情况」出现，后续章节不得重复罗列\n"
            f"- 勘测定界数据（各地类/各权属单位面积）只属于第4章，不得出现在第7章四性分析\n"
            f"- 问卷/座谈数据（份数、支持率、参会人数、诉求）只属于第6章「各利益相关者对决策事项的反映」\n"
            f"- 风险因素与评分只属于第8章「分析预测风险源」；防范化解措施只属于第9章\n"
            f"- 文号（如洪拟征告〔2026〕X号）必须严格来自「可用数据」里的真实文号，绝不编造、不改号、不抄范文里的文号\n"
            f"- 同一数据全文只写一次，避免同一组数字在多章反复出现\n"
            f"## 🚫 范文数据一律作废（最重要）\n"
            f"- 范文/参考里的所有具体数字（面积/地类/户数/文号/村组名）都是**别的项目的示例数据**，一个字都不能抄\n"
            f"- 你只能从「可用数据」里取真实数据。范文数字只是让你看「该在哪写什么类型的数据」，不是让你抄数值\n"
            f"- 勘测数据每次每个项目都不一样，绝不能用范文里的勘测面积套到本项目\n"
            f"- 如果「可用数据」里没有某项数据 → 写规范话术（如「以现状调查确认结果为准」），绝不能拿范文的数字填进去\n\n"
            f"## 🚫 禁止模糊表达（写不出具体就写规范话术，绝不写笼统话）\n"
            f"- ❌ 禁止写「有关单位」「相关部门」「上级部门」「有关人员」——必须写具体单位名称，不知道就写「以现状调查确认结果为准」\n"
            f"- ❌ 禁止写「依据相关法律法规」「根据国家有关规定」「按照相关政策要求」——必须写具体法规名称+文号，知识库没有就写「依据现行土地管理法律法规」，不编造\n"
            f"- ❌ 禁止写「相关部门负责」「有关方面」等责任主体模糊表述——直接写「责任单位：XX」或「以现状调查确认结果为准」\n"
            f"- ✅ 正确示范：写「责任单位为〔取事实卡的责任单位〕」或「依据《土地管理法》第四十六条」，写不出具体就写「以现状调查确认结果为准」\n"
            f"- 空话凑字数（如「各单位应高度重视」）一律禁止，宁可短，不要空\n\n"
            f"## ✅ 鼓励\n"
            f"- 段落长短错落，短句穿插长段\n"
            f"- 基层过渡语：「结合入户走访」「根据社区书记反馈」\n"
            f"- 结论适度模糊：「大概率」「初步判断」「需重点关注」\n"
            f"- 政策引用精简，只列本章直接相关的\n"
            f"- 实施单位：{_impl_unit_hint}\n"
            f"- 表格用 markdown 语法写，有数据支撑才写，缺数据写「以现状调查确认结果为准」\n"
            f"- 每个小标题下必须有实质内容，禁止标题后空白\n"
            f"{skeleton_rule}\n"
            # 🔴 标准小标题约束（修复死代码：heading_constraint 之前没拼进返回）
            f"{heading_constraint}\n"
            # 🔴 章节二级标题结构规范（内容要点+数据+图片规格，权威结构）
            f"{section_spec}\n"
            # 🔴 本章人工模板底稿（从数据库模板按 12 章切块，结构与行文基准）
            f"{template_block}\n"
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
            f"⛔ 涉及村组只能用「可用数据 / 事实卡」里的真实村组；事实卡没有就写「以现状调查确认结果为准」，禁止出现任何其他社区名/村名。\n"
            f"🔒 用户提供的公告/勘测报告中的数据是权威数据，禁止编造或修改。\n"
            f"   如无数据写\"以正式公告为准\"，不要编造具体数字。\n"
            f"{prev_summary}\n"
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
            TEMPLATE_TABLE_DATA_RULES,
            TEMPLATE_TABLE_SKELETONS, RISK_LEVEL_CRITERIA,
        )

        kb_ctx = get_knowledge_context_for_chapter(state, self.chapter_number)
        parts = []

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
        """清理 LLM 幻觉残留。

        🔴 不写死任何项目名/地名——地名一律由「事实卡」约束（prompt 里已强制）。
        这里只做与项目无关的通用清理（裸文件名、AI 套词）。
        """
        import re as _re_hn
        # 去掉正文里裸文件名
        content = _re_hn.sub(r'\n[^\n]{5,60}\.(png|jpg|jpeg|PNG|JPG)\n', '\n', content)
        content = _re_hn.sub(r'^[^\n]{5,60}\.(png|jpg|jpeg|PNG|JPG)\s*$', '', content, flags=_re_hn.MULTILINE)
        # AI 套词兜底
        content = content.replace('有力支撑', '有效支持')
        content = content.replace('有力保障', '有效保障')
        return content

    @staticmethod
    def _strip_boundary_point_mentions(content: str) -> str:
        """删掉正文里的「界址点 N 个」表述（表格里已有该列，正文重复是冗余）。

        逐句处理：
        - 从句里去掉该分句后，句子还剩实质内容 → 保留剩余部分；
        - 去掉后整句只剩个主语（如「本项目。」）→ 整句删掉，避免留下残句。
        """
        import re as _re_bp
        _CLAUSE = _re_bp.compile(
            r'[，,、；;]?\s*(?:其中)?\s*(?:共)?\s*界址点(?:数)?\s*(?:为|共|达)?\s*'
            r'\d+\s*个?\s*(?:[（(]\s*共?\s*\d+\s*个\s*[)）])?')
        _MIN_KEEP = 8  # 剩余字数少于这个数，判为残句

        out_lines = []
        for line in str(content or "").split('\n'):
            if '界址点' not in line:
                out_lines.append(line)
                continue
            # 表格行不动
            if line.strip().startswith('|'):
                out_lines.append(line)
                continue
            kept = []
            for seg in _re_bp.split(r'(?<=[。；;])', line):
                if not seg or '界址点' not in seg:
                    kept.append(seg)
                    continue
                cleaned = _CLAUSE.sub('', seg)
                cleaned = _re_bp.sub(r'[，,]\s*([。；;])', r'\1', cleaned)
                cleaned = _re_bp.sub(r'([。；;])\s*\1+', r'\1', cleaned)
                core = cleaned.strip().rstrip('。；;').strip()
                if len(core) < _MIN_KEEP:
                    continue          # 残句，整句丢掉
                kept.append(cleaned)
            out_lines.append(''.join(kept))
        return '\n'.join(out_lines)

    @staticmethod
    def _split_table_trailing_prose(content: str) -> str:
        """表格末尾被吞进来的解读句 → 拆出来单独成段。

        模型写完表格后常把「上表所列…」这类解读直接接成一行表格行，
        形如 `| 上表所列法规中，…的判断。 | — | — | — |`。
        判据：首格是长句（>40 字）且其余格全是空/破折号。
        """
        _EMPTY = ('', '—', '-', '－', '–', '/')
        out = []
        for ln in str(content or "").split('\n'):
            s = ln.strip()
            if s.startswith('|') and s.endswith('|') and s.count('|') >= 3:
                cells = [c.strip() for c in s.strip('|').split('|')]
                if len(cells) >= 2 and len(cells[0]) > 40 and \
                        all(c in _EMPTY for c in cells[1:]):
                    out.append('')
                    out.append(cells[0])
                    continue
            out.append(ln)
        return '\n'.join(out)

    @staticmethod
    def _fix_garbled_tables(content: str) -> str:
        """Fix LLM-generated single-line tables where rows are concatenated with — separators."""

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
