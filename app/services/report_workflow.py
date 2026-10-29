"""LangGraph 5-layer report generation agent — enterprise architecture.

Flow:
  file_parse → field_validate → build_dynamic_outline → outline_check
  → [chapter loop: generate → review → route_retry]
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
    OUTLINE="outline"; GENERATING="generating"
    REVIEWING="reviewing"; ASSEMBLING="assembling"; COMPLETE="complete"; ERROR="error"

MAX_RETRY = 2
# 🔴 终稿质量循环轮数（env 可配）。每轮 = 完整「审查→批量重写→再审查」循环：
# 第1轮重写初次被标记的章节；重写后若仍有章节不过审，进入第2轮重写，直到干净或轮数耗尽。
# 默认2轮：只给1轮时，一次批量重写修不完的章节（如6、9章）会带病进终稿。
MAX_QUALITY_ROUNDS = int(os.environ.get("MAX_QUALITY_ROUNDS", "2"))

# 🔴 人工复核开关（env 可配，默认关闭）：
#   false → 生成全程不暂停人工干涉，章节问题由 agent 自行重试/生成，报告直接产出
#   true  → 章节 AI 重试耗尽后推入人工队列，暂停等待前端提交意见/审批
ENABLE_HUMAN_REVIEW = os.environ.get("ENABLE_HUMAN_REVIEW", "false").lower() in ("1", "true", "yes")

# 🔴 数据提问开关（env 可配，默认 **关闭提问**）：
#   业主口径（2026-09-29）：「尽量少让用户提供数据，要解析所有的资料，
#   agent 要能识别 PDF 中的资料内容并且运用在报告上，**不会再问之前需要确认的问题**。」
#   false → 逐章缺字段 / 正文缺口**一律不弹窗**：
#            · 逐章缺失字段 → 标 `_confirmed_`（不再问）+ 文字类字段自动拟兜底稿（`_fallbacks`）
#            · 正文缺口（空泛句）→ AI 草稿写进 `_fallbacks`，不打断
#            · 数据合理性异常（支持率非 100%、文号冲突等）→ 只记日志，自动按材料值走
#   true  → 恢复旧行为（弹窗问用户）
#   ⚠️ 评审表「人员四要素」（责任单位负责人/联系人/我方联系人电话）与
#      「资料里确实没有」的关键字段仍会问 —— 那类信息只有用户知道，
#      不问就只能印占位符。这是刻意保留的**唯一**提问点。
ENABLE_DATA_INQUIRY = os.environ.get("ENABLE_DATA_INQUIRY", "false").lower() in ("1", "true", "yes")

# 🔴 缺口弹窗单独一个开关（业主 2026-10-29：「缺少数据的可以弹窗问用户啊」）：
#    AI 能拟出替代句的缺口**不打扰**（就地改写），只把**真缺硬数据**的那几条弹窗问。
#    与 `ENABLE_DATA_INQUIRY`（生成前的整批字段核对）分开，避免又把「一堆字段」弹给用户。
ENABLE_GAP_INQUIRY = os.environ.get("ENABLE_GAP_INQUIRY", "true").lower() in ("1", "true", "yes")

# ── Progress emitter ───────────────────────────────────────────────────────────
_progress_callback = None
def set_workflow_progress_callback(cb): global _progress_callback; _progress_callback = cb
async def _emit(phase: str, steps: dict):
    if _progress_callback:
        try: await _progress_callback(phase, steps)
        except: pass


def _now_iso() -> str:
    """返回 ISO 时间戳（人工队列入队/审批时间用）。"""
    from datetime import datetime
    return datetime.now().isoformat()


def push_human_chapters(state: dict, chapters) -> list:
    """把章节推入「人工复核队列」（幂等）。

    终稿审核阶段被判定为「需人工介入」的章节（缺真实数据、缺材料、Skill 硬违规等）
    统一走这里入队，避免同一段逻辑在多个节点里重复书写。

    Args:
        state: 工作流状态（就地修改 human_items / human_queue）。
        chapters: 需入队的章节号集合。

    Returns:
        本次新增入队的章节号列表（已在队列中的不重复计入）。
    """
    human_items = state.setdefault("human_items", {})
    queue = state.setdefault("human_queue", [])
    added: List[int] = []
    for ch in chapters or []:
        hi = human_items.setdefault(ch, {
            "chapter": ch, "retry_count": MAX_RETRY, "in_human_queue": False,
            "human_approved": False, "human_override": False, "human_opinion": "",
        })
        hi["in_human_queue"] = True
        hi["status"] = "queued"
        hi["queued_at"] = _now_iso()
        if ch not in queue:
            queue.append(ch)
            added.append(ch)
    return added


# ── 流程耗时统计 ──────────────────────────────────────────────────────────────
import time as _time_mod
from app.domains import domain_of_state

def _mark(state: dict, name: str):
    """记录流程节点耗时（支持多次运行累加，如每章生成）。

    首次调用：记录开始时间。再次调用（同name）：累加耗时。
    """
    timings = state.setdefault("_timings", {})
    now = _time_mod.time()
    t = timings.get(name)
    if t and isinstance(t, dict) and "start" in t:
        elapsed = now - t["start"]
        t["secs"] = round((t.get("secs") or 0) + elapsed, 1)
        del t["start"]
        logger.info(f"[TIMING] {name} 累计耗时 {t['secs']} 秒")
    else:
        prev_secs = (t or {}).get("secs", 0) if isinstance(t, dict) else 0
        timings[name] = {"start": now, "secs": prev_secs}
    return state


def _timing_summary(state: dict) -> str:
    """汇总各流程耗时，供日志输出。"""
    timings = state.get("_timings", {})
    parts = []
    for name, t in timings.items():
        if isinstance(t, dict) and t.get("secs"):
            parts.append(f"{name}={t['secs']}s")
    return " | ".join(parts) if parts else ""


def timed(name: str):
    """节点耗时统计装饰器：自动记录节点开始/结束耗时。"""
    def decorator(fn):
        async def wrapper(state):
            _mark(state, name)
            try:
                result = await fn(state)
            finally:
                _mark(state, name)
            return result
        return wrapper
    return decorator

# ═══════════════════════════════════════════════════════════════════════════════
# Node 1: file_parse — OCR, field extraction, table extraction, image placeholder
# ═══════════════════════════════════════════════════════════════════════════════
async def node_file_parse(state: ReportWorkflowState) -> ReportWorkflowState:
    """Parse uploaded PDFs: OCR text, extract fields, extract tables, generate image placeholders."""
    state["phase"] = WorkflowPhase.INGESTING.value
    already_analyzed = bool(state.get("_project_materials")) or bool(state.get("_project_material_facts"))
    if already_analyzed:
        # 🔴 前置分析（report.py Phase 1）已完成：不再重复"解析上传资料"，
        # 只构建图片/表格索引，避免日志误导（重复解析/0图片）。
        state.setdefault("logs", []).append("📂 构建图片/表格索引...")
    else:
        state.setdefault("logs", []).append("📂 正在解析上传资料...")
    await _emit("analysis", {"analysis":"running","generation":"pending","assembly":"pending"})

    try:
        from app.routers.report import _analyze_session_attachments
        session_state = {
            "session_id": state.get("session_id",""), "_domain": domain_of_state(state),
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

        # 🔴 仅在未分析过时调用（幂等：已入库文件会被跳过，但调用本身会触发日志/汇总）
        if not already_analyzed:
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

        # 🔴 日志明确口径：这里统计的是 PDF 内嵌图片（扫描件通常为0）；
        # 用户上传的照片已在前置分析按类别分类（见"已分类图片"），不在此列。
        state["logs"].append(f"✅ 图片/表格索引构建完成: {len(images)} 张PDF内嵌图, {len(tables)} 张表格")
        await _emit("analysis", {"analysis":"done","generation":"pending","assembly":"pending"})
    except Exception as e:
        state.setdefault("errors",[]).append(f"文件解析失败: {e}")
        logger.error(f"file_parse failed: {e}")
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 2: field_validate — value range check, conflict detection, confidence mark
# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# 逐章缺失字段定义（HITL：每章生成前按需询问，不再启动时一次性问完）
#   (key, 中文名, 类型, 正则, 最大长度, 样例)
#   类型：text/number/date/phone —— 前端据此渲染输入控件与校验
# ═══════════════════════════════════════════════════════════════════════════════
CHAPTER_REQUIRED_FIELDS = {
    # 第7章 稳评工作方案（7.4 评估程序及步骤 / 7.5 评估时限 / 7.6 工作计划，模板里有示例日期，必须逐项提供）
    7: [
        ("commission_date", "委托日期",       "date", None, 10, "2026-06-02"),
        ("plan_date",       "制定方案日期",   "date", None, 10, "2026-06-03"),
        ("notice_date",     "现场公示开始",   "date", None, 10, "2026-06-04"),
        ("notice_end_date", "现场公示结束",   "date", None, 10, "2026-06-10"),
        ("survey_date",     "实地勘察日期",   "date", None, 10, "2026-06-05"),
        ("symposium_date",  "座谈会日期",     "date", None, 10, "2026-06-05"),
        ("risk_predict_date",     "风险预测开始", "date", None, 10, "2026-06-08"),
        ("risk_predict_end_date", "风险预测结束", "date", None, 10, "2026-06-09"),
        ("report_draft_date",     "报告编制开始", "date", None, 10, "2026-06-10"),
        ("report_draft_end_date", "报告编制结束", "date", None, 10, "2026-06-11"),
        ("review_date",     "专家评审开始",   "date", None, 10, "2026-06-12"),
        ("review_end_date", "专家评审结束",   "date", None, 10, "2026-06-15"),
        ("plan_approve_date", "方案审核日期", "date", None, 10, "2026-06-03"),
        ("notice_publish_date", "公告发布日期", "date", None, 10, "2026-06-01"),
    ],
    # 第6章 项目基本情况（6.1 项目概况）
    6: [
        ("project_name",   "项目名称",        "text",   None,             60, "XX风电项目 / XX拟征告〔2026〕X号土地征收决策"),
        ("org_name",       "稳评责任单位",    "text",   None,             40, "XX市XX区XX镇人民政府 / XX公司"),
        ("location",       "项目位置",        "text",   None,             60, "XX镇XX社区"),
        ("area_mu",        "用地面积（亩）",  "number", r'^\d+(\.\d+)?$', 12, "100.00"),
        ("doc_reference",  "公告/批复文号",   "text",   None,             40, "XX拟征告〔2026〕X号"),
        ("household_count","涉及户数",        "number", r'^\d+$',         8,  "100"),
        ("funding",        "资金筹措单位",    "text",   None,             40, "XX区财政统筹 / 企业自筹"),
    ],
    # 第4章 社会稳定风险评估工作报告
    4: [
        ("report_month", "报告编写年月", "text", r'^\d{4}-\d{1,2}$', 7, "2026-06"),
        # 🔴 稳评公示日期由**用户填写**，公示期（一周）由系统推算（见 derive_publicity_period）。
        #    注意这与「征收土地预公告期限」不是一回事：那是公告期，这是稳评公示期。
        ("publicity_start_date", "稳评公示开始日期", "date", None, 10, "2026-06-04"),
    ],
    # 第9章 各利益相关者对项目的反映（9.1 问卷调查情况）
    9: [
        ("total_samples",  "问卷份数",        "number", r'^\d+$',         6,  "50"),
        ("support_rate",   "群众支持率（%）", "number", r'^\d+(\.\d+)?$', 5,  "100"),
        ("notice_date",    "现场公示时间",    "date",   None,             10, "2026-06-02"),
        ("survey_date",    "实地勘察时间",    "date",   None,             10, "2026-06-03"),
        ("symposium_date", "座谈会时间",      "date",   None,             10, "2026-06-03"),
    ],
}

# 🔴 评审表（不编号前置部分）专用字段：在第1章之前询问，用于渲染制式表
REVIEW_TABLE_FIELDS = [
    ("org_leader",       "责任单位负责人",   "text",  None,             20, "张三"),
    ("org_leader_title", "负责人职务",       "text",  None,             20, "主任"),
    ("org_contact",      "责任单位联系人",   "text",  None,             20, "李四"),
    # 🔴 电话放行座机（0517-80920828 这种）：只收手机号会把合规的固话拦在门外
    ("org_phone",        "责任单位联系电话", "phone",
     r'^(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})$', 13, "13800000000"),
    # 🔴 实施单位（本公司）的负责人/职务/联系人/联系方式同样**问用户**；
    #    负责人与职务正常会由**公司档案**（`companies.leader_name/leader_title`）
    #    按账号归属自动填好 —— 那种情况下 `_missing_for` 判「已有值」**不会问**，
    #    只有公司档案也没填时才弹出来问，避免报告里印「待填写」。
    ("impl_leader",      "实施单位负责人",   "text",  None,             20, "陈春"),
    ("impl_leader_title","实施单位负责人职务", "text", None,            20, "法定代表人"),
    ("impl_contact",     "实施单位联系人",   "text",  None,             20, "王五"),
    ("impl_phone",       "实施单位联系电话", "phone",
     r'^(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})$', 13, "13800000000"),
]

# 评审表字段在弹窗里的归属标记 —— 必须是**非数字**，前端据此显示「评审表」而非「第X章」
_REVIEW_TABLE_OWNER = "评审表"


def _missing_for(filled: dict, fields, chapter=None) -> list:
    """返回缺失字段（带弹窗所需元数据 + chapter 归属 + used_in 用途说明）。

    `fields` 有两种写法：
      - `[(key, label, type, pattern, maxlen, example), ...]` —— 归属统一取 `chapter` 参数；
      - `[((...), chapter), ...]` —— 每项自带归属（用于「第N章 + 评审表」混合询问）。
    """
    out = []
    for item in (fields or []):
        # 每项自带归属的写法：((6元组), chapter)
        if (isinstance(item, (tuple, list)) and len(item) == 2
                and isinstance(item[0], (tuple, list))):
            spec, owner = item
        else:
            spec, owner = item, chapter
        try:
            key, label, ftype, pat, maxlen, ex = spec
        except (ValueError, TypeError):
            continue
        # 🔴 用户**确认过**的键不再问 —— 包括他确认「确实没有」的情况：
        #    那种情况值是空的，「已有值不问」的规则盖不住，只能靠 `_confirmed_`。
        #    （`_locked_` 判不了这个：材料解析也会打 `_locked_`，见 field_sources 的说明。）
        if (filled or {}).get(f"_confirmed_{key}"):
            continue
        v = (filled or {}).get(key, "")
        if not v or str(v).startswith("【待补充】"):
            out.append({"key": key, "label": label, "chapter": owner,
                        "type": ftype, "pattern": pat, "maxlength": maxlen, "example": ex})
    try:
        from app.services.data_usage_map import attach_usage
        attach_usage(out)  # 补「用于 第X章 某小节」
    except Exception:
        pass
    return out


def derive_publicity_period(filled: dict) -> None:
    """由「稳评公示开始日期」推算公示期（一周），就地写入 filled。

    业主口径：「公告日期需要询问用户填写，然后自己推算一周的时间。」
    → 用户只填开始日；结束日与「共X天」的文字由系统算，避免用户手算出错。

    🔴 口径：**含首尾共 7 天**（如 4月13日 起 → 4月19日 止）。
    与「征收土地预公告期限」（notice_date/notice_end_date，那是公告期）是两回事，互不覆盖。
    """
    import re as _re
    from datetime import datetime, timedelta

    raw = str((filled or {}).get("publicity_start_date") or "").strip()
    if not raw:
        return
    m = _re.search(r'(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})', raw)
    if not m:
        return
    try:
        y, mo, d = (int(x) for x in m.groups())
        start = datetime(y, mo, d)
    except ValueError:
        return
    end = start + timedelta(days=6)          # 含首尾共 7 天
    filled["publicity_start_date"] = start.strftime("%Y-%m-%d")
    try:
        from app.services.field_sources import mark
        mark(filled, "system", "publicity_start_date", "publicity_end_date",
             "publicity_period_text")
    except Exception:
        pass
    filled.setdefault("publicity_end_date", end.strftime("%Y-%m-%d"))
    filled.setdefault(
        "publicity_period_text",
        f"{start.year}年{start.month}月{start.day}日至"
        f"{end.year}年{end.month}月{end.day}日（共7天）")


async def node_field_validate(state: ReportWorkflowState) -> ReportWorkflowState:
    """Validate extracted fields: range check, multi-source conflict, confidence marking."""
    state["phase"] = WorkflowPhase.VALIDATING.value
    filled = state.setdefault("filled_data",{})
    logs = state.setdefault("logs",[])

    # 🔴 字段清洗：剔除「未检测到/未提及/未显示」这类**模型自述**被当成字段值的伪值，
    # 并回填可推导的 project_name。必须排在 survey merge 之前——否则下游的
    # 「空值 → 缺失字段」判断会全部失灵，伪值会原样印进报告。
    try:
        from app.services.field_sanitizer import sanitize_fields
        _cleared = sanitize_fields(filled, verbose=True, state=state)
        if _cleared:
            logs.append(f"🧹 已剔除 {len(_cleared)} 个无效字段值（如「未检测到」类自述）")
    except Exception as _fse:
        logger.warning(f"field sanitize skipped: {_fse}")

    # 🔴 稳评公示期：用户只填开始日，结束日与「共7天」文字由系统推算
    try:
        derive_publicity_period(filled)
        if filled.get("publicity_period_text"):
            logs.append(f"📅 稳评公示期：{filled['publicity_period_text']}")
    except Exception as _pe:
        logger.warning(f"公示期推算跳过: {_pe}")

    # 🔴 注入权威问卷数据（座谈会PDF逐页解析 → 落盘缓存 → 人工核验数据集，分层取）：
    # 覆盖不完整的 OCR 提取，并修正「问卷数被户数污染」的问题（如 26户→50份问卷）
    try:
        from app.services.survey_data import merge_survey_into_state
        if merge_survey_into_state(state):
            _src = (state.get("generated_sections") or {}).get("survey_data_source", "")
            _sa = (state.get("generated_sections") or {}).get("survey_analysis", {}) or {}
            logs.append(f"📊 已载入问卷数据（{_src}）：{_sa.get('total_samples', 0)}份 / "
                        f"支持率{_sa.get('support_rate', 0)}%")
    except Exception as _se:
        logger.warning(f"survey_data merge skipped: {_se}")

    # 🔴 座谈会签到表 → `symposium_attendees`。
    #    第8章 spec 声明依赖这个字段，但它此前**永远是 None**：签到表是纯扫描件
    #    （无文字层），`questionnaire_parser` 又显式跳过签到表页，于是第8章只能写
    #    「实际参加人员以座谈会签到表为准」这类遁词把缺口圆过去。
    #    按指纹缓存，只有首次跑要 ~15s 的视觉识别。
    try:
        from app.services.signin_parser import ensure_symposium_attendees
        _sr = await ensure_symposium_attendees(state, llm_service=None)
        if _sr.get("names"):
            _k = "评审会" if _sr.get("_kind") == "review" else "座谈会"
            logs.append(f"🖊 签到表识别：{_k} {len(_sr['names'])} 人")
    except Exception as _ge:
        logger.warning(f"签到表解析跳过: {_ge}")

    # 🔴 用勘测定界表纠正被正则抓错的字段（land_use/location/project_name）——按表头语义，不锁位置
    try:
        from app.services.field_fixer import fix_fields_from_tables
        _tabs = (state.get("_project_material_facts", {}) or {}).get("_extracted_tables") \
                or (state.get("filled_data", {}) or {}).get("_extracted_tables") or []
        fix_fields_from_tables(filled, _tabs)
    except Exception as _ffe:
        logger.warning(f"field_fixer skipped: {_ffe}")

    # 🔴 注入公司固定数据（营业执照/资质证书等），避免审核误报「未检索到公司固定数据」
    try:
        from app.services.fixed_data_service import get_fixed_data_for_domain
        _fixed = await get_fixed_data_for_domain(state.get("_domain", "stability"))
        state["_fixed_company_assets"] = _fixed
        if _fixed.get("company_name"):
            logs.append(f"🏢 已载入公司固定数据：{_fixed['company_name']}")
    except Exception as _fe:
        logger.warning(f"fixed_data load skipped: {_fe}")
        state["_fixed_company_assets"] = {"company_name": "", "assets": [], "images": [], "kb_docs": []}

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

    # 字段提取统一走下方 parse_announcement（预公告完整解析），不再调用 LLM 抽字段

    # 🔴 预公告**完整解析**（唯一入口）：一次拿到文号/主体/位置/标题/正文各段，
    #    不再散落多个零散正则。同时修复 location 混入视觉分析 dict/JSON 的残留。
    from app.services.announcement_extract import parse_announcement
    _ann = parse_announcement(pdf_text) if pdf_text else {}

    loc_val = str(filled.get("location", "") or "").strip()
    if loc_val.startswith("{"):
        _loc = _ann.get("location") or ""
        filled["location"] = _loc
        logs.append(f"📝 location 已从 PDF 原文修复: {_loc}" if _loc
                    else "⚠️ location 含视觉分析残留且无法从PDF提取，已清空待补充")

    state["filled_data"] = filled
    if pdf_text and _ann:
        # 缺失或仍为占位符的字段，用公告解析结果补齐
        for _k, _label in (("doc_reference", "文号"), ("org_name", "决策主体"),
                           ("location", "位置"), ("project_name", "项目名称")):
            _cur = str(filled.get(_k, "") or "").strip()
            if _cur and not _cur.startswith("【"):
                continue
            _v = _ann.get(_k)
            if not _v and _k == "project_name":
                from app.services.announcement_extract import derive_project_name
                _v = derive_project_name(_ann.get("doc_reference", ""),
                                         _ann.get("announcement_title", ""))
            if _v:
                filled[_k] = _v
                logs.append(f"📝 {_label} 已从预公告提取: {_v}")
        # 正文各段（征收目的/范围/期限/工作安排/其他事项/法律依据…）一并入库
        _extra = {k: v for k, v in _ann.items() if k not in filled and not k.startswith("_")}
        if _extra:
            filled.update(_extra)
            logs.append(f"📄 预公告正文已解析 {len(_extra)} 项：{'、'.join(list(_extra)[:6])}…")

    # 🔴 启动时不再一次性询问全部字段——改为「逐章生成前按章提问」
    #    （见 node_chapter_generate 与模块级 CHAPTER_REQUIRED_FIELDS / REVIEW_TABLE_FIELDS）。
    #    此处只做缺失标记，不再 interrupt。

    # 🔴 **不往事实卡里写占位符**。
    #    原来这里把用户没提供的字段一律写成 `【待补充】`，两个后果都是实打实的：
    #      ① 违反业主硬约束「成稿中不得出现【待补充】」—— 占位符沿事实卡流进
    #         表格单元格和正文引用，被**原样印出来**；
    #      ② 被下游当**值**用 —— `float('【待补充】')` 抛 ValueError，
    #         **整个组装阶段挂掉、报告一个字都产不出来**（实测崩过：
    #         `assemble failed: could not convert string to float: '【待补充】'`）。
    #    正确做法：**缺就让它空着**。正文由「规范表述 / 缺口清单」兜，
    #    数值由 `report_assembler._num_or / _int_or` 兜。
    _still_missing = [
        k for k in ("project_name", "org_name", "location", "area_mu", "land_use",
                    "household_count", "total_samples", "compensation_standard",
                    "doc_reference", "support_rate")
        if not str(filled.get(k) or "").strip()
    ]
    if _still_missing:
        logs.append("📝 以下字段暂无来源，正文将用规范表述：" + "、".join(_still_missing))

    # 🔴 实施单位 + 负责人 / 职务：都取**本报告所属公司**的档案；没有公司就留空，
    #    报告里显示「待填写」—— 绝不写死成我们自己的名字或负责人。
    from app.services.company_service import load_company
    _co = await load_company(state.get("_company_id")) or {}

    if not filled.get("implement_unit"):
        _unit = str(_co.get("implement_unit") or "").strip()
        if _unit:
            filled["implement_unit"] = _unit
            filled["_locked_implement_unit"] = True

    # 🔴 负责人 / 职务 → 评审表「稳评实施单位·负责人」「职务」两格。
    #    原先这两格**没有数据来源**：弹窗不问（旧注释写「固定的陈春/法定代表人」），
    #    库里也没有字段，最后只能靠 `review_table_filler` 的
    #    `setdefault("impl_leader_title", "法人")` 兜一句写死的「法人」。
    #    多租户下这是错的：每家公司负责人不同，必须按 `companies` 行取。
    #    取值优先级：用户弹窗填的（filled 已有值）> 公司档案 > 【待填写】。
    #    ⚠️ 独立于 implement_unit 判断 —— 用户手填了单位名也要能取到负责人。
    for _fk, _ck in (("impl_leader", "leader_name"),
                     ("impl_leader_title", "leader_title")):
        _v = str(_co.get(_ck) or "").strip()
        if _v and not str(filled.get(_fk) or "").strip():
            filled[_fk] = _v
            filled[f"_locked_{_fk}"] = True
    # 🔴 公司档案里的 `contact` 是**联系方式**一格，可能写手机/座机，也可能写人名。
    #    形态可判：像电话 → 填 `impl_phone`；否则当联系人 → 填 `impl_contact`。
    #    （实测张三号 company.contact 是「0517-1111111」，只往 impl_contact 塞会
    #      把电话号码印到「联系人」那一栏。）
    _co_contact = str(_co.get("contact") or "").strip()
    if _co_contact:
        _is_phone = bool(re.match(r'^(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})$', _co_contact))
        _fk, _dup = ("impl_phone", "impl_contact") if _is_phone else ("impl_contact", "impl_phone")
        if not str(filled.get(_fk) or "").strip():
            filled[_fk] = _co_contact
            filled[f"_locked_{_fk}"] = True
        # 兜底：另一格还空着且形态对得上，也一并填（档案只写了一格很常见）
        if _is_phone and not str(filled.get(_dup) or "").strip():
            filled[_dup] = _co_contact
    # 🔴 预公告落款的联系人/电话 → 评审表「责任单位联系人/联系电话」
    #    （见 `field_resolver.CANONICAL_ALIASES` 的 org_contact/org_phone 说明）
    try:
        from app.services.field_resolver import canonicalize_material
        _added = canonicalize_material(filled)
        if _added:
            logs.append("📇 资料中已识别并沿用：" + "、".join(_added))
    except Exception as _ce:
        logger.warning(f"材料字段归一失败（不影响生成）: {_ce}")

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

    # ── ①.5 日期：**不判断、不修改** ──────────────────────────────────
    # 🔴 业主规则（2026-09-24）：
    #      「如果用户资料中有时间就不要确认，而且**以用户资料为准**，
    #        后期不需要做任何判断或是再修改；如果没有，就让用户去填写，
    #        再以用户填写为准。」
    #    这里原来有一套「工作流先后」逐对校验（委托→方案→公示→勘察→座谈→…），
    #    把倒挂的挑出来问用户。线上数据来自**用户资料或用户自己填**，
    #    按上面的规则**一律照用** —— 既不问、也不改。
    #    日期空着的情况由下面的 ② 兜底（问用户填）。

    # ── ② 待核对项：**机器给的、用户还没看过**的关键字段 ──────────────────
    # 🔴 业主：「解析报告的内容是否准确找用户确认。」
    #    材料识别 / AI 抽取的字段此前直接进报告，用户没有任何机会核对。
    #    这里挑出「有值、不是用户填的、用户还没确认过」的关键字段（按 `data_usage_map`
    #    挑影响 ≥2 个章节的），连同**当前值**一起弹给用户。
    checks = []
    try:
        from app.services.data_usage_map import FIELD_USAGE
        from app.services import field_sources as _fs
        from app.agent.chapter_definitions import NON_ASKABLE_FIELDS
        _ranked = sorted(FIELD_USAGE.keys(),
                         key=lambda k: -len(FIELD_USAGE.get(k) or []))
        # 🔴 与「缺字段弹窗」同一条判据：**问了也答不上来的不问**
        #    （风险等级是 scoring_service 算的、界址描述只可能来自勘测定界…）。
        _cand = [k for k in _ranked
                 if len(FIELD_USAGE.get(k) or []) >= 2 and k not in NON_ASKABLE_FIELDS]
        # 🔴 **材料已经抽到的值不再拉出来问**（业主：「如果用户提供的资料里面有公示日期
        #    这些数据，就不要再弹窗问用户日期了」）。
        #    原先判据是 `field_sources.unconfirmed()` =「有值 且 来源≠user 且 未确认」——
        #    也就是**专门**把材料识别出来的值挑出来让用户「核对」，而 `notice_date`/
        #    `survey_date`/`symposium_date` 正在其中。对用户来说就是
        #    「系统都从材料里认出来了，还问我一遍」。
        #    现在只问**真正没有值**的关键字段（提前问一次，省得生成到一半再打断）。
        _todo = [
            k for k in _cand
            if not str(filled.get(k) or "").strip()
            or str(filled.get(k) or "").strip().startswith("【待")
        ][:15]
        from app.agent.chapter_definitions import FIELD_META
        for k in _todo:
            meta = FIELD_META.get(k)
            if meta is None:
                # 兜底不能回落英文 key（弹窗上写 `doc_reference` 业主看不懂）
                logger.warning(f"[FIELD_META 缺登记] {k} —— 核对弹窗显示「补充信息」")
                meta = ("补充信息", "text", None, 100, "")
            checks.append({
                "key": k, "label": meta[0], "type": meta[1],
                "pattern": meta[2], "maxlength": meta[3], "example": meta[4],
                "chapter": "数据核对",
                # 🔴 预填**真实解析值**给用户核对；`【待补充】` 是占位符，不是值，
                #    直接预填进去会让用户以为「系统认为这就是答案」。
                "current": ("" if str(filled.get(k) or "").strip().startswith("【待")
                            else str(filled.get(k) or "").strip()),
                "source": _fs.label_of(filled, k),           # 「材料识别」/「AI 提取」
            })
        if checks:
            from app.services.data_usage_map import attach_usage
            attach_usage(checks)
    except Exception as _ce:
        logger.warning(f"待核对字段收集跳过: {_ce}")

    # 🔴 合理性异常（issues）**不再打断弹窗**（业主 2026-09-29：尽量少问用户）：
    #    · 支持率非 100% → 按材料/问卷值照用（业主口径「资料里有就照用，不判断不改」）
    #    · 补偿标准抽到 0 → 明显伪值，直接剔除，交给后续兜底，不打扰用户
    #    · 项目名与文号冲突 → 以公告解析的 `doc_reference`（`_locked_`）为准，只记日志
    if issues:
        for _i in issues:
            _f = _i.get("field")
            if _f == "compensation_standard":
                filled[_f] = ""
            logs.append(f"ℹ️ 数据核对已自动处理（不询问）：{_i.get('message', '')}")
        state["filled_data"] = filled

    # 🔴 默认路径（`ENABLE_DATA_INQUIRY=false`）：**不打断**。
    #    业主 2026-09-29 定稿口径：「只留评审表一次」——
    #      · 逐章弹窗：已取消（缺字段自动标 `_confirmed_` + 拟兜底稿）
    #      · 缺口核对弹窗：已取消（AI 拟替代文字进 `_fallbacks`）
    #      · **本处（开头一次性问「资料中完全没有的关键字段」）：取消**
    #      · 评审表弹窗：**保留**（责任单位负责人/职务等材料里绝对没有的，
    #        不弹的话评审表只能印【待填写】，比问一次更糟）
    #    这里必须打 `_confirmed_`：否则下游 `_missing_for` 每轮重算都发现它们还是空，
    #    换个入口又问一遍。
    if checks and not ENABLE_DATA_INQUIRY:
        _names = "、".join(str(c.get("label") or c.get("key")) for c in checks[:8])
        logs.append(f"ℹ️ 数据核对：{len(checks)} 项关键字段在所有资料中均未识别到，"
                    f"已自动兜底（不询问）：{_names}")
        try:
            from app.services import field_sources as _fsx
            _fsx.mark_confirmed(filled, *[c["key"] for c in checks if c.get("key")])
        except Exception as _fxe:
            logger.warning(f"数据核对字段确认标记失败: {_fxe}")
        state["filled_data"] = filled
        checks = []                       # 清空 → 下面的 interrupt 分支自然跳过

    if checks:
        from langgraph.types import interrupt
        logs.append(f"⏸️ 数据核对：{len(checks)} 项关键字段在所有资料中均未识别到，需要你补充")
        user_input = interrupt({
            "type": "data_inquiry",
            # 🔴 前端 `AssistantView.vue` 那块现成弹窗渲染的是 `missing_fields`
            #    （`{key,label,type,pattern,maxlength,example,chapter,used_in}`）。
            #    这里就按那个结构给，前端一行不用改就能显示；
            #    多带的 `current`/`source` 用来**预填当前值**并标注来源。
            "missing_fields": checks,
            "title": "请补充以下资料中没有的信息",
        })
        # 用户回应后写入
        if isinstance(user_input, dict):
            _touched = []
            for it in checks:
                field = it["key"]
                if field not in user_input:
                    # 🔴 用户没改这一项 = **确认保持原值** —— 也算确认过，别再问
                    _touched.append(field)
                    continue
                val = user_input.get(field, "")
                if val and str(val).strip():
                    filled[field] = str(val).strip()
                    filled[f"_locked_{field}"] = True
                    _touched.append(field)
                    logs.append(f"✅ 用户确认 {it['label']}: {val}")
            if _touched:
                try:
                    from app.services import field_sources as _fs2
                    _fs2.mark(filled, _fs2.USER, *_touched)
                    _fs2.mark_confirmed(filled, *_touched)
                except Exception:
                    pass

    state["filled_data"] = filled
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3: build_dynamic_outline — LLM generates chapter structure
# ═══════════════════════════════════════════════════════════════════════════════
def _standard_outline(domain: str = "stability") -> List[Dict[str, Any]]:
    """按**域的案卷结构**生成确定性大纲（结构由各域章节目录决定）。

    稳评：十四章案卷结构（参照件《白马湖…报告》），逐字段与历史一致；
    复垦：十章结构（`reclamation_definitions`）。域分发收口在 `chapter_catalog`。
    """
    from app.agent.chapter_catalog import standard_outline
    return standard_outline(domain)


async def node_build_dynamic_outline(state: ReportWorkflowState) -> ReportWorkflowState:
    """构建案卷大纲（确定性，保证与参照件结构一致）。"""
    state["phase"] = WorkflowPhase.OUTLINE.value
    logs = state.setdefault("logs", [])

    outline_list = _standard_outline(domain_of_state(state))
    state["_outline"] = {"chapters": [
        {"num": int(c["chapter_no"]), "title": c["title"]} for c in outline_list
    ]}
    state["outline_list"] = outline_list
    logs.append(f"📋 按案卷模板构建大纲: {len(outline_list)} 章")
    await _emit("outline", {"analysis": "done", "outline": "done",
                            "generation": "pending", "assembly": "pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 4: outline_check — validate chapter order, check resource availability
# ═══════════════════════════════════════════════════════════════════════════════
async def node_outline_check(state: ReportWorkflowState) -> ReportWorkflowState:
    """Validate outline: business logic order, resource dependency check."""
    outline_list = state.get("outline_list",[]) or []
    filled = state.get("filled_data",{})
    logs = state.setdefault("logs",[])

    # 🔴 结构强制：大纲必须等于**本域**案卷模板的章结构（确定性），任何偏差一律重置
    from app.agent.chapter_catalog import all_chapters, total_chapters
    _domain = domain_of_state(state)
    _expected = all_chapters(_domain)
    nums = []
    for ch in outline_list:
        try:
            nums.append(int(ch.get("chapter_no", 0)))
        except (ValueError, TypeError):
            nums.append(0)
    if nums != _expected:
        logs.append(f"⚠️ 大纲结构与案卷模板不一致（{nums}），重置为标准 {total_chapters(_domain)} 章结构")
        outline_list = _standard_outline(_domain)
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
# Node 6: chapter_generate — per-chapter generation with chapter loop inside
# ═══════════════════════════════════════════════════════════════════════════════
async def _bridge_agent_events(q: asyncio.Queue, session_id: str, logs: Optional[list] = None) -> None:
    """把章节 agent 的实时事件（thinking/工具调用）桥接到前端。

    1) 追加到 workflow logs（AssistantView 轮询显示日志面板）
    2) 同时以 WS thinking 事件转发（供 report.js thinkingLog / 聊天界面）
    """
    try:
        from app.services.ws_manager import ws_manager
    except Exception:
        ws_manager = None
    while True:
        try:
            evt = await asyncio.wait_for(q.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        except Exception:
            return
        ev_name = evt.get("event", "")
        ev_data = evt.get("data") or {}
        # 🔴 结束哨兵：本章生成完由生成侧投递，桥把队列里剩余事件转发完后**自己退出**。
        #    没有它就只能靠 `cancel()` —— 实测取消不生效，得干等 5s 超时（每章 5s）。
        if ev_name == "_end":
            return
        if ev_name == "thinking":
            content = ev_data.get("content", "") or ""
            # 工具调用提示（"🔧 调用工具..."）优先写入日志面板
            if content and ("工具" in content or "调用" in content or "🧰" in content or "🔧" in content):
                if logs is not None and content not in logs:
                    logs.append("    " + content)
            if ws_manager is not None and content:
                try:
                    await ws_manager.send(session_id, "thinking", {"content": content})
                except Exception:
                    pass
        elif ev_name == "agent_status":
            if ws_manager is not None:
                try:
                    await ws_manager.send(session_id, "agent_status", ev_data)
                except Exception:
                    pass


def _drop_verbatim_chapters(nums, domain: str = "stability"):
    """🔴 从「待重写章节」里剔除参照件照搬章。

    照搬章（稳评 2/5/7；复垦 2/5/7/10）的 markdown 是范文原文的字面替换结果，
    质量审查的「字数不足/表后缺分析/AI 味」等判据会把它们判为问题并触发重写，
    而重写走的是 LLM 自由写 —— 照搬就失效了。故在进入重写队列前先剔除。

    🔴 必须按域取照搬章号：不传 domain 会拿到**稳评**的 {2,5,7}，
       复垦第 10 章就会漏剔除、被 LLM 重写掉照搬稿。
    """
    try:
        from app.agent.agents.chapters import reference_verbatim_chapters
        vb = reference_verbatim_chapters(domain)
        if not vb:
            return list(nums)
        kept = [c for c in nums if int(c) not in vb]
        if len(kept) != len(list(nums)):
            logger.info(f"[参照件] 重写队列剔除照搬章: {sorted(vb)}")
        return kept
    except Exception:
        return list(nums)

# 🔴 **不给兜底拟稿**的字段 —— 拟出来一定是错的，比留空更糟。
#    实测：让模型按事实卡给「稳评责任单位」拟一版，它写成了
#    「稳评责任单位为江苏众拓项目代理咨询有限公司」—— 那是**实施单位**（本公司），
#    责任单位是**政府**。事实卡里两者并存，模型分不清。
#    这批是**甲方人事信息**，只能由用户提供；缺了就走中性表述 + 缺口清单，
#    绝不自动编一个「像样的」填上（这条口径 `field_resolver` 里也立过）。
_NO_FALLBACK_FIELDS = {
    "org_name",        # 稳评责任单位
    "org_leader",      # 责任单位负责人
    "org_leader_title", # 负责人职务
    "org_contact",     # 责任单位联系人
    "org_phone",       # 责任单位联系电话
    "decision_unit",   # 决策主体
}


async def _draft_field_fallbacks(miss_items: list, filled: dict,
                                 domain: str = "stability") -> Dict[str, str]:
    """用户**跳过**的字段 → 先拟一版兜底文字，让成品「拿到手简单改改就能用」。

    🔴 业主口径（2026-09-22）：
        「如果用户跳过了你该如何去填写一些兜底的模板，好让用户拿到生成之后的内容
          可以就简单的改改。」

        原行为：用户跳过 → 字段仍为空 → 正文只好写「以现状调查确认结果为准」这类遁词，
        用户拿到后得**从零写一段**。现在改成：跳过 → agent 按事实卡拟一版规范表述，
        用户只需在上面改几个字。

    🔴 为什么不直接写进 `filled_data`
        `filled_data` 是本报告的**唯一事实源**（「不留空、不编造」）。AI 拟的稿不是事实，
        写进去会污染事实卡、并让 `build_project_card` 把它当真实数据发给每一章。
        所以走独立通道 `filled["_fallbacks"]`，由 `build_project_card` 以
        「📝 兜底草稿（用户跳过，AI 拟稿待改）」的名义带进提示词。

    Returns: {字段 key: 草稿文字}
    """
    # 只对**文字类**字段兜底 —— 日期/数字拟不出来，也不该拟（编个日期比留空更糟）
    todo = [f for f in (miss_items or [])
            if str(f.get("type") or "text") in ("text", "phone")
            and f.get("key") and f["key"] not in _NO_FALLBACK_FIELDS]
    if not todo:
        return {}
    try:
        from app.services.master_orchestrator import build_project_card
        from app.services.llm_service import LLMService
        from app.agent.chapter_catalog import report_kind_name
        _card = build_project_card(filled or {}, domain)
        _lines = [f'{i}. 「{f["label"]}」（第{f.get("chapter")}章）'
                  for i, f in enumerate(todo, 1)]
        _prompt = (
            f"下面是一份{report_kind_name(domain)}里，用户**暂时无法提供**的几个字段。\n"
            "请按事实卡为每一个拟一句**规范表述**，要求：\n"
            "1. 是**完整可用的中文句子**（用户拿到后改几个字就能用），不是提示语；\n"
            "2. **只依据「本项目事实卡」**；事实卡里没有的**不要编数字/人名/文号**，\n"
            "   用「以…为准」「按规定执行」这类规范写法把话说完整即可；\n"
            "3. 不要出现「待补充」「待填写」「请填写」这类占位字样。\n\n"
            f"{_card}\n\n"
            "── 待拟稿的字段 ──\n" + "\n".join(_lines) + "\n\n"
            '严格输出 JSON：{"1": "第1个字段的表述", "2": "第2个字段的表述", ...}'
        )
        _res = await asyncio.wait_for(
            LLMService().chat(messages=[{"role": "user", "content": _prompt}],
                              max_tokens=3000, temperature=0.3),
            timeout=180.0,
        )
        import json as _json
        _m = re.search(r'\{[\s\S]*\}', str(_res or ""))
        if not _m:
            return {}
        _data = _json.loads(_m.group(0))
        out = {}
        for i, f in enumerate(todo, 1):
            v = str(_data.get(str(i)) or "").strip()
            if v:
                out[f["key"]] = v
        logger.info(f"[兜底] 已为用户跳过的 {len(out)}/{len(todo)} 个字段拟稿")
        return out
    except Exception as e:
        logger.warning(f"兜底拟稿跳过: {e}")
        return {}


async def node_chapter_generate(state: ReportWorkflowState) -> ReportWorkflowState:
    """生成「当前」章节（图级循环，每次只生成一章，生成后直接入库自推进）。

    章节循环由 LangGraph 条件路由驱动：本节点只负责生成 outline_list[_chapter_idx] 这一章，
    生成完成即写入 state["chapters"] 并自推进；逐章硬规则审核层已移除，
    质量/数据/规范把关统一收敛到终稿 quality_review。
    """
    state["phase"] = WorkflowPhase.GENERATING.value
    logs = state.setdefault("logs",[])
    filled = state.get("filled_data",{})
    outline_list = state.get("outline_list",[]) or []
    idx = state.get("_chapter_idx", 0)
    retry = state.get("_chapter_retry", 0)
    quality_rewrite = state.get("_quality_rewrite_chapters", None)

    # 🔴 计算一次权威风险等级，写入 state，供第5/6/8/9章统一引用（避免各章自说自话）
    if "_risk_assessment" not in state:
        try:
            from app.services.scoring_service import scoring_service
            _ra = scoring_service.build_scoring_report(filled, {})
            state["_risk_assessment"] = {
                "pre_total": _ra["pre_measures"]["total"],
                "post_total": _ra["post_measures"]["total"],
                "level": _ra["meta"]["risk_level"],
            }
            logs.append(f"📐 权威风险等级: 措施前{state['_risk_assessment']['pre_total']}分→"
                        f"措施后{state['_risk_assessment']['post_total']}分，"
                        f"{state['_risk_assessment']['level']}")
        except Exception as _rae:
            logger.warning(f"risk_assessment compute skipped: {_rae}")

    # 🔴 质量重写模式：只重写审核发现问题章节
    if quality_rewrite is not None:
        rewrite_list = [int(c) for c in quality_rewrite]
        if idx >= len(rewrite_list):
            # 重写完成 → 清空质量重写状态，进入下一轮审核
            state["_quality_rewrite_chapters"] = None
            state["_next_action"] = "quality_review"
            return state
        target_ch = rewrite_list[idx]
        # 找到该章节定义
        ch = next((c for c in outline_list if int(c.get("chapter_no", 0)) == target_ch), None)
        if ch is None:
            state["_chapter_idx"] = idx + 1
            return state
        ch_num = target_ch
        # 🔴 质量重写反馈：把审核发现的问题传给 LLM
        issues = state.get("_quality_rewrite_issues", {}).get(str(ch_num), [])
        if issues:
            ch["review_msg"] = "## ⚠️ 终稿审核发现以下问题，请修正后重写：\n" + "\n".join(f"- {i}" for i in issues)
        else:
            ch["review_msg"] = "## ⚠️ 终稿审核要求重新生成本章，请确保内容完整、数据准确"
    else:
        # 所有章节已处理完 → 进入终稿质量审查（逐章硬规则审核已移除）
        if idx >= len(outline_list):
            state["_next_action"] = "quality_review"
            return state
        ch = outline_list[idx]
        ch_num = int(ch.get("chapter_no","1"))

    # 🔴 逐章动态补缺：本章所需数据缺失 → 弹窗只问这一章（不再启动时一次性问完）
    #    归属标记：本章字段带数字章号；评审表字段带「评审表」（不编号前置部分），
    #    不能借用第 1 章的章号，否则弹窗会把评审表字段显示成「第一章」。
    # 🔴 清单**动态推导**（业主：「弹窗的日期这些不能固定，agent 觉得缺少什么找用户」）：
    #    `CHAPTER_SECTION_SPECS[n]` 声明了本章每一节依赖哪些字段 —— 那是事实源；
    #    再并上 `CHAPTER_REQUIRED_FIELDS[ch]` 兜底（第1/3/11/13 章的 spec.data 是空的），
    #    最后滤掉「问了也答不上来」的（风险等级是系统算的、界址描述只可能来自勘测定界…）。
    #    好处：改 `CHAPTER_SECTION_SPECS` 弹窗自动跟着变，不用再同步第二份硬编码清单。
    from app.agent.chapter_definitions import chapter_need_fields
    _need = [(f, ch_num) for f in chapter_need_fields(ch_num)]
    # 🔴 首章生成前，先把「评审表（不编号前置部分）」需要的字段一并问掉
    #    ⚠️ **仅在开启提问时**并进来：默认（不提问）路径会把本章缺失字段统统标
    #       `_confirmed_`，若把评审表字段也带上，它们会被提前标成「已确认」，
    #       导致 `node_review_table`（**唯一保留**的提问点）再也问不出来 →
    #       评审表的负责人/联系人只能印【待填写】。
    if idx == 0 and ENABLE_DATA_INQUIRY:
        _need = [(f, _REVIEW_TABLE_OWNER) for f in REVIEW_TABLE_FIELDS] + _need
    if _need:
        _miss = _missing_for(filled, _need)
        if _miss:
            if ENABLE_DATA_INQUIRY:
                from langgraph.types import interrupt
                _owner = _miss[0].get("chapter")
                _label = f"第{ch_num}章" if _owner == ch_num else str(_owner)
                logs.append(f"⏸️ {_label}缺少 {len(_miss)} 项数据，暂停等待补充")
                # resume 后本节点会从头部重跑，interrupt() 这次返回用户填的值（不再抛异常）
                _ui = interrupt({"type": "missing_data", "chapter": _owner, "missing_fields": _miss})
                _skipped = []
                if isinstance(_ui, dict):
                    for _f in _miss:
                        _v = _ui.get(_f["key"], "")
                        if _v and str(_v).strip():
                            filled[_f["key"]] = str(_v).strip()
                            filled[f"_locked_{_f['key']}"] = True
                            logs.append(f"✅ 第{ch_num}章已补充 {_f['label']}: {_v}")
                        else:
                            _skipped.append(_f)
                else:
                    _skipped = list(_miss)
            else:
                # 🔴 默认路径（不提问）：资料里没有的字段直接走「用户跳过」的处理 ——
                #    标 `_confirmed_`（防任何路径再问）+ 文字类字段自动拟兜底稿，
                #    正文按规范表述写足，**不把活推给用户**。
                _skipped = list(_miss)
                _names = "、".join(str(f.get("label") or f.get("key")) for f in _miss[:8])
                logs.append(f"✍️ 第{ch_num}章 {len(_miss)} 项数据资料中未识别到，"
                            f"已自动兜底（不询问）：{_names}")
            # 🔴 用户**跳过**的字段必须打 `_confirmed_` —— 否则**同一个弹窗会无限重弹**：
            #    `_missing_for` 每次重算都发现它们还是空，于是再次 interrupt；
            #    前端/脚本如果跳过同一批（很常见，用户就是不想填），就会卡死在这里。
            #    实测端到端跑第7章那 12 个日期时就是这么卡住的。
            #    「跳过」= 用户明确表示「别问了」，`_confirmed_` 正是这个语义。
            if _skipped:
                try:
                    from app.services import field_sources as _fs
                    _fs.mark_confirmed(filled, *[f["key"] for f in _skipped if f.get("key")])
                except Exception as _fce:
                    logger.warning(f"跳过字段标记失败: {_fce}")
            # 🔴 同时拟一版兜底稿，让成品「拿到手简单改改就能用」
            #    （业主：「如果用户跳过了你该如何去填写一些兜底的模板…」）。
            #    走独立通道 `_fallbacks`，**不进事实卡** —— AI 拟的稿不是事实，
            #    写进 `filled_data` 会污染唯一事实源。
            if _skipped:
                try:
                    _fb = await _draft_field_fallbacks(_skipped, filled,
                                                        domain_of_state(state))
                    if _fb:
                        filled.setdefault("_fallbacks", {}).update(_fb)
                        logs.append(f"📝 用户跳过 {len(_skipped)} 项，已拟兜底草稿 "
                                    f"{len(_fb)} 条（正文里标注为待核改）")
                except Exception as _fbe:
                    logger.warning(f"兜底拟稿跳过: {_fbe}")
            state["filled_data"] = filled

    from app.services.llm_service import LLMService; llm = LLMService()
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.master_orchestrator import build_chapter_prompt
    from app.services.image_catalog import build_image_catalog

    # 🔴 首次进入时准备 few-shot 和图片 catalog（后续章节/重试复用，不重复加载）
    if not state.get("_gen_prepared"):
        try:
            from app.services.few_shot_service import refresh_few_shot_cache
            proj = {"location": filled.get("location",""), "area_mu": filled.get("area_mu",""),
                    "land_use": filled.get("land_use",""), "project_name": filled.get("project_name","")}
            await refresh_few_shot_cache(proj, domain_of_state(state))
        except Exception as e:
            logger.warning(f"Few-shot cache refresh failed (non-critical): {e}")
        # 传 dict（保留 original_name）；并带上 AI 视觉分类结果（此前此处漏传，与组装器行为不一致）
        uploaded_items = []
        for f in (state.get("_uploaded_files", []) or []):
            if isinstance(f, dict) and f.get("path"):
                uploaded_items.append({"path": f["path"],
                                       "original_name": f.get("original_name")
                                                        or os.path.basename(str(f["path"]))})
            elif isinstance(f, str):
                uploaded_items.append(f)
        state["_img_catalog"] = build_image_catalog(
            uploaded_items, ai_classifications=state.get("_classified_images"),
            domain=domain_of_state(state))
        state["_gen_prepared"] = True

    # 🔴 RAG 素材不在这里检索 —— 每个章节 agent 自己按小节检索并注入
    #    （见 `chapter_base._retrieve_rag` / `_format_rag_for_prompt`）。
    #    原先这里检索一遍、agent 里又检索一遍，同一份素材做两次。
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

    # 🔴 人工干预回流：模式2a（人工已改写全文）→ 直接用，跳过 AI 重写
    human_items = state.setdefault("human_items", {})
    hi = human_items.setdefault(ch_num, {"chapter": ch_num, "retry_count": 0,
                                         "in_human_queue": False, "human_opinion": "",
                                         "human_override": False, "human_approved": False})
    if hi.get("human_override") and hi.get("override_content"):
        override_md = hi["override_content"]
        ch["raw_content"] = override_md
        state["outline_list"] = outline_list
        chapters = state.setdefault("chapters", {})
        chapters[ch_num] = {"markdown": override_md, "title": ch["title"], "status": "human_reviewed"}
        state["chapters"] = chapters
        hi["status"] = "human_reviewed"
        state["human_items"] = human_items
        logs.append(f"  👤 第{ch_num}章采用人工改写内容（跳过 AI 重写）")
        # 逐章审核层已移除：人工改写内容直接入库，统一由顶部判终（正常/质量重写模式皆同）
        state["_chapter_idx"] = idx + 1
        state["_next_action"] = "generate"
        return state

    # 🔴 模板章 / 表格章：确定性渲染，**不经 LLM**（保证与人工成品一致、杜绝编造）
    #    章类型/标题一律**按域**取 —— 复垦第 3 章是叙述章（fact），
    #    稳评第 3 章才是评审表（table）；按稳评取会把复垦第 3 章误当表格章跳过。
    from app.agent.chapter_catalog import chapter_kind as _ck, chapter_title as _ct
    _domain = domain_of_state(state)
    _kind = _ck(_domain, ch_num)

    # 🔴 `table` / `attachment` 两类章由**组装器确定性渲染**（第3章=江苏省制式评审表、
    #    第14章=附件），根本不经 LLM。旧实现只跳 `template`，于是第3章被当普通章
    #    跑了一整轮 LLM（实测 1246~1520 字正文，白烧 2~4 分钟/轮），还被质量审核
    #    挑进重写名单 [1,3,4,6,8] 再重写一遍 —— 那段正文**根本不会进报告**，纯浪费。
    #    脚本路径（scripts/gen_full_report.py）一直是跳过的，这里补齐。
    if _kind in ("table", "attachment"):
        logs.append(f"  📄 第{ch_num}章「{_ct(_domain, ch_num)}」由组装器渲染（{_kind}），跳过 LLM")
        state["_chapter_idx"] = idx + 1
        state["_next_action"] = "generate" if idx + 1 < len(outline_list) else "quality_review"
        return state

    if _kind == "template":
        # 🔴 模板章：确定性渲染，不经 LLM（保证与人工成品一致、杜绝编造）
        #    （评审表是不编号前置部分，不在章节循环里，由组装器单独渲染）
        _md = ""
        try:
            from app.agent.chapter_templates import (
                get_template, render_template, build_template_values, sectionize)
            _tpl = get_template(ch_num)
            if _tpl:
                _md = sectionize(
                    render_template(_tpl, build_template_values(filled)), ch_num)
        except Exception as _te:
            logger.warning(f"Ch{ch_num} 模板渲染失败，回退 LLM: {_te}")
        if _md and _md.strip():
            chapters = state.setdefault("chapters", {})
            chapters[ch_num] = {"markdown": _md, "title": _ct(_domain, ch_num), "status": "template"}
            state["chapters"] = chapters
            ch["raw_content"] = _md
            state["outline_list"] = outline_list
            logs.append(f"  📄 第{ch_num}章「{_ct(_domain, ch_num)}」按模板渲染（{len(_md)}字，未经 LLM）")
            state["_chapter_idx"] = idx + 1
            state["_next_action"] = "generate"
            return state

    # 🔴 重试/质量重写时构建反馈（包含可用数据 + 上一版内容）
    feedback = ""
    logger.info(f"[GEN] 第{ch_num}章 idx={idx} retry={retry} quality_rewrite={'None' if quality_rewrite is None else quality_rewrite} 反馈={bool(ch.get('review_msg'))}")
    # 🔴 修复：质量重写时 _chapter_retry=0，原条件 `retry>0 and` 会把审核反馈整个拦截掉，
    #     LLM 盲写导致同样问题复发（日志中 6/9 章重写后仍不过审即由此造成）。
    #     只要 review_msg 有内容（普通重试或终稿质量重写）就应携带反馈。
    if ch.get("review_msg"):
        # 🔴 优先用带修正建议的结构化反馈（review_feedback），比粗描述更能让 agent 定向修复
        review_body = ch.get("review_feedback") or ch.get("review_msg") or ""
        fb_parts = [review_body]
        if ch_data:
            fb_parts.append("\n## 📋 本章可用数据（只有这些数据是真实的，其他一律不准编造）")
            for k, v in ch_data.items():
                fb_parts.append(f"- {k}: {v}")
        prev_md = ch.get("raw_content","") or ""
        if prev_md:
            fb_parts.append(f"\n## 📝 上一版生成内容（参考结构，修正数据问题后重写）\n{prev_md[:2000]}")
        feedback = "\n".join(fb_parts)

    # 🔴 人工意见回流：模式1（人工给思路，AI 重写）——即使无 review_msg 也注入
    human_opinion = hi.get("human_opinion", "")
    if human_opinion:
        feedback = (feedback + "\n" if feedback else "") + \
                   f"## 👤 人工修改意见（必须严格遵循）\n{human_opinion}"

    # 🔴 论证主线上下文（整份大纲 + 每章论证任务）
    outline_context = state.get("_outline", {})
    # 🔴 传**完整事实卡**（filled），不是本章依赖子集（ch_data）。
    #    子集只用于「本章可用数据」提示；事实卡若只给子集，模型就拿不到预公告全文、
    #    省级依据、勘测定界数据 → 只能写「以现状调查确认结果为准」这类空话。
    prompt = build_chapter_prompt(ch_def, filled,
                                  previous_chapters=state.get("chapters", {}),
                                  feedback=feedback if feedback else None,
                                  outline_context=outline_context, state=state,
                                  domain=_domain)
    agent = get_chapter_agent(ch_num, llm_service=llm, domain=_domain)
    # 🔴 这里的 agent_state 以前只给「本章依赖子集」(`ch_data`)，比脚本路径
    #    （scripts/gen_full_report.py）少三样东西，界面产出因此系统性偏差：
    #      ① `filled_data` 给的是子集 → 章节 agent 读不到完整事实卡（预公告全文、
    #         勘测定界数据），只能写「以现状调查确认结果为准」这类空话；
    #      ② 没有 `project_context` → RAG 检索词只剩章节模板，召回质量差；
    #      ③ 没有 `_extracted_tables` → 第6章/第4章的 `[SYS_TABLE:…]` 渲染不出来，
    #         标记被跳过，**表直接不见**。
    #    子集另经 `_chapter_data` 单独给出（`chapter_base` 用它做「本章可用数据」提示）。
    agent_state = {"session_id": state.get("session_id", ""),
                   "report_title": filled.get("project_name", ""),
                   "project_context": " ".join(
                       str(filled.get(k) or "") for k in
                       ("project_name", "location", "land_use", "doc_reference")),
                   "filled_data": filled,
                   "_chapter_data": ch_data,
                   "_domain": _domain, "_report_style": "jinhu",
                   "current_chapter": ch_num, "chapters": {},
                   "_custom_prompt": prompt, "_use_custom_prompt": True,
                   "_uploaded_files": state.get("_uploaded_files", []),
                   "_img_catalog": state.get("_img_catalog", {}),
                   "_extracted_tables": (filled or {}).get("_extracted_tables", []),
                   "_selected_personnel": state.get("selected_personnel") or []}
    q = asyncio.Queue()
    md = ""
    _mark(state, f"chapter_{ch_num}")  # 🔴 每章生成开始计时

    # 🔴 Agent 事件桥接：把章节 agent 的 thinking/工具调用事件实时转发到前端 WebSocket
    # （agent._emit_thinking 写入 q，此前无人消费 → 工具调用等过程不显示；此处桥接修复）
    sid_ws = str(state.get("session_id", ""))
    relay_task = asyncio.create_task(_bridge_agent_events(q, sid_ws, logs))
    try:
        # 🔴 超时预算与 scripts/gen_full_report.py 对齐：第12章（评分体系 + 6 张表）
        #    要输出 16k token，300s 会被硬砍成骨架；其余章也给足。
        _timeout = 1800.0 if ch_num == 12 else 900.0
        await asyncio.wait_for(agent.run(agent_state, q), timeout=_timeout)
        cd = agent_state.get("chapters", {}).get(ch_num, {})
        md = cd.get("markdown", "") if isinstance(cd, dict) else (cd if isinstance(cd, str) else "")
    except asyncio.TimeoutError:
        logger.error(f"Ch{ch_num} gen timeout (300s)")
    except Exception as e:
        logger.error(f"Ch{ch_num} gen failed: {e}")
    finally:
        # 🔴 事件桥必须「投哨兵 + 取消 + 有上限地等」，**不能裸 `await relay_task`**。
        #    `_bridge_agent_events` 长期停在 `asyncio.wait_for(q.get(), 0.5)` 上，
        #    实测 `cancel()` 之后 `await relay_task` **永不返回** —— 整个图就此停摆：
        #    日志停在「[GEN] 第N章…生成」**之前**、进程 0% CPU、无任何网络连接，
        #    既不报错也不超时（`Ch{n} gen failed/timeout` 都不会打）。
        #    先投哨兵让它把余下事件转发完主动退出；再给 5s 兜底上限，
        #    超时也照样前进 —— 桥只是转发事件的旁路，绝不能拖住正文生成。
        try:
            q.put_nowait({"event": "_end", "data": {}})
        except Exception:
            pass
        relay_task.cancel()
        try:
            await asyncio.wait_for(relay_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"事件桥 5s 内未退出（ch{ch_num}），放弃等待，继续生成")
        except asyncio.CancelledError:
            pass
    _mark(state, f"chapter_{ch_num}")  # 🔴 每章生成结束计时

    # 🔴 骨架保护（与 scripts/gen_full_report.py 同一判据）：
    #    · 生成失败/超时 → `md` 为空，直接写回会把**整章清空**；
    #    · LLM 超时或重试耗尽会退化成「（本节待补充：…）」占位骨架，长度也够，
    #      会一路写进报告。
    #    两种情况都保留上一版正文（若有）。
    _prev = (state.get("chapters", {}) or {}).get(ch_num) or {}
    _prev_md = _prev.get("markdown", "") if isinstance(_prev, dict) else str(_prev or "")
    if not (md or "").strip():
        if _prev_md.strip():
            md = _prev_md
            logs.append(f"  ⚠️ 第{ch_num}章本次无产出，保留上一版正文（{len(_prev_md)}字）")
    elif (("本节待补充" in md) or (md.count("【待补充】") >= 2)) \
            and len(_prev_md.strip()) > len(md.strip()) * 2:
        md = _prev_md
        logs.append(f"  ⚠️ 第{ch_num}章新产出疑为占位骨架，保留上一版正文（{len(_prev_md)}字）")

    ch["raw_content"] = md or f"【第{ch_num}章生成失败，请人工复核】"
    state["outline_list"] = outline_list
    # 🔴 质量重写模式：直接把新内容写回 chapters，推进 idx 继续下一个重写章节
    if quality_rewrite is not None:
        chapters = state.setdefault("chapters", {})
        chapters[ch_num] = {"markdown": md, "title": ch["title"], "status": "rewritten"}
        state["chapters"] = chapters
        state["_chapter_idx"] = idx + 1
        state["_next_action"] = "generate"  # 回 chapter_generate 处理下一个重写章节
    else:
        # 逐章硬规则审核已移除：章节直接入库并自推进；全部章生成完 → quality_review 唯一把关
        chapters = state.setdefault("chapters", {})
        chapters[ch_num] = {"markdown": md, "title": ch["title"], "status": "passed"}
        state["chapters"] = chapters
        state["_chapter_idx"] = idx + 1
        state["_next_action"] = "generate" if idx + 1 < len(outline_list) else "quality_review"
    logs.append(f"  ✍️ 第{ch_num}章「{ch['title']}」已生成 ({len(md)}字)")
    logger.info(f"[GEN] 第{ch_num}章「{ch['title']}」生成 {len(md)} 字 (idx={idx}, agent={'None' if not agent else agent.__class__.__name__})")
    await _emit("generating", {"analysis":"done","outline":"done","knowledge":"done",
                               "generation":"running","quality":"pending","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# (逐章硬规则审核层已移除) 每章生成后直接入库并自推进，
# 质量/数据/规范把关统一收敛到终稿 quality_review。


def route_after_generate(state: ReportWorkflowState) -> str:
    """条件路由（章节生成后自推进，无逐章硬规则审核节点）。

    - "generate" → 回到 chapter_generate 生成下一章
      （正常模式按大纲顺序；质量重写模式按 _quality_rewrite_chapters 列表）
    - "quality_review" → 全部章节已生成/重写完毕，进入终稿质量审查
    """
    return state.get("_next_action", "quality_review")


def route_after_quality(state: ReportWorkflowState) -> str:
    """终稿质量审查后的条件路由。

    - "quality_rewrite" → 回 chapter_generate 重写问题章节（质量循环）
    - "assemble" → 质量通过，进入组装
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

        # 🔴 事实比对：报告各章关键数字回对项目事实（唯一事实源），对不上 → 数据问题
        from app.validation.content_guardrails import (
            build_project_facts, check_chapters_against_facts,
        )
        facts = build_project_facts(state.get("filled_data", {}) or {})
        fact_issues = check_chapters_against_facts(state.get("chapters", {}) or {}, facts)
        if fact_issues:
            # 合并进审核结果，让现有重写循环识别这些章节
            _regen_set = set(result.get("regenerate_chapters", []) or [])
            for fi in fact_issues:
                fi["chapter"] = int(fi["chapter"])
                result.setdefault("all_issues", []).append(fi)
                _regen_set.add(fi["chapter"])
            result["regenerate_chapters"] = sorted(_regen_set)
            logs.append(f"📊 数据比对：发现 {len(fact_issues)} 处报告数字与资料不一致，触发对应章节重写")
            for fi in fact_issues[:8]:
                logs.append(f"   ⚠️ {fi['message']}")

        # 🔴 Skill 输出解析 → 章节级审核任务（禁止只打日志不生成任务）
        from app.validation.audit_dispatcher import (
            parse_skill_audit_to_chapter_tasks, split_tasks, collect_global_human_items,
        )
        chapter_tasks = parse_skill_audit_to_chapter_tasks(result)
        state["chapter_audits"] = chapter_tasks
        ai_chapters, human_chapters = split_tasks(chapter_tasks)

        # 🔴 终稿阶段新发现的「需人工介入」章节 → 推入人工队列（仅人工复核开启时）
        human_items = state.setdefault("human_items", {})
        if ENABLE_HUMAN_REVIEW:
            push_human_chapters(state, human_chapters)
        # chapter=0 的全局人工待办（如缺营业执照）→ 单独承载，不入具体章重写
        global_items = collect_global_human_items(result)
        if global_items:
            state.setdefault("_global_human_items", []).extend(global_items)

        # 🔴 全局重写队列只保留「还允许 AI 修复」的章节：
        #    过滤已人工介入（human_queue / human_approved / human_override）的章节
        from app.validation.audit_dispatcher import filter_ai_rewrite_chapters
        rewrite_chapters = filter_ai_rewrite_chapters(
            result.get("regenerate_chapters", []),
            human_items, state.get("human_queue", []), ai_chapters,
        )
        quality_round = state.get("_quality_round", 0)
        if rewrite_chapters and quality_round < MAX_QUALITY_ROUNDS:
            # 🔴 控制每轮重写章节数（避免全量重写拖时间，最多5章/轮）
            rewrite_chapters = _drop_verbatim_chapters(rewrite_chapters,
                                                       domain_of_state(state))
            rewrite_list = sorted(rewrite_chapters)[:5]
            if len(rewrite_chapters) > 5:
                logs.append(f"⚠️ 需重写章节过多({len(rewrite_chapters)})，本轮只重写前{len(rewrite_list)}章，其余保留原内容")
            # 设置重写队列：把需重写的章节号记录，路由回生成
            state["_quality_rewrite_chapters"] = rewrite_list
            state["_quality_rewrite_issues"] = {
                str(c): [i.get("message","") for i in result.get("all_issues", [])
                         if i.get("chapter") == c and i.get("severity") == "critical"]
                for c in rewrite_list
            }
            state["_quality_round"] = quality_round + 1
            state["_chapter_idx"] = 0  # 从头重写问题章节
            state["_next_action"] = "quality_rewrite"
            logs.append(f"🔄 第{quality_round + 1}轮质量修正：重写 {len(rewrite_list)} 章")
            await _emit("quality", {"analysis":"done","outline":"done","generation":"running","quality":"running","assembly":"pending"})
            return state
        elif rewrite_chapters:
            # 🔴 质量轮数已耗尽仍有问题章节
            state["_quality_audit"] = result
            state["_quality_audit"]["passed"] = False
            state["_quality_unfixed"] = sorted(rewrite_chapters)
            if ENABLE_HUMAN_REVIEW:
                # 人工复核开启 → 推入人工队列，不静默放行
                push_human_chapters(state, rewrite_chapters)
                logs.append(f"⚠️ 已达最大质量修正轮数({MAX_QUALITY_ROUNDS})，仍有 {len(rewrite_chapters)} 章未修复: {sorted(rewrite_chapters)}，已推入人工队列")
            else:
                # 人工复核关闭 → agent 自行生成，保留当前内容继续，不暂停不入队
                logs.append(f"⚠️ 已达最大质量修正轮数({MAX_QUALITY_ROUNDS})，仍有 {len(rewrite_chapters)} 章未修复: {sorted(rewrite_chapters)}，agent 自行生成（保留当前内容）")
            logger.warning(f"[QUALITY] 轮数耗尽未修复章节: {sorted(rewrite_chapters)}")
    except Exception as e:
        logger.warning(f"Quality review failed (non-critical): {e}")
        # 🔴 异常时不得视为通过——标记未通过，避免问题报告静默进最终报告
        state["_quality_audit"] = {"passed": False, "error": str(e)}
        from app.agent.chapter_catalog import all_chapters
        state["_quality_unfixed"] = all_chapters(domain_of_state(state)) if state.get("chapters") else []

    # 🔴 人工复核检查点：有人工队列未处理 → 中断等前端提交意见
    #   （ENABLE_HUMAN_REVIEW=false 时直接跳过，agent 自行生成，报告直接产出）
    if not ENABLE_HUMAN_REVIEW:
        state["human_queue"] = []
        logs.append("ℹ️ 人工复核已关闭，agent 自行生成全部章节")
        state["_next_action"] = "assemble"
        await _emit("quality", {"analysis":"done","outline":"done","generation":"done","quality":"done","assembly":"pending"})
        return state
    _human_queue = state.get("human_queue", [])
    _human_items = state.get("human_items", {})
    _pending = [c for c in _human_queue
                if not _human_items.get(c, {}).get("human_approved")
                and not _human_items.get(c, {}).get("human_override")]
    state["human_queue"] = _pending
    if _pending:
        from langgraph.types import interrupt
        resume_payload = interrupt({
            "type": "human_review",
            "chapters": sorted(_pending),
            "human_items": {c: _human_items.get(c, {}) for c in _pending},
        })
        # resume 后：把前端提交的人工意见/审批标记写回 state
        if isinstance(resume_payload, dict):
            submitted = resume_payload.get("human_items", {})
            if submitted:
                merged_items = state.setdefault("human_items", {})
                for c, item in submitted.items():
                    if isinstance(item, dict):
                        target = merged_items.setdefault(int(c), {})
                        target.update(item)
                state["human_items"] = merged_items
                # 清掉已处理项，避免重复入队
                state["human_queue"] = [
                    c for c in state.get("human_queue", [])
                    if not merged_items.get(c, {}).get("human_approved")
                    and not merged_items.get(c, {}).get("human_override")
                ]
                # 模式2a：人工直接改写全文 → 立即写回 chapters，保证全审批/全覆盖场景也能生效
                chapters_state = state.setdefault("chapters", {})
                for c, item in merged_items.items():
                    if isinstance(chapters_state, dict) and item.get("human_override") and item.get("override_content"):
                        try:
                            cc = int(c)
                        except (ValueError, TypeError):
                            continue
                        chapters_state[cc] = {
                            "markdown": item["override_content"],
                            "title": chapters_state.get(cc, {}).get("title", f"第{cc}章"),
                            "status": "human_reviewed",
                        }
                        state["chapters"] = chapters_state
                        logs.append(f"  👤 第{cc}章采用人工改写内容（resume 后直接生效）")
        # 有「人工意见」的章节 → 路由回生成重写（模式1）
        rewrite_targets = _drop_verbatim_chapters([
            c for c in state.get("human_queue", [])
            if state.get("human_items", {}).get(c, {}).get("human_opinion")],
            domain_of_state(state))
        if rewrite_targets:
            state["_quality_rewrite_chapters"] = sorted(rewrite_targets)
            state["_quality_rewrite_issues"] = {str(c): [] for c in rewrite_targets}
            state["_chapter_idx"] = 0
            state["_next_action"] = "quality_rewrite"
            await _emit("quality", {"analysis":"done","outline":"done","generation":"running","quality":"running","assembly":"pending"})
            return state

    state["_next_action"] = "assemble"
    await _emit("quality", {"analysis":"done","outline":"done","generation":"done","quality":"done","assembly":"pending"})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Node 7b-2: gap_review — 把「正文里 agent 自己承认没数据」的地方交给用户
