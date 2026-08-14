"""Report Generation API routes — /api/v1/reports/*

Rewritten for RAG + LangGraph chapter-by-chapter streaming generation.
Replaces the old template fill-in-the-blanks workflow.
"""

import json
import sys
import os
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Optional, List
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.knowledge_db import get_knowledge_db
from app.database.history_db import get_history_db
from app.schemas.report import (
    StartGenerationRequest, StartGenerationResponse, ChatRequest,
    GenerationStatusResponse, ActionResponse,
    WorkflowStartRequest, WorkflowResumeRequest, WorkflowStatusData,
)
from app.schemas.knowledge import ApiResponse
from app.services.report_service import report_service
from app.services.file_service import file_service
from app.services.workflow_service import workflow_service
from app.config import settings
from app.utils.sse import (
    sse_event, sse_message, sse_progress, sse_status,
    sse_data_filled, sse_error, sse_complete,
    sse_chapter_start, sse_chapter_stream, sse_chapter_complete,
    sse_thinking, sse_thinking_stream, sse_phase_change,
    sse_placeholder_filled, sse_collecting_question,
    sse_step_transition, sse_agent_status, sse_step_progress_sync,
)

router = APIRouter(prefix="/api/v1/reports", tags=["报告生成"])


# ═══════════════════════════════════════════════════════════════════════════════
# 上传资料引导配置（意图分析入口用）
# ═══════════════════════════════════════════════════════════════════════════════
REPORT_MATERIAL_GUIDE = {
    "stability": {
        "title": "社会稳定风险评估报告",
        "description": "用于土地征收、重大决策等事项的社会稳定风险评估",
        "required": [
            {"name": "征收土地预公告", "format": "PDF（盖章版）", "note": "含项目名称、位置、面积、用途、文号"},
            {"name": "勘测定界报告", "format": "PDF", "note": "含地块面积、界址点、地类面积"},
            {"name": "群众调查问卷/座谈会记录", "format": "PDF 或图片", "note": "含支持率、反对率、群众诉求"},
            {"name": "专家评审意见", "format": "图片", "note": "专家签字评审表、签到表"},
        ],
        "optional": [
            {"name": "公示照片", "format": "图片", "note": "公告栏张贴照片"},
            {"name": "现场照片", "format": "图片", "note": "地块现状、现场勘查照片"},
            {"name": "补偿标准文件", "format": "PDF", "note": "征地区片综合地价标准"},
        ],
    },
    "bidding": {
        "title": "投标文件",
        "description": "用于招投标项目的技术文件编制",
        "required": [
            {"name": "招标文件", "format": "PDF", "note": "含技术要求、评分标准、资质要求"},
            {"name": "公司资质证书", "format": "图片/PDF", "note": "营业执照、资质证书、人员证书"},
            {"name": "项目背景资料", "format": "PDF", "note": "项目需求、技术方案"},
        ],
        "optional": [
            {"name": "业绩证明材料", "format": "PDF/图片", "note": "类似项目业绩"},
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# Session Management (compatible with existing report_service)
# ═══════════════════════════════════════════════════════════════════════════════


def _ensure_graph():
    """LangGraph removed — using ChapterOrchestrator instead."""
    return None


def _regex_extract_data(text: str) -> dict:
    """Extract structured project data from Chinese natural language using regex.

    Fallback when LLM intent extraction doesn't parse data fields.
    """
    import re as _re
    extracted = {}

    patterns = [
        # (key, [aliases], regex)
        ("project_name", ["项目名称", "决策名称", "报告标题"], None),
        ("location", ["项目位置", "位置", "坐落", "地址", "位于", "地理位置"],
         _re.compile(r'(?:项目位置|位置|坐落|地址|位于|地理位置)[：:]\s*(\S{3,60})')),
        ("area_m2", ["面积", "平方米", "㎡", "用地面积", "征收面积"],
         _re.compile(r'(?:面积|用地面积|征收面积)[^\d]*(\d[\d,.]*)\s*(?:平方米|㎡|m2)')),
        ("area_mu", ["亩数", "亩"],
         _re.compile(r'(\d+\.?\d*)\s*亩')),
        ("land_use", ["用途", "地类", "用地性质", "土地用途"],
         _re.compile(r'(?:用途|地类|用地性质|土地用途)[：:]\s*(\S{2,20})')),
        ("org_name", ["责任单位", "决策单位", "征收主体", "稳评责任单位", "征收单位"],
         _re.compile(r'(?:责任单位|决策单位|征收主体|稳评责任单位|征收单位)[：:]\s*(\S{4,40})')),
        ("implement_unit", ["实施单位", "稳评单位", "评估单位"],
         _re.compile(r'(?:实施单位|稳评单位|评估单位)[：:]\s*(\S{4,40})')),
        ("household_count", ["户数", "农户", "涉及户"],
         _re.compile(r'(?:涉及|约|共)?\s*(\d+)\s*(?:户|农户)')),
        ("funding", ["资金", "投资", "补偿金额", "总金额"],
         _re.compile(r'(?:资金|投资|补偿金额|总金额)[^\d]*(\d[\d,.]*)\s*(?:万元|元)')),
        ("doc_reference", ["文号", "批文号", "公告号"],
         _re.compile(r'([一-鿿\w〔〕\[\]【】（）\(\)\d]{3,30}(?:告|批|函|字|号|文)[一-鿿\w〔〕\[\]【】（）\(\)\d]*)')),
    ]

    for key, aliases, regex in patterns:
        if regex:
            m = regex.search(text)
            if m:
                extracted[key] = m.group(1).strip()
        else:
            # For keys without regex, try alias matching
            for alias in aliases:
                if alias in text:
                    parts = text.split("：", 1) if "：" in text else text.split(":", 1)
                    if len(parts) == 2:
                        # Find the alias position
                        idx = parts[0].find(alias)
                        if idx >= 0:
                            extracted[key] = parts[1].strip()
                            break

    return extracted


def _extract_attachment_text(state: dict, att: str) -> bool:
    """Extract text from any supported attachment (PDF/DOCX/DOC/TXT).

    Returns True if text was successfully extracted.
    """
    from app.services.file_service import file_service
    try:
        att_lower = att.lower()
        text = None
        if att_lower.endswith(".pdf"):
            text = file_service.extract_pdf_text(att)
        elif att_lower.endswith((".docx", ".doc")):
            text = file_service.extract_docx_text(att)
        elif att_lower.endswith((".txt", ".md")):
            text = file_service.read_text_file(att)

        if text and len(text.strip()) > 50:
            text_cache = state.setdefault("_pdf_texts", {})
            text_cache[att] = text[:30000]
            return True
        elif att_lower.endswith(".pdf"):
            state.setdefault("_pdf_need_ocr", []).append(att)
    except Exception:
        if att.lower().endswith(".pdf"):
            state.setdefault("_pdf_need_ocr", []).append(att)
    return False


async def _llm_extract_fields(pdf_text: str, llm) -> dict:
    """Use LLM to extract required project fields from document text.

    Regex can only match exact patterns; LLM understands context like
    '洪拟征地公告' → project_name, org_name, location, area_mu, land_use.
    """
    # Truncate to avoid token limits — keep first 8000 chars per document section
    sections = pdf_text.split("\n[")
    trimmed = ""
    for sec in sections:
        if len(trimmed) > 12000:
            break
        trimmed += sec[:4000] + "\n"

    prompt = f"""从以下征地项目资料中提取关键信息。返回纯JSON。

## 文档内容
{trimmed[:12000]}

## 需要提取的字段
- project_name: 项目名称（含文号，如"洪拟征告〔2026〕7号"）
- org_name: 决策主体/征收责任单位（政府全称）
- location: 征收土地位置（完整到村组）
- area_mu: 征收面积（亩，纯数字）
- land_use: 土地用途/规划用途
- implement_unit: 稳评实施单位/第三方机构
- doc_reference: 公告文号
- compensation_standard: 征地补偿标准/区片综合地价
- household_count: 涉及农户数
- total_samples: 问卷调查样本数

## 规则
1. 如果找不到某字段，省略该key
2. area_mu只返回数字
3. location写完整到村组
4. 返回严格JSON，不要有其他文字

```json
{{"project_name": "...", "org_name": "...", ...}}
```"""

    import json as _json, re as _re2, logging as _llog
    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.1,
        )
        content = str(response) if response else ""
        _llog.getLogger(__name__).info(f"LLM extraction response: {len(content)} chars")
        # Extract JSON
        json_match = _re2.search(r'\{[^{}]*\}', content, _re2.DOTALL)
        if json_match:
            result = _json.loads(json_match.group())
            _llog.getLogger(__name__).info(f"LLM extracted fields: {list(result.keys())}")
            return result
        _llog.getLogger(__name__).warning(f"LLM extraction: no JSON in response: {content[:200]}")
    except Exception as e:
        _llog.getLogger(__name__).warning(f"LLM extraction call failed: {e}")
    return {}


async def _analyze_session_attachments(state: dict, attachments: list[str]) -> dict:
    """Analyze uploaded project materials and persist normalized session state."""
    from app.services.material_ingestion_service import material_ingestion_service

    existing_materials = state.setdefault("_project_materials", [])
    existing_paths = {item.get("source_path") for item in existing_materials if isinstance(item, dict)}
    # Skip files already ingested during upload
    new_paths = [att for att in attachments if isinstance(att, str) and att not in existing_paths]
    if new_paths:
        import logging as _logging
        _logging.getLogger(__name__).info(
            f"Analyzing {len(new_paths)} new files (skipping {len(attachments) - len(new_paths)} already parsed)")
        analyzed = await material_ingestion_service.ingest_many(
            new_paths,
            scope="session",
            domain=state.get("_domain", "stability"),
            metadata={"session_id": state.get("session_id", "")},
        )
        existing_materials.extend(analyzed)
        state["_project_materials"] = existing_materials

    summary = material_ingestion_service.summarize_analysis(existing_materials)
    state["_project_material_facts"] = summary.get("facts", {})
    state["_project_material_summary"] = summary

    # 🔴 Extract authoritative data from PDF OCR text (user-provided data ALWAYS wins)
    for item in existing_materials:
        txt = str(item.get('text_content', '') or '')
        if not txt:
            continue
        # Store for downstream use
        all_text = state.setdefault("_pdf_raw_text", "")
        state["_pdf_raw_text"] = all_text + "\n" + txt

        # Extract key fields with regex
        import re as _re_data
        extracts = {}
        # Area
        m = _re_data.search(r'(\d{5,7})\s*(?:平方米|㎡)', txt)
        if m: extracts['area_m2'] = m.group(1)
        m = _re_data.search(r'(\d+\.?\d*)\s*亩', txt)
        if m: extracts['area_mu'] = m.group(1)
        # Location
        m = _re_data.search(r'(?:朱坝|高良涧|黄码|淮高)\S{0,10}(?:街道|镇)\S{0,10}(?:社区|村)\S{0,10}(?:组|队)', txt)
        if m: extracts['location'] = m.group(0)
        # Document reference
        m = _re_data.search(r'洪拟征告\s*〔?\s*\d{4}\s*〕?\s*\d+\s*号', txt)
        if m: extracts['doc_reference'] = m.group(0)
        # Land use
        m = _re_data.search(r'(?:用途|用于)[：:]*\s*(\S{2,30})', txt)
        if m: extracts['land_use'] = m.group(1)
        # Write as LOCKED data (prefix prevents LLM override)
        filled = state.setdefault("filled_data", {})
        for k, v in extracts.items():
            if v and k not in filled:
                filled[k] = str(v)
                filled[f"_locked_{k}"] = True  # Mark as user-provided, not to be overridden
    state["_project_material_chunks"] = [
        {
            "source_path": item.get("source_path", ""),
            "source_type": item.get("source_type", "file"),
            "retrieval_text": item.get("retrieval_text", "")
    }
        for item in existing_materials
        if item.get("retrieval_text")
    ]
    state["_project_material_sources"] = [
        {
            "source_path": item.get("source_path", ""),
            "source_name": item.get("source_name", ""),
            "source_type": item.get("source_type", "file"),
            "status": item.get("status", "completed")
    }
        for item in existing_materials
    ]

    # 🔴 Propagate extracted facts to filled_data so downstream agents can use them
    facts = summary.get("facts", {})
    filled = state.setdefault("filled_data", {})
    for key, value in facts.items():
        if value and not key.startswith("_"):
            filled[key] = value

    # 🔴 Store extracted images for image categorization in assembler
    extracted_imgs = facts.get("_extracted_images", [])
    if extracted_imgs:
        filled["_extracted_images"] = extracted_imgs
        # Also add to uploaded_files so _get_session_images picks them up
        uploaded = state.setdefault("_uploaded_files", [])
        for img in extracted_imgs:
            if img not in uploaded:
                uploaded.append(img)

    # 🔴 Copy extracted tables for table_registry to use
    extracted_tables = facts.get("_extracted_tables", [])
    if extracted_tables:
        filled["_extracted_tables"] = extracted_tables

    # 🔴 Run deep material analysis — vision AI classification + OCR + data extraction
    try:
        from app.services.deep_material_analyzer import analyze_all_materials, apply_analysis_to_state
        import logging as _log2
        _log2.getLogger(__name__).info("Starting deep material analysis (vision AI + OCR)...")
        analysis = await analyze_all_materials(state, max_vision_images=15)
        apply_analysis_to_state(state, analysis)
        _log2.getLogger(__name__).info(
            f"Deep analysis complete: {len(analysis.get('classified_images', {}))} categories, "
            f"{len(analysis.get('filled_data_updates', {}))} data fields"
        )
    except Exception as e:
        import logging as _log3
        _log3.getLogger(__name__).warning(f"Deep analysis skipped (non-critical): {e}")

    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# Generation Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/analyze-intent", response_model=ApiResponse)
async def analyze_intent(request: dict):
    """意图分析入口：分析用户要生成什么报告，返回上传资料引导。

    用户输入需求（如"生成征地稳评报告"），系统判断领域（stability/bidding），
    返回该领域需要上传的资料清单 + 格式要求。
    """
    user_input = str(request.get("user_input", "") or "").strip()
    if not user_input:
        return ApiResponse(message="请输入需求描述", data={"needs_input": True})

    from app.agent.agents.intent_clarification_agent import IntentClarificationAgent
    agent = IntentClarificationAgent()
    try:
        intent = await agent.analyze_intent({}, user_input)
    except Exception:
        intent = {"primary_intent": "unknown", "confidence": 0}

    # 判断领域
    primary = intent.get("primary_intent", "")
    if primary == "bidding_generation" or any(kw in user_input for kw in ["招标", "投标", "评标", "中标"]):
        domain = "bidding"
    else:
        domain = "stability"

    guide = REPORT_MATERIAL_GUIDE.get(domain, {})
    return ApiResponse(
        message="意图分析完成",
        data={
            "user_input": user_input,
            "intent": intent,
            "domain": domain,
            "guide": guide,
        },
    )


@router.post("/reviews/expert-feedback", response_model=ApiResponse)
async def submit_expert_feedback(request: dict):
    """专家提交对报告的评估反馈（优化点/不足）。

    request: {domain, report_title, feedback: [{chapter_num, issue_type, issue_desc, suggestion, severity}]}
    """
    from app.services import skill_service
    domain = str(request.get("domain", "stability") or "stability")
    report_title = str(request.get("report_title", "") or "")
    session_id = str(request.get("session_id", "") or "")
    report_file_path = str(request.get("report_file_path", "") or "")
    feedback_list = request.get("feedback", []) or []

    recorded = 0
    for fb in feedback_list:
        if not isinstance(fb, dict) or not fb.get("issue_desc"):
            continue
        rid = await skill_service.record_expert_feedback(
            report_title=report_title,
            session_id=session_id,
            report_file_path=report_file_path,
            domain=domain,
            chapter_num=int(fb.get("chapter_num", 0) or 0),
            issue_type=str(fb.get("issue_type", "") or ""),
            issue_desc=str(fb.get("issue_desc", "") or ""),
            suggestion=str(fb.get("suggestion", "") or ""),
            severity=str(fb.get("severity", "warning") or "warning"),
        )
        if rid:
            recorded += 1

    # 🔴 后台自动蒸馏（专家无需关心蒸馏过程和结果）
    if recorded > 0:
        try:
            import asyncio
            _distill_tasks = getattr(submit_expert_feedback, "_distill_tasks", set())
            task = asyncio.create_task(skill_service.distill_skills(domain))
            _distill_tasks.add(task)
            task.add_done_callback(_distill_tasks.discard)
            submit_expert_feedback._distill_tasks = _distill_tasks
        except Exception:
            pass  # 蒸馏失败不影响专家提交

    return ApiResponse(message=f"已记录 {recorded} 条专家反馈", data={"recorded": recorded})


@router.post("/reviews/distill", response_model=ApiResponse)
async def distill_review_skills(request: dict):
    """把累积的专家反馈蒸馏成审核 skill（LLM 自动蒸馏）。"""
    from app.services import skill_service
    domain = str(request.get("domain", "stability") or "stability")
    result = await skill_service.distill_skills(domain)
    return ApiResponse(message="蒸馏完成", data=result)


@router.get("/reviews/skills", response_model=ApiResponse)
async def get_review_skills(domain: str = "stability"):
    """获取当前的审核 skill（供审核 agent 和生成使用）。"""
    from app.services import skill_service
    skills = await skill_service.get_active_skills(domain)
    return ApiResponse(message="审核 skill", data=skills)


@router.post("/generate/start", response_model=ApiResponse)
async def start_generation(
    request: StartGenerationRequest,
):
    """Initialize a new report generation session.

    Creates a session with project context and initializes the 10-chapter structure.
    No template needed — chapters are built-in per the professional standard.
    """
    from app.agent.state import create_initial_state

    # ---- Template: use built-in structure, not a fixed template file ----
    # Chapter agents generate content from knowledge base RAG + user data.
    # Templates in DB are only used as formatting/style reference.
    template_id = 0
    template_name = "内置10章标准结构（知识库综合生成）"
    template_path = ""
    templates = []  # Initialize before try block

    # Resolve and pin the report domain before any template/knowledge lookup.
    user_msg = (request.initial_message or "").strip()
    explicit_intent = (request.domain or request.intent or "").strip()
    try:
        from app.domains import detect_domain, get_domain
        domain_id = detect_domain(message=user_msg, explicit=explicit_intent or None)
        domain_cfg = get_domain(domain_id)
    except Exception:
        domain_id = "bidding" if explicit_intent == "bidding" else "stability"
        domain_cfg = None

    # Load template list from DB only for reference (available_templates)
    try:
        from app.database.knowledge_db import async_session as async_knowledge_session
        from sqlalchemy import select
        from app.models.knowledge import Template

        async with async_knowledge_session() as db:
            result = await db.execute(
                select(Template)
                .where(Template.is_active == True, Template.domain == domain_id)
                .order_by(Template.id.desc())
            )
            templates = list(result.scalars().all())
    except Exception:
        pass

    # Store available templates in state for region matching
    # 🔴 Only include .docx templates — exclude PDFs accidentally stored in templates/
    available_templates = [
        {"id": t.id, "name": t.name, "path": t.template_file_path or "", "domain": getattr(t, "domain", domain_id)}
        for t in templates
        if t.template_file_path and t.template_file_path.lower().endswith('.docx')
    ]

    session = report_service.create_session(
        template_id=template_id,
        template_name=template_name,
        template_path=template_path,
        initial_message=request.initial_message,
    )
    # Store report style preference
    session.state["_report_style"] = getattr(request, "style", None) or "jinhu"

    # Initialize state with new AgentState — report_title starts empty,
    # will be collected from user in first interaction
    state = create_initial_state(
        session_id=session.session_id,
        report_title="",  # Will be collected via first chat message
        project_context=request.initial_message or "",
    )

    # Set template info in state
    state["template_id"] = template_id
    state["template_name"] = template_name
    state["template_path"] = template_path
    state["_available_templates"] = available_templates  # For region-based matching
    state["workflow_mode"] = "step_wizard"
    state["use_master_agent"] = True
    state["_domain"] = domain_id
    state["_conversation_domain"] = domain_id
    state["domain"] = domain_id
    if domain_cfg:
        state["_domain_display_name"] = domain_cfg.display_name
        state["_domain_collection"] = domain_cfg.default_collection

    # ---- Template preserved sections (keep as-is, do not modify) ----
    state["preserved_sections"] = [
        {"name": "项目工作组人员及分工情况", "description": "项目团队人员信息（稳评负责人、调研工作、风险研判、数据汇总、资料档案）—— 保留模板原文不修改"},
        {"name": "公司营业执照", "description": "公司营业执照图片 —— 保留模板原文不修改"},
        {"name": "稳评平台备案", "description": "稳评平台备案信息 —— 保留模板原文不修改"},
        {"name": "人员证书", "description": "人员资质证书 —— 保留模板原文不修改"},
    ]

    # ---- Analyze template for location names and preserved content ----
    if template_path:
        try:
            from app.services.template_analyzer import template_analyzer
            analysis = template_analyzer.analyze(template_path)
            state["_template_locations"] = analysis.location_names
            state["_template_companies"] = analysis.company_names
        except Exception:
            pass

    # ---- Auto-fill fixed values that never change across reports ----
    state.setdefault("filled_data", {})
    
    state["_fixed_values"] = {
        
    }

    # Copy state fields to session
    for key, value in state.items():
        session.state[key] = value

    report_service.add_message(session, "user", request.initial_message or "")

    # Build response
    chapters_summary = {}
    for num, ch in session.state.get("chapters", {}).items():
        chapters_summary[str(num)] = {
            "number": num,
            "title": ch.get("title", ""),
            "status": ch.get("status", "pending")
    }

    # ---- First message — dynamic identity based on user intent ----
    _GENERIC_GEN_KEYWORDS = [
        "帮我生成", "帮我写", "帮我做", "编制报告", "编写报告",
    ]

    is_bidding = domain_id == "bidding"
    is_stability = domain_id == "stability"

    if is_bidding:
        first_message = "你好，我是投标文件编写助手，请上传招标文件或描述需求。"
    elif is_stability and (user_msg and not any(kw in user_msg for kw in _GENERIC_GEN_KEYWORDS)):
        first_message = "你好，有什么可以帮你的？"
    elif is_stability:
        first_message = "你好，有什么可以帮你的？"
    elif any(kw in user_msg for kw in _GENERIC_GEN_KEYWORDS):
        first_message = "好的，请上传资料或描述项目信息，我来帮您生成报告。"
    else:
        first_message = "你好，有什么可以帮你的？"

    report_service.add_message(session, "agent", first_message, message_type="text")

    return ApiResponse(
        message="会话已创建",
        data=StartGenerationResponse(
            session_id=session.session_id,
            status="setup",
            agent_message=first_message,
            domain=domain_id,
            template_name=template_name,
            next_placeholder={
                "key": "report_title",
                "display_name": "报告大标题（决策名称）",
                "expected_type": "text",
                "section_title": "封面与第1章",
                "description": "请输入报告的大标题，例如：金征预告〔2026〕3号（高铁枢纽北片区开发地块项目）。系统将自动派生决策名称（标题+决策）。"
    },
        ).model_dump(),
    )


@router.post("/generate/{session_id}/chat")
async def chat_with_agent(
    session_id: str,
    request: ChatRequest,
):
    """SSE streaming endpoint for chapter generation and user feedback.

    This is the main interaction endpoint. User messages can be:
    - Project information (initial setup)
    - Chapter feedback (approve/revise/skip)

    The response is an SSE stream with chapter content.
    """
    session = report_service.get_session(session_id)
    user_message = request.message
    attachments = getattr(request, 'attachments', None) or []

    # 🔴 Resolve/pin the report domain BEFORE MasterAgent runs, via the registry.
    # Explicit frontend intent wins; else keep whatever the session already has;
    # else detect from the message. Both keys are set for backward compatibility.
    explicit_intent = (getattr(request, 'domain', '') or getattr(request, 'intent', '') or '').strip()
    try:
        from app.domains import detect_domain
        resolved = detect_domain(
            message=user_message or "",
            explicit=explicit_intent or None,
            state=session.state,
        )
        session.state["_domain"] = resolved
        session.state["_conversation_domain"] = resolved
    except Exception:
        if explicit_intent in ("bidding", "stability"):
            session.state["_conversation_domain"] = explicit_intent
            session.state["_domain"] = explicit_intent

    async def event_generator():
        try:
            # Add user message to history
            report_service.add_message(session, "user", user_message, message_type="text")

            # Create stream queue for bridging LangGraph events to SSE
            stream_queue: asyncio.Queue = asyncio.Queue()
            session.state["_stream_queue"] = stream_queue

            phase = session.state.get("phase", "setup")

            # ═══════════════════════════════════════════════════════════════
            # Orchestrator Interaction: handle messages when orchestrator is running
            # ═══════════════════════════════════════════════════════════════
            orch_state = session.state.get("chapter_orchestrator_state", "")
            is_orch_mode = session.state.get("generation_mode") == "chapter_by_chapter"

            if is_orch_mode and orch_state and not session.state.get("user_action"):
                user_input = user_message.strip()

                # 🔴 Signal orchestrator via Event (no polling!)
                event = session.state.get("_action_event")
                signal = isinstance(event, __import__('asyncio').Event)

                if orch_state == "outline":
                    if user_input and any(kw in user_input for kw in [
                        "确认大纲", "开始生成", "确认", "可以", "没问题", "同意", "好的", "行",
                    ]):
                        session.state["user_action"] = "approve"
                        if signal: event.set()
                        yield sse_thinking("✅ 已确认，开始逐章生成...")
                    elif user_input:
                        if any(kw in user_input for kw in ["修改大纲", "调整大纲", "重写大纲"]):
                            session.state["outline_status"] = "needs_revision"
                            session.state["outline_feedback"] = user_input
                            yield sse_thinking("已收到大纲修改意见。")
                        else:
                            # 🔴 User provided data — parse into filled_data so it's used
                            import re as _re_parse
                            cn_aliases = {
                                "org_name": ["责任单位", "决策单位", "征收主体", "征收单位"],
                                "implement_unit": ["实施单位", "稳评单位", "评估单位"],
                                "project_name": ["项目名称", "决策名称", "报告标题"],
                                "location": ["位置", "坐落", "地址", "位于", "地理位置"],
                                "area_m2": ["面积", "平方米", "㎡", "用地面积", "征收面积"],
                                "area_mu": ["亩数", "亩"],
                                "land_use": ["用途", "地类", "用地性质", "土地用途"],
                                "funding": ["资金", "投资", "补偿金额", "总金额"],
                                "total_samples": ["人数", "样本", "调查人数", "问卷人数"],
                                "support_rate": ["支持率", "同意率", "赞成率"],
                                "household_count": ["户数", "农户", "涉及户"],
                                "doc_reference": ["文号", "批文号", "公告号"],
                                "compensation_standard": ["补偿标准", "补偿单价"]
    }
                            filled = session.state.setdefault("filled_data", {})
                            parsed = 0
                            for line in user_input.split("\n"):
                                for key, aliases in cn_aliases.items():
                                    for alias in aliases:
                                        if alias in line:
                                            parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                                            if len(parts) == 2 and parts[1].strip():
                                                filled[key] = parts[1].strip()
                                                parsed += 1
                                            break
                            yield sse_thinking(
                                f"📝 已解析 {parsed} 条数据，正在更新分析结果..."
                            )
                            session.state["user_action"] = "approve"  # Auto-approve after processing data
                            if signal: event.set()
                    session.state.pop("_stream_queue", None)
                    return

                elif orch_state in ("generating", "reviewing"):
                    if user_input:
                        session.state["user_action"] = "revise"
                        session.state["chapter_feedback"] = user_input
                        if signal: event.set()
                        yield sse_thinking(
                            f"📝 收到第{session.state.get('current_chapter', '?')}章反馈"
                        )
                    session.state.pop("_stream_queue", None)
                    return

            # ---- Phase: Setup → Collect Title, then Smart Fill Template ----
            if phase == "setup":
                # Check if this is the report title being provided
                current_title = session.state.get("report_title", "")

                # In MasterAgent mode: only auto-parse title if message looks like one
                use_master = session.state.get("use_master_agent", False)

                # 🔴 Fixed title detection:
                # A real title looks like: "XX告〔2026〕X号（XX项目）土地征收决策社会稳定风险评估报告"
                # NOT like: "帮我生成一份稳评报告" or "淮安市洪泽区，项目是..."
                import re as _re_title
                _is_request = any(kw in user_message for kw in [
                    "帮我", "请帮我", "生成", "写一份", "做一份", "你好", "您好",
                    "我要生成", "我要做", "我要写", "帮我做",
                ])
                _has_doc_id = bool(_re_title.search(
                    r'[^\s]{2,8}告\s*〔?\d{4}〕?\s*\d+\s*号', user_message
                ))
                _is_real_title = bool(_re_title.search(
                    r'(?:土地征收|社会稳定|风险|评估报告|稳评报告)', user_message
                )) and not _is_request

                # Only treat as title when: has a doc ID, or looks like an actual report title
                _is_likely_title = _has_doc_id or (_is_real_title and len(user_message) < 80)

                if (not current_title or current_title.strip() == "" or current_title == "社会稳定风险评估报告") and _is_likely_title:
                    # User is providing the report title — extract cleanly
                    title = user_message.strip()
                    if "\n" in title:
                        title = title.split("\n")[0].strip()
                    for prefix in ["项目名称：", "项目名称:", "报告标题：", "报告标题:", "报告名称：", "报告名称:"]:
                        if title.startswith(prefix):
                            title = title[len(prefix):].strip()
                            break

                    session.state["report_title"] = title
                    decision_name = f"{title}决策"
                    session.state["project_context"] = (
                        f"报告标题：{title}\n"
                        f"决策名称：{decision_name}\n"
                        f"附加信息：{user_message[len(title):].strip() if len(user_message) > len(title) else ''}"
                    )
                elif not _is_likely_title and use_master:
                    # Not a title — check if user is asking for generation directly
                    _gen_kw = [
                        "生成报告", "开始生成", "逐章生成", "开始写报告",
                        "需要生成", "想要生成", "帮我生成报告", "帮我写报告",
                        "写报告", "做报告", "生成稳评", "编写报告", "编制报告",
                    ]
                    _is_gen = any(kw in user_message for kw in _gen_kw)
                    _has_files = bool(attachments)

                    # Process any attached files (extract text for later use)
                    if _has_files:
                        yield sse_thinking("📎 正在处理上传的文件...")
                        summary = await _analyze_session_attachments(session.state, [att for att in attachments or [] if isinstance(att, str)])
                        step_key = f"step_{session.state.get('current_step', 1)}"
                        structured = session.state.setdefault("structured_data", {})
                        step_data = structured.get(step_key, {})
                        step_data["attachments"] = attachments
                        step_data["images"] = attachments
                        step_data["material_summary"] = summary
                        structured[step_key] = step_data
                        session.state["structured_data"] = structured
                        yield sse_phase_change("analysis", summary)
                        yield sse_event("material_analysis_complete", summary)
                        missing = summary.get("missing_fields") or []
                        if missing:
                            yield sse_thinking(
                                f"✅ 资料解析完成，仍建议补充：{'、'.join(missing)}。"
                            )
                        else:
                            yield sse_thinking("✅ 资料解析完成，请继续补充信息或回复「生成报告」开始逐章编写。")

                    # Let MasterAgent handle the conversation
                    from app.agent.agents.master import _add_to_history
                    _add_to_history(session.state, "user", user_message.strip())
                    session.state["_latest_user_input"] = user_message.strip()

                    if session.state.get("_master_agent_stage", 0) == 0:
                        # 🔴 Only set stability stage if NOT a bidding request
                        _is_bidding = any(kw in user_message for kw in ["招标", "投标", "评标", "中标", "标书"])
                        if not _is_bidding:
                            _REPORT_TRIGGERS = [
                                "稳评", "社会稳定风险", "风险评估报告", "生成报告",
                                "帮我生成", "帮我写报告", "编制报告", "编写报告", "征地稳评",
                                "稳定性报告", "我要生成", "社会稳定性",
                            ]
                            if any(t in user_message for t in _REPORT_TRIGGERS):
                                session.state["_master_agent_stage"] = 1

                    async for evt in workflow_service.run_master_agent(
                        session, user_input=user_message.strip()
                    ):
                        yield evt

                    if session.state.get("phase") == "setup":
                        session.state["phase"] = "collecting"
                        session.state["status"] = "collecting"

                    session.state.pop("_stream_queue", None)
                    return
                else:
                    # Title already set or no title indicators — treat as additional project context
                    extra = user_message.strip()
                    if extra and current_title:
                        existing_ctx = session.state.get("project_context", "")
                        session.state["project_context"] = f"{existing_ctx}\n附加项目信息：{extra}"
                        # Transition to collecting if MasterAgent mode
                        if use_master:
                            session.state["phase"] = "collecting"
                            session.state["status"] = "collecting"
                            yield sse_phase_change("collecting")
                            from app.agent.agents.master import _add_to_history
                            _add_to_history(session.state, "user", user_message.strip())
                            session.state["_latest_user_input"] = user_message.strip()
                            if session.state.get("_master_agent_stage", 0) == 0:
                                session.state["_master_agent_stage"] = 1
                            async for evt in workflow_service.run_master_agent(
                                session, user_input=user_message.strip()
                            ):
                                yield evt
                            session.state.pop("_stream_queue", None)
                            return

                title = session.state.get("report_title", user_message.strip())
                decision_name = f"{title}决策"

                workflow_mode = session.state.get("workflow_mode", "")

                # ── Step Wizard mode: skip run_collect_setup, go straight to collecting ──
                if workflow_mode == "step_wizard":
                    yield sse_thinking("🔍 正在解析报告标题...")
                    yield sse_thinking_stream(f"标题：「{title}」")
                    yield sse_thinking_stream(f"自动派生决策名称：「{decision_name}」")

                    session.state["phase"] = "collecting"
                    session.state["status"] = "collecting"
                    yield sse_phase_change("collecting")

                    # ---- Master Agent mode: let DeepSeek-R1 handle the conversation ----
                    if session.state.get("use_master_agent"):
                        from app.agent.agents.master import _add_to_history
                        _add_to_history(session.state, "user", user_message.strip())

                        # 🔴 Only set stage to 1 if not already past stage 1
                        if session.state.get("_master_agent_stage", 0) < 1:
                            session.state["_master_agent_stage"] = 1

                        # Sync step progress so sidebar updates immediately
                        workflow_service._sync_step_progress(session.state)
                        step_statuses = session.state.get("step_statuses", {})
                        from app.agent.agents.master import MasterAgent
                        all_phs = session.state.get("template_placeholders", [])
                        user_questions = MasterAgent._filter_user_questions(all_phs)
                        yield sse_step_progress_sync(
                            step_statuses=step_statuses,
                            current_step=session.state.get("current_step", 1),
                            placeholders=user_questions,
                        )

                        yield sse_agent_status(
                            agent="MasterAgent", status="idle", message="专家已就绪",
                        )
                        for agent_name in ("DataCollector", "SurveyAnalyzer", "RationalityAgent", "RiskScorer"):
                            yield sse_agent_status(agent=agent_name, status="idle", message="等待调度")

                        # Let DeepSeek-R1 generate the next question (not a hardcoded template)
                        async for evt in workflow_service.run_master_agent(
                            session, user_input=user_message.strip()
                        ):
                            yield evt
                    else:
                        yield sse_step_transition(step=1, total=12, label="决策名称与责任单位", needs_review=False)
                        yield sse_message(
                            "## 📋 步骤 1/12：决策名称与责任单位\n\n"
                            f"报告标题已确认为「**{title}**」。\n\n"
                            "请继续填写以下信息：\n"
                            "- 稳评责任单位名称\n"
                            "- 实施单位名称\n"
                            "- 其他基本信息\n\n"
                            "请在下方输入框中填写，然后点击 **发送** 继续。",
                            message_type="step_guidance",
                        )
                    session.state.pop("_stream_queue", None)
                    return

                # ── Regular mode: full collect_setup flow ──
                session.state["phase"] = "collecting"
                session.state["status"] = "collecting"
                yield sse_phase_change("collecting")

                # Delegate to WorkflowService for the full setup flow
                async for evt in workflow_service.run_collect_setup(
                    session, title, decision_name,
                ):
                    yield evt

                session.state.pop("_stream_queue", None)
                return

            # ---- Phase: Collecting → Section-by-section Q&A ----
            elif phase == "collecting":
                user_input = user_message.strip()
                current_step = getattr(request, 'current_step', None)

                yield sse_thinking("🔍 正在处理您的输入...")

                # ---- Chapter Review Mode: user is reviewing a generated chapter ----
                # The orchestrator's _wait_for_user_action() polls state["user_action"].
                # When user sends text during review, treat it as a revision request.
                if session.state.get("generation_mode") == "chapter_by_chapter" and \
                   session.state.get("chapter_orchestrator_state") == "generating":
                    if user_input:
                        # Treat any text as revision feedback for the current chapter
                        session.state["user_action"] = "revise"
                        session.state["chapter_feedback"] = user_input
                        yield sse_thinking("📝 收到修改意见，正在重新生成章节...")
                        yield sse_phase_change("chapter_generation", {
                            "message": f"根据反馈重新生成第{session.state.get('current_chapter', '?')}章..."
    })
                        session.state.pop("_stream_queue", None)
                        return
                    else:
                        # No text, just attachments — process files as data supplement
                        if attachments:
                            yield sse_thinking("📎 正在处理上传的补充材料...")
                            await _analyze_session_attachments(session.state, [att for att in attachments if isinstance(att, str)])
                            yield sse_thinking("✅ 补充材料已处理，继续等待章节确认...")
                        else:
                            yield sse_thinking("📎 收到消息，继续等待章节确认...")
                        session.state.pop("_stream_queue", None)
                        return

                # ---- Master Agent mode: route all messages through LLM-powered MasterAgent ----
                if session.state.get("use_master_agent"):
                    # 🔴 Bidding domain check: NEVER use stability fast-path for bidding
                    _is_bidding_domain = (
                        session.state.get("_conversation_domain") == "bidding"
                        or bool(session.state.get("_bidding_data"))
                        or any(kw in user_input for kw in ["招标", "投标", "评标", "中标", "标书"])
                    )

                    # 🔴 Fast-path: only for stability assessment generation requests
                    # Bidding requests always go through MasterAgent for proper intent routing
                    if not _is_bidding_domain:
                        _gen_keywords = [
                            "生成报告", "开始生成", "逐章生成", "开始写报告", "开始逐章生成",
                            "需要生成", "想要生成", "帮我生成报告", "帮我写报告", "帮我做报告",
                            "写报告", "做报告", "生成稳评", "编写报告", "编制报告",
                            "生成一份", "写一份", "做一份",
                        ]
                        _is_gen_request = any(kw in user_input for kw in _gen_keywords)

                        if _is_gen_request:
                            session.state["generation_mode"] = "chapter_by_chapter"

                            # Always re-process ALL known file paths (from current msg + history)
                            all_attachments = [att for att in (attachments or []) if isinstance(att, str)]
                            # Also pull from _uploaded_files (files uploaded this session)
                            for u in session.state.get("_uploaded_files", []):
                                path = u.get("path", u) if isinstance(u, dict) else u
                                if isinstance(path, str) and path not in all_attachments:
                                    all_attachments.append(path)

                            if all_attachments:
                                yield sse_thinking(f"📎 已收到 {len(all_attachments)} 个文件，点击「开始生成报告」后将自动分析")
                                summary = {}
                            else:
                                # Check disk for any files that exist but aren't tracked
                                from app.services.file_service import file_service
                                from pathlib import Path
                                img_dir = Path(file_service._get_storage_path(file_service.IMAGES, "")) if hasattr(file_service, '_get_storage_path') else None
                                summary = {}
                                step_key = f"step_{session.state.get('current_step', 1)}"
                                structured = session.state.setdefault("structured_data", {})
                                step_data = structured.get(step_key, {})
                                step_data["attachments"] = attachments
                                step_data["images"] = attachments
                                step_data["material_summary"] = summary
                                structured[step_key] = step_data
                                session.state["structured_data"] = structured
                                yield sse_phase_change("analysis", summary)
                                yield sse_event("material_analysis_complete", summary)

                            # 🔴 Regex fallback: extract data from all previous user messages
                            import re as _re
                            filled = session.state.setdefault("filled_data", {})
                            all_user_text = " ".join(
                                m.get("content", "") for m in session.state.get("messages", [])
                                if m.get("role") == "user"
                            )
                            if all_user_text:
                                regex_data = _regex_extract_data(all_user_text)
                                for k, v in regex_data.items():
                                    if v and not filled.get(k):
                                        filled[k] = v
                            # Also extract from current message
                            current_regex = _regex_extract_data(user_input)
                            for k, v in current_regex.items():
                                if v and not filled.get(k):
                                    filled[k] = v

                            real_filled = {k: v for k, v in session.state.get("filled_data", {}).items()
                                          if not k.startswith("_")}
                            material_summary = session.state.get("_project_material_summary", {})
                            materials = session.state.get("_project_materials", [])
                            uploaded = session.state.get("_uploaded_files", [])
                            has_real_data = (
                                len(real_filled) >= 1
                                or bool(session.state.get("_pdf_texts"))
                                or bool((material_summary.get("facts") or {}))
                                or material_summary.get("completed_files", 0) > 0
                                or len(materials) > 0
                                or len(uploaded) > 0
                                or len(attachments) > 0  # Files sent with this message
                            )

                            if has_real_data:
                                missing = material_summary.get("missing_fields") or []
                                if missing:
                                    yield sse_thinking(
                                        f"📋 已完成资料分析，但建议先补充：{'、'.join(missing)}。如需直接生成，也可以继续。"
                                    )
                                yield sse_thinking("🚀 启动逐章生成流程...")
                                async for evt in workflow_service.run_chapter_by_chapter(
                                    session, start_chapter=session.state.get("current_chapter", 1),
                                ):
                                    yield evt
                                session.state.pop("_stream_queue", None)
                                return
                            else:
                                # Use LLM to generate a contextual response about what's missing
                                filled = {k: v for k, v in session.state.get("filled_data", {}).items()
                                         if not k.startswith("_") and v}
                                have_summary = "\n".join(f"- {k}: {v}" for k, v in list(filled.items())[:5]) if filled else "（暂无）"
                                try:
                                    from app.services.llm_service import llm_service
                                    llm_response = await llm_service.chat(
                                        messages=[{"role": "user", "content": (
                                            f"你是稳评报告专家。用户提供了以下信息但数据不足，请用2-3句话友好地告诉用户"
                                            f"还需要提供什么（征收文号、位置、面积、户数等），并建议上传勘测定界报告PDF。\n"
                                            f"已有数据：\n{have_summary}\n"
                                            f"直接输出回复内容，不要加前缀。"
                                        )}],
                                        max_tokens=300, temperature=0.7,
                                    )
                                    msg = llm_response.get("content", "") if isinstance(llm_response, dict) else str(llm_response)
                                    msg = msg.strip().strip('"').strip("'")
                                except Exception:
                                    msg = (
                                        "我已经收到您的信息。目前还需要以下关键数据：\n"
                                        "1. 📑 征收文号\n2. 📍 项目位置\n3. 📐 土地面积\n\n"
                                        "💡 建议直接上传勘测定界报告PDF，我会自动提取。"
                                    )
                                yield sse_message(msg)
                                session.state.pop("_stream_queue", None)
                                return

                    # 🔴 All messages (including bidding) go through MasterAgent for proper routing
                    async for evt in workflow_service.run_master_agent(
                        session, user_input, attachments,
                        folder_structure=request.folder_structure,
                    ):
                        yield evt
                    session.state.pop("_stream_queue", None)
                    return

                # ---- Step Wizard mode: if current_step is provided, run wizard step ----
                if current_step and 1 <= current_step <= 12:
                    async for evt in workflow_service.run_wizard_step(
                        session, current_step, user_input, attachments,
                    ):
                        yield evt
                    session.state.pop("_stream_queue", None)
                    return
                if user_input.startswith("__section_submit__"):
                    import json as _json
                    try:
                        payload = user_input[len("__section_submit__"):]
                        answers = _json.loads(payload)
                    except Exception:
                        answers = {}
                    async for evt in workflow_service.run_collect_section_submit(
                        session, answers,
                    ):
                        yield evt
                    session.state.pop("_stream_queue", None)
                    return

                # ---- Section skip: __section_skip__ ----
                if user_input == "__section_skip__":
                    async for evt in workflow_service.run_collect_section_skip(
                        session,
                    ):
                        yield evt
                    session.state.pop("_stream_queue", None)
                    return

                # Check for wizard mode: "步骤N" or "step N"
                wizard_match = None
                import re as _re
                _wm = _re.match(r'步骤\s*(\d+)|step\s*(\d+)', user_input, _re.IGNORECASE)
                if _wm:
                    wizard_match = int(_wm.group(1) or _wm.group(2))

                if wizard_match:
                    # Run specific wizard step
                    async for evt in workflow_service.run_wizard_step(
                        session, wizard_match, user_input, attachments,
                    ):
                        yield evt
                else:
                    # Delegate to WorkflowService for collecting step
                    async for evt in workflow_service.run_collect_step(
                        session, user_input, attachments,
                    ):
                        yield evt

                session.state.pop("_stream_queue", None)
                return

            # ---- Phase: Reviewing/Generating → Redirect to ChapterOrchestrator ----
            elif phase in ("reviewing", "generating"):
                yield sse_thinking("正在通过Agent逐章生成流程处理...")
                async for evt in workflow_service.run_chapter_by_chapter(
                    session, start_chapter=session.state.get("current_chapter", 1),
                ):
                    yield evt

            # Clean up stream queue
            session.state.pop("_stream_queue", None)
            session.state["status"] = session.state.get("phase", "reviewing")

            # Final yield if no chapter was sent
            yield sse_status(session.state.get("status", "reviewing"))

        except asyncio.CancelledError:
            # Client disconnected from SSE stream — clean up running tasks
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"SSE client disconnected for session {session_id}, cleaning up...")

            # Cancel running orchestrator
            state = session.state
            orchestrator = state.pop("_orchestrator", None)
            if orchestrator and hasattr(orchestrator, 'cancel'):
                orchestrator.cancel()
            orch_task = state.pop("_orchestrator_task", None)
            if orch_task and not orch_task.done():
                orch_task.cancel()
            state["_workflow_running"] = False
            state["_analysis_running"] = False
            state.pop("_stream_queue", None)

            # Signal any waiting events
            action_event = state.get("_action_event")
            if action_event and hasattr(action_event, 'set'):
                action_event.set()

            # Cancel all tracked background tasks
            for bg_task in state.pop("_bg_tasks", []):
                if not bg_task.done():
                    bg_task.cancel()

            # Clean up uploaded files
            uploaded = state.pop("_uploaded_files", [])
            for item in uploaded:
                fpath = item.get("path", item) if isinstance(item, dict) else item
                try:
                    file_service.delete_file(fpath)
                except Exception:
                    pass
            if uploaded:
                logger.info(f"Cleaned up {len(uploaded)} files after SSE disconnect")

            return  # Don't yield after disconnect — client is gone

        except Exception as e:
            session.state.pop("_stream_queue", None)
            import logging, traceback
            logger = logging.getLogger(__name__)
            logger.error(f"Chat error: {type(e).__name__}: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")

            # Clean up orphaned uploaded files on error
            uploaded = session.state.pop("_uploaded_files", [])
            for item in uploaded:
                fpath = item.get("path", item) if isinstance(item, dict) else item
                try:
                    file_service.delete_file(fpath)
                except Exception:
                    pass
            if uploaded:
                logger.info(f"Cleaned up {len(uploaded)} uploaded files after error")

            yield sse_message(
                f"抱歉，处理您的消息时遇到了问题（{type(e).__name__}: {str(e)[:150]}）。请重新发送。\n\n"
                "💡 如果问题持续，可以尝试换一种方式描述您的需求。",
                message_type="error",
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
    },
    )


