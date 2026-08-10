"""LangGraph-based report generation workflow with human-in-the-loop.

Flow:
  ingest → check_requirements → [missing?] → pause(prompt user) → resume
       → generate_chapters → assemble_docx → complete

When required data is missing (company info, project details, etc.),
the workflow pauses and sends a `missing_data_prompt` SSE event to the
frontend. The user fills in the missing fields, then the workflow resumes.
"""

import asyncio
import logging
import os
import time
from typing import List, Dict, Any, Optional
from enum import Enum

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# State Definition
# ═══════════════════════════════════════════════════════════════

class WorkflowPhase(str, Enum):
    IDLE = "idle"
    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    CHECKING = "checking_requirements"
    PAUSED = "paused"              # Waiting for user input
    GENERATING = "generating"
    ASSEMBLING = "assembling"
    COMPLETE = "complete"
    ERROR = "error"


# 🔴 Use plain dict to avoid LangGraph stripping extra keys (e.g., _uploaded_files, _pdf_raw_text)
ReportWorkflowState = dict


# ═══════════════════════════════════════════════════════════════
# Required Fields Definition
# ═══════════════════════════════════════════════════════════════

REQUIRED_FIELDS = [
    {
        "key": "org_name",
        "label": "决策主体/责任单位",
        "description": "负责本次征收决策的政府单位全称",
        "example": "淮安市洪泽区人民政府",
        "category": "project_info",
    },
    {
        "key": "project_name",
        "label": "项目名称",
        "description": "征收项目的正式名称（含文号）",
        "example": "洪拟征告〔2026〕7号（朱坝街道三圩社区商业服务业设施用地项目）",
        "category": "project_info",
    },
    {
        "key": "location",
        "label": "项目位置",
        "description": "征收土地的具体位置（省-市-区-街道-村组）",
        "example": "江苏省淮安市洪泽区朱坝街道三圩社区二组、三组、六组",
        "category": "project_info",
    },
    {
        "key": "area_mu",
        "label": "征收面积（亩）",
        "description": "拟征收土地总面积，单位为亩",
        "example": "489.513",
        "category": "project_info",
    },
    {
        "key": "land_use",
        "label": "土地用途",
        "description": "征收后的规划土地用途",
        "example": "商业服务业设施用地",
        "category": "project_info",
    },
    {
        "key": "implement_unit",
        "label": "稳评实施单位",
        "description": "承担社会稳定风险评估的第三方机构全称",
        "example": "江苏众拓项目代理咨询有限公司",
        "category": "company_info",
        "default": "江苏众拓项目代理咨询有限公司",
    },
    {
        "key": "company_address",
        "label": "公司地址",
        "description": "稳评实施单位的注册地址",
        "example": "淮安经济技术开发区XX路XX号",
        "category": "company_info",
    },
    {
        "key": "company_contact",
        "label": "公司联系人",
        "description": "稳评项目负责人姓名",
        "example": "陈春",
        "category": "company_info",
    },
    {
        "key": "compensation_standard",
        "label": "补偿标准",
        "description": "征地补偿标准（区片综合地价或具体金额）",
        "example": "按江苏省征地区片综合地价标准执行",
        "category": "compensation",
    },
    {
        "key": "household_count",
        "label": "涉及户数",
        "description": "征收涉及的农户总户数",
        "example": "85",
        "category": "survey",
    },
    {
        "key": "total_samples",
        "label": "问卷调查样本数",
        "description": "社会稳定风险调查发放的问卷总数",
        "example": "150",
        "category": "survey",
    },
]


# ═══════════════════════════════════════════════════════════════
# Progress emitter (set by API endpoint before running workflow)
# ═══════════════════════════════════════════════════════════════
_progress_callback = None

def set_workflow_progress_callback(cb):
    global _progress_callback
    _progress_callback = cb

async def _emit_progress(phase: str, steps: dict):
    if _progress_callback:
        try:
            await _progress_callback(phase, steps)
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
# Workflow Nodes
# ═══════════════════════════════════════════════════════════════

