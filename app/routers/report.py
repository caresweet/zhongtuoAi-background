"""Report Generation API routes — /api/v1/reports/*

Rewritten for RAG + LangGraph chapter-by-chapter streaming generation.
Replaces the old template fill-in-the-blanks workflow.
"""

import json
import sys
import os
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, List
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.database.auth_db import get_auth_db
from app.deps import get_current_user, is_staff
from app.models.user import User

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
# 🔴 报告域相关取值（类型名/模板名）一律经此缝隙按域取，调用点不写 `if domain == ...`。
from app.agent.chapter_catalog import (
    report_kind_name as _kind_name, template_name as _catalog_template_name,
    chapter_title as _chapter_title,
)
from app.utils.sse import (
    sse_event, sse_message, sse_progress, sse_status,
    sse_data_filled, sse_error, sse_complete,
    sse_chapter_start, sse_chapter_stream, sse_chapter_complete,
    sse_thinking, sse_thinking_stream, sse_phase_change,
    sse_placeholder_filled, sse_collecting_question,
    sse_step_transition, sse_agent_status, sse_step_progress_sync,
)

router = APIRouter(prefix="/api/v1/reports", tags=["报告生成"])

# 🔴 WebSocket 路由必须挂在**不带 HTTP 鉴权依赖**的独立 router 上，不能复用上面这个。
#    为什么：`main.py` 用 `include_router(report.router, dependencies=[Depends(get_current_user)])`
#    装配，而 router 级 dependencies 会**同样套到 `@router.websocket` 上**。
#    浏览器 WebSocket API **不支持自定义请求头**，握手时带不了 `Authorization`，
#    于是 `get_current_user` 直接判 401，握手被拒 —— 前端 `onerror` 报
#    「There was a bad response from the server」。实测直连后端就是 HTTP 401。
#    WS 的鉴权改由 `authenticate_websocket()` 走 `?token=` query 参数自查（见下方 ws_chat），
#    未带合法 token 的连接会被 `close(4001)`，**不等于放空鉴权**。
#    ⚠️ 动这里要同步 `main.py` 的 `WS_ROUTERS`。
ws_router = APIRouter(prefix="/api/v1/reports")

logger = logging.getLogger(__name__)


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