async def _drain_queue(queue: asyncio.Queue):
    """Async generator that yields all items from an asyncio.Queue as SSE event strings."""
    while True:
        try:
            item = queue.get_nowait()
            event_type = item.get("event", "message")
            data = item.get("data", {})
            yield sse_event(event_type, data)
            queue.task_done()
        except asyncio.QueueEmpty:
            break


# ═══════════════════════════════════════════════════════════════════════════════
# Status & Management Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/generate/{session_id}/status", response_model=ApiResponse)
async def get_generation_status(session_id: str):
    """Get current generation status with chapter progress."""
    session = report_service.get_session(session_id)
    state = session.state

    chapters_summary = {}
    for num, ch in state.get("chapters", {}).items():
        chapters_summary[str(num)] = {
            "number": num,
            "title": ch.get("title", ""),
            "status": ch.get("status", "pending"),
            "char_count": len(ch.get("markdown", "")),
            "table_count": len(ch.get("tables", [])),
            "revision_count": len(ch.get("revision_history", []))
    }

    # Check subprocess progress file
    import json as _json
    from pathlib import Path as _Path
    result_file = _Path(f"/tmp/report_result_{session_id}.json")
    subprocess_progress = {}
    if result_file.exists():
        try:
            subprocess_progress = _json.loads(result_file.read_text())
        except Exception:
            pass

    # Merge subprocess progress with state
    progress = subprocess_progress if subprocess_progress else state.get("progress", {})
    status = subprocess_progress.get("status") or state.get("status", "unknown")

    total_chars = progress.get("total_chars", 0) or sum(
        len(ch.get("markdown", "")) if isinstance(ch, dict) else 0
        for ch in state.get("chapters", {}).values()
    )

    output_path = subprocess_progress.get("output_path") or state.get("output_path", "")
    # Verify file actually exists before returning download URL
    download_url = None
    if output_path:
        abs_path = settings.STORAGE_DIR / output_path
        if abs_path.exists():
            download_url = f"/api/v1/files/{output_path}"

    # If subprocess completed, sync state so frontend stops polling & shows download
    if status == "completed":
        if state.get("status") != "completed":
            state["status"] = "completed"
            state["phase"] = "complete"
            state["output_path"] = output_path
            # Persist to history DB
            try:
                report_id = await report_service.persist_report(session)
                if report_id:
                    import logging
                    logging.getLogger(__name__).info(f"Report persisted to history: id={report_id}")
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"History persist failed (non-critical): {e}")
        # 🔴 Ensure phase is always "complete" when done, even across restarts
        state["phase"] = "complete"

    return ApiResponse(
        data={
            "session_id": session_id,
            "status": status,
            "phase": state.get("phase", "idle"),
            "current_chapter": progress.get("current_chapter", state.get("current_chapter", 0)),
            "total_chapters": progress.get("total_chapters", 10),
            "message": progress.get("message", state.get("progress", {}).get("message", "")),
            "total_chars": total_chars,
            "chapters_done": sum(1 for ch in state.get("chapters", {}).values()
                                if isinstance(ch, dict) and ch.get("markdown")),
            "section_progress": list(chapters_summary.values()),
            "last_activity": session.last_activity.isoformat(),
            "report_file_path": output_path,
            "download_url": download_url
    },
    )