# ═══════════════════════════════════════════════════════════════════════════════
async def _draft_gap_fills(pending: list, filled: dict,
                           domain: str = "stability") -> Dict[str, str]:
    """给每个缺口**先起草一段能直接用的话**，再拿给用户确认。

    🔴 业主口径（2026-09-22）：
        「『待取得批准文件后依法组织实施征收』把数据推给后续程序，请确认现在能否补齐
          —— 这类问题是这么问出来的。**用户希望的是你帮忙他们写这块**。」
        「群众意见你不能没有解析就去直接让用户去写，这一段是需要你发挥的，
          用户只能提供想法。」

        也就是说：把「请你补一段话」这种**把活推给用户**的问法，改成
        「我按事实卡拟了一版，你看着改」—— 用户只需给方向，不用自己组织行文。

    Returns: {缺口 key: 草稿文字}（失败返回空 dict，调用方回退成空白输入框）
    """
    if not pending:
        return {}
    try:
        from app.services.master_orchestrator import build_project_card
        from app.services.llm_service import LLMService
        from app.agent.chapter_catalog import report_kind_name
        _card = build_project_card(filled or {}, domain)
        _items = []
        for _i, (_key, _ch, _g) in enumerate(pending, 1):
            _items.append(
                f"{_i}. （第{_ch}章）问题：{str(_g.get('detail') or '')[:160]}\n"
                f"   原句：{str(_g.get('sentence') or '')[:160]}"
            )
        _prompt = (
            f"下面是一份{report_kind_name(domain)}里**缺少素材支撑**的几处。\n"
            "请针对每一处，**按事实卡写出可以直接替换原句的中文段落**（2–4 句）。\n"
            "写得像有经验的编制人员说话：短句、直接说事、不用公文腔，"
            "能用数字就用数字（面积、户数、日期、文号）。\n\n"
            "铁律：\n"
            "1. **只能依据「本项目事实卡」里的真实数据**（面积/日期/单位/人名/问卷数）；\n"
            "2. 事实卡里没有的，**不要编数字**，改写成不含捏造数据的规范表述；\n"
            "3. 不要出现「以…为准」「待…后补充」这类把问题推走的说法 —— 那正是要修的；\n"
            "4. 直接给正文，不要解释、不要小标题。\n\n"
            f"{_card}\n\n"
            "── 待处理的缺口 ──\n" + "\n".join(_items) + "\n\n"
            '严格输出 JSON：{"1": "第1处的正文", "2": "第2处的正文", ...}（键就是上面的序号）'
        )
        _res = await asyncio.wait_for(
            LLMService().chat(messages=[{"role": "user", "content": _prompt}],
                              max_tokens=4000, temperature=0.3),
            timeout=180.0,
        )
        import json as _json
        _m = re.search(r'\{[\s\S]*\}', str(_res or ""))
        if not _m:
            return {}
        _data = _json.loads(_m.group(0))
        out = {}
        for _i, (_key, _ch, _g) in enumerate(pending, 1):
            _v = str(_data.get(str(_i)) or "").strip()
            if _v:
                out[_key] = _v
        logger.info(f"[缺口] 已为 {len(out)}/{len(pending)} 处草拟了替代文字")
        return out
    except Exception as e:
        logger.warning(f"缺口草拟跳过（改为让用户自行填写）: {e}")
        return {}