async def _web_search_fallback(chapter_title: str, region: str) -> str:
    """RAG检索为空时的联网降级 — 用DashScope Qwen + enable_search搜索规范依据.

    仅降级时调用,用现有 VISION_API_KEY(DashScope),不影响主模型。
    失败返回空字符串,不阻塞生成。
    """
    try:
        from app.config import settings
        from app.services.llm_service import LLMService
        vision_key = getattr(settings, "VISION_API_KEY", "") or ""
        if not vision_key:
            return ""
        # Temp LLMService pointed at DashScope for built-in web search
        llm = LLMService()
        llm.api_key = vision_key
        llm.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        llm.model = "qwen-max"
        query = f"{region} 征地社会稳定风险评估 {chapter_title} 相关法律法规和规范依据要点"
        result = await llm.chat(
            messages=[{"role": "user", "content": query}],
            max_tokens=1000, temperature=0.2, enable_search=True,
        )
        return str(result) if result else ""
    except Exception as e:
        logger.warning(f"[RAG] web search fallback failed: {e}")
        return ""


async def node_ingest_materials(state: ReportWorkflowState) -> ReportWorkflowState:
    """Node 1: Ingest and analyze uploaded materials. Skips if already analyzed."""
    # 🔴 Skip if Phase 1 already analyzed (check multiple indicators)
    has_text = (state.get("_pdf_raw_text") and len(state.get("_pdf_raw_text", "")) > 500)
    has_materials = bool(state.get("_project_materials"))
    if has_text or has_materials:
        state["logs"].append(f"📂 资料已解析(文本{len(state.get('_pdf_raw_text',''))}字)，跳过重复分析")
        await _emit_progress("analysis", {"analysis": "done", "outline": "running", "generation": "pending", "assembly": "pending"})
        return state

    state["phase"] = WorkflowPhase.INGESTING.value
    state["logs"].append("📂 正在解析上传资料...")
    await _emit_progress("analysis", {"analysis": "running", "outline": "pending", "knowledge": "pending", "generation": "pending", "quality": "pending", "assembly": "pending"})

    try:
        from app.routers.report import _analyze_session_attachments
        # Build minimal state for analysis
        session_state = {
            "session_id": state["session_id"],
            "_domain": "stability",
            "filled_data": state.get("filled_data", {}),
            "_uploaded_files": [],
        }
        # Scan materials_dir (from state or from uploaded files)
        import os as _os
        materials_dir = state.get("materials_dir", "")
        if materials_dir and _os.path.exists(materials_dir):
            for root, dirs, files in _os.walk(materials_dir):
                for fn in files:
                    if not fn.startswith('.'):
                        session_state["_uploaded_files"].append(_os.path.join(root, fn))
        # Also use files already uploaded to session
        for f in state.get("_uploaded_files", []) or []:
            path = f.get("path", f) if isinstance(f, dict) else f
            if isinstance(path, str) and path not in session_state["_uploaded_files"]:
                session_state["_uploaded_files"].append(path)

        # 🔴 Pass existing analysis results so _analyze_session_attachments skips already-done work
        if state.get("_project_materials"):
            session_state["_project_materials"] = state["_project_materials"]
        if state.get("_pdf_raw_text"):
            session_state["_pdf_raw_text"] = state["_pdf_raw_text"]

        await _analyze_session_attachments(session_state, session_state["_uploaded_files"])

        # Merge extracted data back to workflow state
        for k, v in session_state.get("filled_data", {}).items():
            if v and k not in state.get("filled_data", {}):
                state["filled_data"][k] = v
        # 🔴 Copy OCR text so node_check_requirements can use it for LLM extraction
        if session_state.get("_pdf_raw_text"):
            state["_pdf_raw_text"] = session_state["_pdf_raw_text"]
        if session_state.get("_project_materials"):
            state["_project_materials"] = session_state["_project_materials"]

        state["logs"].append(f"✅ 解析完成：{len(session_state.get('_uploaded_files', []))} 个文件")
        await _emit_progress("analyzing", {"analysis": "done", "outline": "running", "fixed_data": "pending", "knowledge": "pending", "generation": "pending", "quality": "pending", "assembly": "pending"})
    except Exception as e:
        state["errors"].append(f"材料解析失败: {e}")
        state["logs"].append(f"⚠️ 材料解析跳过: {e}")

    return state