@router.get("/generate/{session_id}/chapters", response_model=ApiResponse)
async def get_chapters(session_id: str):
    """Get all chapter contents and statuses."""
    session = report_service.get_session(session_id)
    state = session.state

    chapters_data = {}
    for num, ch in state.get("chapters", {}).items():
        chapters_data[str(num)] = {
            "number": num,
            "title": ch.get("title", ""),
            "status": ch.get("status", "pending"),
            "markdown": ch.get("markdown", ""),
            "tables": ch.get("tables", []),
            "rag_sources": ch.get("rag_sources", []),
            "revision_count": len(ch.get("revision_history", []))
    }

    return ApiResponse(data={
        "chapters": chapters_data,
        "current_chapter": state.get("current_chapter", 1),
        "phase": state.get("phase", ""),
        "report_title": state.get("report_title", "")
    })


@router.get("/generate/{session_id}/conversation", response_model=ApiResponse)
async def get_conversation(session_id: str):
    """Get full conversation history."""
    session = report_service.get_session(session_id)
    messages = report_service.get_conversation(session)
    return ApiResponse(data=messages)


@router.post("/generate/{session_id}/skip", response_model=ApiResponse)
async def skip_current(session_id: str):
    """Skip the current chapter (mark as approved without changes)."""
    session = report_service.get_session(session_id)
    chapter_num = session.state.get("current_chapter", 1)

    if chapter_num in session.state.get("chapters", {}):
        session.state["chapters"][chapter_num]["status"] = "approved"

    # Move to next chapter
    next_chapter = chapter_num + 1
    if next_chapter > 10:
        session.state["user_action"] = "assemble"
        session.state["phase"] = "assembling"
        msg = "所有章节已完成，可以组装报告。请发送'生成报告'来生成最终文档。"
    else:
        session.state["current_chapter"] = next_chapter
        session.state["chapters"][next_chapter]["status"] = "generating"
        msg = f"已跳过第{chapter_num}章，准备生成第{next_chapter}章。"

    report_service.add_message(session, "system", msg, message_type="system_event")

    return ApiResponse(
        message="已跳过",
        data=ActionResponse(
            success=True,
            message=msg,
            session_id=session_id,
        ).model_dump(),
    )


