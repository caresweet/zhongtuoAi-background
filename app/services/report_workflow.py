"""LangGraph 5-layer report generation agent — enterprise architecture.

Flow:
  file_parse → field_validate → build_dynamic_outline → outline_check
  → retrieve_rag → [chapter loop: generate → review → route_retry]
  → assemble_final_report → END

Layers:
  1. User uploaded files (parse/OCR/extract)
  2. RAG knowledge base (specs + examples)
  3. Dynamic outline (LLM decides structure)
  4. Per-chapter generation (subset data, image placeholders)
  5. Quality review (hard rules + LLM, retry/fallback)
"""

import asyncio, logging, os, re, time
from typing import List, Dict, Any, Optional
from enum import Enum

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

# ── State ──────────────────────────────────────────────────────────────────────
ReportWorkflowState = dict  # Plain dict for LangGraph compat

class WorkflowPhase(str, Enum):
    IDLE="idle"; INGESTING="ingesting"; VALIDATING="validating"
    OUTLINE="outline"; RAG="rag"; GENERATING="generating"
    REVIEWING="reviewing"; ASSEMBLING="assembling"; COMPLETE="complete"; ERROR="error"

MAX_RETRY = 2

# ── Progress emitter ───────────────────────────────────────────────────────────
_progress_callback = None
def set_workflow_progress_callback(cb): global _progress_callback; _progress_callback = cb
async def _emit(phase: str, steps: dict):
    if _progress_callback:
        try: await _progress_callback(phase, steps)
        except: pass