async def node_generate_chapters(state: ReportWorkflowState) -> ReportWorkflowState:
    """Generate outline + chapters with per-chapter human-in-the-loop.

    Flow:
    1. LLM extracts basic fields from OCR text (org_name, project_name, etc.)
    2. Generate dynamic outline based on materials
    3. For each chapter: generate content, if missing critical data → interrupt()
    4. On resume: continue from interrupted chapter
    """
    state["phase"] = WorkflowPhase.GENERATING.value
    is_resume = state.get("_is_resume", False)

    try:
        from app.services.llm_service import LLMService
        llm = LLMService()
        filled = state.get("filled_data", {})

        # ═══ Step 1: LLM extraction from OCR text ═══
        pdf_text = state.get("_pdf_raw_text", "") or state.get("_pdf_texts", "")
        # 🔴 Fallback: build text from _project_materials if not directly available
        if not pdf_text:
            materials = state.get("_project_materials", []) or []
            parts = []
            for m in materials:
                txt = m.get("text_content", "") if isinstance(m, dict) else ""
                if txt and len(txt) > 50:
                    parts.append(txt)
            if parts:
                pdf_text = "\n".join(parts)
                state["_pdf_raw_text"] = pdf_text
        if pdf_text and len(pdf_text) > 100:
            try:
                from app.routers.report import _llm_extract_fields
                extracts = await _llm_extract_fields(pdf_text, llm)
                for k, v in extracts.items():
                    if v and str(v).strip() and not filled.get(k):
                        filled[k] = str(v).strip()
                if extracts:
                    state["logs"].append(f"🤖 从资料中提取到 {len(extracts)} 个字段：{list(extracts.keys())}")
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}")

        # ═══ Step 1.5: Pre-check ALL missing fields once (not per-chapter) ═══
        required_keys = ("org_name", "project_name", "location", "area_mu",
                         "land_use", "compensation_standard")
        _MISSING_LABELS = {"org_name":"决策主体/责任单位","project_name":"项目名称",
                           "location":"项目位置","area_mu":"征收面积（亩）",
                           "land_use":"土地用途","compensation_standard":"补偿标准"}
        missing_all = []
        for key in required_keys:
            if not filled.get(key):
                missing_all.append({"key": key, "label": _MISSING_LABELS.get(key, key)})
        if missing_all:
            state["paused"] = True
            state["logs"].append(f"⏸️ 缺少 {len(missing_all)} 项信息：{[m['label'] for m in missing_all]}")
            user_responses = interrupt({
                "type": "missing_data",
                "missing_fields": missing_all,
                "message": f"请补充以下 {len(missing_all)} 项信息后继续生成报告",
            })
            state["paused"] = False
            for key, value in (user_responses or {}).items():
                if value and str(value).strip():
                    filled[key] = str(value).strip()
            state["filled_data"] = filled
            state["logs"].append(f"📝 已补充 {len([k for k,v in (user_responses or {}).items() if v])} 项信息")

        # ═══ Step 2: Generate or reuse outline ═══
        _outline = state.get("_outline")
        if _outline and _outline.get("chapters"):
            state["logs"].append(f"📋 复用大纲：{len(_outline['chapters'])} 章")
        else:
            state["logs"].append("📋 根据材料动态生成大纲...")
            from app.services.master_orchestrator import generate_outline
            has_announcement = bool(filled.get("doc_reference") or filled.get("project_name"))
            has_survey = bool(filled.get("total_samples") or filled.get("survey_total_count"))
            _outline = await generate_outline(llm, filled, has_survey, has_review=False, has_announcement=has_announcement)
            state["_outline"] = _outline
            state["logs"].append(f"📋 大纲：{len(_outline.get('chapters', []))} 章")

        # ═══ Step 3: Build image catalog ═══
        from app.services.image_catalog import build_image_catalog, get_chapter_image_guide
        uploaded = state.get("_uploaded_files", []) or []
        uploaded_paths = []
        for item in uploaded:
            if isinstance(item, str): uploaded_paths.append(item)
            elif isinstance(item, dict): uploaded_paths.append(item.get("path", ""))
        img_catalog = build_image_catalog(uploaded_paths,
                                          ai_classifications=state.get("_classified_images"))

        # ═══ Step 4: Generate chapters — with per-chapter interrupt ═══
        from app.agent.agents.chapters import get_chapter_agent
        chapters = {}
        chapter_defs = _outline.get("chapters", [])
        total = len(chapter_defs)

        # On resume, carry over completed chapters
        if is_resume:
            existing = state.get("chapters", {})
            for cn, cd in existing.items():
                if isinstance(cd, dict) and cd.get("markdown") and cd.get("status") != "error":
                    chapters[cn] = cd
            state["logs"].append(f"🔄 续写：已完成 {len(chapters)}/{total} 章")

        # 🔴 Split: early chapters (1-3) sequential with interrupt; later chapters parallel
        early_defs = [d for d in chapter_defs if d.get("num", 0) <= 3]
        late_defs = [d for d in chapter_defs if d.get("num", 0) > 3]

        async def _gen_one_chapter(ch_def, sem):
            async with sem:
                ch_num = ch_def.get("num", 0)
                if ch_num in chapters:
                    return
                img_guide = get_chapter_image_guide(ch_num, img_catalog)

                # 🔴 RAG: retrieve regulations + example reports for this chapter
                rag_ctx = None
                try:
                    from app.rag.rag_service import rag_service
                    ch_title = ch_def.get("title", "")
                    rag_ctx = await rag_service.retrieve_for_chapter(
                        chapter_number=ch_num,
                        session_id=state.get("session_id", ""),
                        project_context=ch_title,
                        n_results=8,
                        domain=None,
                    )
                    n_reg = len((rag_ctx.get("chapter_context", "") or "")) + len((rag_ctx.get("local_regulation_context", "") or ""))
                    n_ex = len((rag_ctx.get("example_context", "") or ""))
                    logger.info(f"[RAG] Ch{ch_num} retrieved {n_reg} chars regulation + {n_ex} chars example")
                    # 🔴 Fallback: nothing retrieved → web search
                    if n_reg == 0 and n_ex == 0:
                        region = filled.get("location", "") or filled.get("org_name", "")
                        web_text = await _web_search_fallback(ch_title, region)
                        if web_text:
                            rag_ctx = {"chapter_context": web_text, "local_regulation_context": "",
                                       "example_context": "", "project_context": "", "sources": []}
                            logger.info(f"[RAG] Ch{ch_num} fallback to web search: {len(web_text)} chars")
                except Exception as e:
                    logger.warning(f"[RAG] Ch{ch_num} retrieval failed: {e}")

                max_retries = 2 if ch_num <= 3 else 1
                md = ""
                for attempt in range(max_retries):
                    from app.services.master_orchestrator import build_chapter_prompt, review_chapter
                    prompt = build_chapter_prompt(
                        ch_def, filled, img_guide, chapters,
                        feedback=state.get(f"_ch{ch_num}_feedback") if attempt > 0 else None,
                        rag_context=rag_ctx,
                    )
                    agent = get_chapter_agent(ch_num, llm_service=llm)
                    agent_state = {
                        "session_id": state.get("session_id", ""),
                        "report_title": state.get("report_title", filled.get("project_name", "")),
                        "project_context": state.get("project_context", ""),
                        "filled_data": filled, "_domain": "stability", "_report_style": "jinhu",
                        "current_chapter": ch_num, "chapters": {},
                        "_custom_prompt": prompt, "_use_custom_prompt": True,
                    }
                    q = asyncio.Queue()
                    try:
                        await asyncio.wait_for(agent.run(agent_state, q), timeout=180.0)
                        ch_data = agent_state.get("chapters", {}).get(ch_num, {})
                        md = ch_data.get("markdown", "") if isinstance(ch_data, dict) else (ch_data if isinstance(ch_data, str) else "")
                    except Exception as e:
                        logger.error(f"Ch{ch_num} generation failed: {e}")
                        md = ""
                    fb = review_chapter(ch_num, md, ch_def, img_guide)
                    if fb:
                        state[f"_ch{ch_num}_feedback"] = fb
                        if attempt < max_retries - 1: continue
                    else:
                        break
                chapters[ch_num] = {
                    "markdown": md or f"【第{ch_num}章生成失败】",
                    "title": ch_def.get("title", ""), "status": "approved" if md else "error",
                }

        # Generate early chapters sequentially
        for ch_def in early_defs:
            ch_num = ch_def.get("num", 0)
            if ch_num in chapters: continue
            await _gen_one_chapter(ch_def, asyncio.Semaphore(1))

        # 🔴 Generate late chapters in parallel (2 at a time)
        if late_defs:
            sem = asyncio.Semaphore(2)
            await asyncio.gather(*[_gen_one_chapter(d, sem) for d in late_defs
                                   if d.get("num", 0) not in chapters])
        state["chapters"] = chapters
        state["filled_data"] = filled
        state["logs"].append(f"✍️ 章节生成完成：{len(chapters)}/{total} 章")

    except Exception as e:
        # 🔴 Re-raise interrupt so LangGraph can handle it (NOT an error)
        from langgraph.errors import GraphInterrupt
        if isinstance(e, GraphInterrupt):
            raise
        state["errors"].append(f"章节生成失败: {e}")
        state["phase"] = WorkflowPhase.ERROR.value
        logger.error(f"node_generate_chapters failed: {e}")
        import traceback; traceback.print_exc()

    return state