@router.post("/generate/{session_id}/retry", response_model=ApiResponse)
async def retry_generation(session_id: str):
    """Retry the current chapter generation."""
    session = report_service.get_session(session_id)
    session.state["error_message"] = None
    chapter_num = session.state.get("current_chapter", 1)

    return ApiResponse(
        message=f"正在重试第{chapter_num}章...",
        data=ActionResponse(
            success=True,
            message=f"正在重试第{chapter_num}章",
            session_id=session_id,
        ).model_dump(),
    )


@router.post("/generate/{session_id}/cancel", response_model=ApiResponse)
async def cancel_generation(session_id: str):
    """Cancel the running generation — stops workflow, OCR, and cleans up."""
    import logging
    _log = logging.getLogger(__name__)

    session = report_service.get_session(session_id)
    state = session.state

    # 1. Set cancellation flags
    state["_cancel_requested"] = True
    state["_workflow_running"] = False
    state["_analysis_running"] = False
    state["status"] = "cancelled"
    state["phase"] = "idle"
    session.state = state

    # 2. Cancel the background task
    _bg_tasks = state.get("_bg_tasks", [])
    for task in _bg_tasks:
        if not task.done():
            task.cancel()
            _log.info(f"Cancelled background task for session {session_id}")
    state["_bg_tasks"] = []

    _log.info(f"Generation cancelled for session {session_id}")

    # 3. Cancel the background asyncio task if stored
    orch_task = state.get("_orchestrator_task")
    if orch_task and not orch_task.done():
        orch_task.cancel()
        _log.info(f"Orchestrator task cancelled for session {session_id}")

    # 3b. Cancel all tracked background tasks (analysis, ingestion, workflow)
    for bg_task in state.pop("_bg_tasks", []):
        if not bg_task.done():
            bg_task.cancel()
    _log.info(f"Background tasks cleaned up for session {session_id}")

    # 4. Signal any waiting events (unblock the orchestrator's wait loops)
    action_event = state.get("_action_event")
    if action_event and hasattr(action_event, 'set'):
        action_event.set()

    # 5. Clean up temp files
    file_service.cleanup_temp(session_id)

    uploaded = state.pop("_uploaded_files", [])
    for item in uploaded:
        fpath = item.get("path", item) if isinstance(item, dict) else item
        try:
            file_service.delete_file(fpath)
        except Exception:
            pass
    if uploaded:
        _log.info(f"Cleaned up {len(uploaded)} files for cancelled session {session_id}")

    # 6. Clean up RAG session collection
    try:
        from app.rag.vector_store import VectorStoreService
        vs = VectorStoreService()
        vs.delete_session_collection(session_id)
    except Exception:
        pass

    # 7. Do NOT remove session — keep it so the frontend can show cancellation status.
    # The frontend will call resetState() which starts a fresh session.

    return ApiResponse(
        message="生成已停止",
        data=ActionResponse(
            success=True,
            message="生成已取消，您可以重新开始或继续对话。",
            session_id=session_id,
        ).model_dump(),
    )