def _has_real_data(state: dict, attachments: list | None = None) -> bool:
    """会话里有没有**真材料**（不是用户敲进去的几个字）。

    🔴 这是「能不能生成」的门槛判据。原先只存在于**聊天**那条路里（内联一段），
       而「🚀 开始生成报告」按钮走的是 `/workflow/start`，**完全不过这道门** ——
       一个文件都不传也能一路跑完出报告，正文靠模型自由发挥。
       现在两条路共用这一个定义，免得两处判据各自漂移。
    """
    if not isinstance(state, dict):
        return False
    filled = state.get("filled_data") or {}
    real_filled = {k: v for k, v in filled.items() if not str(k).startswith("_") and v}
    summary = state.get("_project_material_summary") or {}
    return bool(
        len(real_filled) >= 1
        or state.get("_pdf_texts")
        or (summary.get("facts") or {})
        or summary.get("completed_files", 0) > 0
        or state.get("_project_materials")
        or state.get("_uploaded_files")
        or attachments  # 随本条消息一起发来的文件
    )


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
    # 🔴 预公告解析要**跨材料挑最好的那一份**，不能「先到先得」：
    #    勘测定界报告/座谈会记录里也常出现「根据《中华人民共和国…」这类残缺引用，
    #    谁排前面谁就把 purpose_text 之类的字段占了（曾导致征收目的只剩 10 个字）。
    _ann_keys = ("doc_reference", "org_name", "location", "publish_date",
                 "announcement_title", "preamble_text", "legal_basis_cited",
                 "purpose_text", "purpose_legal_basis", "scope_text",
                 "notice_period_text", "notice_date", "notice_end_date",
                 "work_arrangement_text", "work_scope", "other_matters_text",
                 "contact_name", "contact_phone", "attachment_note",
                 "map_plots", "map_scale", "map_date")
    _ann_best: dict = {}
    _ann_best_n = -1
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
        # Location / 文号（🔴 通用：不写死「X街道/X镇」行政区划层级，不写死「拟征告」前缀，
        #    权威来源＝用户上传的预公告逐字文本）
        # 🔴 预公告**完整解析**：正文五个部分的段落都是有效内容（征收目的/征收范围/
        #    公告期限/工作安排/其他事项），不能只取文号与位置。
        from app.services.announcement_extract import (
            extract_location, extract_doc_reference, parse_announcement,
        )
        _ann = parse_announcement(txt)
        # 谁解析出的字段多，谁就是真正的预公告（后面统一写盘）
        _n = sum(1 for _v in _ann.values() if _v)
        if _n > _ann_best_n:
            _ann_best, _ann_best_n = _ann, _n
        if not _ann:
            _loc = extract_location(txt)
            if _loc:
                extracts['location'] = _loc
            _doc = extract_doc_reference(txt)
            if _doc:
                extracts['doc_reference'] = _doc
        # Land use（要求冒号，避免在「土地用途 总面积」这种表头上误抓成"总面积"）
        m = _re_data.search(r'(?:土地用途|用地性质|用途|用于)[：:]\s*(\S{2,20})', txt)
        if m and m.group(1) not in ("总面积", "面积", "合计"):
            extracts['land_use'] = m.group(1)
        # 写进 filled_data，来源标 **material**
        #
        # 🔴 这里原来打的是 `_locked_{k}`，注释写「user-provided」—— **是错的**：
        #    这些值是从公告 PDF 里**正则抓出来的**，不是用户填的。
        #    `_locked_` 一打，后面**勘测定界的实测面积就再也覆盖不了它**，
        #    于是报告里公告概数与实测数并存、两张表两个口径。
        #    正确做法：来源记 `material`（`field_sources` 有优先级 user>material>ai>system），
        #    `_locked_` 只留给**用户在界面填/确认过**的值（见 `resume_workflow`）。
        filled = state.setdefault("filled_data", {})
        _written = []
        for k, v in extracts.items():
            if v and k not in filled:
                filled[k] = str(v)
                _written.append(k)
        if _written:
            try:
                from app.services.field_sources import mark
                mark(filled, "material", *_written)
            except Exception:
                pass
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
    _fact_keys = []
    for key, value in facts.items():
        if value and not key.startswith("_"):
            filled[key] = value
            _fact_keys.append(str(key))
    # 🔴 来源=材料识别。这里是**材料摄入的总入口**，一处覆盖全部抽取字段。
    try:
        from app.services.field_sources import mark
        mark(filled, "material", *_fact_keys)
    except Exception:
        pass

    # 🔴 预公告：对**拼接全文**再解析一次，取「字段最全」的结果覆盖上面可能被残缺引用
    #    占掉的字段（extract 逐份材料时预公告常常只是片段，facts 又会把结果盖回去）。
    #    「更长者胜」——残缺值一律让位给完整值。
    try:
        from app.services.announcement_extract import parse_announcement as _pa
        _full_txt = state.get("_pdf_raw_text") or ""
        _ann_full = _pa(_full_txt) if _full_txt else {}
        _ann_src = _ann_full if sum(1 for v in _ann_full.values() if v) >= _ann_best_n else _ann_best
        for _k in _ann_keys:
            _v = _ann_src.get(_k)
            if _v and len(str(_v)) >= len(str(filled.get(_k) or "")):
                filled[_k] = str(_v)
                filled[f"_locked_{_k}"] = True
                try:
                    from app.services.field_sources import mark
                    mark(filled, "material", _k)
                except Exception:
                    pass
    except Exception as _ae:
        logger.warning(f"预公告全文复核跳过: {_ae}")

    # 🔴 稳评实施单位（责任单位 ≠ 实施单位，LLM 常把两者混为一谈）。
    #    取**本报告所属公司**的单位名；没有公司就**留空**，报告里显示「待填写」——
    #    绝不写死成我们自己的名字：外部用户的报告抬头不能被印成我们的。
    if not filled.get("implement_unit"):
        from app.services.company_service import load_company
        _co = await load_company(state.get("_company_id"))
        _unit = str((_co or {}).get("implement_unit") or "").strip()
        if _unit:
            filled["implement_unit"] = _unit
            filled["_locked_implement_unit"] = True

    # 🔴 抽取图（PDF 内嵌图）**不整体并入**——这是「报告 700 多页」的根因。
    #    但**按类型限量的关键图**要进报告：预公告附图（位置示意图）、勘测定界图、盖章公告页。
    extracted_imgs = facts.get("_extracted_images", [])
    if extracted_imgs:
        filled["_extracted_images"] = extracted_imgs
    try:
        from app.services.pdf_key_images import collect_key_images
        _key_imgs = collect_key_images(existing_materials)
        if _key_imgs:
            filled["_pdf_key_images"] = _key_imgs
            # 位置示意图 / 勘测定界图 → 第3章（三）；盖章公告页 → 附件
            _paths = [k["path"] for k in _key_imgs if k.get("chapter") == 6]
            _appendix = [k["path"] for k in _key_imgs if k.get("chapter") == 0]
            # 🔴 `_uploaded_files` 全程统一为 dict（含 original_name）——混入裸路径会
            #    让去重失效、并让图片归属判断丢掉文件夹线索，最后全掉进附件。
            if _paths:
                state.setdefault("_uploaded_files", [])
                _exist = {str(x.get("path") if isinstance(x, dict) else x)
                          for x in state["_uploaded_files"]}
                for p in _paths:
                    if p not in _exist:
                        state["_uploaded_files"].append(
                            {"path": p, "original_name": os.path.basename(str(p))})
                        _exist.add(p)
            if _appendix:
                state.setdefault("_appendix_files", [])
                for p in _appendix:
                    if p not in state["_appendix_files"]:
                        state["_appendix_files"].append(p)
            logger.info(f"PDF 关键图 {len(_key_imgs)} 张已接入（正文 {len(_paths)} / 附件 {len(_appendix)}）")
    except Exception as _ke:
        logger.warning(f"PDF 关键图提取跳过: {_ke}")

    # 🔴 Copy extracted tables for table_registry to use
    extracted_tables = list(facts.get("_extracted_tables", []) or [])
    # 🔴 勘测定界报告：用专门解析器产出规范表格（表3-1 汇总表 + 土地分类面积表），
    #    并在事实卡里补上 坐落/面积/用途/权属（比逐页残缺提取可靠）。
    #    🔴 走 `collect_land_survey`（扫**上传清单**）而不是只扫 `existing_materials`：
    #       勘测定界常常没被登记进 `_project_materials`（本会话 177 个上传里只登了 2 个），
    #       只扫后者会漏；该函数内部还会把「相对 storage 的路径」还原成真实路径。
    #       脚本路径（`scripts/gen_full_report.py`）调的是**同一个函数**，两条路不分叉。
    try:
        from app.services.land_survey_parser import collect_land_survey
        _sur_tables, _sur = collect_land_survey(state)
        if _sur:
            extracted_tables.extend(_sur_tables)
            # 🔴 **面积以勘测定界为准**（业主：「统一都直接使用勘测定界报告中的数据」）。
            #    勘测定界是**实测**，公告里的是概数。原先是「只有 filled 为空才取勘测定界」，
            #    而公告正则会先把值写进去 → 勘测定界永远排不上，报告里两张表两个口径。
            #    ⚠️ 用户**在界面填过**的（`_confirmed_`）不动 —— 那才是权威。
            try:
                from app.services.field_sources import mark
            except Exception:
                mark = None

            _m2 = _sur.get("area_m2")
            if _m2 and not filled.get("_confirmed_area_m2"):
                filled["area_m2"] = str(_m2)
                # 勘测定界报告**只写平方米**，亩由系统换算（1 亩 = 666.67 ㎡）
                try:
                    filled["area_mu"] = f"{round(float(_m2) / 666.67, 4):g}"
                except (TypeError, ValueError):
                    pass
                if mark:
                    try: mark(filled, "material", "area_m2", "area_mu")
                    except Exception: pass

            for _k in ("location", "land_use", "boundary_points"):
                _v = _sur.get(_k)
                if _v and str(_v).strip() and not str(filled.get(_k, "")).strip():
                    filled[_k] = str(_v)
                    if mark:
                        try: mark(filled, "material", _k)
                        except Exception: pass
            if _sur.get("owner_units"):
                # 🔴 权属单位去重：`owner_units` 是**按二级地类逐行**给出的，
                #    同一权属会出现多次（实测 2 个地块 → 6 条重复记录）。
                #    不去重的话正文会写成「八里居委会集体、八里居委会汪庄组、
                #    八里居委会朱庄组、八里居委会集体、…」这种复读。
                _owners: list = []
                for _x in _sur["owner_units"]:
                    _s = str(_x or "").strip()
                    if _s and _s not in _owners:
                        _owners.append(_s)
                filled["_land_owners"] = _owners
                # 🔴 权属构成**文本**（第6章 1.5「涉及村组」/ 第10章合法性论证用）——
                #    此前只存 list，全仓库没有任何读取点，等于解析了没人用。
                _ow = "、".join(_owners)
                if _ow:
                    filled["land_ownership"] = _ow
                    if mark:
                        try: mark(filled, "material", "land_ownership")
                        except Exception: pass
            # 🔴 地类明细一律来自勘测定界（按源表第二级地类，**合计对得上**）
            if _sur.get("by_category"):
                filled["_land_by_category"] = _sur["by_category"]
                # 🔴 地类构成**文本** —— 第6章 spec 的 data 依赖 `land_classification`，
                #    而此前**全仓库无人写这个键**（勘测定界只写 `_land_by_category`），
                #    第6章「涉及村组 + 地类构成」拿不到数据只能自圆其说。
                _ct = "、".join(f"{k}{v}㎡" for k, v in _sur["by_category"].items()
                                if str(k).strip())
                if _ct:
                    filled["land_classification"] = _ct
                    if mark:
                        try:
                            mark(filled, "material", "land_classification", "land_ownership")
                        except Exception:
                            pass
            if _sur.get("by_sub_category"):
                filled["_land_by_sub_category"] = _sur["by_sub_category"]
            logger.info(f"勘测定界已解析：{len(_sur.get('plots',[]))} 地块 / "
                        f"{len(_sur.get('classifications',[]))} 权属 / 面积 {_sur.get('area_m2')}")
    except Exception as _se:
        logger.warning(f"勘测定界解析跳过: {_se}")
    if extracted_tables:
        filled["_extracted_tables"] = extracted_tables

    # 🔴 用勘测定界表(表1-1)纠正被正则抓错的字段（按表头语义取值，不锁位置）：
    #    land_use（治「总面积」误抓）、location（取【土地坐落】列）、project_name（由文号派生）
    try:
        from app.services.field_fixer import fix_fields_from_tables
        fix_fields_from_tables(filled, extracted_tables)
    except Exception:
        pass

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

    # 🔴 材料字段**别名归一**：材料侧抽出来的键名（`responsible_unit` / `publish_date`）
    #    与报告字段名（`org_name` / `notice_publish_date`）不是一套，弹窗的「缺失判断」
    #    只认规范键 —— 归一键不补，材料里明明有的值照样会再问用户一遍。
    try:
        from app.services.field_resolver import canonicalize_material
        _canon = canonicalize_material(filled)
        if _canon:
            state.setdefault("logs", []).append(
                "🔗 材料字段已归一到报告字段：" + "、".join(_canon))
    except Exception as _ce:
        logger.warning(f"字段别名归一跳过: {_ce}")

    # 🔴 **关键信息识别核验** —— 直接回答「用户到底有没有正确上传」：
    #    已认出哪些关键信息（带值）、哪些没认到（生成时会逐项问）。
    #    此前少传/传错时系统一声不吭，用户只能等生成到一半被弹窗追问才知道材料没传对。
    try:
        from app.services.material_ingestion_service import (
            material_ingestion_service as _mis, KEY_RECOGNITION_FIELDS,
        )
        _rep = _mis.recognition_report(filled)
        _rec = _rep.get("recognized", []) or []
        _miss = _rep.get("missing", []) or []
        _total = len(KEY_RECOGNITION_FIELDS)
        if _rec:
            _show = "、".join(f"{i['label']}＝{i['value']}" for i in _rec[:8])
            state.setdefault("logs", []).append(
                f"✅ 材料识别：已认出 {len(_rec)}/{_total} 项关键信息 —— {_show}"
                + ("…" if len(_rec) > 8 else ""))
        if _miss:
            state.setdefault("logs", []).append(
                "⚠️ 材料中未识别到（生成时会逐项询问，可提前补齐）："
                + "、".join(i["label"] for i in _miss))
        else:
            state.setdefault("logs", []).append(f"✅ 材料识别：{_total} 项关键信息全部认到")
        state["_recognition_report"] = _rep
    except Exception as _re_:
        logger.warning(f"识别核验报告生成跳过: {_re_}")

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


@router.post("/reviews/auto-extract", response_model=ApiResponse)
async def auto_extract_skills(request: dict):
    """自动提炼高频问题 → 生成 skill（从生成反馈的质量审计中统计高频问题）。"""
    from app.services import skill_service
    domain = str(request.get("domain", "stability") or "stability")
    min_count = int(request.get("min_count", 3) or 3)
    result = await skill_service.auto_extract_high_freq_issues(domain, min_count)
    return ApiResponse(message="自动提炼完成", data=result)