def _apply_gap_drafts(chapters: dict, pending: list, drafts: dict):
    """把 AI 拟好的替代句**就地替换**正文里的遁词句。

    `pending` 是 `[(key, 章号, gap_dict)]`，`gap_dict["sentence"]` 是原始那句遁词。
    返回 `(已替换数, 未替换数, 已解决的 key 集合)`。

    为什么必须在正文里替换：以前草稿只进 `filled["_fallbacks"]`，正文那句
    「以…为准」原样留着 —— 报告读起来仍是空心话，前端还一直显示「N 处待补充」。
    """
    applied, left, resolved = 0, 0, set()
    for key, ch, g in (pending or []):
        draft = str((drafts or {}).get(key, "") or "").strip()
        sent = str((g or {}).get("sentence") or "").strip()
        ch_obj = chapters.get(ch) or chapters.get(str(ch))
        md = str((ch_obj or {}).get("markdown") or "") if isinstance(ch_obj, dict) else ""
        if draft and sent and sent in md:
            ch_obj["markdown"] = md.replace(sent, draft, 1)
            resolved.add(key)
            applied += 1
        else:
            left += 1
    return applied, left, resolved


async def node_gap_review(state: ReportWorkflowState) -> ReportWorkflowState:
    """生成后缺口核对。

    🔴 业主："agent 觉得缺少什么找用户。"

        `gap_detector.detect_evasions()` 找的就是**模型自己承认没数据**的地方 ——
        它写「实际参加人员以座谈会签到表为准」时，等于在说「我这里没数据」。
        这些缺口此前只写进 Word 批注（客户看得到），却没有**回问用户的通道**。

        这里把它接上：有 warn 级缺口 → interrupt 问用户 → 用户补的内容进
        `filled_data["_supplement_notes"]`（事实卡会带上，写到那章就有依据了），
        缺口章进重写队列（复用现成的 `_quality_rewrite_chapters` 回环）。
        用户没填的记为「已确认保持」，下次不再问。
    """
    logs = state.setdefault("logs", [])
    chapters = state.get("chapters", {}) or {}
    filled = state.setdefault("filled_data", {})

    # 只跑一次：重写回环回来时不再触发（否则会无限循环）
    if state.get("_gap_review_done"):
        state["_next_action"] = "assemble"
        return state
    state["_gap_review_done"] = True

    acked = set(state.get("_gap_ack") or [])
    try:
        from app.services.gap_detector import collect_gaps
        _ch_md = {k: (c or {}).get("markdown", "")
                  for k, c in chapters.items() if isinstance(c, dict)}
        gaps = collect_gaps(state, _ch_md)
    except Exception as e:
        logger.warning(f"缺口检测跳过: {e}")
        gaps = {}

    items, pending = [], []
    for ch in sorted(gaps):
        for i, g in enumerate(gaps[ch] or []):
            if g.get("severity") != "warn":
                continue
            key = f"_gap_{ch}_{i}"
            if key in acked:
                continue
            _sent = str(g.get("sentence") or "").strip()
            items.append({
                "key": key, "label": str(g.get("detail") or "")[:80] or "请补充依据",
                "type": "text", "chapter": f"第{ch}章",
                "maxlength": 200, "example": "", "current": "",
                "source": "缺口核对",
                "used_in": (f"原句：{_sent[:100]}" if _sent else ""),
            })
            pending.append((key, int(ch), g))

    if not items:
        state["_next_action"] = "assemble"
        return state

    # 🔴 **先让 agent 拟一版**再问用户 —— 不是把活推给用户。
    #    弹窗里预填草稿，用户只需「采纳 / 改一句方向 / 跳过」（业主：
    #    「用户希望的是你帮忙他们写这块」「用户只能提供想法」）。
    _drafts = await _draft_gap_fills(pending, filled, domain_of_state(state))
    for _it in items:
        _draft = _drafts.get(_it["key"], "")
        _it["current"] = _draft
        _it["source"] = "AI 已拟稿，可采纳或改写" if _draft else "缺口核对"
        _it["example"] = "直接采纳，或写一句你的想法让 AI 重写"

    # 🔴 两步走（业主 2026-10-29 口径）：
    #    ① AI 能拟出替代句的缺口 → **就地改写正文**，不打扰用户；
    #    ② 真缺硬数据、AI 也拟不出的 → **弹窗问用户**（以前只在清单里列出来）。
    #
    # ⚠️ 改写必须放在 interrupt **之后**执行：LangGraph 的 `interrupt()` 恢复时，
    #    节点会**从头重跑**、中断前写进 state 的内容全部丢弃（只有节点 return 才落盘）。
    #    所以这里先**预判**哪些能改写（纯读），弹窗回来后统一落笔。
    def _can_rewrite(key, ch, g) -> bool:
        d = str(_drafts.get(key, "") or "").strip()
        sent = str((g or {}).get("sentence") or "").strip()
        obj = chapters.get(ch) or chapters.get(str(ch))
        md = str((obj or {}).get("markdown") or "") if isinstance(obj, dict) else ""
        return bool(d and sent and sent in md)

    _fb = filled.setdefault("_fallbacks", {})
    _resolvable = {k for k, c, g in pending if _can_rewrite(k, c, g)}
    _residual = [it for it in items if it.get("key") not in _resolvable]

    def _finalize_rewrite():
        """落笔：替换正文、草稿进兜底区、清旧清单。返回改写处数。"""
        applied, _left_n, _res = _apply_gap_drafts(chapters, pending, _drafts)
        for _k, _c, _g in pending:
            _d = str(_drafts.get(_k, "") or "").strip()
            if _d:
                _fb.setdefault(f"gap:{_k}", _d)
        # 旧清单里已经被改写掉的句子不能留着，否则组装时 `collect_gaps` 又并回来
        state["_chapter_gaps"] = {}
        state["filled_data"] = filled
        state["chapters"] = chapters
        return applied, _res

    # 全部能改完，或用户明确关掉了缺口问询 → 直接组装
    if not _residual or not ENABLE_GAP_INQUIRY:
        _applied, _ = _finalize_rewrite()
        acked |= {k for k, _c, _g in pending}      # 不重复问
        state["_gap_ack"] = sorted(acked)
        logs.append(f"✍️ 正文 {len(items)} 处缺口：AI 已就地改写 {_applied} 处"
                    + (f"，{len(_residual)} 处保留提示（缺硬数据）" if _residual else ""))
        state["_next_action"] = "assemble"
        return state

    from langgraph.types import interrupt
    _res_keys = {it["key"] for it in _residual}
    logs.append(f"✍️ 可自动改写 {len(_resolvable)} 处；⏸️ 还有 {len(_residual)} 处缺硬数据，"
                f"已弹窗请你补充（可跳过）")
    ui = interrupt({"type": "gap_review", "gaps": _residual, "missing_fields": _residual})

    # 恢复后**先落笔改写**（中断前写的不算数），再处理用户补充的内容
    _applied, _resolved = _finalize_rewrite()

    _notes = filled.setdefault("_supplement_notes", [])
    rewrite_ch, rewrite_issues = [], {}
    if isinstance(ui, dict):
        for key, ch, g in pending:
            if key not in _res_keys:        # 已由 AI 就地改写，不再问
                continue
            val = ui.get(key, "")
            if val and str(val).strip():
                _notes.append({"chapter": ch, "note": str(val).strip(),
                               "about": str(g.get("sentence") or "")[:120]})
                if ch not in rewrite_ch:
                    rewrite_ch.append(ch)
                    rewrite_issues[str(ch)] = []
                rewrite_issues[str(ch)].append(f"补数据：{str(val).strip()[:120]}")
            else:
                acked.add(key)          # 用户没填 = 确认保持，别再问
    state["_gap_ack"] = sorted(acked)
    state["filled_data"] = filled

    if rewrite_ch:
        state["_quality_rewrite_chapters"] = rewrite_ch
        state["_quality_rewrite_issues"] = rewrite_issues
        state["_chapter_idx"] = 0
        logs.append(f"🔁 用户补充了 {len(rewrite_ch)} 章的素材，重写这些章：{rewrite_ch}")
        state["_next_action"] = "quality_rewrite"
    else:
        state["_next_action"] = "assemble"
    return state