@router.post("/generate/{session_id}/validate", response_model=ApiResponse)
async def validate_report_format(session_id: str):
    """Validate the generated report's format against DB32/T4013-2021 standards."""
    session = report_service.get_session(session_id)
    output_path = session.state.get("output_path", "")

    if not output_path:
        raise HTTPException(status_code=400, detail="报告尚未生成，无法校验")

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from mcp_server.wps_validator import (
            validate_report_impl,
            check_fonts_impl,
            check_headings_impl,
        )
        import json

        abs_path = str(file_service.get_absolute_path(output_path))
        validation = json.loads(validate_report_impl(abs_path))
        fonts = json.loads(check_fonts_impl(abs_path))
        headings = json.loads(check_headings_impl(abs_path))

        return ApiResponse(
            message="校验完成",
            data={
                "validation": validation,
                "fonts": fonts,
                "headings": headings
    },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"校验失败: {str(e)}")


async def _bg_extract_pdf(session, rel_path: str, original_name: str):
    """Background task: extract text/tables from uploaded PDF."""
    import logging as _log
    _lg = _log.getLogger(__name__)
    try:
        from app.services.material_ingestion_service import material_ingestion_service
        analyzed = await material_ingestion_service.ingest_many(
            [rel_path], scope="session",
            domain=session.state.get("_domain", "stability"),
            metadata={"session_id": session.state.get("session_id", "")},
        )
        existing = session.state.setdefault("_project_materials", [])
        existing.extend(analyzed)
        session.state["_project_materials"] = existing
        summary = material_ingestion_service.summarize_analysis(existing)
        session.state["_project_material_facts"] = summary.get("facts", {})
        session.state["_project_material_summary"] = summary
        _lg.info(f"PDF extracted: '{original_name}' → {len(analyzed)} items, "
                 f"{summary.get('extracted_table_count', 0)} tables")
        # Notify via WebSocket if connected
        try:
            from app.services.ws_manager import ws_manager
            await ws_manager.send(session.state.get("session_id", ""), "upload_done", {
                "file": original_name,
                "tables": summary.get("extracted_table_count", 0)
    })
        except: pass

        # 🔴 Auto-index extracted text into knowledge base for RAG retrieval
        try:
            from app.rag.chunker import ChineseReportChunker
            from app.rag.embedder import EmbedderService
            from app.rag.vector_store import VectorStoreService
            chunker = ChineseReportChunker(); embedder = EmbedderService(); vs = VectorStoreService()
            coll = vs.get_or_create_global_collection()
            for item in analyzed:
                pages = item.get('pages', []) or []
                txt = '\n'.join(p.ocr_text or '' for p in pages) if pages else ''
                if not txt:
                    txt = str(item.get('retrieval_text', '') or '')
                if txt and len(txt) > 50:
                    chunks = chunker.chunk_markdown(txt)
                    ct = [c.text if hasattr(c,'text') else str(c) for c in chunks]
                    merged = []; buf = ""
                    for c in ct:
                        if len(buf)+len(c)<500: buf+=c+"\n\n"
                        else:
                            if buf.strip(): merged.append(buf.strip())
                            buf=c+"\n\n"
                    if buf.strip(): merged.append(buf.strip())
                    if not merged: merged=[txt[:1500]]
                    embs = await embedder.embed_texts(merged)
                    ids = [f"up_{original_name[:20]}_{i}" for i in range(len(merged))]
                    metas = [{"document_type":"user_upload","source_file":original_name,"chunk_index":i,"total_chunks":len(merged)} for i in range(len(merged))]
                    coll.add(ids=ids, documents=merged, embeddings=embs, metadatas=metas)
                    _lg.info(f"[KB] 自动索引: {original_name} → {len(merged)} chunks")
        except Exception as e2:
            _lg.warning(f"[KB] 自动索引失败: {e2}")
    except Exception as e:
        _lg.warning(f"PDF extraction failed for '{original_name}': {e}")


@router.post("/generate/{session_id}/upload", response_model=ApiResponse)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
):
    import logging as _ulog
    _ul = _ulog.getLogger(__name__)
    _ul.info(f"[UPLOAD] 收到文件: {file.filename} ({file.content_type}), session={session_id[:12]}")
    session = report_service.get_session(session_id)
    result = await file_service.save_attachment(file)
    _ul.info(f"[UPLOAD] 保存完成: {result['relative_path']} ({result.get('size_bytes',0)} bytes)")

    # Track file in session state
    uploaded_files = session.state.setdefault("_uploaded_files", [])
    uploaded_files.append({
        "path": result["relative_path"],
        "original_name": result.get("original_name", result["relative_path"].split("/")[-1]),
        "file_type": result.get("file_type", "image")
    })
    session.state = session.state
    report_service._save_sessions()
    _ul.info(f"[UPLOAD] 会话文件数: {len(uploaded_files)}")
    rel_path = result["relative_path"]
    file_type = result.get("file_type", "image")
    ext_lower = rel_path.lower()
    if ext_lower.endswith((".pdf", ".docx", ".doc", ".txt")):
        try:
            if ext_lower.endswith(".pdf"):
                # Fast text extraction only (pdfplumber) — OCR for scanned PDFs is in background
                txt = file_service.extract_pdf_text_fast(rel_path)
                if not txt:
                    _ul.warning(f"PDF无可提取文本（可能是扫描版）: {result['original_name']}")
            elif ext_lower.endswith((".docx", ".doc")):
                txt = file_service.extract_docx_text(rel_path)
            else:
                txt = file_service.read_text_file(rel_path)
            if txt and len(txt.strip()) > 50:
                text_cache = session.state.setdefault("_pdf_texts", {})
                text_cache[rel_path] = txt[:50000]
            # Background: heavy ingestion (tables, images, OCR for scanned PDFs)
            import asyncio as _bg_asyncio
            _pdf_task = _bg_asyncio.create_task(_bg_extract_pdf(session, rel_path, result['original_name']))
            _bg_tasks = session.state.setdefault("_bg_tasks", [])
            _bg_tasks.append(_pdf_task)
        except Exception as e:
            _ul.warning(f"Text extraction failed for {result['original_name']}: {e}")

    return ApiResponse(
        message="上传成功" + ("（已自动解析）" if file_type == "pdf" else ""),
        data={
            "file_path": result["relative_path"],
            "original_name": result["original_name"],
            "url": result["url"],
            "size_bytes": result["size_bytes"],
            "file_type": file_type
    },
    )