# ═══════════════════════════════════════════════════════════════
# 用户报告要求（skill_type='user_requirement'，无条件注入生成）
# ═══════════════════════════════════════════════════════════════
@router.get("/reviews/requirements", response_model=ApiResponse)
async def get_requirements(domain: str = "stability"):
    """获取用户对生成报告的要求列表。"""
    from app.services import skill_service
    reqs = await skill_service.get_user_requirements(domain)
    return ApiResponse(message="用户报告要求", data={"requirements": reqs})


@router.post("/reviews/requirements", response_model=ApiResponse)
async def add_requirements(request: dict):
    """新增用户对生成报告的要求。

    request: {domain, requirements: [str]} 或 {domain, requirement: str, correction: str}
    """
    from app.services import skill_service
    domain = str(request.get("domain", "stability") or "stability")
    added_ids = []
    count = 0
    # 支持批量数组 + 单条两种形式
    batch = request.get("requirements") or []
    if not isinstance(batch, list):
        batch = []
    if request.get("requirement"):
        batch.append(request.get("requirement"))
    for item in batch:
        if not isinstance(item, str) or not item.strip():
            continue
        result = await skill_service.add_user_requirement(item, domain)
        if result.get("added"):
            count += 1
            if result.get("id"):
                added_ids.append(result["id"])
    return ApiResponse(
        message=f"已添加 {count} 条报告要求" if count else "无新要求（可能已存在）",
        data={"added": count, "ids": added_ids},
    )


@router.delete("/reviews/requirements/{skill_id}", response_model=ApiResponse)
async def delete_requirement(skill_id: int, domain: str = "stability"):
    """删除一条用户报告要求（软删除）。"""
    from app.services import skill_service
    result = await skill_service.delete_user_requirement(skill_id, domain)
    return ApiResponse(message=result.get("message", ""), data=result)


@router.post("/generate/start", response_model=ApiResponse)
async def start_generation(
    request: StartGenerationRequest,
    user: User = Depends(get_current_user),
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
    template_path = ""
    templates = []  # Initialize before try block

    # Resolve and pin the report domain before any template/knowledge lookup.
    # 走域注册表：explicit 命中已知域即用它，否则按关键词识别，最后回落默认域。
    user_msg = (request.initial_message or "").strip()
    explicit_intent = (request.domain or request.intent or "").strip()
    from app.domains import get_domain, detect_domain

    domain_id = detect_domain(message=user_msg, explicit=explicit_intent or None)
    domain_cfg = get_domain(domain_id)      # 未知时自动回落默认域

    # 模板显示名按域取 —— 收口在 chapter_catalog（复垦不得顶着稳评的「十四章」字样）。
    template_name = _catalog_template_name(domain_id)

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
        # 🔴 报告归属必须在**建会话**时记下（后台任务里拿不到当前用户）
        user_id=str(user.id),
        company_id=user.company_id,
    )
    # Store report style preference
    session.state["_report_style"] = getattr(request, "style", None) or "jinhu"
    # 🔴 公司归属：报告里的公司简介/编写单位/资质证书按它取。为空 → 全部「待填写」。
    session.state["_company_id"] = user.company_id

    # Initialize state with new AgentState — report_title starts empty,
    # will be collected from user in first interaction
    # 🔴 章集合 / 兜底标题 / 模板名按域在建 state 时就定好：复垦不得长出稳评的 14 章。
    state = create_initial_state(
        session_id=session.session_id,
        report_title="",  # Will be collected via first chat message
        project_context=request.initial_message or "",
        domain=domain_id,
    )

    # Set template info in state（template_name 已由 create_initial_state 按域写入，见上）
    state["template_id"] = template_id
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
        user: User = Depends(get_current_user),
):
    """SSE streaming endpoint for chapter generation and user feedback.

    This is the main interaction endpoint. User messages can be:
    - Project information (initial setup)
    - Chapter feedback (approve/revise/skip)

    The response is an SSE stream with chapter content.
    """
    session = report_service.get_session_for_user(session_id, user)
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

                # 🔴 「标题尚未被用户填过」的判据按域取：兜底标题即该域的报告类型名
                #    （稳评「社会稳定风险评估报告」/ 复垦「土地复垦方案」）。写死稳评名
                #    会让复垦的兜底标题挡掉用户首条消息里的标题录入。
                _default_title = _kind_name(
                    session.state.get("_domain") or session.state.get("_conversation_domain")
                )
                if (not current_title or current_title.strip() == "" or current_title == _default_title) and _is_likely_title:
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
                            has_real_data = _has_real_data(session.state, attachments)

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

            # 保留用户上传原件：连接断开 ≠ 删除资料（原件供后续重新生成/审计复用）
            logger.info("SSE disconnect: user uploads kept")

            return  # Don't yield after disconnect — client is gone

        except Exception as e:
            session.state.pop("_stream_queue", None)
            import logging, traceback
            logger = logging.getLogger(__name__)
            logger.error(f"Chat error: {type(e).__name__}: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")

            # 保留用户上传原件：处理出错不回滚删除，避免资料丢失
            logger.error("Chat error: user uploads kept for retry")

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


# ═══════════════════════════════════════════════════════════════════════════════
# Status & Management Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/generate/{session_id}/status", response_model=ApiResponse)
async def get_generation_status(session_id: str, user: User = Depends(get_current_user)):
    """Get current generation status with chapter progress."""
    session = report_service.get_session_for_user(session_id, user)
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
async def get_chapters(session_id: str, user: User = Depends(get_current_user)):
    """Get all chapter contents and statuses."""
    session = report_service.get_session_for_user(session_id, user)
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
async def get_conversation(session_id: str, user: User = Depends(get_current_user)):
    """Get full conversation history."""
    session = report_service.get_session_for_user(session_id, user)
    messages = report_service.get_conversation(session)
    return ApiResponse(data=messages)


@router.post("/generate/{session_id}/skip", response_model=ApiResponse)
async def skip_current(session_id: str, user: User = Depends(get_current_user)):
    """Skip the current chapter (mark as approved without changes)."""
    session = report_service.get_session_for_user(session_id, user)
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
async def retry_generation(session_id: str, user: User = Depends(get_current_user)):
    """Retry the current chapter generation."""
    session = report_service.get_session_for_user(session_id, user)
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
async def cancel_generation(session_id: str, user: User = Depends(get_current_user)):
    """Cancel the running generation — stops workflow, OCR, and cleans up."""
    import logging
    _log = logging.getLogger(__name__)

    session = report_service.get_session_for_user(session_id, user)
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

    # 5. Clean up temp files only —— 用户上传原件一律保留（取消 ≠ 删资料，
    #    否则再次生成/编辑时现场照片等材料将永久丢失）
    file_service.cleanup_temp(session_id)
    _log.info(f"Cancelled session {session_id}: temp cleaned, user uploads kept")

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
async def validate_report_format(session_id: str, user: User = Depends(get_current_user)):
    """Validate the generated report's format against DB32/T4013-2021 standards."""
    session = report_service.get_session_for_user(session_id, user)
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


async def _bg_questionnaire(session):
    """后台：解析座谈会问卷（第9章表9-1/9-2、附件2、评分支持率的数据源）。

    🔴 触发点在**资料上传时**，不是在生成期间 —— 视觉解析约 10 分钟且吃 TPM，
        放到生成期间会和逐章 LLM 抢额度、把生成拖慢。
        之前这条路是界面上的一个「📊 解析座谈会问卷」按钮，已删除：它正是
        「问卷数据：未解析」永远停在那儿的来源（判据 `find_symposium_pdf` 恒返回空）。
    命中缓存（`storage/questionnaire_cache`，按 PDF 指纹）时秒回，不重复烧 token。
    """
    import logging as _log
    _lg = _log.getLogger(__name__)
    from app.services.ws_manager import ws_manager
    sid = session.state.get("session_id", "")
    try:
        from app.services.survey_data import ensure_questionnaire_result
        res = await ensure_questionnaire_result(session.state)
        if res and res.get("total_samples"):
            session.state["_questionnaire_result"] = res
            report_service._save_sessions()
            await ws_manager.send(sid, "log", {
                "message": f"📊 座谈会问卷已解析：{res['total_samples']} 份 / "
                           f"支持率 {res.get('support_rate', 0)}%"})
            _lg.info(f"[问卷解析] 完成，{res['total_samples']} 份")
    except Exception as e:
        _lg.warning(f"[问卷解析] 后台解析失败（不影响生成）: {e}")