def route_after_gap(state: ReportWorkflowState) -> str:
    """缺口核对后的路由：补了素材就回写那几章；否则去评审表。

    🔴 「评审表环节」是**稳评专有** —— 稳评第 3 章是江苏省制式评审表，且这一步会
       **停下来问**「评审表专用字段」（负责人/职务/联系人/电话）。复垦没有评审表章，
       走进去就停在提问上等输入，**永远到不了组装**。按域直连组装。
    """
    _a = state.get("_next_action", "assemble")
    if _a == "quality_rewrite":
        return "quality_rewrite"
    from app.agent.chapter_catalog import has_review_table
    from app.domains import domain_of_state
    return "review_table" if has_review_table(domain_of_state(state)) else "assemble"


# ═══════════════════════════════════════════════════════════════════════════════
# Node 8: review_table — 生成评审表（原注释错写成 assemble_final_report）
# ═══════════════════════════════════════════════════════════════════════════════
async def node_review_table(state: ReportWorkflowState) -> ReportWorkflowState:
    """生成评审表：① 先按需询问「评审表专用字段」（负责人/职务/联系人/电话），② 再生成评审表 docx。

    这是「评审表环节」——评审表专用字段在这里才问，不在章节生成时问。
    """
    logs = state.setdefault("logs", [])
    filled = state.setdefault("filled_data", {})

    # 🔴 只有**AI 猜的**值才清掉；用户填的 / 材料识别出来的**一律保留**
    #    （业主 2026-09-29：「要解析所有的资料，agent 要能识别 PDF 中的资料内容
    #      并且运用在报告上」—— 征收预公告落款里的责任单位联系人与电话、
    #      公司档案里的负责人，都是**资料里真实有的信息**，
    #      再回头问用户一遍等于让用户重抄一遍材料）。
    #    判据用 `field_sources` 的来源等级，而不是 `_locked_` —— 后者材料解析也会打，
    #    回答不了「这个值用户确认过没有」。
    from app.services import field_sources as _fsr
    _kept = []
    for _spec in REVIEW_TABLE_FIELDS:
        _k = _spec[0] if isinstance(_spec, (tuple, list)) else str(_spec)
        if not str(filled.get(_k) or "").strip():
            continue
        _src = _fsr.source_of(filled, _k)
        if filled.get(f"_locked_{_k}") or _src in (_fsr.USER, _fsr.MATERIAL):
            _kept.append(_k)      # 用户填过 / 材料里识别到 → 保留，不再问
        else:
            filled[_k] = ""       # AI 抽的（实测抓到过我方人员/公示联系人）→ 清掉重问
    if _kept:
        logs.append("📋 评审表沿用资料/用户已有信息：" + "、".join(_kept))
    state["filled_data"] = filled

    # ① 评审表专用字段缺失 → interrupt 询问（归属标为「评审表」）
    _miss = _missing_for(filled, REVIEW_TABLE_FIELDS, _REVIEW_TABLE_OWNER)
    if _miss:
        from langgraph.types import interrupt
        logs.append(f"⏸️ 评审表缺少 {len(_miss)} 项信息，暂停等待补充")
        _ui = interrupt({"type": "missing_data", "chapter": "评审表", "missing_fields": _miss})
        _skipped_rt = []
        if isinstance(_ui, dict):
            for _f in _miss:
                _v = _ui.get(_f["key"], "")
                if _v and str(_v).strip():
                    filled[_f["key"]] = str(_v).strip()
                    filled[f"_locked_{_f['key']}"] = True
                    logs.append(f"✅ 评审表已补充 {_f['label']}: {_v}")
                else:
                    _skipped_rt.append(_f)
        else:
            _skipped_rt = list(_miss)
        # 🔴 同章节弹窗：跳过的必须打 `_confirmed_`，否则无限重弹
        if _skipped_rt:
            try:
                from app.services import field_sources as _fs2
                _fs2.mark_confirmed(filled, *[f["key"] for f in _skipped_rt if f.get("key")])
            except Exception:
                pass
        state["filled_data"] = filled

    # ② 生成评审表 docx（江苏省统一制式 14 栏）
    try:
        import os as _os
        from app.services.review_table_service import generate_review_table
        from app.config import settings as _settings
        _sid = str(state.get("session_id", "report"))[:12]
        _out = str(_settings.STORAGE_DIR / "generated" / f"评审表_{_sid}.docx")
        _os.makedirs(_os.path.dirname(_out), exist_ok=True)

        # 决策概况 7 条 + 各方意见两栏：用 LLM 从正文归纳（失败则用规则抽取兜底）
        # 🔴 「基层组织意见 / 党委政府或相关部门意见」规则抽取常落空（动态小节名对不上
        #    关键词），印出来是个「【待填写】」空栏。一并交给 agent 归纳。
        _extracted = {}
        try:
            from app.agent.agents.review_table_agent import ReviewTableAgent
            _agent = ReviewTableAgent()
            _basic = await _agent._summarize_decision_overview(state, {}) or {}
            try:
                _opin = await _agent._summarize_opinions(state) or {}
                _basic.update(_opin)
            except Exception as _oe:
                logger.warning(f"各方意见归纳跳过: {_oe}")
            if _basic:
                _extracted = {"项目基本情况": _basic}
                # 🔴 键名必须是 `_review_extracted` —— `assemble()` 里**本来就在读它**
                #    （`state.get("_review_extracted")`），只是从来没有人往里写过：
                #    于是 agent 归纳的决策概况/两栏意见被整段丢掉，
                #    主报告第三章永远显示规则兜底的旧文本或「【待填写】」。
                state["_review_extracted"] = _extracted
                state["_review_table_extracted"] = _extracted
        except Exception as _ge:
            logger.warning(f"决策概况归纳跳过: {_ge}")

        path = generate_review_table(_out, filled, state.get("chapters", {}) or {}, extracted=_extracted)
        state["review_table_path"] = path
        logs.append(f"📋 评审表已生成: {path}")
    except Exception as e:
        logger.warning(f"评审表生成失败（非致命）: {e}")
    return state