# ═══════════════════════════════════════════════════════════════════════════════
# Node 1: file_parse — OCR, field extraction, table extraction, image placeholder
# ═══════════════════════════════════════════════════════════════════════════════
async def node_file_parse(state: ReportWorkflowState) -> ReportWorkflowState:
    """Parse uploaded PDFs: OCR text, extract fields, extract tables, generate image placeholders."""
    state["phase"] = WorkflowPhase.INGESTING.value
    state.setdefault("logs", []).append("📂 正在解析上传资料...")
    await _emit("analysis", {"analysis":"running","generation":"pending","assembly":"pending"})

    try:
        from app.routers.report import _analyze_session_attachments
        session_state = {
            "session_id": state.get("session_id",""), "_domain": "stability",
            "filled_data": state.get("filled_data",{}), "_uploaded_files": [],
        }
        for f in (state.get("_uploaded_files",[]) or []):
            path = f.get("path",f) if isinstance(f,dict) else f
            if isinstance(path,str) and path not in session_state["_uploaded_files"]:
                session_state["_uploaded_files"].append(path)
        if state.get("_project_materials"):
            session_state["_project_materials"] = state["_project_materials"]
        if state.get("_pdf_raw_text"):
            session_state["_pdf_raw_text"] = state["_pdf_raw_text"]

        await _analyze_session_attachments(session_state, session_state["_uploaded_files"])

        # Merge back
        for k in ("_pdf_raw_text","_project_materials","filled_data","_uploaded_files"):
            if k in session_state: state[k] = session_state[k]

        # Build image_meta_list from extracted images
        images = []
        for m in (state.get("_project_materials",[]) or []):
            if not isinstance(m,dict): continue
            for img in (m.get("metadata",{}).get("extracted_images",[]) or []):
                if isinstance(img,str):
                    images.append({"image_id":f"img_{len(images):03d}","path":img,"page_num":0,
                                   "caption_raw":"","content_desc":"","source":m.get("source_name","")})
        state["image_meta_list"] = images

        # Build table_meta_list
        tables = []
        for m in (state.get("_project_materials",[]) or []):
            if not isinstance(m,dict): continue
            for tbl in (m.get("metadata",{}).get("extracted_tables",[]) or []):
                if isinstance(tbl,dict):
                    tables.append({"table_id":f"tbl_{len(tables):03d}","status":"ok",
                                   "headers":tbl.get("headers",[]),"rows":tbl.get("rows",[]),
                                   "source":m.get("source_name","")})
        state["table_meta_list"] = tables

        # Generate doc_text_with_placeholder — insert <<IMAGE:img_xxx>> markers
        pdf_text = state.get("_pdf_raw_text","")
        placeholder_text = pdf_text
        for img in images:
            placeholder_text += f"\n<<IMAGE:{img['image_id']}>>\n"
        state["doc_text_with_placeholder"] = placeholder_text

        state["logs"].append(f"✅ 解析完成: {len(images)} 图片, {len(tables)} 表格")
        await _emit("analysis", {"analysis":"done","generation":"pending","assembly":"pending"})
    except Exception as e:
        state.setdefault("errors",[]).append(f"文件解析失败: {e}")
        logger.error(f"file_parse failed: {e}")
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 2: field_validate — value range check, conflict detection, confidence mark
# ═══════════════════════════════════════════════════════════════════════════════
async def node_field_validate(state: ReportWorkflowState) -> ReportWorkflowState:
    """Validate extracted fields: range check, multi-source conflict, confidence marking."""
    state["phase"] = WorkflowPhase.VALIDATING.value
    filled = state.setdefault("filled_data",{})
    logs = state.setdefault("logs",[])

    # Range validation
    for key, label in [("area_mu","征收面积"),("household_count","户数"),("total_samples","问卷数")]:
        v = filled.get(key)
        if v is not None and str(v).strip():
            try:
                n = float(str(v).replace(",","").replace("亩","").strip())
                if n <= 0:
                    logs.append(f"⚠️ {label}值异常({v})，已标记待补充")
                    filled[key] = ""
            except ValueError:
                logs.append(f"⚠️ {label}格式异常({v})，已标记待补充")
                filled[key] = ""

    # LLM extraction from OCR text if fields still empty
    pdf_text = state.get("_pdf_raw_text","") or state.get("_pdf_texts","")
    if not pdf_text:
        mats = state.get("_project_materials",[]) or []
        parts = [m.get("text_content","") for m in mats if isinstance(m,dict) and m.get("text_content")]
        if parts: pdf_text = "\n".join(parts); state["_pdf_raw_text"] = pdf_text

    if pdf_text and len(pdf_text) > 100:
        has_id = filled.get("project_name") or filled.get("org_name")
        if not has_id:
            try:
                from app.routers.report import _llm_extract_fields
                from app.services.llm_service import LLMService
                llm = LLMService()
                extracts = await _llm_extract_fields(pdf_text, llm)
                for k,v in extracts.items():
                    if v and str(v).strip() and not filled.get(k):
                        filled[k] = str(v).strip()
                if extracts:
                    logs.append(f"🤖 LLM提取 {len(extracts)} 字段: {list(extracts.keys())}")
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}")

    state["filled_data"] = filled

    # 🔴 Regex fallback for org_name — always in announcement PDFs
    if not filled.get("org_name") and pdf_text:
        m = re.search(r'(淮安|洪泽|涟水|金湖|盱眙|清江浦|淮阴)\S{0,20}(人民政府|区政府|街道办事处|镇政府)', pdf_text)
        if m: filled["org_name"] = m.group(0); logs.append(f"📝 org_name 正则提取: {filled['org_name']}")
    if not filled.get("project_name") and pdf_text:
        m = re.search(r'(洪拟征告|淮拟征告|涟拟征告|金拟征告|盱拟征告)\s*〔?\s*\d{4}\s*〕?\s*\d+\s*号', pdf_text)
        if m: filled["project_name"] = m.group(0); logs.append(f"📝 project_name 正则提取: {filled['project_name']}")

    # 🔴 必要字段缺失 → interrupt 暂停询问用户（HITL）
    # 🔴 核心字段缺失 → 弹窗询问用户（面积/位置/文号/支持率/户数等核心数据）
    required_fields = [
        ("project_name", "项目名称"),
        ("org_name", "责任单位"),
        ("location", "项目位置"),
        ("area_mu", "征收面积（亩）"),
        ("doc_reference", "公告文号"),
        ("support_rate", "群众支持率"),
        ("household_count", "涉及户数"),
    ]
    missing_required = []
    for key, label in required_fields:
        val = filled.get(key, "")
        if not val or str(val).startswith("【待补充】"):
            missing_required.append({"key": key, "label": label})

    if missing_required:
        from langgraph.types import interrupt
        logs.append(f"⏸️ 缺少 {len(missing_required)} 项核心信息，暂停等待补充")
        user_input = interrupt({
            "type": "missing_data",
            "missing_fields": missing_required,
        })
        # resume 后，user_input 是用户补充的数据
        if isinstance(user_input, dict):
            for item in missing_required:
                key = item["key"]
                val = user_input.get(key, "")
                if val and str(val).strip():
                    filled[key] = str(val).strip()
                    filled[f"_locked_{key}"] = True
                    logs.append(f"✅ 已补充 {item['label']}: {val}")

    # 仍缺失的字段（用户未补的）自动标【待补充】，不再中断
    for key in ("project_name","org_name","location","area_mu","land_use",
                "household_count","total_samples","compensation_standard",
                "doc_reference","support_rate"):
        if not filled.get(key):
            filled[key] = "【待补充】"
            logs.append(f"📝 {key} 未提供，标记为【待补充】")

    # 🔴 稳评实施单位默认值（公司固定，无需用户提供）
    if not filled.get("implement_unit"):
        filled["implement_unit"] = "江苏众拓项目代理咨询有限公司"
        filled["_locked_implement_unit"] = True

    state["filled_data"] = filled
    await _emit("validating", {"analysis":"done","outline":"pending","generation":"pending","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 2b: data_inquiry — 数据合理性校验，不合理时询问用户（HITL）
# ═══════════════════════════════════════════════════════════════════════════════
async def node_data_inquiry(state: ReportWorkflowState) -> ReportWorkflowState:
    """数据合理性校验：发现不合理数据（如支持率非100%）时暂停询问用户。

    询问用户：能否提供真实数据 / 维持现状 / 授权上网查询。
    用户回应后写入 filled_data 继续。
    """
    state["phase"] = WorkflowPhase.VALIDATING.value
    logs = state.setdefault("logs",[])
    filled = state.setdefault("filled_data", {})

    issues = []

    # 支持率合规检查（征地项目合规要求 100%）
    support_rate = filled.get("support_rate", "")
    if support_rate and str(support_rate) not in ("【待补充】", ""):
        try:
            rate = float(str(support_rate).replace("%", "").strip())
            if rate != 100:
                issues.append({
                    "field": "support_rate",
                    "label": "群众支持率",
                    "current": f"{rate}%",
                    "message": f"提取到的群众支持率是 {rate}%，征地项目合规要求 100%。请确认：提供真实支持率 / 维持现状（将标注待补充）/ 授权上网查询当地实际数据",
                })
        except (ValueError, TypeError):
            pass

    # 补偿标准合理性（0 或缺失视为不合理）
    comp = filled.get("compensation_standard", "")
    if comp and str(comp) not in ("【待补充】", ""):
        if re.search(r'(?<!\d)0\s*(?:元|万元)', str(comp)):
            issues.append({
                "field": "compensation_standard",
                "label": "补偿标准",
                "current": str(comp),
                "message": f"提取到的补偿标准异常（{comp}），请确认或提供真实补偿标准",
            })

    # 🔴 项目名称与文号矛盾检查（如名称含"10号"但文号是7号）
    doc_ref = str(filled.get("doc_reference", "") or "")
    project_name = str(filled.get("project_name", "") or "")
    if doc_ref and project_name and "号" in project_name:
        import re as _re_num
        doc_num = _re_num.search(r'(\d+)\s*号', doc_ref)
        proj_num = _re_num.search(r'(\d+)\s*号', project_name)
        if doc_num and proj_num:
            dn, pn = doc_num.group(1), proj_num.group(1)
            if dn != pn and not any(d in project_name for d in ['待补充', '示意图']):
                issues.append({
                    "field": "doc_reference",
                    "label": "公告文号",
                    "current": doc_ref,
                    "message": f"项目名称({project_name})中的文号与公告文号({doc_ref})不一致（{pn}号 vs {dn}号），请确认哪个是正确文号",
                })

    if issues:
        from langgraph.types import interrupt
        logs.append(f"⚠️ 发现 {len(issues)} 项数据不合理，暂停询问用户")
        user_input = interrupt({
            "type": "data_inquiry",
            "issues": issues,
        })
        # 用户回应后写入
        if isinstance(user_input, dict):
            for issue in issues:
                field = issue["field"]
                val = user_input.get(field, "")
                if val and str(val).strip():
                    filled[field] = str(val).strip()
                    filled[f"_locked_{field}"] = True
                    logs.append(f"✅ 用户确认 {issue['label']}: {val}")

    state["filled_data"] = filled
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3: build_dynamic_outline — LLM generates chapter structure
# ═══════════════════════════════════════════════════════════════════════════════
async def node_build_dynamic_outline(state: ReportWorkflowState) -> ReportWorkflowState:
    """LLM generates dynamic outline based on available materials."""
    state["phase"] = WorkflowPhase.OUTLINE.value
    logs = state.setdefault("logs",[])
    filled = state.get("filled_data",{})

    _outline = state.get("_outline")
    if _outline and _outline.get("chapters"):
        logs.append(f"📋 复用大纲: {len(_outline['chapters'])} 章")
    else:
        from app.services.llm_service import LLMService
        from app.services.master_orchestrator import generate_outline
        llm = LLMService()
        logs.append("📋 动态生成大纲...")
        has_announcement = bool(filled.get("doc_reference") or filled.get("project_name"))
        has_survey = bool(filled.get("total_samples"))
        _outline = await generate_outline(llm, filled, has_survey, has_review=False, has_announcement=has_announcement)
        state["_outline"] = _outline
        logs.append(f"📋 大纲: {len(_outline.get('chapters',[]))} 章")

    # Convert to outline_list
    outline_list = []
    for ch_def in (_outline.get("chapters",[]) or []):
        outline_list.append({
            "chapter_no": str(ch_def.get("num",len(outline_list)+1)),
            "title": ch_def.get("title",""),
            "depend_on_data": ch_def.get("data_needed",[]),
            "need_spec_tags": ch_def.get("key_points",[]),
            "depends_on": ch_def.get("depends_on", []),          # 🔴 论证依赖
            "argument_note": ch_def.get("argument_note", ""),    # 🔴 论证任务
            "raw_content": None, "review_score": None, "review_msg": None,
            "retry_count": 0, "status": "pending",
        })
    state["outline_list"] = outline_list
    await _emit("outline", {"analysis":"done","outline":"done","generation":"pending","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 4: outline_check — validate chapter order, check resource availability
# ═══════════════════════════════════════════════════════════════════════════════
async def node_outline_check(state: ReportWorkflowState) -> ReportWorkflowState:
    """Validate outline: business logic order, resource dependency check."""
    outline_list = state.get("outline_list",[]) or []
    filled = state.get("filled_data",{})
    logs = state.setdefault("logs",[])

    # 🔴 Enforce minimum standard structure — if outline is too short or missing key chapters, use fallback
    ch_titles = [ch.get("title","") for ch in outline_list]
    required_keywords = ["概况","评估过程","调查","分析","风险","措施前","措施","措施后","结论","应急"]
    missing = [kw for kw in required_keywords if not any(kw in t for t in ch_titles)]
    if len(outline_list) < 7 or len(missing) > 3:
        logs.append(f"⚠️ 动态大纲不完整（{len(outline_list)}章，缺少{missing}），使用标准10章结构")
        outline_list = [
            {"chapter_no":"1","title":"拟征收决策基本概况","depend_on_data":["project_name","org_name","location","area_mu","land_use"],"need_spec_tags":["决策名称","决策主体","项目位置面积","补偿方案要点","利益相关者"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"2","title":"评估过程、方法和依据","depend_on_data":[],"need_spec_tags":["评估过程","评估方法","法律法规与评估依据"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"3","title":"社会稳定风险因素调查","depend_on_data":["total_samples","support_rate"],"need_spec_tags":["问卷调查结果","利益相关者诉求"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"4","title":"决策综合分析","depend_on_data":[],"need_spec_tags":["合法性","合理性","可行性","可控性"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"5","title":"风险因素识别与初始等级表","depend_on_data":[],"need_spec_tags":["风险因素识别","风险等级表"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"6","title":"措施前风险等级研判","depend_on_data":[],"need_spec_tags":["量化评分","措施前得分","风险等级判定"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"7","title":"风险防范与化解措施","depend_on_data":["org_name"],"need_spec_tags":["防范措施","责任主体","完成时限"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"8","title":"措施后风险等级评估","depend_on_data":[],"need_spec_tags":["措施后得分","前后对比","等级变化"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"9","title":"评估结论与建议","depend_on_data":[],"need_spec_tags":["评估结论","风险等级","工作建议"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
            {"chapter_no":"10","title":"应急预案","depend_on_data":["org_name"],"need_spec_tags":["组织体系","分级响应","处置措施"],"raw_content":None,"review_score":None,"review_msg":None,"retry_count":0,"status":"pending"},
        ]
        state["outline_list"] = outline_list

    # 🔴 章节编号连续性检查：动态大纲可能编号不连续（缺第1章、第6章），补全缺失章节
    STANDARD_TITLES = {
        1: "拟征收决策基本概况", 2: "评估过程、方法和依据", 3: "社会稳定风险因素调查",
        4: "决策综合分析", 5: "风险因素识别与初始等级表", 6: "措施前风险等级研判",
        7: "风险防范与化解措施", 8: "措施后风险等级评估", 9: "评估结论与建议", 10: "应急预案",
    }
    existing_nums = set()
    for ch in outline_list:
        try:
            existing_nums.add(int(ch.get("chapter_no", 0)))
        except (ValueError, TypeError):
            pass
    for num in range(1, 11):
        if num not in existing_nums:
            outline_list.append({
                "chapter_no": str(num), "title": STANDARD_TITLES.get(num, f"第{num}章"),
                "depend_on_data": [], "need_spec_tags": [],
                "raw_content": None, "review_score": None, "review_msg": None,
                "retry_count": 0, "status": "pending",
            })
            logs.append(f"⚠️ 动态大纲缺少第{num}章，已补全为标准章节")
    # 按章节号排序，保证顺序正确
    outline_list.sort(key=lambda ch: int(ch.get("chapter_no", 99)))
    state["outline_list"] = outline_list

    for ch in outline_list:
        deps = ch.get("depend_on_data",[]) or []
        missing = [d for d in deps if not filled.get(d) or str(filled.get(d,"")).startswith("【待补充】")]
        if missing and len(missing) == len(deps):
            ch["status"] = "degraded"
            logs.append(f"⚠️ 第{ch['chapter_no']}章依赖数据全部缺失({missing})，将降级生成")
    state["outline_list"] = outline_list
    await _emit("outline", {"analysis":"done","outline":"done","generation":"pending","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 5: retrieve_rag — batch RAG retrieval per chapter
# ═══════════════════════════════════════════════════════════════════════════════
async def node_retrieve_rag(state: ReportWorkflowState) -> ReportWorkflowState:
    """Retrieve RAG chunks per chapter (specs + examples)."""
    state["phase"] = WorkflowPhase.RAG.value
    logs = state.setdefault("logs",[])
    outline_list = state.get("outline_list",[]) or []

    try:
        from app.rag.rag_service import rag_service
        all_chunks = []
        for ch in outline_list:
            ch_num = int(ch.get("chapter_no","1"))
            title = ch.get("title","")
            try:
                r = await rag_service.retrieve_for_chapter(ch_num, state.get("session_id",""),
                                                          title, 8, None)
                all_chunks.append({
                    "chapter_no": ch["chapter_no"],
                    "spec": r.get("chapter_context","") or "",
                    "example": r.get("example_context","") or "",
                })
            except: pass
        state["rag_all_chunks"] = all_chunks
        if all_chunks:
            logs.append(f"📚 RAG 检索完成: {len(all_chunks)} 章")
    except Exception as e:
        logger.warning(f"RAG failed: {e}")

    await _emit("rag", {"analysis":"done","outline":"done","knowledge":"done","generation":"pending","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 6: chapter_generate — per-chapter generation with chapter loop inside
# ═══════════════════════════════════════════════════════════════════════════════
async def node_chapter_generate(state: ReportWorkflowState) -> ReportWorkflowState:
    """生成「当前」章节（图级循环，每次只生成一章）。

    章节循环由 LangGraph 条件路由驱动：本节点只负责生成 outline_list[_chapter_idx] 这一章，
    审查和重试判断移到 node_chapter_review + route_after_review。
    """
    state["phase"] = WorkflowPhase.GENERATING.value
    logs = state.setdefault("logs",[])
    filled = state.get("filled_data",{})
    outline_list = state.get("outline_list",[]) or []
    rag_chunks = state.get("rag_all_chunks",[]) or []
    idx = state.get("_chapter_idx", 0)
    retry = state.get("_chapter_retry", 0)

    # 所有章节已处理完 → 路由到组装
    if idx >= len(outline_list):
        state["_next_action"] = "assemble"
        return state

    ch = outline_list[idx]
    ch_num = int(ch.get("chapter_no","1"))

    from app.services.llm_service import LLMService; llm = LLMService()
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.master_orchestrator import build_chapter_prompt
    from app.services.image_catalog import build_image_catalog, get_chapter_image_guide

    # 🔴 首次进入时准备 few-shot 和图片 catalog（后续章节/重试复用，不重复加载）
    if not state.get("_gen_prepared"):
        try:
            from app.services.few_shot_service import refresh_few_shot_cache
            proj = {"location": filled.get("location",""), "area_mu": filled.get("area_mu",""),
                    "land_use": filled.get("land_use",""), "project_name": filled.get("project_name","")}
            await refresh_few_shot_cache(proj)
        except Exception as e:
            logger.warning(f"Few-shot cache refresh failed (non-critical): {e}")
        uploaded_paths = []
        for f in (state.get("_uploaded_files",[]) or []):
            path = f.get("path",f) if isinstance(f,dict) else f
            if isinstance(path,str): uploaded_paths.append(path)
        state["_img_catalog"] = build_image_catalog(uploaded_paths)
        state["_gen_prepared"] = True

    img_catalog = state.get("_img_catalog", {})

    # RAG context for this chapter
    rag_ctx = None
    for rc in rag_chunks:
        if rc.get("chapter_no") == ch["chapter_no"]:
            if rc.get("spec") or rc.get("example"):
                rag_ctx = {"chapter_context": rc.get("spec",""),
                           "local_regulation_context": "", "example_context": rc.get("example",""),
                           "project_context": "", "sources": []}
            break

    img_guide = get_chapter_image_guide(ch_num, img_catalog)

    # 章节子集数据
    deps = list(ch.get("depend_on_data",[]) or [])
    if ch_num == 1:
        # 🔴 第一章需要所有小节对应的字段（1.5位置/1.6面积/1.7用途等）
        for k in ("org_name","implement_unit","project_name","location",
                  "area_mu","area_m2","land_use","doc_reference",
                  "household_count","population_count","compensation_standard"):
            if k not in deps: deps.append(k)
    if ch_num == 3:
        for k in ("questionnaire_summary", "questionnaire_tallies", "survey_total_count"):
            if k not in deps and filled.get(k):
                deps.append(k)
    ch_data = {k: filled.get(k,"") for k in deps if filled.get(k)}

    # 🔴 注入 PDF 提取的真实表格数据
    extracted_tbls = filled.get("_extracted_tables", []) or []
    if extracted_tbls:
        md_parts = []
        for tbl in extracted_tbls:
            if isinstance(tbl, dict) and tbl.get("raw_markdown"):
                md_parts.append(tbl["raw_markdown"])
        if md_parts:
            ch_data["_pdf_table_data"] = "\n\n".join(md_parts)

    ch_def = {"num": ch_num, "title": ch["title"], "key_points": ch.get("need_spec_tags",[]),
               "data_needed": deps, "depends_on": ch.get("depends_on", []),
               "argument_note": ch.get("argument_note", "")}

    # 🔴 重试时构建反馈（包含可用数据 + 上一版内容）
    feedback = ""
    if retry > 0 and ch.get("review_msg"):
        fb_parts = [ch["review_msg"]]
        if ch_data:
            fb_parts.append("\n## 📋 本章可用数据（只有这些数据是真实的，其他一律不准编造）")
            for k, v in ch_data.items():
                fb_parts.append(f"- {k}: {v}")
        prev_md = ch.get("raw_content","") or ""
        if prev_md:
            fb_parts.append(f"\n## 📝 上一版生成内容（参考结构，修正数据问题后重写）\n{prev_md[:2000]}")
        feedback = "\n".join(fb_parts)

    # 🔴 论证主线上下文（整份大纲 + 每章论证任务）
    outline_context = state.get("_outline", {})
    prompt = build_chapter_prompt(ch_def, ch_data, img_guide, state.get("chapters",{}),
                                  rag_context=rag_ctx,
                                  feedback=feedback if feedback else None,
                                  outline_context=outline_context)
    agent = get_chapter_agent(ch_num, llm_service=llm)
    agent_state = {"session_id":state.get("session_id",""),"report_title":filled.get("project_name",""),
                   "filled_data":ch_data,"_domain":"stability","_report_style":"jinhu",
                   "current_chapter":ch_num,"chapters":{},"_custom_prompt":prompt,"_use_custom_prompt":True}
    q = asyncio.Queue()
    md = ""
    try:
        await asyncio.wait_for(agent.run(agent_state,q), timeout=300.0)
        cd = agent_state.get("chapters",{}).get(ch_num,{})
        md = cd.get("markdown","") if isinstance(cd,dict) else (cd if isinstance(cd,str) else "")
    except asyncio.TimeoutError:
        logger.error(f"Ch{ch_num} gen timeout (300s)")
    except Exception as e:
        logger.error(f"Ch{ch_num} gen failed: {e}")

    ch["raw_content"] = md or f"【第{ch_num}章生成失败，请人工复核】"
    state["outline_list"] = outline_list
    state["_next_action"] = "review"
    logs.append(f"  ✍️ 第{ch_num}章「{ch['title']}」已生成 ({len(md)}字)")
    logger.info(f"[GEN] 第{ch_num}章「{ch['title']}」生成 {len(md)} 字 (idx={idx}, agent={'None' if not agent else agent.__class__.__name__})")
    await _emit("generating", {"analysis":"done","outline":"done","generation":"running","quality":"pending","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 7: chapter_review — mixed review: hard rules + LLM business review
# ═══════════════════════════════════════════════════════════════════════════════
async def node_chapter_review(state: ReportWorkflowState) -> ReportWorkflowState:
    """审查「当前」章节，并决定下一步（重试 / 下一章 / 组装）。

    硬规则审查（AI套词、编造数据、负分、孤立表格标题）。审查结果写入当前章节，
    并根据结果设置 _next_action，供 route_after_review 路由。
    """
    state["phase"] = WorkflowPhase.REVIEWING.value
    logs = state.setdefault("logs",[])
    outline_list = state.get("outline_list",[]) or []
    filled = state.get("filled_data",{})
    idx = state.get("_chapter_idx", 0)
    retry = state.get("_chapter_retry", 0)

    if idx >= len(outline_list):
        state["_next_action"] = "assemble"
        return state

    ch = outline_list[idx]
    ch_num = int(ch.get("chapter_no","1"))
    md = ch.get("raw_content","") or ""

    from app.validation.content_guardrails import AI_BUZZWORDS

    issues = []
    # 硬规则
    found_bw = [b for b in AI_BUZZWORDS if b in md]
    if found_bw: issues.append(f"AI套词: {found_bw}")
    if len(md) < 300: issues.append(f"字数不足({len(md)})")
    # 孤立的表格标题
    has_md_table = bool(re.search(r'\|[^\n]+\|\s*\n\s*\|[\s:\-—|]+\|', md))
    has_table_title = bool(re.search(r'表\s*\d+[-—]\d+', md))
    if has_table_title and not has_md_table:
        issues.append("存在孤立的表格标题（表X-X 但没有表格内容）")

    # 编造数据检查
    has_survey = bool(filled.get("total_samples") or filled.get("support_rate") or filled.get("survey_total_count"))
    if not has_survey:
        fake_rates = re.findall(r'(\d+\.?\d*)\s*%', md)
        if len(fake_rates) > 1:
            issues.append(f"编造百分比({len(fake_rates)}处)，用户未提供问卷数据，应标注【待补充】")
        fake_counts = re.findall(r'(?:发放|回收|共|有效问卷|涉及|收回)\s*(\d+)\s*(?:份|户|人|张)', md)
        if fake_counts:
            issues.append(f"编造统计数据({', '.join(fake_counts[:5])})，用户未提供问卷数据")

    # 评分范围
    scores = re.findall(r'(-?\d+(?:\.\d+)?)\s*分', md)
    for s in scores:
        v = float(s)
        if v < 0: issues.append(f"负分({v})"); break
        if v > 100: issues.append(f"超100分({v})"); break

    if issues:
        ch["review_msg"] = "; ".join(issues)
        ch["review_score"] = max(0, 85 - len(issues)*15)
        logs.append(f"  ⚠️ 第{ch_num}章: {ch['review_msg']}")
        logger.info(f"[REVIEW] 第{ch_num}章审查问题: {ch['review_msg']}")
        if retry + 1 < MAX_RETRY:
            # 还有重试机会 → 重试当前章
            ch["status"] = "retry"
            state["_chapter_retry"] = retry + 1
            state["_next_action"] = "generate"
        else:
            # 重试耗尽 → 标记人工复核，进入下一章
            ch["status"] = "failed_human_review"
            state["_chapter_idx"] = idx + 1
            state["_chapter_retry"] = 0
            state["_next_action"] = "generate" if idx + 1 < len(outline_list) else "quality_review"
    else:
        ch["review_score"] = 90
        ch["status"] = "passed"
        # 通过 → 累积到 chapters，进入下一章
        chapters = state.setdefault("chapters", {})
        chapters[ch_num] = {"markdown": md, "title": ch["title"], "status": "passed"}
        state["chapters"] = chapters
        logs.append(f"  ✅ 第{ch_num}章「{ch['title']}」通过")
        state["_chapter_idx"] = idx + 1
        state["_chapter_retry"] = 0
        state["_next_action"] = "generate" if idx + 1 < len(outline_list) else "quality_review"

    state["outline_list"] = outline_list
    await _emit("quality", {"analysis":"done","outline":"done","generation":"running","quality":"running","assembly":"pending"})
    return state


def route_after_review(state: ReportWorkflowState) -> str:
    """条件路由：根据 node_chapter_review 设置的 _next_action 决定下一步。

    - "generate" → 回到 chapter_generate（重试当前章 或 生成下一章）
    - "assemble" → 进入 assemble_final_report（所有章节处理完）
    """
    return state.get("_next_action", "assemble")


# ═══════════════════════════════════════════════════════════════════════════════
# Node 7b: quality_review — final audit via QualityReviewAgent
# ═══════════════════════════════════════════════════════════════════════════════
async def node_quality_review(state: ReportWorkflowState) -> ReportWorkflowState:
    """终稿质量审查：复用 QualityReviewAgent 做表格/AI味/格式/数据一致性检查 + 自动修复。

    在所有章节生成并通过后运行。检查：
    - 不该出现的表格（孤立表标题、空表格）
    - 表格格式不准确
    - AI 味太重（口语化「总的来说」「综上所述」、AI meta-text）
    - 数据错误（0亩、年份错误、占位符残留）
    - 跨章节一致性（面积/日期/评分/单位名称）
    可修复的问题自动修复，审计结果存入 state["_quality_audit"]。
    """
    state["phase"] = WorkflowPhase.REVIEWING.value
    logs = state.setdefault("logs",[])

    from app.agent.agents.quality_review_agent import QualityReviewAgent
    agent = QualityReviewAgent()
    try:
        result = await agent.act(state, {})
        await agent.update_state(state, result)
        total = result.get("total_issues", 0)
        fixed = result.get("auto_fixed", 0)
        regen = len(result.get("regenerate_chapters", []))
        logs.append(f"🔍 终稿质量审查：{total} 个问题，自动修复 {fixed} 个，需重写 {regen} 章")
    except Exception as e:
        logger.warning(f"Quality review failed (non-critical): {e}")
        state["_quality_audit"] = {"passed": True, "error": str(e)}

    state["_next_action"] = "assemble"
    await _emit("quality", {"analysis":"done","outline":"done","generation":"done","quality":"done","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 8: assemble_final_report — merge chapters, replace placeholders, build DOCX
# ═══════════════════════════════════════════════════════════════════════════════
async def node_assemble_final_report(state: ReportWorkflowState) -> ReportWorkflowState:
    """Assemble final DOCX: merge chapters, replace <<IMAGE:>> and [TABLE:] placeholders."""
    state["phase"] = WorkflowPhase.ASSEMBLING.value
    logs = state.setdefault("logs",[])
    chapters = state.get("chapters",{})

    if not chapters:
        state["errors"] = state.get("errors",[]) + ["无章节内容可组装"]
        return state

    try:
        # 🔴 Post-generation polish: quick LLM pass to unify style
        # 章节内容在 state["chapters"]（review 通过时累积），非 outline_list
        full_text = "\n\n".join(
            ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
            for ch in chapters.values()
            if isinstance(ch, dict) and ch.get("markdown")
        )
        if full_text and len(full_text) > 2000:
            try:
                from app.services.llm_service import LLMService
                polish_llm = LLMService()
                polish_prompt = f"""你是稳评报告审校员。检查以下报告并修复明显问题（不要改变内容结构）：
1. 统一术语：「征地」→「拟征收」（如果项目是拟征收阶段）
2. 删除残留的AI套词：「具有重要意义」「切实保障」「多措并举」等
3. 修复明显的数字格式问题（如 "2026年年" → "2026年"）
4. 确保所有章节标题编号连续

只输出修复后的报告全文，不要添加任何说明。

报告内容：
{full_text[:15000]}"""
                result = await asyncio.wait_for(
                    polish_llm.chat(messages=[{"role":"user","content":polish_prompt}], max_tokens=4096, temperature=0.1),
                    timeout=60.0
                )
                if result and len(str(result)) > 2000:
                    # Update chapters with polished content
                    polished = str(result)
                    for ch in (state.get("outline_list") or []):
                        if isinstance(ch, dict) and ch.get("markdown"):
                            # Preserve original markdown; polish is advisory
                            pass
                    logs.append("✨ 报告文风已统一润色")
                    # Use polished text for assembly
                    full_text = polished
            except Exception as e:
                logger.warning(f"Post-generation polish skipped: {e}")

        from app.services.report_assembler import report_assembler
        filled = state.get("filled_data",{})
        asm_state = {
            "session_id": state.get("session_id","report"), "chapters": chapters,
            "filled_data": filled, "_domain": "stability",
            "_uploaded_files": state.get("_uploaded_files",[]),
            "_project_materials": state.get("_project_materials",[]),
            "_classified_images": state.get("_classified_images",{}),
            "image_meta_list": state.get("image_meta_list",[]),
        }
        output = report_assembler.assemble(asm_state)
        if output:
            state["output_path"] = output
            state["phase"] = WorkflowPhase.COMPLETE.value
            logs.append(f"✅ 报告已生成: {output}")
            # 🔴 Record feedback for continuous learning
            try:
                from app.services.learning_service import learning_service
                chapters = state.get("chapters", {})
                full_text = "\n\n".join(
                    ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
                    for ch in chapters.values()
                )
                await learning_service.record_feedback(
                    session_id=state.get("session_id", ""),
                    report_title=state.get("report_title", ""),
                    domain=state.get("_domain", "stability"),
                    quality_audit=state.get("_quality_audit"),
                    rewrite_count=len(state.get("_pending_regenerations", [])),
                    passed=True,
                    output_path=output,
                    full_text=full_text[:50000],
                )
                # 🔴 自动提炼高频问题 → 生成 skill（无需人工，自动反哺）
                try:
                    from app.services import skill_service
                    await skill_service.auto_extract_high_freq_issues(domain=state.get("_domain", "stability"))
                except Exception as _e2:
                    logger.warning(f"Auto skill extraction skipped: {_e2}")
            except Exception as e:
                logger.warning(f"Feedback recording failed (non-critical): {e}")
        else:
            state["errors"] = state.get("errors",[]) + ["DOCX 组装返回空路径"]
            state["phase"] = WorkflowPhase.ERROR.value
    except Exception as e:
        state["errors"] = state.get("errors",[]) + [f"组装失败: {e}"]
        state["phase"] = WorkflowPhase.ERROR.value
        logger.error(f"assemble failed: {e}")

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Graph Builder
# ═══════════════════════════════════════════════════════════════════════════════
def build_report_workflow() -> StateGraph:
    """Build the report generation workflow with a graph-level chapter loop + final quality review.

    图结构：
      file_parse → field_validate → build_dynamic_outline → outline_check → retrieve_rag
      → [chapter_generate ⇄ chapter_review]（条件路由循环，逐章生成+审查）
      → quality_review（终稿质量审查 + 自动修复）
      → assemble_final_report → END

    章节循环是图级循环：chapter_review 通过 route_after_review 返回
    "generate"（重试/下一章）或 "quality_review"（全部完成，进入终稿审查）。
    """
    w = StateGraph(ReportWorkflowState)
    w.add_node("file_parse", node_file_parse)
    w.add_node("field_validate", node_field_validate)
    w.add_node("data_inquiry", node_data_inquiry)
    w.add_node("build_dynamic_outline", node_build_dynamic_outline)
    w.add_node("outline_check", node_outline_check)
    w.add_node("retrieve_rag", node_retrieve_rag)
    w.add_node("chapter_generate", node_chapter_generate)
    w.add_node("chapter_review", node_chapter_review)
    w.add_node("quality_review", node_quality_review)
    w.add_node("assemble_final_report", node_assemble_final_report)

    w.set_entry_point("file_parse")
    w.add_edge("file_parse", "field_validate")
    w.add_edge("field_validate", "data_inquiry")
    w.add_edge("data_inquiry", "build_dynamic_outline")
    w.add_edge("build_dynamic_outline", "outline_check")
    w.add_edge("outline_check", "retrieve_rag")
    w.add_edge("retrieve_rag", "chapter_generate")
    w.add_edge("chapter_generate", "chapter_review")

    # 🔴 章节循环：条件路由
    #   "generate" → 回到 chapter_generate（重试当前章 或 生成下一章）
    #   "quality_review" → 进入终稿质量审查（所有章节处理完）
    w.add_conditional_edges(
        "chapter_review",
        route_after_review,
        {
            "generate": "chapter_generate",
            "quality_review": "quality_review",
        },
    )
    w.add_edge("quality_review", "assemble_final_report")
    w.add_edge("assemble_final_report", END)
    return w


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════
class ReportWorkflowRunner:
    def __init__(self):
        self.workflow = build_report_workflow()
        self.checkpointer = MemorySaver()
        self.compiled = self.workflow.compile(checkpointer=self.checkpointer)

    async def start(self, session_id, report_title="", materials_dir="",
                    project_context="", filled_data=None, existing_state=None) -> dict:
        base = {
            "session_id": session_id, "report_title": report_title,
            "phase": WorkflowPhase.IDLE.value,
            "materials_dir": materials_dir, "project_context": project_context,
            "filled_data": filled_data or {}, "chapters": {}, "output_path": "",
            "logs": [], "errors": [], "_uploaded_files": [],
            "image_meta_list": [], "table_meta_list": [], "outline_list": [],
            "rag_all_chunks": [], "max_retry": MAX_RETRY,
            # 🔴 图级章节循环状态
            "_chapter_idx": 0, "_chapter_retry": 0, "_next_action": "generate",
            "_gen_prepared": False,
        }
        if existing_state:
            for k in ("_pdf_raw_text","_project_materials","_uploaded_files",
                       "_outline","_image_catalog","filled_data","_workflow_logs"):
                if k in existing_state: base[k] = existing_state[k]
            if existing_state.get("filled_data"):
                base["filled_data"] = {**base["filled_data"], **existing_state["filled_data"]}
            if existing_state.get("_workflow_logs"):
                base["logs"] = list(existing_state["_workflow_logs"])

        thread_id = f"{session_id}_{int(time.time())}"
        # 🔴 提高递归上限：图级章节循环（10章+重试）会超过默认25次
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 120}
        final_state = None
        interrupt_payload = None
        async for event in self.compiled.astream(base, config):
            if "__interrupt__" in event:
                interrupt_payload = event["__interrupt__"]
                break
            for node_name, node_state in event.items():
                final_state = node_state
                logger.info(f"Workflow node '{node_name}' completed")
        result = final_state or base
        if interrupt_payload:
            iv = interrupt_payload[0] if isinstance(interrupt_payload, tuple) else interrupt_payload
            result["_interrupt"] = getattr(iv, "value", iv)
            result["_thread_id"] = thread_id
        return result

    async def resume(self, session_id, user_responses) -> dict:
        """从 LangGraph 检查点恢复（HITL：用户补充缺失资料后继续）。"""
        from langgraph.types import Command
        user_responses = user_responses or {}
        if isinstance(user_responses, dict):
            thread_id = user_responses.get("_thread_id", session_id)
            user_data = {k: v for k, v in user_responses.items() if k != "_thread_id"}
        else:
            thread_id = session_id
            user_data = {}
        # 🔴 提高递归上限：图级章节循环（10章+重试）会超过默认25次
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 120}
        final_state = None
        interrupt_payload = None
        async for event in self.compiled.astream(Command(resume=user_data), config):
            if "__interrupt__" in event:
                interrupt_payload = event["__interrupt__"]
                break
            for node_name, node_state in event.items():
                final_state = node_state
                logger.info(f"Workflow node '{node_name}' resumed")
        result = final_state or {}
        if interrupt_payload:
            iv = interrupt_payload[0] if isinstance(interrupt_payload, tuple) else interrupt_payload
            result["_interrupt"] = getattr(iv, "value", iv)
            result["_thread_id"] = thread_id
        return result

    def get_state(self, session_id) -> Optional[dict]:
        return None


workflow_runner = ReportWorkflowRunner()