async def node_quality_review(state: ReportWorkflowState) -> ReportWorkflowState:
    """Post-generation quality review: cross-reference, format compliance, data validation.
    If quality fails, re-generate problematic chapters automatically."""
    state["logs"].append("🔍 正在执行质量审查...")
    await _emit_progress("quality", {"analysis": "done", "outline": "done", "fixed_data": "done",
                                      "knowledge": "done", "generation": "done", "quality": "running", "assembly": "pending"})
    chapters = state.get("chapters", {})
    if not chapters:
        return state

    retry_chapters = set()
    quality_report = []

    for ch_num, ch in sorted(chapters.items()):
        if not isinstance(ch, dict): continue
        md = ch.get("markdown", "")
        issues = []

        # 1. Minimum content length (aligned with master orchestrator)
        min_words = {4: 2000, 6: 1800, 7: 1800, 8: 1800}.get(ch_num, 1000)
        if len(md) < min_words:
            issues.append(f"字数不足({len(md)}<{min_words})")

        # 2. AI buzzwords
        buzzwords = ['具有重要意义', '切实保障', '多措并举', '统筹推进', '综上所述', '有力支撑', '奠定了坚实基础']
        found_bw = [bw for bw in buzzwords if bw in md]
        if found_bw:
            issues.append(f"AI套词: {found_bw}")

        # 3. Must contain image markers if images are available
        img_guide = state.get("_chapter_image_guide", "")
        if "📸 本章可用图片" in str(img_guide) and "（无图片）" not in str(img_guide):
            if '![' not in md:
                issues.append("缺少图片标记")

        # 4. Must contain survey table markers for Ch3
        if ch_num == 3:
            if '[TABLE:ch3_public_survey]' not in md:
                issues.append("缺少公众调查表标记")
            if '[TABLE:ch3_dept_survey]' not in md:
                issues.append("缺少部门调查表标记")

        # 5. Scoring chapters must reference scoring data
        if ch_num in (6, 8) and '分' not in md:
            issues.append("评分章节缺少分数")

        # 5b. 🔴 Table format check — garbled tables must be regenerated
        if '| — |' in md or '|—|' in md:
            issues.append("表格格式乱码（|—|分隔符），需要重新生成")
        if md.count('|---|') < 1 and '|' in md:
            # Has pipe characters but no proper separator row
            issues.append("表格缺少分隔行，格式不正确")
        # Support rate must be 100% (征地项目合规要求)
        support_patterns = [r'支持率[：:]\s*(\d+\.?\d*)\s*%', r'(\d+\.?\d*)%\s*(?:支持|赞成)']
        for pat in support_patterns:
            m = __import__('re').search(pat, md)
            if m:
                rate = float(m.group(1))
                if rate < 100 and rate > 0:
                    issues.append(f"支持率{rate}%不合理，征地项目应为100%")
                break

        # Overly precise numbers (more than 2 decimal places or very specific)
        precise_nums = __import__('re').findall(r'\b\d+\.\d{3,}\b', md)
        if len(precise_nums) > 2:
            issues.append(f"数字过于精确({len(precise_nums)}处)，应使用模糊区间")

        # Compensation should use fuzzy ranges
        if __import__('re').search(r'(?:补偿|地价).*?\d+\.?\d*\s*万', md):
            if not __import__('re').search(r'(?:约|大概|左右|上下|～|~)', md):
                issues.append("补偿标准应使用模糊表述（如'约5-6万/亩'）")

        if issues:
            quality_report.append(f"Ch{ch_num}: {'; '.join(issues)}")
            retry_chapters.add(ch_num)

    if retry_chapters:
        state["logs"].append(f"  ⚠️ {len(retry_chapters)} 章不达标: {retry_chapters}")
        for issue in quality_report:
            state["logs"].append(f"    {issue}")

        # 🔴 Auto-retry: re-generate problematic chapters
        state["logs"].append("  🔄 自动重写不达标章节...")
        from app.agent.agents.chapters import get_chapter_agent
        from app.services.llm_service import LLMService
        llm = LLMService()

        for ch_num in sorted(retry_chapters):
            try:
                from app.agent.state import create_initial_state
                agent = get_chapter_agent(ch_num, llm_service=llm)
                retry_state = dict(create_initial_state(
                    session_id=state.get("session_id", "retry"),
                    report_title=state.get("report_title", ""),
                    project_context=state.get("project_context", ""),
                ))
                retry_state["filled_data"] = dict(state.get("filled_data", {}))
                retry_state["_domain"] = "stability"
                retry_state["_report_style"] = "jinhu"
                retry_state["current_chapter"] = ch_num
                retry_state["chapters"] = {}
                retry_state["chapter_feedback"] = f"上一版不达标：{'; '.join(quality_report)}。必须包含[TABLE:xxx]标记、删除AI套词。"
                q = __import__('asyncio').Queue()
                await __import__('asyncio').wait_for(agent.run(retry_state, q), timeout=200.0)
                retry_chapters_dict = retry_state.get("chapters", {})
                if not isinstance(retry_chapters_dict, dict):
                    retry_chapters_dict = {}
                new_ch = retry_chapters_dict.get(ch_num, {})
                if not isinstance(new_ch, dict):
                    new_ch = {"markdown": str(new_ch) if new_ch else ""}
                new_md = new_ch.get("markdown", "") if isinstance(new_ch, dict) else ""
                if new_md and len(new_md) >= min_words.get(ch_num, 600):
                    chapters[ch_num] = {"markdown": new_md, "status": "approved"}
                    state["logs"].append(f"  ✅ Ch{ch_num} 重写完成：{len(new_md)}字")
                else:
                    state["logs"].append(f"  ⚠️ Ch{ch_num} 重写仍不达标，保留原版")
            except Exception as e:
                state["logs"].append(f"  ❌ Ch{ch_num} 重写失败: {e}")

        state["chapters"] = chapters
    else:
        state["logs"].append("  ✅ 全部章节质量达标")

    await _emit_progress("quality", {"analysis": "done", "outline": "done", "fixed_data": "done",
                                      "knowledge": "done", "generation": "done", "quality": "done", "assembly": "pending"})
    return state