async def node_assemble_final_report(state: ReportWorkflowState) -> ReportWorkflowState:
    """Assemble final DOCX: merge chapters, replace <<IMAGE:>> and [TABLE:] placeholders."""
    state["phase"] = WorkflowPhase.ASSEMBLING.value
    logs = state.setdefault("logs",[])
    chapters = state.get("chapters",{})

    if not chapters:
        state["errors"] = state.get("errors",[]) + ["无章节内容可组装"]
        return state

    # 🔴 方案乙兜底：组装前按事实库确定性强制修正关键数字（面积/支持率/问卷/户数）。
    #   LLM 重写经常改不对，这里用 ProjectFacts 正确值直接替换，保证数据准确。
    try:
        from app.validation.content_guardrails import (
            build_project_facts, fix_chapters_against_facts,
        )
        facts = build_project_facts(state.get("filled_data", {}) or {})
        chapters, fact_fixes = fix_chapters_against_facts(chapters, facts)
        if fact_fixes:
            state["chapters"] = chapters
            logs.append(f"🔧 数据强制修正 {len(fact_fixes)} 处：")
            for ch_num, old, new in fact_fixes[:10]:
                logs.append(f"   第{ch_num}章 {old} → {new}")
    except Exception as e:
        logger.warning(f"fact fix skipped: {e}")

    try:
        # 🔴 这里原本有一次「全文文风统一润色」的 LLM 调用（取 `full_text[:15000]`，
        #    timeout=60.0）。**它是纯浪费，已删除**：
        #      · 结果赋给局部变量 `full_text` 后**再没被读过** —— 组装用的是
        #        `chapters`（下面 `asm_state` 传的就是 chapters），润色稿根本没进报告；
        #      · `for ch in state["outline_list"]: … pass` 那段循环体是空的；
        #      · 唯一实际效果是往日志里加一句「✨ 报告文风已统一润色」——
        #        **一句假话**，会让人以为做过润色。
        #    每次生成白烧 60 秒 + 一次 15000 字的 LLM 调用。
        from app.services.report_assembler import report_assembler
        from app.services.company_service import load_company
        filled = state.get("filled_data",{})
        # 🔴 公司档案在**这里**（异步）读好再传下去：装配器是同步的，
        #    没法自己查异步的 auth 库。读不到 → 空 dict → 报告里显示「待填写」。
        _company = await load_company(state.get("_company_id"))
        asm_state = {
            "session_id": state.get("session_id","report"), "chapters": chapters,
            "filled_data": filled, "_domain": domain_of_state(state),
            # 🔴 公司归属**必须带进来**：装配器靠它决定「公司简介 / 编写单位 /
            #    营业执照·资质证书取哪一套」。外来的用户没有公司 → 报告里显示
            #    「待填写」，**绝不回落成江苏众拓**（那等于把别人的客户抬头印成我们的）。
            #    这个 dict 是白名单式构造 —— 漏加就等于丢，且不会有任何报错。
            "_company_id": state.get("_company_id"),
            "_company": _company,
            "_uploaded_files": state.get("_uploaded_files",[]),
            "_project_materials": state.get("_project_materials",[]),
            "_classified_images": state.get("_classified_images",{}),
            "image_meta_list": state.get("image_meta_list",[]),
            # 🔴 勾选人员**必须带进来**：装配器靠它决定第4章签字表、第7章稳评小组名单、
            #    附件里的培训证书取图。以前没传 → 这三处一律空（界面专有，脚本不受影响）。
            "selected_personnel": state.get("selected_personnel") or [],
            "_selected_personnel": state.get("selected_personnel") or [],
            # 🔴 「选择证书」是另一个独立多选框（最多 3 人）→ 决定前置「公司资质证书」区
            #    （营业执照之后）贴哪几张人员证书图。不传则装配器回退为工作组成员。
            "selected_cert_personnel": state.get("selected_cert_personnel") or [],
            "_selected_cert_personnel": state.get("selected_cert_personnel") or [],
        }
        output = report_assembler.assemble(asm_state)
        if output:
            state["output_path"] = output
            # 🔴 **缺口清单要回写**：装配器把 `collect_gaps` 的结果写进了 `asm_state`
            #    （那是个白名单式构造的**临时 dict**，装配完就丢），不回写的话
            #    session.state 里 `_chapter_gaps` 永远是空 —— 前端 `GET /gaps` 恒返回 0，
            #    缺口就只剩「Word 批注」一个出口。复垦现在不写批注（业主口径），
            #    不回写等于缺口**彻底看不见**（实测 2026-10-10：接口 total=0，
            #    而同一批缺口在批注里明明有 9 章）。
            for _k in ("_chapter_gaps", "_table_issues"):
                if asm_state.get(_k):
                    state[_k] = asm_state[_k]
            state["phase"] = WorkflowPhase.COMPLETE.value
            logs.append(f"✅ 报告已生成: {output}")
            # 🔴 输出各流程耗时，供后续逐步优化
            timing_summary = _timing_summary(state)
            if timing_summary:
                logger.info(f"[TIMING] 各流程耗时: {timing_summary}")
                logs.append(f"⏱️ 流程耗时: {timing_summary}")
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
                    # 🔴 效果验证：检查 skill 规则是否仍被违反（skill 是否生效）
                    try:
                        verify = await skill_service.verify_skill_effect(
                            domain=state.get("_domain", "stability"), full_text=full_text[:50000]
                        )
                        if verify.get("ineffective"):
                            state["_skill_effect"] = verify
                            logs.append(
                                f"🔍 skill效果验证：{verify.get('effective')}条生效，"
                                f"{verify.get('ineffective')}条仍被违反"
                            )
                    except Exception as _e3:
                        logger.warning(f"Skill effect verify skipped: {_e3}")
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
        # 🔴 必须带堆栈：这里只打一句话时，线上只能看到
        #    「'str' object has no attribute 'get'」这种没头没尾的消息，
        #    定位不到是哪一段把 dict 写成了 str（实测 2026-10-28 稳评装配失败）。
        logger.error(f"assemble failed: {e}", exc_info=True)

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Graph Builder
# ═══════════════════════════════════════════════════════════════════════════════
def build_report_workflow() -> StateGraph:
    """Build the report generation workflow with a graph-level chapter loop + final quality review.

    图结构：
      file_parse → field_validate → build_dynamic_outline → outline_check
      → [chapter_generate 自推进循环]（逐章生成，无逐章硬规则审核）
      → quality_review（终稿质量审查 + 自动修复 + 问题章重写循环）
      → assemble_final_report → END

    章节循环是图级循环：chapter_generate 每章生成后直接入库并自推进，
    全部章生成完进入 quality_review（终稿质量审查，唯一质量/数据/规范把关）。
    """
    w = StateGraph(ReportWorkflowState)
    # 🔴 用 timed() 装饰器包装节点，自动统计各流程耗时
    w.add_node("file_parse", timed("file_parse")(node_file_parse))
    w.add_node("field_validate", timed("field_validate")(node_field_validate))
    w.add_node("data_inquiry", timed("data_inquiry")(node_data_inquiry))
    w.add_node("build_dynamic_outline", timed("outline")(node_build_dynamic_outline))
    w.add_node("outline_check", timed("outline_check")(node_outline_check))
    w.add_node("chapter_generate", timed("chapter_generate")(node_chapter_generate))
    w.add_node("quality_review", timed("quality_review")(node_quality_review))
    w.add_node("gap_review", timed("gap_review")(node_gap_review))
    w.add_node("review_table", timed("review_table")(node_review_table))
    w.add_node("assemble_final_report", timed("assemble")(node_assemble_final_report))

    w.set_entry_point("file_parse")
    w.add_edge("file_parse", "field_validate")
    w.add_edge("field_validate", "data_inquiry")
    w.add_edge("data_inquiry", "build_dynamic_outline")
    w.add_edge("build_dynamic_outline", "outline_check")
    w.add_edge("outline_check", "chapter_generate")

    # 🔴 章节循环（生成后自推进，无逐章硬规则审核节点）：
    #   "generate" → chapter_generate 生成下一章（正常模式按大纲 / 质量重写模式按重写列表）
    #   "quality_review" → 所有章节已生成或重写完毕，进入终稿质量审查
    w.add_conditional_edges(
        "chapter_generate",
        route_after_generate,
        {
            "generate": "chapter_generate",
            "quality_review": "quality_review",
        },
    )
    # 🔴 质量循环：条件路由
    #   "quality_rewrite" → 回 chapter_generate 重写问题章节（质量循环）
    #   "assemble" → 通过，进入组装
    w.add_conditional_edges(
        "quality_review",
        route_after_quality,
        {
            "quality_rewrite": "chapter_generate",
            # 🔴 质量通过后**先过缺口核对**：正文里 agent 用遁词圆过去的地方
            #    （「实际参加人员以座谈会签到表为准」这类）请用户补真实内容。
            #    补了就回 chapter_generate 重写那几章，没补才继续走评审表。
            "assemble": "gap_review",
        },
    )
    w.add_conditional_edges(
        "gap_review",
        route_after_gap,
        {
            "quality_rewrite": "chapter_generate",
            "review_table": "review_table",
            # 🔴 无评审表的域（复垦）直连组装，见 route_after_gap
            "assemble": "assemble_final_report",
        },
    )
    # 🔴 评审表环节：缺口核对后，先生成评审表（含按需询问评审表专用字段），再组装报告
    w.add_edge("review_table", "assemble_final_report")
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
            "max_retry": MAX_RETRY,
            # 🔴 图级章节循环状态
            "_chapter_idx": 0, "_chapter_retry": 0, "_next_action": "generate",
            "_gen_prepared": False,
            # 🔴 质量循环状态
            "_quality_round": 0, "_quality_done": False,
            # 🔴 人工干预闭环状态
            "human_queue": [], "human_items": {}, "chapter_audits": {},
        }
        if existing_state:
            for k in ("_pdf_raw_text","_project_materials","_uploaded_files",
                       "_outline","_image_catalog","filled_data","_workflow_logs",
                       # 🔴 报告域标记 —— 由 report.py 的意图识别一次性钉在会话 state 上。
                       #    **必须在图状态起点就带上**：整条工作流里所有按域分叉的消费点
                       #    （_standard_outline 的 14 章 vs 10 章、chapter_kind / target_words /
                       #    sections_for_prompt、装配器、提示词工厂）都经 `domain_of_state(state)`
                       #    取域；图状态里没有它 → 一律回落 "stability" → 复垦会话顶着稳评
                       #    14 章大纲跑（第1章「编制说明」而非复垦的「前言」）。实测 2026-10-09。
                       #    resume 走 LangGraph 检查点，起点带了这里就带得全。
                       "_domain", "_conversation_domain",
                       # 🔴 用户勾选的报告人员（前置工作组名单 + 培训证书取图）
                       "selected_personnel",
                       # 🔴 「选择证书」独立多选框（最多 3 人）→ 公司资质证书区的人员证书图
                       "selected_cert_personnel",
                       # 🔴 座谈会 PDF 问卷解析结果（第9章/表9-1/9-2/附件2/评分支持率）
                       "_questionnaire_result",
                       # 🔴 公司归属（companies 表主键）—— 装配器靠它加载公司档案：
                       #    公司简介 / 编写单位 / 营业执照·资质证书 / 人员证书图全按它取。
                       #    漏传 → load_company(None) 返回空 → 简介一律「待填写」、
                       #    前置资质页整块消失（实测 2026-09-28 就是这个症状）。
                       "_company_id"):
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