@router.get("/generate/{session_id}/messages", response_model=ApiResponse)
async def get_session_messages(session_id: str):
    """Get conversation messages for a session (restore on page refresh)."""
    session = report_service.get_session(session_id)
    messages = session.state.get("messages", [])
    return ApiResponse(data={"messages": messages, "session_id": session_id})


@router.get("/generate/{session_id}/materials", response_model=ApiResponse)
async def get_material_summary(session_id: str):
    """Get analysis summary for uploaded project materials."""
    session = report_service.get_session(session_id)
    return ApiResponse(data=session.state.get("_project_material_summary", {}))


@router.get("/generate/{session_id}/quality", response_model=ApiResponse)
async def get_quality_report(session_id: str):
    """Get T1-T4 quality validation report for the generated report.

    Returns the four-dimensional quality score (结构完整性/内容充实度/格式规范性/数据准确性)
    with detailed checks and recommendations.
    """
    session = report_service.get_session(session_id)
    state = session.state

    # Check if we have a cached quality report from generation
    cached = state.get("quality_report")
    if cached:
        return ApiResponse(
            message="质量报告已获取（来自生成阶段）",
            data=cached,
        )

    # If not cached, run validation on demand
    output_path = state.get("output_path", "")
    if not output_path:
        raise HTTPException(status_code=400, detail="报告尚未生成，无法获取质量报告")

    try:
        from app.integration.test import test_integration
        from app.services.file_service import file_service
        abs_output = file_service.get_absolute_path(output_path)

        test_report = await test_integration.run_full_validation(
            str(abs_output),
            template_path=state.get("template_path", ""),
            context={"filled_data": state.get("filled_data", {}),
                      "report_title": state.get("report_title", "")},
        )
        result = test_integration.to_api_response(test_report)
        state["quality_report"] = result

        return ApiResponse(
            message=f"质量评分: {test_report.overall_grade}级",
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"质量评分失败: {str(e)}")


@router.get("/generate/{session_id}/fields", response_model=ApiResponse)
async def get_template_fields(session_id: str):
    """Get classified template fields (A/B/C/D/E) from the product skill analysis."""
    session = report_service.get_session(session_id)
    state = session.state

    template_path = state.get("template_path", "")
    if not template_path:
        raise HTTPException(status_code=400, detail="未关联模板")

    try:
        from app.integration.product import product_integration
        from app.services.file_service import file_service
        abs_path = str(file_service.get_absolute_path(template_path))

        result = product_integration.analyze_template(abs_path)

        # Add current fill progress
        filled = state.get("filled_data", {})
        for f in result["fields"]:
            f["is_filled"] = f["key"] in filled
            f["filled_value"] = filled.get(f["key"], "")

        result["progress"] = {
            "total_b_e_fields": result["needs_user_input"],
            "filled_b_e_fields": sum(1 for f in result["fields"]
                                      if f["category"] in ("B", "E") and f["key"] in filled)
    }

        return ApiResponse(
            message="模板字段分析完成",
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模板分析失败: {str(e)}")


# ═══════════════════════════════════════════════════════
# Chapter-by-Chapter Generation API — 逐章生成+用户确认
# ═══════════════════════════════════════════════════════

@router.post("/generate/{session_id}/chapter/confirm", response_model=ApiResponse)
async def confirm_chapter(session_id: str, request: dict):
    """Confirm/revise/skip a generated chapter during chapter-by-chapter generation.

    Request body:
    {
        "chapter": 1,              // Chapter number (1-10)
        "action": "approve",       // "approve" | "revise" | "skip"
        "revision_text": "..."     // Required for "revise" action
    }
    """
    chapter = request.get("chapter", 0)
    action = request.get("action", "")
    revision_text = request.get("revision_text", "")

    if chapter < 1 or chapter > 50:
        raise HTTPException(status_code=400, detail="chapter must be 1-50")
    if action not in ("approve", "revise", "skip"):
        raise HTTPException(status_code=400, detail="action must be approve/revise/skip")
    if action == "revise" and not revision_text:
        raise HTTPException(status_code=400, detail="revision_text is required for revise action")

    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.workflow_service import workflow_service
    result = await workflow_service.process_chapter_feedback(
        session, chapter, action, revision_text,
    )

    return ApiResponse(message=result["message"], data=result)


@router.post("/generate/{session_id}/chapter/generate", response_model=ApiResponse)
async def generate_chapter(session_id: str, request: dict):
    """Manually trigger generation of a specific chapter (SSE streaming).

    Request body: {"chapter": 1}
    """
    chapter = request.get("chapter", 0)
    if chapter < 1 or chapter > 50:
        raise HTTPException(status_code=400, detail="chapter must be 1-50")

    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        from app.services.workflow_service import workflow_service
        from app.services.llm_service import llm_service
        from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

        state = session.state
        stream_queue = asyncio.Queue()

        orchestrator = ChapterOrchestrator(llm_service=llm_service)

        task = asyncio.create_task(
            orchestrator.generate_single_chapter(chapter, state, stream_queue)
        )

        while not task.done():
            try:
                event_data = await asyncio.wait_for(stream_queue.get(), timeout=5.0)
                event_type = event_data.get("event", "message")
                data = event_data.get("data", {})
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                if task.done():
                    break
                yield f"event: thinking\ndata: {json.dumps({'content': '⏳ 生成中...'}, ensure_ascii=False)}\n\n"

        try:
            state = await task
            session.state = state
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e), 'recoverable': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/generate/{session_id}/chapter/{chapter_num}", response_model=ApiResponse)
async def get_chapter(session_id: str, chapter_num: int):
    """Get the status and content of a specific chapter."""
    if chapter_num < 1 or chapter_num > 10:
        raise HTTPException(status_code=400, detail="chapter_num must be 1-10")

    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.workflow_service import workflow_service
    result = workflow_service.get_chapter_status(session, chapter_num)
    return ApiResponse(message=f"Chapter {chapter_num} status", data=result)


@router.get("/generate/{session_id}/chapters/all", response_model=ApiResponse)
async def get_all_chapters(session_id: str):
    """Get the status of all 10 chapters."""
    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.workflow_service import workflow_service
    result = workflow_service.get_all_chapters_status(session)
    return ApiResponse(message="All chapters status", data=result)


@router.post("/generate/{session_id}/missing-data", response_model=ApiResponse)
async def submit_missing_data(session_id: str, request: dict):
    """Submit missing data for a chapter during chapter generation.

    Request body:
    {
        "chapter": 3,
        "data": {"total_samples": "120", "support_rate": "95%", ...}
    }
    """
    chapter = request.get("chapter", 0)
    data = request.get("data", {})

    if chapter < 1 or chapter > 10:
        raise HTTPException(status_code=400, detail="chapter must be 1-10")
    if not data:
        raise HTTPException(status_code=400, detail="data is required")

    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.state

    # Add/update filled_data with submitted data
    filled = state.setdefault("filled_data", {})
    filled.update(data)

    # Also store in structured_data for the chapter
    structured = state.setdefault("structured_data", {})
    step_key = f"step_ch{chapter}"
    step_data = structured.get(step_key, {})
    step_data.update(data)
    structured[step_key] = step_data

    session.state = state

    return ApiResponse(
        message=f"Data received for chapter {chapter}",
        data={"chapter": chapter, "filled_keys": list(data.keys())},
    )