@router.post("/generate/{session_id}/upload", response_model=ApiResponse)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
        user: User = Depends(get_current_user),
):
    import logging as _ulog
    _ul = _ulog.getLogger(__name__)
    _ul.info(f"[UPLOAD] 收到文件: {file.filename} ({file.content_type}), session={session_id[:12]}")
    session = report_service.get_session_for_user(session_id, user)
    result = await file_service.save_attachment(file)
    _ul.info(f"[UPLOAD] 保存完成: {result['relative_path']} ({result.get('size_bytes',0)} bytes)")

    # Track file in session state
    uploaded_files = session.state.setdefault("_uploaded_files", [])
    # 🔴 浏览器上传文件夹时 filename 是相对路径（如「公示照片4/微信图片_x.jpg」）——
    #    把**文件夹名并入文件名**，保住「公示/现场/座谈/专家评审」这类分类线索；
    #    否则存储名是 uuid，正文图片归属判断全落空、图片只能全塞附件。
    _orig = str(result.get("original_name") or result["relative_path"].split("/")[-1])
    _orig = _orig.replace("\\", "/")
    if "/" in _orig:
        _parts = [x for x in _orig.split("/") if x and x not in (".", "..")]
        _orig = "_".join(_parts[-2:]) if len(_parts) >= 2 else _parts[-1]
    uploaded_files.append({
        "path": result["relative_path"],
        "original_name": _orig,
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
            # 🔴 座谈会/问卷扫描件 → 后台解析问卷统计（第9章表9-1/9-2、附件2、评分支持率）
            from app.services.survey_data import looks_like_symposium_pdf
            if looks_like_symposium_pdf(_orig):
                _q_task = _bg_asyncio.create_task(_bg_questionnaire(session))
                _bg_tasks.append(_q_task)
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
async def get_session_messages(session_id: str, user: User = Depends(get_current_user)):
    """Get conversation messages for a session (restore on page refresh)."""
    session = report_service.get_session_for_user(session_id, user)
    messages = session.state.get("messages", [])
    return ApiResponse(data={"messages": messages, "session_id": session_id})


@router.get("/generate/{session_id}/materials", response_model=ApiResponse)
async def get_material_summary(session_id: str, user: User = Depends(get_current_user)):
    """Get analysis summary for uploaded project materials."""
    session = report_service.get_session_for_user(session_id, user)
    return ApiResponse(data=session.state.get("_project_material_summary", {}))


@router.get("/generate/{session_id}/quality", response_model=ApiResponse)
async def get_quality_report(session_id: str, user: User = Depends(get_current_user)):
    """Get T1-T4 quality validation report for the generated report.

    Returns the four-dimensional quality score (结构完整性/内容充实度/格式规范性/数据准确性)
    with detailed checks and recommendations.
    """
    session = report_service.get_session_for_user(session_id, user)
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


def _jsonable(obj, _depth: int = 0):
    """把会话 state 转成可 JSON 化的结构（脚本走 HTTP 取数要用）。

    `state` 里混着 Path / set / datetime / 自定义对象 —— 直接返回会让 FastAPI
    序列化失败。这里递归降级：认不出的类型一律 `str()`，**不丢键**。
    """
    import datetime as _dt
    from pathlib import Path as _P
    if _depth > 12:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, _P):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return [_jsonable(x, _depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x, _depth + 1) for x in obj]
    try:
        import json as _j
        _j.dumps(obj)
        return obj
    except Exception:
        return str(obj)


@router.get("/generate/{session_id}/state", response_model=ApiResponse)
async def get_session_state(session_id: str, user: User = Depends(get_current_user)):
    """🔴 **给脚本用的会话状态读取口**（`gen_full_report.py` 等）。

    为什么需要：脚本原来读自己那份 `storage/tmp/review_state.pkl`，与界面会话是
    **两份互不同步的状态** —— 实测用户在界面弹窗里填的 15 个日期，生成时全丢，
    报告里印的还是材料识别出来的旧日期。改成按 `session_id` 从服务器取，
    界面与脚本共用同一份事实源。

    返回整个 state（含 `_` 开头的键 —— `_uploaded_files` / `_extracted_tables`
    这些脚本要用），只做 JSON 降级，不裁剪。
    """
    session = report_service.get_session_for_user(session_id, user)
    return ApiResponse(data={"session_id": session_id,
                             "state": _jsonable(dict(session.state or {}))})


@router.get("/generate/{session_id}/recognition", response_model=ApiResponse)
async def get_recognition_report(session_id: str, user: User = Depends(get_current_user)):
    """🔴 **上传核验**：材料里认到了哪些关键信息、还缺哪些 —— 回给前端弹通知。

    为什么单独开一个口：日志面板（`log-panel`）只对**内部人员**可见，
    外部用户上传完材料**看不到任何识别结果**，少传/传错时只能等生成到一半
    被弹窗逐项追问。这个接口把「到底有没有正确上传」直接答给用户。

    取值优先级：用户自己填/确认的值 > 材料识别值（与正文取值口径一致）。
    """
    session = report_service.get_session_for_user(session_id, user)
    st = dict(session.state or {})

    from app.services.material_ingestion_service import (
        material_ingestion_service as _mis, KEY_RECOGNITION_FIELDS,
    )
    src: dict = {}
    for k, v in (st.get("_project_material_facts") or {}).items():
        if v not in (None, "", [], {}):
            src[k] = v
    for k, v in (st.get("filled_data") or {}).items():
        if not str(k).startswith("_") and v not in (None, "", [], {}):
            src.setdefault(k, v)

    rep = _mis.recognition_report(src)
    return ApiResponse(message="识别核验完成", data={
        "recognized": rep.get("recognized", []),
        "missing": rep.get("missing", []),
        "total": len(KEY_RECOGNITION_FIELDS),
        "uploaded_files": len((st.get("_uploaded_files") or [])),
        "material_files": len((st.get("_project_materials") or [])),
    })


@router.get("/generate/{session_id}/gaps", response_model=ApiResponse)
async def get_session_gaps(session_id: str, user: User = Depends(get_current_user)):
    """缺口清单 —— 「这章缺什么、为什么缺」，供前端显示「待补充 N 项」。

    来源是 `app/services/gap_detector.py`：既包括各章 agent 上报的缺数据，
    也包括**遁词检测**（正文用「以…为准」把缺口圆过去的地方）。
    🔴 缺口只在**批注与这份清单**里暴露，**不写进正文** ——
       正文留占位符违反硬约束（成稿中不得出现【待补充】/【待填写】）。
    """
    session = report_service.get_session_for_user(session_id, user)
    raw = (session.state or {}).get("_chapter_gaps") or {}
    gaps = {}
    for k, v in raw.items():
        try:
            gaps[int(k)] = v
        except (ValueError, TypeError):
            continue
    items = [dict(g, chapter=ch) for ch in sorted(gaps) for g in (gaps[ch] or [])]
    return ApiResponse(data={
        "session_id": session_id,
        "total": len(items),
        "by_chapter": {str(ch): len(gaps[ch] or []) for ch in sorted(gaps)},
        "warnings": sum(1 for g in items if g.get("severity") == "warn"),
        "items": _jsonable(items),
    })


@router.post("/generate/{session_id}/state", response_model=ApiResponse)
async def put_session_state(session_id: str, request: dict = Body(...), user: User = Depends(get_current_user)):
    """🔴 **给脚本用的会话状态写回口** —— 生成完写回**同一个界面会话**。

    `filled_data` 做**浅合并**（脚本新算出来的键覆盖旧值，没给到的键保留 ——
    不能整块替换，否则会把用户在界面填的 `_locked_*` 冲掉）；
    其他键（chapters / output_path / generated_sections…）直接覆盖。
    """
    session = report_service.get_session_for_user(session_id, user)
    incoming = (request or {}).get("state") or {}
    # 🔴 JSON 往返会把 `chapters` / `_extracted_tables` / `_chapter_image_map`
    #    的**整数键变成字符串**，直接落进 state 会让 `sorted(chapters.keys())`
    #    与 `chapters.get(6)` 全部失效（实测崩在 master_orchestrator）。
    from app.services.session_io import normalize_int_keys
    normalize_int_keys(incoming)

    fd_in = incoming.get("filled_data") or {}
    if isinstance(fd_in, dict) and fd_in:
        fd = session.state.setdefault("filled_data", {})
        # 🔴 用户填/确认过的键（`_locked_<key>`）**不许被脚本覆盖** ——
        #    「用户填的 > 材料识别 > 模板默认」这条优先级规则在这里强制。
        _locked = {k[len("_locked_"):] for k in fd if k.startswith("_locked_") and fd.get(k)}
        _kept = {k: v for k, v in fd_in.items() if k in _locked}
        fd.update({k: v for k, v in fd_in.items() if k not in _locked})
        for k, v in _kept.items():
            logging.getLogger(__name__).info(f"[会话状态] 保留界面填入的 {k}={v}（脚本值被忽略）")

    for k, v in incoming.items():
        if k != "filled_data":
            session.state[k] = v

    report_service._save_sessions()
    await report_service.archive_filled_data(session)
    return ApiResponse(message="会话状态已更新", data={"session_id": session_id})


@router.get("/generate/{session_id}/fields", response_model=ApiResponse)
async def get_template_fields(session_id: str, user: User = Depends(get_current_user)):
    """Get classified template fields (A/B/C/D/E) from the product skill analysis."""
    session = report_service.get_session_for_user(session_id, user)
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
async def confirm_chapter(session_id: str, request: dict, user: User = Depends(get_current_user)):
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
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.workflow_service import workflow_service
    result = await workflow_service.process_chapter_feedback(
        session, chapter, action, revision_text,
    )

    return ApiResponse(message=result["message"], data=result)


@router.post("/generate/{session_id}/chapter/generate", response_model=ApiResponse)
async def generate_chapter(session_id: str, request: dict, user: User = Depends(get_current_user)):
    """Manually trigger generation of a specific chapter (SSE streaming).

    Request body: {"chapter": 1}
    """
    chapter = request.get("chapter", 0)
    if chapter < 1 or chapter > 50:
        raise HTTPException(status_code=400, detail="chapter must be 1-50")

    try:
        session = report_service.get_session_for_user(session_id, user)
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
async def get_chapter(session_id: str, chapter_num: int, user: User = Depends(get_current_user)):
    """Get the status and content of a specific chapter."""
    if chapter_num < 1 or chapter_num > 10:
        raise HTTPException(status_code=400, detail="chapter_num must be 1-10")

    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.workflow_service import workflow_service
    result = workflow_service.get_chapter_status(session, chapter_num)
    return ApiResponse(message=f"Chapter {chapter_num} status", data=result)


@router.get("/generate/{session_id}/chapters/all", response_model=ApiResponse)
async def get_all_chapters(session_id: str, user: User = Depends(get_current_user)):
    """Get the status of all 10 chapters."""
    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.services.workflow_service import workflow_service
    result = workflow_service.get_all_chapters_status(session)
    return ApiResponse(message="All chapters status", data=result)


@router.post("/generate/{session_id}/missing-data", response_model=ApiResponse)
async def submit_missing_data(session_id: str, request: dict, user: User = Depends(get_current_user)):
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
        session = report_service.get_session_for_user(session_id, user)
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

    # 🔴 新一轮开始：**清掉上一轮的结果标记**。
    #    `output_path` 不清的话，`/workflow/status` 会一直拿它当「本轮已完成」：
    #      · `phase` 虽然被 `paused` 分支挡住，但
    #      · `step_statuses["assembly"]` 只要 output 非空就报 `done` ——
    #        前端进度条据此 `_maxPct = 100`。
    #    实测现象就是业主说的「用户没填写完弹窗，进度条就已经 100% 了」。
    state["output_path"] = ""
    state["report_id"] = None
    state["_gaps"] = []
    state["step_statuses"] = {"analysis": "done" if state.get("_is_resume") else "running",
                              "generation": "pending", "assembly": "pending"}

    async def _progress_cb(phase, steps):
        state["phase"] = phase; state["step_statuses"] = steps; session.state = state
        # Add log entry for each phase change
        labels = {"analysis": "📂 正在解析上传资料...", "checking": "📋 正在检查数据完整性...",
                  "generating": "✍️ 正在逐章生成报告...", "assembling": "📄 正在组装 DOCX 报告..."}
        if phase in labels and labels[phase] not in _shared_logs:
            _shared_logs.append(labels[phase])
        cur = steps.get("current_chapter", 0)
        if cur > 0:
            from app.agent.chapter_definitions import TOTAL_CHAPTERS
            msg = f"  ✅ 已完成 {cur}/{TOTAL_CHAPTERS} 章"
            if msg not in _shared_logs: _shared_logs.append(msg)
        try:
            await ws_manager.send(sid, "step_progress", {"step": phase, "status": "running", "step_statuses": steps})
            if cur > 0:
                # 🔴 `total` 必须是 **TOTAL_CHAPTERS（14）**，不能写死 10 ——
                #    前端进度条按 `10 + (cur/total)*75` 算，写死 10 的话
                #    第 10 章就跑到 85%，剩下 4 章进度条纹丝不动
                #    （业主反馈的「进度条与实际报告完成不符」）。
                #    上一行刚 import 了 TOTAL_CHAPTERS，这里却写死 —— 抄漏了。
                await ws_manager.send(sid, "chapter_progress",
                                      {"current": cur, "total": TOTAL_CHAPTERS,
                                       "status": "generating"})
        except: pass
    set_workflow_progress_callback(_progress_cb)

    try:
        # Phase 1: Material analysis — skip on resume (already analyzed)
        if state.get("_is_resume"):
            _shared_logs.append("📂 资料已解析，直接继续生成...")
        else:
            await ws_manager.send(sid, "step_progress", {"step": "analysis", "status": "running", "step_statuses": {"analysis": "running", "generation": "pending", "assembly": "pending"}})
            # 🔴 统一分析入口：OCR + 视觉分类 + 数据提取，一次完成
            _shared_logs.append("📂 正在解析上传资料（OCR + 图片分类 + 数据提取）...")
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
                # 🔴🔴 **中断时必须把工作流状态合并回会话**。
                #     完成分支（下面）有这段同步，**中断分支原先没有** ——
                #     后果是节点刚写进 `wf_state` 的东西全丢：最典型的就是
                #     「用户跳过弹窗后打的 `_confirmed_` 标记」。
                #     resume 时 `_run_workflow_bg` 从 `session.state` 重新起步，
                #     标记没了 → 同一批字段再问一遍 → **弹窗无限重弹**（实测卡死过）。
                try:
                    _wf_filled = (wf_state or {}).get("filled_data") or {}
                    if _wf_filled:
                        _sess_filled = state.setdefault("filled_data", {})
                        _sess_filled.update(_wf_filled)     # 工作流的更**新**，以它为准
                except Exception as _me:
                    _log.warning(f"[WORKFLOW] 中断时状态合并失败（不影响暂停）: {_me}")
                # 🔴 `data_inquiry`（数据核对）与 `missing_data`（缺字段）**走同一套前端弹窗** ——
                #    两者都渲染 `missing_fields`。之前 `data_inquiry` 掉进下面的 `else`，
                #    既不设 `_workflow_paused` 也不设 `phase` → `/workflow/status` 不返回 paused
                #    → 前端两个轮询器都不会命中 → **「发现数据不合理就暂停问用户」这个功能
                #    从来没在界面上出现过**（后端逻辑一直是完整的，白写了）。
                if isinstance(interrupt_data, dict) and interrupt_data.get("type") in ("missing_data", "data_inquiry"):
                    _fields = interrupt_data.get("missing_fields") or []
                    state["_workflow_paused"] = True
                    state["_missing_fields"] = _fields
                    state["phase"] = "paused"
                    _what = "数据核对" if interrupt_data.get("type") == "data_inquiry" else "基本信息"
                    _shared_logs.append(f"⏸️ {len(_fields)} 项{_what}待确认，请核对后继续")
                    # 🔴 Save state for resume — including unique thread_id for checkpoint
                    state["_workflow_logs"] = wf_state.get("logs", _shared_logs)
                    state["_wf_thread_id"] = wf_state.get("_thread_id", "")
                    session.state = state
                    return
                elif isinstance(interrupt_data, dict) and interrupt_data.get("type") == "gap_review":
                    # 🔴 生成后缺口核对：正文里 agent 用遁词圆过去的那些地方，请用户补真实内容。
                    #    也翻译成 `missing_fields` —— 前端那块弹窗**同一套**就能渲染，
                    #    不用新增组件、不用改 schema（`key` 用 `_gap_<章>_<序号>`，
                    #    `label` 是缺口描述，`used_in` 是原句）。
                    _gaps = interrupt_data.get("missing_fields") or interrupt_data.get("gaps") or []
                    state["_workflow_paused"] = True
                    state["_workflow_running"] = False
                    state["phase"] = "paused"
                    state["_missing_fields"] = _gaps
                    state["_gaps"] = _gaps
                    state["_wf_thread_id"] = wf_state.get("_thread_id", "")
                    state["_workflow_logs"] = wf_state.get("logs", _shared_logs)
                    _shared_logs.append(f"⏸️ 正文有 {len(_gaps)} 处缺少素材支撑，请补充或确认保持")
                    session.state = state
                    return
                elif isinstance(interrupt_data, dict) and interrupt_data.get("type") == "human_review":
                    # 🔴 人工复核检查点：章节被推入人工队列，暂停等待前端提交意见/审批
                    state["_workflow_paused"] = True
                    state["_workflow_running"] = False
                    state["phase"] = "human_review"
                    state["human_queue"] = interrupt_data.get("chapters", [])
                    state["human_items"] = interrupt_data.get("human_items", {}) or state.get("human_items", {})
                    state["chapter_audits"] = wf_state.get("chapter_audits", state.get("chapter_audits", {}))
                    state["_wf_thread_id"] = wf_state.get("_thread_id", "")
                    state["_workflow_logs"] = wf_state.get("logs", _shared_logs)
                    ch_list = "、".join(f"第{c}章" for c in interrupt_data.get("chapters", []))
                    _shared_logs.append(f"⏸️ {len(interrupt_data.get('chapters',[]))} 个章节需人工复核（{ch_list}），请查看违规并提交意见/审批后继续")
                    state["_workflow_logs"] = _shared_logs
                    session.state = state
                    try:
                        await ws_manager.send(sid, "phase_change", {
                            "phase": "human_review",
                            "chapters": interrupt_data.get("chapters", []),
                            "message": f"{len(interrupt_data.get('chapters',[]))} 个章节需人工复核",
                        })
                    except Exception:
                        pass
                    return
                else:
                    _log.warning(f"[WORKFLOW] Unknown interrupt: {interrupt_data}")

            # Handle completion — sync workflow state back to session state
            state["chapters"] = wf_state.get("chapters", {})
            state["output_path"] = wf_state.get("output_path", "")
            state["_outline"] = wf_state.get("_outline", {})
            # 🔴 同步审核元数据 / 人工干预状态回 session state（供前端队列/批注查询）
            if wf_state.get("chapter_audits"):
                state["chapter_audits"] = wf_state.get("chapter_audits")
            if wf_state.get("human_items"):
                state["human_items"] = wf_state.get("human_items")
            if wf_state.get("human_queue"):
                state["human_queue"] = wf_state.get("human_queue")
            if wf_state.get("_quality_audit"):
                state["_quality_audit"] = wf_state.get("_quality_audit")
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
            # 🔴 异常原文与堆栈**只进后端日志文件**（`logs/backend.log`），
            #    **绝不进 `_workflow_logs`** —— 那个字段会原样出现在接口响应里，
            #    前端还会把它渲染到界面上：里面有文件路径、调用栈、SQL 片段，
            #    等于把内部实现摊给用户看。业主口径：「不给用户看运行日志」。
            _log.exception(f"[WORKFLOW] Pipeline failed: {e}")
            _shared_logs.append("❌ 生成失败，请稍后重试；若反复失败请联系管理员（详细日志已记录）")
            state["_workflow_running"] = False
            state["phase"] = "error"
            state["_workflow_logs"] = _shared_logs
            session.state = state
            try:
                await ws_manager.send(sid, "step_progress", {
                    "step": "error", "status": "error",
                    "message": "生成失败，请稍后重试"})
            except: pass
    except Exception as e:
        _log.exception(f"[WORKFLOW] fatal: {e}")
        state["_workflow_running"] = False; state["phase"] = "error"
        state["_workflow_logs"] = ["❌ 生成失败，请稍后重试（详细日志已记录）"]
        try:
            await ws_manager.send(sid, "step_progress", {
                "step": "error", "status": "error", "message": "生成失败，请稍后重试"})
        except: pass


@router.post("/generate/{session_id}/workflow/start", response_model=ApiResponse)
async def start_workflow(session_id: str, request: WorkflowStartRequest, user: User = Depends(get_current_user)):
    """Start the LangGraph report generation workflow.

    If required data is missing, pauses and returns missing_fields list.
    Frontend should display a form for each missing field.
    """
    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.state

    # 🔴 Guard: reject if workflow already running or paused
    if state.get("_workflow_running"):
        raise HTTPException(status_code=409, detail="工作流已在运行中，请等待完成或取消")
    if state.get("_workflow_paused"):
        raise HTTPException(status_code=409, detail="工作流已暂停，请补充信息后继续，无需重新开始")

    # 🔴 资料门槛：一个材料都没有时**不让生成**。
    #    之前这条路完全没有门槛 —— 0 个文件也能跑完并出 Word，正文全靠模型自由发挥
    #    （第11/13章的风险清单与化解措施甚至完全不问用户）。门槛判据与聊天路径共用
    #     `_has_real_data`，避免两处漂移。
    if not _has_real_data(state):
        raise HTTPException(
            status_code=400,
            detail="还没有可用的项目资料，请先在「② 上传资料文档」上传项目的"
                   "预公告、勘测定界报告、座谈会记录等材料后再生成。",
        )

    # 🔴 Reset analysis flags: always run fresh analysis for each generation
    state["_analysis_done"] = False
    state["_analysis_running"] = False
    state["_is_resume"] = False

    from app.services.report_workflow import workflow_runner, set_workflow_progress_callback

    materials_dir = request.materials_dir or ""
    project_context = request.project_context or ""
    # 🔴 无标题时的兜底按域取（稳评「社会稳定风险评估报告」/ 复垦「土地复垦方案」），
    #    否则复垦会话没传标题就顶着稳评抬头跑完整条工作流。
    report_title = request.report_title or _kind_name(
        state.get("_domain") or state.get("_conversation_domain")
    )

    # Take snapshot
    sid = session_id
    uploaded = list(state.get("_uploaded_files", []) or [])

    # 🔴 用户勾选的报告人员 → 前置「稳评工作组人员及分工」与培训证书取图
    _sel = [str(x).strip() for x in (request.selected_personnel or []) if str(x).strip()]
    state["selected_personnel"] = _sel
    if _sel and isinstance(state.get("filled_data"), dict):
        state["filled_data"]["_selected_personnel"] = _sel

    # 🔴 证书页与工作组**分开选**（最多 3 人）→ 前置「公司资质证书」区贴哪几张人员证书
    _cert_sel = [str(x).strip() for x in (getattr(request, "selected_cert_personnel", None) or [])
                 if str(x).strip()][:3]
    state["selected_cert_personnel"] = _cert_sel
    if _cert_sel and isinstance(state.get("filled_data"), dict):
        state["filled_data"]["_selected_cert_personnel"] = _cert_sel

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
async def resume_workflow(session_id: str, request: WorkflowResumeRequest, user: User = Depends(get_current_user)):
    """Resume the workflow with user-provided data — continues from pause, not restart."""
    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    user_responses = request.data
    state = session.state
    filled = state.setdefault("filled_data", {})

    # Merge user data into filled_data
    # 🔴 用户在弹窗里提交的值：① 记来源 `user`（优先级最高，后续材料/AI 都盖不掉）；
    #    ② 打 `_confirmed_` —— 表示**用户看过并确认过**，与 `_locked_` 不是一回事
    #    （`_locked_` 还承担「别覆盖我」的旧含义，材料解析也会打，判不了「看过没有」）。
    _user_keys = []
    for key, value in (user_responses or {}).items():
        # 🔴 `_` 开头的键（如内部哨兵 `__skip__`）不进事实卡
        if str(key).startswith("_"):
            continue
        if value and str(value).strip():
            filled[key] = str(value).strip()
            _user_keys.append(str(key))
    if _user_keys:
        try:
            from app.services.field_sources import mark, mark_confirmed
            mark(filled, "user", *_user_keys)
            mark_confirmed(filled, *_user_keys)
        except Exception as _fse:
            logging.getLogger(__name__).warning(f"来源标记失败（不影响生成）: {_fse}")

    # 🔴🔴 **用户跳过的字段必须在「这里」打 `_confirmed_`** —— 这是唯一能存下来的地方。
    #     为什么不能在节点里打：`interrupt()` 抛出时，**节点内那之后的所有写入都会丢**
    #     （`workflow_runner.start` 收到 `__interrupt__` 事件就直接 break，
    #      `final_state` 是**上一个完成节点**的 state，不含中断节点刚写的东西）。
    #     不打标记的后果：下次重跑 `_missing_for` 发现这批字段还是空 → 再弹一次
    #     → 用户再跳过 → **无限重弹**（实测端到端卡死在第7章那 10 个日期上）。
    #     router 是状态的所有者，只有它写的能落到 `session.state`。
    try:
        _asked = [f.get("key") for f in (state.get("_missing_fields") or []) if f.get("key")]
        _skipped = [k for k in _asked if k not in (user_responses or {})]
        if _skipped:
            from app.services.field_sources import mark_confirmed as _mc
            _mc(filled, *_skipped)
            logging.getLogger(__name__).info(
                f"[HITL] 用户跳过 {len(_skipped)} 项 → 已标记不再追问: {_skipped[:8]}")
    except Exception as _ske:
        logging.getLogger(__name__).warning(f"跳过标记失败（不影响生成）: {_ske}")

    # 🔴 **填即归档**：用户一提交就把 filled_data 落进 history_reports.db。
    #    会话只有 24 小时生命（`_SESSION_MAX_AGE_HOURS`），原来只有「生成完成」才写库 ——
    #    用户填完、隔天回来，会话没了、值也没了。这里落一笔，过期也不怕。
    try:
        await report_service.archive_filled_data(session)
    except Exception as _ae:
        logging.getLogger(__name__).warning(f"填写归档失败（不影响生成）: {_ae}")
    # 🔴 同时**立刻落盘会话文件**：`sessions.json` 只在若干事件点才写，
    #    实测用户在 10:35 填的值，会话文件还停在 10:17 —— 这中间任何一次重启
    #    （开发用 `uvicorn --reload`，改一行代码就重启）都会把填写内容丢掉。
    try:
        report_service._save_sessions()
    except Exception as _se:
        logging.getLogger(__name__).warning(f"会话落盘失败（不影响生成）: {_se}")

    state["_missing_fields"] = []
    state["_workflow_paused"] = False
    state["_is_resume"] = True  # 🔴 Mark as resume so pipeline skips data check + reuses outline
    # 🔴🔴 **空 resume 值会让 LangGraph 死循环** —— 这不是我们的 bug，是它的：
    #     `Command(resume={})` 被当成「没有恢复值」，`interrupt()` 于是在 resume 时
    #     **再次抛出**，节点永远走不到中断之后的代码 → **同一个弹窗无限重弹**。
    #     实测（离线最小复现）：
    #        resume={}                → ❌ 又中断，死循环
    #        resume={'x': 1}          → ✅ 正常继续
    #        resume={'__skip__': True}→ ✅ 正常继续
    #     前端「跳过」按钮送的是 `{'__skip__': '1'}`（见 `AssistantView.skipMissing`）——
    #     那正是本条要防的哨兵。这里再兜一层：**任何调用方**送空对象也不再卡死。
    if isinstance(user_responses, dict) and not user_responses:
        user_responses = {"__skip__": True}
        logging.getLogger(__name__).info("[HITL] 用户未填任何内容 → 用哨兵值恢复（避免 LangGraph 空值死循环）")
    state["_pending_responses"] = user_responses  # 🔴 For LangGraph Command(resume=...)
    # 🔴 人工干预闭环：把前端提交的 human_items/human_queue 带进 LangGraph 恢复载荷，
    #    使 node_quality_review 的 interrupt 返回后能读回人工意见并驱动重写。
    if isinstance(user_responses, dict):
        if state.get("human_items"):
            user_responses.setdefault("human_items", state["human_items"])
        if state.get("human_queue"):
            user_responses.setdefault("human_queue", state["human_queue"])
    state["phase"] = "generating"
    state["step_statuses"] = {"analysis": "done", "generation": "running", "assembly": "pending"}
    session.state = state

    # 🔴 Re-run background task with updated data — will skip analysis and go straight to generation
    uploaded = list(state.get("_uploaded_files", []) or [])
    report_title = state.get("report_title",
                            filled.get("project_name")
                            or _kind_name(state.get("_domain") or state.get("_conversation_domain")))
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
async def workflow_status(
    session_id: str,
    user: User = Depends(get_current_user),
    _db=Depends(get_auth_db),
):
    """Get current workflow status — merges session state with subprocess result file."""
    import logging
    _log = logging.getLogger(__name__)

    try:
        session = report_service.get_session_for_user(session_id, user)
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
    # 🔴 **只在「没在跑、也没暂停」时才认这个结果文件**。
    #    该文件在本轮开始/结束时都没有人清理，一旦存在就会让每一次轮询都判「已完成」，
    #    用户表现为「弹窗还没填完，进度条已经 100%」。
    #    另外这里**不再从 GET 里改 state** —— 读接口改状态是上一轮残留值的来源之一。
    if result_file.exists() and not running and not paused:
        try:
            sp_data = _json.loads(result_file.read_text())
            if sp_data.get("status") == "completed":
                subprocess_output = sp_data.get("output_path", "")
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

    # 🔴 不做时间限制——生成时长不固定，改为记录耗时供优化。
    # 若运行超长（>60分钟）仅记录日志，不中断生成。
    _started = state.get("_workflow_started_at", 0)
    if running and _started:
        import time as _time
        _elapsed = _time.time() - _started
        if _elapsed > 3600:  # 60 min only log, no interrupt
            _log.warning(f"[TIMING] 生成运行超长 {_elapsed/60:.0f} 分钟，仍在运行（不中断）")

    output = subprocess_output or state.get("output_path", "")

    # 🔴 「组装完成」的**唯一**判据：有产物 **且** 没在跑 **且** 没暂停。
    #    以前漏了 `not running`，于是**上一轮遗留的 output_path** 会在新一轮
    #    运行/暂停期间把 assembly 报成 done —— 前端进度条直接跳 100%，
    #    而用户还在填「待补充信息」弹窗（业主：「弹窗还没提完就已经 100%」）。
    #    产物真正的落点见 `node_assemble_final_report`（report_workflow.py）。
    _assembly_done = bool(output) and not paused and not running

    # 🔴 Derive step statuses from actual state
    step_statuses = {"analysis": "pending", "generation": "pending", "assembly": "pending"}
    if state.get("_analysis_done"):
        step_statuses["analysis"] = "done"
        step_statuses["generation"] = "running" if running else "done"
    if _assembly_done:
        step_statuses["analysis"] = "done"
        step_statuses["generation"] = "done"
        step_statuses["assembly"] = "done"
    elif paused:
        step_statuses["analysis"] = "done"
        step_statuses["generation"] = "done"
        step_statuses["assembly"] = "pending"
    elif not running and state.get("_analysis_done"):
        step_statuses["assembly"] = "running"
    # Override with explicit values if set
    # 🔴 但**产线没完成时不许被覆盖成 done** —— state 里可能残留上一轮的
    #    `{analysis:done, generation:done, assembly:done}`，那会绕过上面的守卫。
    explicit = state.get("step_statuses", {})
    if explicit:
        if not _assembly_done:
            explicit = {k: v for k, v in explicit.items()
                        if not (k == "assembly" and v == "done")}
        step_statuses.update(explicit)

    # Verify download file actually exists — 同样只在真正完成时才给下载链接
    download_url = ""
    if _assembly_done and output:
        abs_path = settings.STORAGE_DIR / output
        if abs_path.exists():
            download_url = f"/api/v1/files/{output}"

    # Determine phase
    if subprocess_error:
        phase = "error"
        running = False
    elif paused:
        # 🔴 区分：人工复核暂停 vs 缺数据暂停
        phase = "human_review" if state.get("human_queue") else "paused"
    elif _assembly_done:
        # 🔴 用 `_assembly_done` 而不是裸 `output`：后面那个含有「上一轮遗留的
        #    output_path」，会在**正在跑**的时候把 phase 报成 complete
        #    （注意这个分支在 `elif running` 之前，一旦命中就再也走不到 generating）。
        running = False
        phase = "complete"
    elif running:
        phase = "generating"
    else:
        phase = state.get("phase", "idle")

    # 🔴 人工复核队列：暴露给前端展示待复核章节 + 违规
    #   pydantic Dict[str, Any] 要求键为字符串——工作流里 human_items/chapter_audits 是 int 键，
    #   持久化后又可能是 str 键，统一归一化为 str/int 再进模型，避免 500。
    human_queue = [int(c) for c in (state.get("human_queue", []) or [])]
    human_items = {str(k): v for k, v in (state.get("human_items", {}) or {}).items()}
    chapter_audits = {str(k): v for k, v in (state.get("chapter_audits", {}) or {}).items()}

    # 🔴 Typed response — validated by WorkflowStatusData schema
    status_data = WorkflowStatusData(
        phase=phase,
        running=running,
        paused=paused,
        error=subprocess_error,
        total_chapters=state.get("_outline", {}).get("total") or len(state.get("_outline", {}).get("chapters", [])),
        current_chapter=len(state.get("chapters", {})),
        missing_fields=missing,
        human_queue=human_queue,
        human_items=human_items,
        chapter_audits=chapter_audits,
        # 🔴 **运行日志只发给内部人员**（业主：「上线不给用户看运行日志」）。
        #    前端那边也有同样的判断（面板 `v-if="authStore.isStaff"`），
        #    但只藏 UI 挡不住直接调接口的人 —— 这里才是真正的口子。
        logs=(state.get("_workflow_logs", []) or (wf_state.get("logs", []) if wf_state else []))
             if await is_staff(user, _db) else [],
        step_statuses=step_statuses,
        # 🔴 未真正完成时**不回传产物路径** —— 这个字段会被前端当成「可以了」的信号，
        #    回传上一轮遗留值就等于告诉前端「已经生成好」（进度条直接跳满）。
        output_path=output if _assembly_done else "",
        download_url=download_url,
    )
    return ApiResponse(data=status_data.model_dump())


# ═══════════════════════════════════════════════════════
# Pipeline API — 新8阶段流水线
# ═══════════════════════════════════════════════════════

@router.post("/pipeline/run", response_model=ApiResponse)
async def run_pipeline(request: dict, user: User = Depends(get_current_user)):
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
        # 🔴 原来写的是 `create_session(session_id)` —— 那个位置是 `template_id`，
        #    而且 create_session 内部会**重新生成** session_id，传进去的值直接被丢弃。
        #    这里改成按关键字传，免得再被误读成别的参数。
        session = report_service.create_session(
            template_id=0, template_name="", template_path="", initial_message="",
            user_id=str(user.id), company_id=user.company_id,
        )
        session_id = session.session_id
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
async def get_pipeline_status(session_id: str, user: User = Depends(get_current_user)):
    """Get pipeline status for a session."""
    try:
        session = report_service.get_session_for_user(session_id, user)
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
async def generate_report_direct(session_id: str, user: User = Depends(get_current_user)):
    """Trigger report generation via subprocess (isolated from uvicorn event loop).

    Frontend polls GET /status for progress.
    """
    import subprocess, os

    try:
        session = report_service.get_session_for_user(session_id, user)
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

@ws_router.websocket("/ws/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    """WebSocket chat endpoint — full-duplex, auto-reconnect, token streaming."""
    from app.services.ws_manager import ws_manager
    from app.services.workflow_service import workflow_service
    import logging as _ws_logging
    _ws_logger = _ws_logging.getLogger(__name__)
    import asyncio as _asyncio

    # 🔴 WS 认证必须在 `accept()` **之前**，而且要在**取会话之前**：
    #    先 close 会直接拒绝握手；先认证则未登录者连「这个 session 存不存在」
    #    都问不出来。token 走 query 参数（浏览器不能给 WebSocket 设请求头）。
    from app.deps import (
        authenticate_websocket, WS_CLOSE_UNAUTHORIZED, WS_CLOSE_SESSION_NOT_FOUND,
    )
    _ws_user = await authenticate_websocket(websocket)
    if _ws_user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="未登录或登录已过期")
        return

    try:
        # 归属校验：别人的会话一律按「不存在」处理（不泄露 id 是否存在）
        session = report_service.get_session_for_user(session_id, _ws_user)
    except HTTPException:
        await websocket.close(code=WS_CLOSE_SESSION_NOT_FOUND, reason="Session not found")
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
                        "你是社会稳定风险评估报告编制助手。\n"
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


# ═══════════════════════════════════════════════════════════════
# 人工复核闭环 API — Skill 违规 → 人工介入
# ═══════════════════════════════════════════════════════════════

@router.get("/generate/{session_id}/human-queue", response_model=ApiResponse)
async def get_human_queue(session_id: str, user: User = Depends(get_current_user)):
    """① 获取待人工复核章节列表（Skill 违规 + 人工干预状态）。"""
    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.state
    # 🔴 章标题按域取（复垦的章号与稳评重合但标题不同），不能读死稳评的 CHAPTER_DEFINITIONS。
    _dom = state.get("_domain") or state.get("_conversation_domain")
    queue = state.get("human_queue", []) or []
    # 🔴 键归一化：session 持久化后 dict 键变成字符串（"6"≠6），统一转 int 再读
    human_items = {int(k): v for k, v in (state.get("human_items", {}) or {}).items()}
    audits = {int(k): v for k, v in (state.get("chapter_audits", {}) or {}).items()}

    items = []
    for ch in queue:
        hi = human_items.get(int(ch), {})
        items.append({
            "chapter": ch,
            "title": _chapter_title(_dom, int(ch)),
            "status": hi.get("status", "queued"),
            "retry_count": hi.get("retry_count", 0),
            "max_retry": hi.get("max_retry", 0),
            "in_human_queue": hi.get("in_human_queue", False),
            "human_opinion": hi.get("human_opinion", ""),
            "human_approved": hi.get("human_approved", False),
            "human_override": hi.get("human_override", False),
            "violations": audits.get(int(ch), []),
        })
    # 全局人工待办（chapter=0，如缺营业执照）
    global_items = state.get("_global_human_items", []) or []
    return ApiResponse(
        message=f"{len(items)} 章待人工复核",
        data={"chapters": items, "global_items": global_items},
    )


@router.post("/generate/{session_id}/human/opinion", response_model=ApiResponse)
async def submit_human_opinion(session_id: str, request: dict, user: User = Depends(get_current_user)):
    """② 提交人工修改想法 / 审批放行标记。

    body:
      { "chapter": 6,
        "mode": "opinion" | "approve" | "override",
        "opinion": "把支持率改成100%...",     # mode=opinion 必填
        "override_content": "人工改写全文..."  # mode=override 必填
      }

    模式1 (opinion)：人工给修改思路 → 交给 AI 重写（重写时携带 Skill 缺陷 + 人工意见）
    模式2a (override)：人工直接改写全文 → 跳过 AI 重写
    模式2b (approve)：人工审批放行 → 跳过后续所有 AI 审核重写
    """
    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.state
    ch = int(request.get("chapter", 0) or 0)
    mode = request.get("mode", "opinion")
    if ch < 1 or ch > 10:
        raise HTTPException(status_code=400, detail="chapter must be 1-10")
    if mode not in ("opinion", "approve", "override"):
        raise HTTPException(status_code=400, detail="mode must be opinion/approve/override")
    if mode == "opinion" and not request.get("opinion"):
        raise HTTPException(status_code=400, detail="opinion is required for mode=opinion")
    if mode == "override" and not request.get("override_content"):
        raise HTTPException(status_code=400, detail="override_content is required for mode=override")

    # 🔴 键归一化：持久化后 dict 键为字符串，统一 int 键
    raw_hi = state.get("human_items", {}) or {}
    human_items = {int(k): v for k, v in raw_hi.items()}
    hi = human_items.setdefault(ch, {
        "chapter": ch, "retry_count": 0, "in_human_queue": False,
        "human_opinion": "", "human_override": False, "human_approved": False,
        "override_content": None,
    })
    if mode == "opinion":
        hi["human_opinion"] = request.get("opinion", "")
        hi["status"] = "human_reviewed"
        hi["in_human_queue"] = True          # 保持入队，等 AI 重写后复核
    elif mode == "approve":
        hi["human_approved"] = True          # 模式2b：放行，跳过后续 AI 审核重写
        hi["status"] = "approved"
        hi["approved_at"] = __import__("datetime").datetime.now().isoformat()
        hi["in_human_queue"] = False
        if ch in state.get("human_queue", []):
            state["human_queue"].remove(ch)
    elif mode == "override":
        hi["human_override"] = True          # 模式2a：人工直接改写全文
        hi["override_content"] = request.get("override_content", "")
        hi["status"] = "human_reviewed"
        hi["in_human_queue"] = False
        if ch in state.get("human_queue", []):
            state["human_queue"].remove(ch)

    human_items[ch] = hi
    state["human_items"] = human_items
    session.state = state
    return ApiResponse(message=f"第{ch}章人工意见已记录（mode={mode}）", data=hi)


@router.post("/generate/{session_id}/human/rewrite", response_model=ApiResponse)
async def trigger_human_ai_rewrite(session_id: str, request: dict, user: User = Depends(get_current_user)):
    """③ 触发 AI 携人工意见重写排队章节（模式1）。

    只挑「有 human_opinion 且未 human_approved / 未 human_override」的章节重写。
    复用现有 resume 后台任务：把 human_items 作为 LangGraph 恢复载荷。
    """
    try:
        session = report_service.get_session_for_user(session_id, user)
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.state
    requested = request.get("chapters") or state.get("human_queue", []) or []
    # 🔴 键归一化：持久化后 dict 键为字符串，统一 int 键
    human_items = {int(k): v for k, v in (state.get("human_items", {}) or {}).items()}
    queued = [int(c) for c in requested]
    # 🔴 恢复载荷携带所有队列章节的人工状态（意见/审批/改写），
    #    node_quality_review 恢复逻辑会分别处理：有意见→AI重写、审批→放行、改写→直接生效
    payload_items = {str(c): human_items.get(c, {}) for c in queued if c in human_items}
    acted = [int(c) for c, v in payload_items.items()
             if v.get("human_opinion") or v.get("human_approved") or v.get("human_override")]
    if not acted:
        return ApiResponse(message="尚未对任何章节提交意见或审批，请先处理", data={"chapters": []})

    # 置为 resume：node_quality_review 的 interrupt 恢复后读 human_items 驱动重写
    state["_pending_responses"] = {
        "human_items": payload_items,
        "human_queue": queued,
    }
    state["_is_resume"] = True
    state["_workflow_paused"] = False
    state["_workflow_running"] = True
    state["phase"] = "generating"
    session.state = state

    uploaded = list(state.get("_uploaded_files", []) or [])
    report_title = state.get("report_title", state.get("filled_data", {}).get("project_name", "社会稳定风险评估报告"))
    materials_dir = state.get("materials_dir", "")
    project_context = state.get("project_context", "")
    import asyncio as _asyncio
    _wf_task3 = _asyncio.create_task(_run_workflow_bg(
        session_id, session, uploaded, report_title, materials_dir, project_context
    ))
    _bg_tasks3 = session.state.setdefault("_bg_tasks", [])
    _bg_tasks3.append(_wf_task3)

    return ApiResponse(
        message=f"已继续生成：处理 {len(acted)} 个章节的人工意见/审批",
        data={"chapters": queued, "acted": acted},
    )