async def node_assemble_docx(state: ReportWorkflowState) -> ReportWorkflowState:
    """Node 4: Assemble final DOCX from generated chapters."""
    state["phase"] = WorkflowPhase.ASSEMBLING.value
    state["logs"].append("📄 正在组装 DOCX 报告...")
    await _emit_progress("assembling", {"analysis": "done", "outline": "done", "fixed_data": "done", "knowledge": "done", "generation": "done", "quality": "done", "assembly": "running"})
    try:
        chapters = state.get("chapters", {})
        if not chapters:
            state["errors"].append("无章节内容可组装")
            state["phase"] = WorkflowPhase.ERROR.value
            return state

        # Build minimal state for assembler
        from app.services.report_assembler import report_assembler
        asm_state = {
            "session_id": state.get("session_id", "report"),
            "chapters": chapters,
            "filled_data": state.get("filled_data", {}),
            "_domain": "stability",
        }
        output = report_assembler.assemble(asm_state)
        if not output:
            state["errors"].append("DOCX 组装返回空路径")
            state["phase"] = WorkflowPhase.ERROR.value
        else:
            state["output_path"] = output
            state["phase"] = WorkflowPhase.COMPLETE.value
            state["logs"].append(f"✅ 报告已生成: {output}")

            # 🔴 Generate review table (separate file)
            try:
                from app.services.review_table_service import generate_review_table
                from app.config import settings
                review_path = str(settings.STORAGE_DIR / "generated" / f"评审表_{state.get('session_id','report')}.docx")
                generate_review_table(review_path, state.get("filled_data", {}), chapters)
                state["review_table_path"] = f"generated/{os.path.basename(review_path)}"
                state["logs"].append(f"✅ 评审表已生成")
            except Exception as e:
                state["logs"].append(f"⚠️ 评审表生成跳过: {e}")

    except Exception as e:
        state["errors"].append(f"DOCX 组装失败: {e}")
        state["phase"] = WorkflowPhase.ERROR.value

    return state