# ═══════════════════════════════════════════════════════
# LangGraph Workflow API — 人机交互式报告生成
async def _run_workflow_bg(sid: str, session, uploaded: list, report_title: str, materials_dir: str, project_context: str):
    """Background task: runs the full workflow pipeline and updates session state."""
    import logging as _wflog
    _log = _wflog.getLogger(__name__)
    _log.info(f"[WORKFLOW] 启动 sid={sid[:12]} files={len(uploaded)} title={report_title[:30]}")
    from app.services.ws_manager import ws_manager
    from app.services.report_workflow import workflow_runner, set_workflow_progress_callback
    state = session.state

    # Shared log buffer — on resume, continue from existing logs
    _shared_logs = list(state.get("_workflow_logs", [])) if state.get("_is_resume") else []
    state["_workflow_logs"] = _shared_logs

    async def _progress_cb(phase, steps):
        state["phase"] = phase; state["step_statuses"] = steps; session.state = state
        # Add log entry for each phase change
        labels = {"analysis": "📂 正在解析上传资料...", "checking": "📋 正在检查数据完整性...",
                  "generating": "✍️ 正在逐章生成报告...", "assembling": "📄 正在组装 DOCX 报告..."}
        if phase in labels and labels[phase] not in _shared_logs:
            _shared_logs.append(labels[phase])
        cur = steps.get("current_chapter", 0)
        if cur > 0:
            msg = f"  ✅ 已完成 {cur}/10 章"
            if msg not in _shared_logs: _shared_logs.append(msg)
        try:
            await ws_manager.send(sid, "step_progress", {"step": phase, "status": "running", "step_statuses": steps})
            if cur > 0:
                await ws_manager.send(sid, "chapter_progress", {"current": cur, "total": 10, "status": "generating"})
        except: pass
    set_workflow_progress_callback(_progress_cb)

    try:
        # Phase 1: Material analysis — skip on resume (already analyzed)
        if state.get("_is_resume"):
            _shared_logs.append("📂 资料已解析，直接继续生成...")
        else:
            await ws_manager.send(sid, "step_progress", {"step": "analysis", "status": "running", "step_statuses": {"analysis": "running", "generation": "pending", "assembly": "pending"}})
            _shared_logs.append("📂 正在解析上传资料（OCR + AI分析）...")
            await _analyze_session_attachments(state, uploaded)
            try:
                from app.services.deep_material_analyzer import analyze_all_materials, apply_analysis_to_state
                llm = None
                try: from app.services.llm_service import LLMService; llm = LLMService()
                except: pass
                analysis = await analyze_all_materials(state, llm, max_vision_images=15)
                apply_analysis_to_state(state, analysis)
                n_imgs = len(analysis.get("classified_images", {}))
                n_data = len(analysis.get("filled_data_updates", {}))
                _shared_logs.append(f"  ✅ 解析完成：{len(uploaded)} 个文件，{n_imgs} 类图片，{n_data} 个数据字段")
            except Exception as e:
                _shared_logs.append(f"  ⚠️ 深度分析跳过：{e}")

            # 🔴 Force OCR if _pdf_raw_text is still empty (cached items may lack OCR from old code)
            if not state.get("_pdf_raw_text") or len(state.get("_pdf_raw_text", "")) < 100:
                _shared_logs.append("📂 检测到文本缓存为空，强制重新OCR...")
                try:
                    from app.services.pdf_data_extractor import PDFDataExtractor
                    from app.services.llm_service import LLMService
                    from app.services.file_service import file_service
                    _ocr_llm = LLMService()
                    _ocr_ext = PDFDataExtractor(llm_service=_ocr_llm)
                    _parts = []
                    for f in uploaded:
                        path = f.get("path", f) if isinstance(f, dict) else f
                        if isinstance(path, str) and path.lower().endswith('.pdf'):
                            try:
                                abs_path = str(file_service.get_absolute_path(path))
                                _doc = await _ocr_ext.extract_pdf(abs_path)
                                if _doc.full_text and len(_doc.full_text) > 100:
                                    _parts.append(_doc.full_text)
                            except: pass
                    if _parts:
                        state["_pdf_raw_text"] = "\n".join(_parts)
                        _shared_logs.append(f"  ✅ 强制OCR完成：{len(state['_pdf_raw_text'])} 字")
                except Exception as _ocr_e:
                    _shared_logs.append(f"  ⚠️ 强制OCR失败：{_ocr_e}")

        await ws_manager.send(sid, "step_progress", {"step": "analysis", "status": "done", "step_statuses": {"analysis": "done", "generation": "running", "assembly": "pending"}})

        # Phase 2: Generate + Assemble using LangGraph workflow (proper HITL with interrupt())
        await ws_manager.send(sid, "step_progress", {"step": "generation", "status": "running",
            "step_statuses": {"analysis": "done", "generation": "running", "assembly": "pending"}})
        state["step_statuses"] = {"analysis": "done", "generation": "running", "assembly": "pending"}
        session.state = state

        from app.services.report_workflow import workflow_runner
        try:
            _log.info(f"[WORKFLOW] Phase2: _pdf_raw_text={len(state.get('_pdf_raw_text',''))}, _uploaded_files={len(state.get('_uploaded_files',[]))}, filled_data={list(state.get('filled_data',{}).keys())}")
            if state.get("_is_resume"):
                # 🔴 Resume: continue from LangGraph interrupt point
                _shared_logs.append("🔄 继续生成（从暂停点恢复）...")
                user_responses = dict(state.get("_pending_responses", {}))
                user_responses["_thread_id"] = state.get("_wf_thread_id", "")
                wf_state = await workflow_runner.resume(sid, user_responses)
            else:
                # 🔴 Fresh start: run full LangGraph workflow
                wf_state = await workflow_runner.start(
                    session_id=sid,
                    report_title=report_title,
                    materials_dir=materials_dir,
                    project_context=project_context,
                    existing_state=state,  # Pass session state with analysis results
                )

            # Handle interrupt (pause)
            interrupt_data = wf_state.get("_interrupt")
            if interrupt_data:
                if isinstance(interrupt_data, dict) and interrupt_data.get("type") == "missing_data":
                    state["_workflow_paused"] = True
                    state["_missing_fields"] = interrupt_data.get("missing_fields", [])
                    state["phase"] = "paused"
                    _shared_logs.append(f"⏸️ 缺少 {len(interrupt_data.get('missing_fields',[]))} 项基本信息，请补充后继续")
                    # 🔴 Save state for resume — including unique thread_id for checkpoint
                    state["_workflow_logs"] = wf_state.get("logs", _shared_logs)
                    state["_wf_thread_id"] = wf_state.get("_thread_id", "")
                    session.state = state
                    return
                else:
                    _log.warning(f"[WORKFLOW] Unknown interrupt: {interrupt_data}")

            # Handle completion — sync workflow state back to session state
            state["chapters"] = wf_state.get("chapters", {})
            state["output_path"] = wf_state.get("output_path", "")
            state["_outline"] = wf_state.get("_outline", {})
            # 🔴 Sync filled_data back — LLM-extracted fields must reach assembler
            wf_filled = wf_state.get("filled_data", {})
            if wf_filled:
                session_filled = state.setdefault("filled_data", {})
                for k, v in wf_filled.items():
                    if v and not session_filled.get(k):
                        session_filled[k] = v
            if wf_state.get("review_table_path"):
                state["review_table_path"] = wf_state.get("review_table_path")
                _shared_logs.append("✅ 评审表已生成")

            # 🔴 Merge workflow logs back
            wf_logs = wf_state.get("logs", [])
            for log in wf_logs:
                if log not in _shared_logs:
                    _shared_logs.append(log)
            _shared_logs.append("✅ 报告生成完成！")

            n_chapters = len(wf_state.get("chapters", {}))
            output_path = wf_state.get("output_path", "")
            _log.info(f"[WORKFLOW] Pipeline done: {n_chapters} chapters, output={output_path}")

            # 🔴 Push completion with download URL
            await ws_manager.send(sid, "chapter_progress", {"current": n_chapters, "total": n_chapters, "status": "completed"})
            await ws_manager.send(sid, "step_progress", {"step": "assembly", "status": "done",
                "step_statuses": {"analysis": "done", "generation": "done", "assembly": "done"},
                "output_path": output_path,
                "download_url": f"/api/v1/files/{output_path}" if output_path else ""})
            state["step_statuses"] = {"analysis": "done", "generation": "done", "assembly": "done"}
            state["output_path"] = output_path
            state["_workflow_running"] = False
            state["_is_resume"] = False
            state["_workflow_logs"] = _shared_logs
            session.state = state

            # Persist to DB
            try:
                from app.services.report_service import report_service
                await report_service.persist_report(session)
            except: pass

        except Exception as e:
            _log.error(f"[WORKFLOW] Pipeline failed: {e}")
            _shared_logs.append(f"❌ 生成失败: {e}")
            import traceback
            _shared_logs.append(traceback.format_exc())
            state["_workflow_running"] = False
            state["phase"] = "error"
            session.state = state
            try:
                await ws_manager.send(sid, "step_progress", {"step": "error", "status": "error", "message": str(e)})
            except: pass
    except Exception as e:
        state["_workflow_running"] = False; state["phase"] = "error"
        state["_workflow_logs"] = [f"Error: {e}"]
        try: await ws_manager.send(sid, "step_progress", {"step": "error", "status": "error", "message": str(e)})
        except: pass


@router.post("/generate/{session_id}/workflow/start", response_model=ApiResponse)
async def start_workflow(session_id: str, request: WorkflowStartRequest):
    """Start the LangGraph report generation workflow.

    If required data is missing, pauses and returns missing_fields list.
    Frontend should display a form for each missing field.
    """
    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.state

    # 🔴 Guard: reject if workflow already running or paused
    if state.get("_workflow_running"):
        raise HTTPException(status_code=409, detail="工作流已在运行中，请等待完成或取消")
    if state.get("_workflow_paused"):
        raise HTTPException(status_code=409, detail="工作流已暂停，请补充信息后继续，无需重新开始")

    # 🔴 Reset analysis flags: always run fresh analysis for each generation
    state["_analysis_done"] = False
    state["_analysis_running"] = False
    state["_is_resume"] = False

    from app.services.report_workflow import workflow_runner, WorkflowPhase, set_workflow_progress_callback

    materials_dir = request.materials_dir or ""
    project_context = request.project_context or ""
    report_title = request.report_title or "社会稳定风险评估报告"

    # Take snapshot
    sid = session_id
    uploaded = list(state.get("_uploaded_files", []) or [])

    # Init step tracking + auto-fill known project data from 稳评资料
    state["phase"] = "generating"
    state["step_statuses"] = {"analysis": "running", "generation": "pending", "assembly": "pending"}
    state["_workflow_running"] = True
    state["_workflow_started_at"] = __import__('time').time()  # For timeout detection in workflow_status
    # Do NOT auto-fill — let master orchestrator detect missing fields and prompt user
    session.state = state

    # Fire-and-forget — store task reference for cancellation
    import asyncio as _asyncio
    _wf_task = _asyncio.create_task(_run_workflow_bg(sid, session, uploaded, report_title, materials_dir, project_context))
    _bg_tasks = session.state.setdefault("_bg_tasks", [])
    _bg_tasks.append(_wf_task)

    return ApiResponse(message="🚀 工作流已启动", data={"phase": "generating"})


@router.post("/generate/{session_id}/workflow/resume", response_model=ApiResponse)
async def resume_workflow(session_id: str, request: WorkflowResumeRequest):
    """Resume the workflow with user-provided data — continues from pause, not restart."""
    try:
        session = report_service.get_session(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    user_responses = request.data
    state = session.state
    filled = state.setdefault("filled_data", {})

    # Merge user data into filled_data
    for key, value in (user_responses or {}).items():
        if value and str(value).strip():
            filled[key] = str(value).strip()

    state["_missing_fields"] = []
    state["_workflow_paused"] = False
    state["_is_resume"] = True  # 🔴 Mark as resume so pipeline skips data check + reuses outline
    state["_pending_responses"] = user_responses  # 🔴 For LangGraph Command(resume=...)
    state["phase"] = "generating"
    state["step_statuses"] = {"analysis": "done", "generation": "running", "assembly": "pending"}
    session.state = state

    # 🔴 Re-run background task with updated data — will skip analysis and go straight to generation
    uploaded = list(state.get("_uploaded_files", []) or [])
    report_title = state.get("report_title", filled.get("project_name", "社会稳定风险评估报告"))
    materials_dir = state.get("materials_dir", "")
    project_context = state.get("project_context", "")
    import asyncio as _asyncio
    _wf_task2 = _asyncio.create_task(_run_workflow_bg(
        session_id, session, uploaded, report_title, materials_dir, project_context
    ))
    _bg_tasks2 = session.state.setdefault("_bg_tasks", [])
    _bg_tasks2.append(_wf_task2)

    return ApiResponse(message="🚀 工作流已继续（数据已补充）", data={"phase": "generating"})


@router.get("/generate/{session_id}/workflow/status", response_model=ApiResponse)
async def workflow_status(session_id: str):
    """Get current workflow status — merges session state with subprocess result file."""
    try:
        session = report_service.get_session(session_id)
        state = session.state
    except Exception:
        return ApiResponse(message="Session not found", data={"phase": "idle"})

    wf_state = state.get("_workflow_state", {})
    running = state.get("_workflow_running", False)
    paused = state.get("_workflow_paused", False)
    missing = wf_state.get("missing_fields", []) if wf_state else state.get("_missing_fields", [])

    # 🔴 Check subprocess result file — subprocess writes here, state may be stale
    import json as _json
    from pathlib import Path as _Path
    import os as _os
    result_file = _Path(f"/tmp/report_result_{session_id}.json")
    subprocess_output = ""
    subprocess_error = ""
    if result_file.exists():
        try:
            sp_data = _json.loads(result_file.read_text())
            if sp_data.get("status") == "completed":
                subprocess_output = sp_data.get("output_path", "")
                state["output_path"] = subprocess_output
                state["phase"] = "complete"
                state["status"] = "completed"
            elif sp_data.get("status") == "error":
                subprocess_error = sp_data.get("message", "未知错误")
        except Exception:
            pass

    # 🔴 Detect subprocess crash: PID file exists but process dead, no result
    pid_file = _Path(f"/tmp/report_pid_{session_id}.txt")
    if pid_file.exists() and not result_file.exists():
        try:
            _pid = int(pid_file.read_text().strip())
            _os.kill(_pid, 0)  # Signal 0 = check existence
        except (OSError, ValueError):
            subprocess_error = "生成进程异常退出，请重试"
            running = False

    # 🔴 Backend safety timeout: workflow running > 35 min → stuck
    _started = state.get("_workflow_started_at", 0)
    if running and _started:
        import time as _time
        if (_time.time() - _started) > 2100:  # 35 min
            subprocess_error = "生成超时（35分钟），请刷新页面重试"
            running = False

    output = subprocess_output or state.get("output_path", "")

    # 🔴 Derive step statuses from actual state
    step_statuses = {"analysis": "pending", "generation": "pending", "assembly": "pending"}
    if state.get("_analysis_done"):
        step_statuses["analysis"] = "done"
        step_statuses["generation"] = "running" if running else "done"
    if output:
        step_statuses["analysis"] = "done"
        step_statuses["generation"] = "done"
        step_statuses["assembly"] = "done"
    elif not running and state.get("_analysis_done"):
        step_statuses["assembly"] = "running"
    # Override with explicit values if set
    explicit = state.get("step_statuses", {})
    if explicit:
        step_statuses.update(explicit)

    # Verify download file actually exists
    download_url = ""
    if output:
        abs_path = settings.STORAGE_DIR / output
        if abs_path.exists():
            download_url = f"/api/v1/files/{output}"

    # Determine phase
    if subprocess_error:
        phase = "error"
        running = False
    elif paused:
        phase = "paused"
    elif output:
        running = False
        phase = "complete"
    elif running:
        phase = "generating"
    else:
        phase = state.get("phase", "idle")

    # 🔴 Typed response — validated by WorkflowStatusData schema
    status_data = WorkflowStatusData(
        phase=phase,
        running=running,
        paused=paused,
        error=subprocess_error,
        total_chapters=state.get("_outline", {}).get("total") or len(state.get("_outline", {}).get("chapters", [])),
        current_chapter=len(state.get("chapters", {})),
        missing_fields=missing,
        logs=state.get("_workflow_logs", []) or (wf_state.get("logs", []) if wf_state else []),
        step_statuses=step_statuses,
        output_path=output,
        download_url=download_url,
    )
    return ApiResponse(data=status_data.model_dump())


# ═══════════════════════════════════════════════════════
# Pipeline API — 新8阶段流水线
# ═══════════════════════════════════════════════════════

@router.post("/pipeline/run", response_model=ApiResponse)
async def run_pipeline(request: dict):
    """Run the 8-phase report generation pipeline.

    Request body:
    {
        "materials_dir": "~/Downloads/稳评资料",
        "region": "淮安市洪泽区",
        "template_path": "",      // optional, auto-detected if empty
        "example_path": "",       // optional
        "session_id": ""          // optional, auto-generated
    }
    """
    materials_dir = request.get("materials_dir", "")
    region = request.get("region", "")
    template_path = request.get("template_path", "")
    example_path = request.get("example_path", "")
    session_id = request.get("session_id", "")

    if not materials_dir:
        raise HTTPException(status_code=400, detail="请提供 materials_dir（稳评资料路径）")

    # Expand user path
    from pathlib import Path
    materials_dir = str(Path(materials_dir).expanduser().resolve())
    if not Path(materials_dir).exists():
        raise HTTPException(status_code=400, detail=f"稳评资料路径不存在: {materials_dir}")

    if not session_id:
        import uuid
        session_id = f"pipeline_{uuid.uuid4().hex[:12]}"

    # Create a session for tracking
    try:
        session = report_service.create_session(session_id)
    except Exception:
        session = None

    async def event_generator():
        from app.services.workflow_service import workflow_service

        stream_queue = asyncio.Queue()

        async for event in workflow_service.run_pipeline(
            materials_dir=materials_dir,
            region=region,
            template_path=template_path,
            example_path=example_path,
            session_id=session_id,
            stream_queue=stream_queue,
        ):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
    },
    )