# ═══════════════════════════════════════════════════════════════
# Workflow Builder
# ═══════════════════════════════════════════════════════════════

def build_report_workflow() -> StateGraph:
    """Build the LangGraph report generation workflow.

    Flow:
      ingest → check_requirements → generate_chapters → quality_review → assemble_docx → END

    Human-in-the-loop: node_generate_chapters uses interrupt() per-chapter
    when data is missing. Resume with Command(resume=...) continues from
    the interrupted chapter — no restart, no state loss.
    """
    workflow = StateGraph(ReportWorkflowState)

    # Add nodes
    workflow.add_node("ingest", node_ingest_materials)
    workflow.add_node("generate_chapters", node_generate_chapters)  # Includes LLM extraction + per-chapter interrupt
    workflow.add_node("quality_review", node_quality_review)
    workflow.add_node("assemble_docx", node_assemble_docx)

    # Flow: ingest → generate (with per-chapter HITL) → quality → assemble
    workflow.set_entry_point("ingest")
    workflow.add_edge("ingest", "generate_chapters")
    workflow.add_edge("generate_chapters", "quality_review")
    workflow.add_edge("quality_review", "assemble_docx")
    workflow.add_edge("assemble_docx", END)

    return workflow


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════

class ReportWorkflowRunner:
    """Runs the report generation workflow with human-in-the-loop support."""

    def __init__(self):
        self.workflow = build_report_workflow()
        self.checkpointer = MemorySaver()
        self.compiled = self.workflow.compile(checkpointer=self.checkpointer)
        self._progress_callback = None  # Optional async callback(state_dict)

    def set_progress_callback(self, callback):
        """Set callback for progress updates: async def callback(phase, step_states)."""
        self._progress_callback = callback

    async def _emit_progress(self, phase: str, steps: dict):
        """Emit progress to the callback if set."""
        if self._progress_callback:
            try:
                await self._progress_callback(phase, steps)
            except Exception:
                pass

    async def start(
        self,
        session_id: str,
        report_title: str,
        materials_dir: str = "",
        project_context: str = "",
        filled_data: Optional[Dict[str, Any]] = None,
        existing_state: Optional[Dict[str, Any]] = None,
    ) -> ReportWorkflowState:
        """Start the workflow from the beginning.

        If data is missing, the graph pauses via interrupt().
        Returns the interrupt payload in state["_interrupt"].

        Args:
            existing_state: Optional session state dict to merge (analysis results, etc.)
        """
        base: Dict[str, Any] = {
            "session_id": session_id,
            "report_title": report_title,
            "phase": WorkflowPhase.IDLE.value,
            "materials_dir": materials_dir,
            "project_context": project_context,
            "filled_data": filled_data or {
                "implement_unit": "江苏众拓项目代理咨询有限公司",
            },
            "chapters": {},
            "output_path": "",
            "missing_fields": [],
            "user_responses": {},
            "paused": False,
            "pause_message": "",
            "errors": [],
            "logs": [],
        }
        # 🔴 Merge existing session state — preserves analysis results, _analysis_done, etc.
        if existing_state:
            for k in ("_analysis_done", "_uploaded_files", "_image_catalog", "_pdf_texts",
                       "_pdf_raw_text", "_outline", "_is_resume", "_chapter_image_map",
                       "_project_materials", "_project_material_facts"):
                if k in existing_state:
                    base[k] = existing_state[k]
            # Merge filled_data: existing session state takes priority over defaults
            if existing_state.get("filled_data"):
                merged = dict(base["filled_data"])
                merged.update(existing_state["filled_data"])
                base["filled_data"] = merged
            # Preserve existing logs
            if existing_state.get("_workflow_logs"):
                base["logs"] = list(existing_state["_workflow_logs"])

        state: ReportWorkflowState = base

        # 🔴 Use unique thread_id for each fresh start to avoid stale checkpoint conflicts
        import time as _time
        _tid = f"{session_id}_{_time.time()}"
        config = {"configurable": {"thread_id": _tid}}
        final_state = None

        async for event in self.compiled.astream(state, config):
            # 🔴 interrupt() yields {'__interrupt__': (Interrupt(value=...),)} — not an exception!
            if "__interrupt__" in event:
                interrupt_tuple = event["__interrupt__"]
                interrupt_val = interrupt_tuple[0] if interrupt_tuple else None
                interrupt_payload = getattr(interrupt_val, 'value', str(interrupt_val))
                logger.info(f"Workflow interrupted: {interrupt_payload}")
                # Get checkpointed state and attach interrupt payload
                checkpointed = self.checkpointer.get(config)
                if checkpointed:
                    checkpointed["_interrupt"] = interrupt_payload
                    checkpointed["_thread_id"] = _tid  # 🔴 Store for resume
                    return checkpointed
                state["_interrupt"] = interrupt_payload
                state["_thread_id"] = _tid
                return state
            for node_name, node_state in event.items():
                final_state = node_state
                logger.info(f"Workflow node '{node_name}' completed")

        if final_state:
            final_state["_thread_id"] = _tid
        return final_state or state

    async def resume(
        self,
        session_id: str,
        user_responses: Dict[str, str],
    ) -> ReportWorkflowState:
        """Resume the workflow from the interrupt point.

        Uses LangGraph's Command(resume=...) to continue the graph
        from exactly where interrupt() was called.
        """
        # 🔴 Use the thread_id stored from start() — or fall back to session_id
        _tid = user_responses.pop("_thread_id", None) if isinstance(user_responses, dict) else None
        config = {"configurable": {"thread_id": _tid or session_id}}

        final_state = None
        async for event in self.compiled.astream(
            Command(resume=user_responses), config
        ):
            # 🔴 Handle re-interrupt (e.g., still missing data after resume)
            if "__interrupt__" in event:
                interrupt_tuple = event["__interrupt__"]
                interrupt_val = interrupt_tuple[0] if interrupt_tuple else None
                interrupt_payload = getattr(interrupt_val, 'value', str(interrupt_val))
                logger.info(f"Workflow interrupted again: {interrupt_payload}")
                checkpointed = self.checkpointer.get(config)
                if checkpointed:
                    checkpointed["_interrupt"] = interrupt_payload
                    return checkpointed
                return final_state or {"_interrupt": interrupt_payload}
            for node_name, node_state in event.items():
                final_state = node_state
                logger.info(f"Workflow node '{node_name}' completed (resume)")

        return final_state or {}

    def get_state(self, session_id: str) -> Optional[ReportWorkflowState]:
        """Get current workflow state for a session."""
        config = {"configurable": {"thread_id": session_id}}
        return self.checkpointer.get(config)


# Singleton
workflow_runner = ReportWorkflowRunner()