@router.get("/pipeline/status/{session_id}", response_model=ApiResponse)
async def get_pipeline_status(session_id: str):
    """Get pipeline status for a session."""
    try:
        session = report_service.get_session(session_id)
        state = session.state
        return ApiResponse(
            message="Pipeline status",
            data={
                "session_id": session_id,
                "phase": state.get("pipeline_phase", ""),
                "status": state.get("pipeline_status", "running")
    },
        )
    except Exception:
        return ApiResponse(
            message="Pipeline status unavailable",
            data={},
        )


# ═══════════════════════════════════════════════════════════
# LLM Retry Helper
# ═══════════════════════════════════════════════════════════

async def _llm_retry(fn, max_retries: int = 2, base_delay: float = 1.0):
    """Retry an LLM call with exponential backoff."""
    import asyncio as _asyncio2
    import logging as _log
    _l = _log.getLogger(__name__)
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                _l.warning(f"LLM retry {attempt+1}/{max_retries} after {delay}s: {e}")
                await _asyncio2.sleep(delay)
    raise last_err


# ═══════════════════════════════════════════════════════════
# Direct Generation Endpoint — upload files → generate (no chat)
# ═══════════════════════════════════════════════════════════


@router.get("/generate/styles")
async def list_report_styles():
    """List available report styles/templates."""
    from app.agent.report_styles import STYLES, DEFAULT_STYLE
    return ApiResponse(code=0, data={
        "styles": [
            {"id": s.name, "label": s.label, "description": s.description}
            for s in STYLES.values()
        ],
        "default": DEFAULT_STYLE
    })


@router.post("/generate/{session_id}/generate")
async def generate_report_direct(session_id: str):
    """Trigger report generation via subprocess (isolated from uvicorn event loop).

    Frontend polls GET /status for progress.
    """
    import subprocess, os

    try:
        session = report_service.get_session(session_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    state = session.state
    state["phase"] = "generating"
    state["status"] = "generating"
    state["_skip_analysis"] = True
    state["_report_style"] = state.get("_report_style") or "jinhu"
    state["progress"] = {"current_chapter": 0, "total_chapters": 10, "message": "启动生成..."}
    report_service.add_message(session, "user", "生成报告")

    # Launch subprocess in background (with error logging)
    # __file__ = app/routers/report.py → 3 levels up = backend/
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(backend_dir, "scripts", "generate_report.py")
    log_file = open(f"/tmp/report_gen_{session_id}.log", "w")
    proc = subprocess.Popen(
        [sys.executable, script, session_id],
        cwd=backend_dir,
        stdout=log_file,
        stderr=log_file,
    )
    # Save PID so workflow_status can detect crashes
    Path(f"/tmp/report_pid_{session_id}.txt").write_text(str(proc.pid))

    return ApiResponse(code=0, message="报告生成已启动", data={
        "session_id": session_id,
        "status": "generating"
    })


# ═══════════════════════════════════════════════════════════
# WebSocket Chat Endpoint — replaces SSE for reliable bidirectional comm
# ═══════════════════════════════════════════════════════════

@router.websocket("/ws/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    """WebSocket chat endpoint — full-duplex, auto-reconnect, token streaming."""
    from app.services.ws_manager import ws_manager
    from app.services.workflow_service import workflow_service
    import logging as _ws_logging
    _ws_logger = _ws_logging.getLogger(__name__)
    import asyncio as _asyncio

    try:
        session = report_service.get_session(session_id)
    except HTTPException:
        await websocket.close(code=4004, reason="Session not found")
        return

    await ws_manager.connect(session_id, websocket)

    try:
        while True:
            # Receive message from frontend
            raw = await websocket.receive_json()
            msg_type = raw.get("type", "chat")
            text = raw.get("text", "")
            attachments = raw.get("attachments", [])
            intent = raw.get("intent", "stability")

            if msg_type == "chat":
                # Build user message content — include file names if present
                user_content = text
                if attachments and not user_content:
                    file_names = [a.split("/")[-1] if "/" in a else str(a) for a in attachments[:5]]
                    user_content = f"[上传了 {len(attachments)} 个文件: {', '.join(file_names)}]"
                elif attachments and user_content:
                    file_names = [a.split("/")[-1] if "/" in a else str(a) for a in attachments[:5]]
                    user_content = f"{user_content}\n[附件: {', '.join(file_names)}]"

                report_service.add_message(session, "user", user_content)

                # Check if generation request
                gen_kw = ["生成报告", "开始生成", "帮我生成", "写报告"]
                is_gen = any(kw in text for kw in gen_kw)

                if is_gen:
                    session.state["generation_mode"] = "chapter_by_chapter"

                    # Process attachments
                    if attachments:
                        await ws_manager.send(session_id, "thinking", {"content": f"📎 处理 {len(attachments)} 个文件..."})
                        await _analyze_session_attachments(session.state, attachments)

                    # Run orchestrator — forward events to WebSocket
                    from app.services.llm_service import llm_service
                    from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

                    stream_queue = _asyncio.Queue()
                    session.state["_stream_queue"] = stream_queue
                    session.state["_action_event"] = _asyncio.Event()

                    async def _drain():
                        while True:
                            try:
                                evt = await _asyncio.wait_for(stream_queue.get(), timeout=0.5)
                                etype = evt.get("event", "message")
                                edata = evt.get("data", {})
                                await ws_manager.send(session_id, etype, edata)
                            except _asyncio.TimeoutError:
                                if not ws_manager.is_connected(session_id):
                                    break
                            except Exception:
                                break

                    orch = ChapterOrchestrator(llm_service=llm_service)
                    drain_task = _asyncio.create_task(_drain())
                    try:
                        await orch.run_full_pipeline(session.state, stream_queue, start_chapter=1)
                    except Exception as e:
                        await ws_manager.send(session_id, "error", {"message": str(e)})
                    finally:
                        drain_task.cancel()
                        try: await drain_task
                        except: pass
                else:
                    # Regular chat — conversational agent with context
                    await ws_manager.send(session_id, "thinking", {"content": "思考中..."})

                    # ---- Process any new attachments first ----
                    new_files_info = []
                    if attachments:
                        for att in attachments:
                            if not isinstance(att, str):
                                continue
                            fname = att.split("/")[-1] if "/" in att else att
                            ext_lower = att.lower()
                            # Extract text from PDFs/docs
                            if ext_lower.endswith((".pdf", ".docx", ".doc", ".txt")):
                                extracted = False
                                text_cache = session.state.setdefault("_pdf_texts", {})
                                if att not in text_cache:
                                    try:
                                        from app.services.file_service import file_service
                                        if ext_lower.endswith(".pdf"):
                                            txt = file_service.extract_pdf_text(att)
                                        elif ext_lower.endswith((".docx", ".doc")):
                                            txt = file_service.extract_docx_text(att)
                                        else:
                                            txt = file_service.read_text_file(att)
                                        if txt and len(txt.strip()) > 50:
                                            text_cache[att] = txt[:30000]
                                            extracted = True
                                    except Exception:
                                        pass
                                else:
                                    extracted = True
                                if extracted:
                                    new_files_info.append(f"📄 {fname}（已提取文本）")
                                else:
                                    new_files_info.append(f"📄 {fname}（文件已接收）")
                            elif ext_lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp")):
                                new_files_info.append(f"🖼️ {fname}（图片文件）")
                            else:
                                new_files_info.append(f"📎 {fname}")

                        # Also run material ingestion for PDFs
                        pdf_paths = [a for a in attachments if isinstance(a, str) and a.lower().endswith(".pdf")]
                        if pdf_paths:
                            try:
                                await _analyze_session_attachments(session.state, pdf_paths)
                            except Exception:
                                pass

                    # Build conversation context from history
                    history = session.state.get("messages", [])[-10:]
                    conv = []
                    for m in history:
                        role = m.get("role", "user")
                        # LLM API only accepts "user" or "assistant"
                        if role == "agent":
                            role = "assistant"
                        elif role not in ("user", "assistant"):
                            role = "user"
                        # Include file info in user messages
                        content = m.get("content", "")
                        conv.append({"role": role, "content": content})

                    # Add data context + file info
                    filled = {k: v for k, v in session.state.get("filled_data", {}).items()
                             if not k.startswith("_") and v}
                    materials = session.state.get("_project_materials", [])
                    uploaded = session.state.get("_uploaded_files", [])

                    data_context = ""
                    if filled:
                        data_context += "已知项目数据:\n" + "\n".join(f"- {k}: {v}" for k, v in list(filled.items())[:10])
                    if materials:
                        data_context += f"\n已解析 {len(materials)} 份PDF资料"
                    if new_files_info:
                        data_context += "\n用户刚刚上传了以下文件:\n" + "\n".join(new_files_info)
                        data_context += "\n请确认收到文件，并根据文件内容帮助用户。如果文件是PDF资料，文本已自动提取到知识库。"
                    elif uploaded:
                        data_context += f"\n本会话已上传 {len(uploaded)} 个文件"
                    if materials or filled:
                        data_context += "\n如果数据已齐全，请主动建议用户回复「生成报告」开始编写。"

                    system_prompt = (
                        "你是社会稳定风险评估报告编制助手，服务于江苏众拓项目代理咨询有限公司。\n"
                        "你的职责：引导用户提供项目信息，解答稳评相关问题。\n"
                        "如果用户发送了文件或图片，请确认已收到并简要说明文件内容。\n"
                        "风格：专业但友好，简洁直接，不说废话。\n"
                        f"{data_context}\n"
                    )

                    reply = ""
                    try:
                        from app.services.llm_service import llm_service
                        result = await _llm_retry(lambda: llm_service.chat(
                            messages=[
                                {"role": "system", "content": system_prompt},
                                *conv,
                            ],
                            max_tokens=800, temperature=0.7,
                        ))
                        reply = result.get("content", "") if isinstance(result, dict) else str(result)
                    except Exception as e:
                        import traceback
                        _ws_logger.error(f"LLM chat failed: {e}\n{traceback.format_exc()}")
                        reply = f"抱歉，服务暂时不可用。请稍后重试。\n（{str(e)[:80]}）"

                    if reply:
                        report_service.add_message(session, "agent", reply)
                        # Stream sentence-by-sentence
                        await ws_manager.stream_text(session_id, reply, delay=0.03)
                        await ws_manager.send(session_id, "done", {"text": reply, "message_type": "text"})

            elif msg_type == "upload":
                # File already uploaded via HTTP, just acknowledge
                await ws_manager.send(session_id, "upload_ack", {"count": len(attachments)})

    except WebSocketDisconnect:
        _ws_logger.info(f"WS client disconnected: {session_id}")
    except Exception as e:
        _ws_logger.error(f"WS error for {session_id}: {e}")
        try: await websocket.close()
        except: pass
    finally:
        await ws_manager.disconnect(session_id)
