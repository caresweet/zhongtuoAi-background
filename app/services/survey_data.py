"""问卷统计数据 —— 权威来源是**座谈会 PDF 逐页视觉解析**的结果。

业主口径：
    「问卷调查分析是依据座谈会 PDF 中的图表计算的 —— 群众调查分析和部门调查分析。」

因此本模块不再「写死一份数据集」，而是**分层解析**（越靠前越权威）：

    ① state["_questionnaire_result"]        —— **上传座谈会 PDF 时后台自动解析**的结果（会话内）
    ② storage/questionnaire_cache/summary_*.json —— 该 PDF 上次解析的落盘缓存（秒回，不烧 token）
    ③ VERIFIED_SURVEY_DATASETS（按公告文号）  —— 人工核验过的兜底数据集
    ④ 空 {}                                 —— 由调用方回退到 OCR/图片提取的结果

消费方完全不变：`generated_sections.survey_analysis` / `dept_survey_analysis`
（第9章正文、表9-1/9-2、附件2、评分体系的支持率都从这里取）。
"""

from typing import Dict, Any
import logging
import os

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 已核验数据集（人工逐份核对过的结果，作为**兜底**保留）
# ═══════════════════════════════════════════════════════════════════

VERIFIED_SURVEY_DATASETS: Dict[str, Dict[str, Any]] = {
    # 洪拟征告〔2026〕7号（朱坝街道及三圩社区商业服务业设施用地项目）
    "洪拟征告〔2026〕7号": {
        # ── 群众问卷（50份，全部支持、全部了解）──
        "total_samples": 50,
        "valid_samples": 50,
        "support_count": 50,
        "oppose_count": 0,
        "neutral_count": 0,
        "conditional_support": 0,
        "support_rate": 100.0,
        "oppose_rate": 0.0,
        # 六维勾选统计（身份/年龄/职业/了解/态度/诉求）
        "identity_groups": {"本地居民": 48, "租住本区": 1, "附近上班": 1},
        "age_groups": {"16～35": 5, "36～55": 26, "56以上": 19},
        "occupation_groups": {"机关事业": 2, "企业": 4, "待业": 2, "其他": 42},
        "awareness_levels": {"了解": 50},
        "attitude_options": {"支持": 50},
        "resolution_methods": {"调解": 39, "正常反映": 7, "其他": 4},
        # ── 部门问卷（2份）──
        "dept_survey": {
            "total_units": 2,
            "awareness": {"了解": 2},
            "publicity_satisfaction": {"很满意": 2},
            "policy_understanding": {"了解": 2},
            "concerns": {"补偿费用": 2, "土地换社保": 2},
            "risk_assessment": {"低风险": 2},
            "stability_confidence": {"有信心": 2},
            "support_attitude": {"支持": 2},
        },
        "survey_scope": "洪拟征告〔2026〕7号土地征收决策范围内群众及相关部门",
    },
}

# 公告文号 → 数据集的备用匹配键（用于从 filled_data.doc_reference 模糊匹配）
_DATASET_ALIASES = {
    "洪拟征告〔2026〕7号": ["洪拟征告〔2026〕7号", "洪拟征告(2026)7号", "洪拟征告（2026）7号", "2026-7号"],
}

# 座谈会 PDF 的识别关键词（文件名命中即认为是问卷扫描件）
_SYMPOSIUM_HINTS = ("座谈会", "问卷", "调查表", "群众座谈", "村民座谈")

# 🔴 负向关键词：命中即**排除**——这些不是问卷正文（签到表由 signin_parser 单独解析）。
#    真实案例：用户按文件夹上传「村民调查表1/签到表.pdf」→ original_name 被拼成
#    「村民调查表1_签到表.pdf」，仅凭「调查表」三字命中，就会让 find_symposium_pdf
#    把**签到表**当成问卷选走 → 解析必然为空 → 真正的《调查表.pdf》永远轮不到。
_NOT_SURVEY_HINTS = ("签到表", "签到", "会议记录", "会议纪要",
                     "专家意见", "综合意见", "评审意见")

# 🔴 正向打分：越像问卷正文分越高（取分最高的候选，而不是第一个）
_SURVEY_SCORE_RULES = (
    ("群众座谈", 60), ("村民座谈", 60), ("座谈会", 50),
    ("问卷调查表", 40), ("问卷", 35), ("调查表", 30),
)


def _survey_score(name_or_path: str) -> int:
    """候选 PDF 打分：正=像问卷正文；负/0=明确不是或不像（签到表等）。"""
    n = str(name_or_path or "")
    if not n:
        return 0
    if any(x in n for x in _NOT_SURVEY_HINTS):
        return -100
    return sum(w for kw, w in _SURVEY_SCORE_RULES if kw in n)


def match_dataset_key(doc_reference: str) -> str:
    """根据公告文号匹配已核验数据集键；未匹配返回空串。"""
    if not doc_reference:
        return ""
    for key, aliases in _DATASET_ALIASES.items():
        for alias in aliases:
            # 去掉括号差异后再匹配，容忍全角/半角括号
            norm_doc = str(doc_reference).replace("〔", "").replace("〕", "").replace("(", "").replace(")", "").replace("（", "").replace("）", "")
            norm_alias = alias.replace("〔", "").replace("〕", "").replace("(", "").replace(")", "").replace("（", "").replace("）", "")
            if norm_alias in norm_doc or norm_doc in norm_alias:
                return key
    return ""


# ═══════════════════════════════════════════════════════════════════
# ① 找座谈会 PDF
# ═══════════════════════════════════════════════════════════════════

def _abs(p: str) -> str:
    """会话里存的是**相对** storage 的路径（`attachments/<uuid>.pdf`），
    直接 `os.path.exists` 永远为假 —— 这里统一补上 `STORAGE_DIR`。

    ⚠️ `settings` 走函数内导入：本模块被 `agent` 侧导入，
       模块级 `from app.config import settings` 会踩到既有的循环导入。
    """
    s = str(p or "")
    if not s:
        return ""
    if os.path.isabs(s):
        return s
    from app.config import settings
    return str(settings.STORAGE_DIR / s)


def looks_like_symposium_pdf(name_or_path: str) -> bool:
    """文件名是否像座谈会/问卷扫描件 —— **上传后自动触发解析**的判据。

    🔴 不能只看关键词命中：文件夹名会把「调查表」三字带给
       「村民调查表1_签到表.pdf」这类签到表文件，导致把签到表当问卷解析。
       这里用打分制：负向词（签到表/会议记录/专家意见…）直接排除，
       正向词累加打分，**得分必须 > 0** 才算问卷。
    """
    return _survey_score(name_or_path) > 0


def find_symposium_pdfs(state: dict) -> list:
    """在会话上传的文件里找全部问卷扫描件候选（PDF），**按"像问卷的程度"降序**。

    🔴 之前只返回第一个命中关键词的文件 —— 而签到表常因文件夹名
       （「村民调查表1_签到表.pdf」）抢先命中，真正的《调查表.pdf》永远轮不到。
       现在按 `_survey_score` 排序：签到表等负分文件被排除，问卷排最前。
    """
    if not isinstance(state, dict):
        return []
    cands: dict = {}

    def _add(path, name) -> None:
        p = _abs(path)
        if not p or not p.lower().endswith(".pdf"):
            return
        score = max(_survey_score(name), _survey_score(path))
        if score <= 0:
            return
        if os.path.exists(p) and p not in cands:
            cands[p] = score

    for key in ("_uploaded_files", "_project_material_files", "_materials"):
        for f in (state.get(key) or []):
            if isinstance(f, dict):
                _add(f.get("path") or f.get("file_path"), f.get("original_name") or f.get("name"))
            else:
                _add(f, f)
    # 项目材料事实卡里也可能记了路径
    facts = state.get("_project_material_facts") or {}
    if isinstance(facts, dict):
        for k, v in facts.items():
            if "pdf" in str(k).lower() and isinstance(v, str):
                _add(v, v)

    return sorted(cands, key=lambda p: cands[p], reverse=True)


def find_symposium_pdf(state: dict) -> str:
    """返回得分最高的问卷扫描件（兼容旧接口）。找不到返回空串。"""
    lst = find_symposium_pdfs(state)
    return lst[0] if lst else ""


# ═══════════════════════════════════════════════════════════════════
# ② 分层解析
# ═══════════════════════════════════════════════════════════════════

def _from_state_result(state: dict) -> Dict[str, Any]:
    """① 会话内已有的解析结果（用户点过「解析问卷」或后台已跑完）。"""
    r = (state.get("_questionnaire_result") if isinstance(state, dict) else None) or {}
    return r if isinstance(r, dict) and r.get("total_samples") else {}


def _from_disk_cache(state: dict) -> Dict[str, Any]:
    """② 该座谈会 PDF 上次解析的落盘缓存（按 PDF 指纹，秒回）。"""
    pdf = find_symposium_pdf(state)
    if not pdf:
        return {}
    try:
        from app.services.questionnaire_parser import load_cached_summary
        r = load_cached_summary(pdf)
        return r if isinstance(r, dict) and r.get("total_samples") else {}
    except Exception as e:
        logger.warning(f"读问卷解析缓存失败: {e}")
        return {}


def _from_verified(doc_ref: str) -> Dict[str, Any]:
    """③ 人工核验兜底数据集（按公告文号）。"""
    key = match_dataset_key(doc_ref)
    if not key:
        return {}
    return dict(VERIFIED_SURVEY_DATASETS[key])


def _doc_ref(state: dict) -> str:
    filled = state.get("filled_data", {}) or {}
    if isinstance(filled, dict):
        v = str(filled.get("doc_reference", "") or filled.get("project_name", "") or "")
        if v:
            return v
    return str(state.get("report_title", "") or state.get("project_context", "") or "")


def resolve_survey_dataset(state: dict) -> Dict[str, Any]:
    """分层取权威问卷数据集；全无返回 {}。返回值带 `_source` 标明来源层级。"""
    r = _from_state_result(state)
    if r:
        return {**r, "_resolved_by": "会话内解析结果"}
    r = _from_disk_cache(state)
    if r:
        return {**r, "_resolved_by": "座谈会PDF解析缓存"}
    r = _from_verified(_doc_ref(state))
    if r:
        return {**r, "_resolved_by": "人工核验数据集"}
    return {}


def get_verified_survey_analysis(doc_reference: str) -> Dict[str, Any]:
    """按公告文号返回已核验的群众问卷六维统计（兼容旧接口）。

    未匹配返回空 dict。key 结构与 image_analyzer_agent 写入的
    generated_sections.survey_analysis 完全一致，可直接合并。
    """
    ds = _from_verified(doc_reference)
    if not ds:
        return {}
    return _survey_analysis_of(ds)


def get_verified_dept_survey(doc_reference: str) -> Dict[str, Any]:
    """按公告文号返回已核验的部门问卷统计（表3-3 结构）。"""
    ds = _from_verified(doc_reference)
    return (ds.get("dept_survey") or {}) if ds else {}


def _survey_analysis_of(ds: Dict[str, Any]) -> Dict[str, Any]:
    """把数据集裁剪成 survey_analysis 结构（第9章/表9-1 直接消费）。"""
    # 🔴 年龄选项归一成问卷印刷体（「16～35」而非「16～35岁」）——
    #    旧缓存/核验数据集里的键可能是视觉模型加过「岁」的，出口统一纠正。
    try:
        from app.services.questionnaire_parser import canon_public_groups
        _age = canon_public_groups("age", ds.get("age_groups", {}))
    except Exception:
        _age = ds.get("age_groups", {})
    return {
        "total_samples": ds.get("total_samples", 0),
        "valid_samples": ds.get("valid_samples", ds.get("total_samples", 0)),
        "support_count": ds.get("support_count", 0),
        "oppose_count": ds.get("oppose_count", 0),
        "neutral_count": ds.get("neutral_count", 0),
        "conditional_support": ds.get("conditional_support", 0),
        "support_rate": ds.get("support_rate", 0),
        "oppose_rate": ds.get("oppose_rate", 0),
        "identity_groups": ds.get("identity_groups", {}),
        "age_groups": _age,
        "occupation_groups": ds.get("occupation_groups", {}),
        "awareness_levels": ds.get("awareness_levels", {}),
        "attitude_options": ds.get("attitude_options", {}),
        "resolution_methods": ds.get("resolution_methods", {}),
        "main_opinions": ds.get("main_opinions", []),
    }


# ═══════════════════════════════════════════════════════════════════
# ③ 合并进 state
# ═══════════════════════════════════════════════════════════════════

def merge_survey_into_state(state: dict) -> bool:
    """把权威问卷数据合并进 state，供第9章/表9-1/9-2/附件2/评分消费。

    写入位置：
    - generated_sections.survey_analysis（权威，覆盖不完整的 OCR 结果）
    - generated_sections.dept_survey_analysis（部门问卷）
    - filled_data.total_samples / support_count / support_rate / oppose_count
      （修正「26户被误当问卷数」的问题）

    返回是否命中任一层数据源。
    """
    ds = resolve_survey_dataset(state)
    if not ds:
        return False

    analysis = _survey_analysis_of(ds)
    generated = state.setdefault("generated_sections", {})
    generated["survey_analysis"] = analysis
    generated["survey_data_source"] = ds.get("_resolved_by", "")

    dept = ds.get("dept_survey") or {}
    if dept:
        generated["dept_survey_analysis"] = dept

    # 🔴 不能写 `state.get("filled_data", {}) or {}` —— **空 dict 是 falsy**，
    #    新会话（还没填任何字段）时这里会拿到一个全新的孤儿 dict，
    #    下面所有写入（支持率/问卷日期）全部丢掉，`merge` 却返回 True。
    filled = state.get("filled_data")
    if not isinstance(filled, dict):
        filled = state["filled_data"] = {}
    if True:
        filled["total_samples"] = str(analysis["total_samples"])
        filled["survey_total_count"] = str(analysis["total_samples"])
        filled["support_count"] = str(analysis["support_count"])
        filled["oppose_count"] = str(analysis["oppose_count"])
        filled["support_rate"] = f"{analysis['support_rate']:.0f}%"
        # 🔴 必须单独给「反对率」：评分体系里 100−支持率 会把
        #    「有条件支持/无所谓/未表态」也算成反对，凭空虚增扣分。
        filled["oppose_rate"] = f"{analysis['oppose_rate']:.1f}%"
        # 🔴 问卷上的**手写日期** = 调查实际发生日（业主：「每次的座谈会和群众调查
        #    都是有写时间的，应该大多数都是人为书写的，尽量识别出来」）。
        #    · `questionnaire_date` 是**准确语义**（问卷调查日期）——第8/9章写
        #      「于X月X日开展问卷调查」用它；
        #    · `survey_date`（实地勘察日期）仅在**为空**时用问卷日期兜底 ——
        #      survey 本就指走访调查，两者同期，但已有值绝不覆盖。
        _sd = str(ds.get("questionnaire_date") or "").strip()
        if _sd:
            if not str(filled.get("questionnaire_date") or "").strip():
                filled["questionnaire_date"] = _sd
                filled["questionnaire_date_text"] = str(
                    ds.get("questionnaire_date_range") or _sd)
            if not str(filled.get("survey_date") or "").strip():
                filled["survey_date"] = _sd
            try:
                from app.services.field_sources import mark as _mk
                _mk(filled, "material", "questionnaire_date", "questionnaire_date_text",
                    "survey_date")
            except Exception:
                pass
        _dd = ((ds.get("dept_survey") or {}).get("questionnaire_date") or "").strip()
        if _dd and not str(filled.get("dept_questionnaire_date") or "").strip():
            filled["dept_questionnaire_date"] = _dd
        # 🔴 来源=材料识别（问卷是座谈会 PDF 逐页视觉解析出来的）
        try:
            from app.services.field_sources import mark
            mark(filled, "material", "total_samples", "survey_total_count",
                 "support_count", "oppose_count", "support_rate", "oppose_rate",
                 "survey_dimension_text", "dept_survey_count")
        except Exception:
            pass
        # 🔴 逐题统计的可照抄文本块（进事实卡，供第9章表9-1/9-2 逐字取数）
        _dim_text = render_dimension_text(ds)
        if _dim_text:
            filled["survey_dimension_text"] = _dim_text
        if dept:
            filled["dept_survey_count"] = str(dept.get("total_units", 0) or 0)
    return True


def render_dimension_text(ds: Dict[str, Any]) -> str:
    """把逐题勾选统计渲染成**可逐字照抄**的文字块，塞进事实卡。

    🔴 没有这一块，模型看不到分项人数，就会自己编 ——
    实测出现过「调解39人/正常反映7人」被写成「调解1人/正常反映49人」（数字对调），
    以及身份/年龄/职业三项整表丢失。宁可把数据写死在卡片上让它抄。
    """
    if not ds:
        return ""
    # 🔴 分项标题照抄《稳评问卷调查表》表格第一列原文（唯一事实源），
    #    第9章正文/表9-1 引用时与实际问卷逐字一致，不得概括改写。
    try:
        from app.services.questionnaire_parser import PUBLIC_QUESTION_TEXTS as _QTEXT
    except Exception:
        _QTEXT = {
            "identity": "您是", "age": "您的年龄", "occupation": "您的职业",
            "awareness": "您是否了解本决策", "attitude": "对该决策是否支持",
            "resolution": "您会采取何种方式解决诉求",
        }
    # 🔴 年龄选项先归一成印刷体（「16～35」而非「16～35岁」），与问卷原件逐字一致
    try:
        from app.services.questionnaire_parser import canon_public_groups as _cpg
    except Exception:
        _cpg = None
    _age_groups = ds.get("age_groups")
    if _cpg is not None:
        _age_groups = _cpg("age", _age_groups)
    _dims = [
        (_QTEXT.get("identity", "您是"), ds.get("identity_groups")),
        (_QTEXT.get("age", "您的年龄"), _age_groups),
        (_QTEXT.get("occupation", "您的职业"), ds.get("occupation_groups")),
        (_QTEXT.get("awareness", "您是否了解本决策"), ds.get("awareness_levels")),
        (_QTEXT.get("attitude", "对该决策是否支持"), ds.get("attitude_options")),
        (_QTEXT.get("resolution", "您会采取何种方式解决诉求"), ds.get("resolution_methods")),
    ]
    lines = []
    total = ds.get("total_samples", 0)
    for title, groups in _dims:
        if not groups:
            continue
        # 🔴 用括号包裹人数：选项本身以数字结尾（如「36～55」「16～35」），
        #    写成「36～5526人」会被读成 5526 人
        parts = "、".join(f"{k}（{v}人）" for k, v in groups.items())
        lines.append(f"  · {title}：{parts}")
    if not lines:
        return ""

    out = [f"【问卷调查分项统计（共 {total} 份，逐题勾选结果）】"]
    # 🔴 问卷上的手写日期（调查实际发生日）——第8/9章写调查过程时必须用它，
    #    而不是用系统生成时间。
    _qd = str(ds.get("questionnaire_date_range") or ds.get("questionnaire_date") or "").strip()
    if _qd:
        out.append(f"  · 问卷调查日期（问卷手写原迹）：{_qd}")
    out += lines
    dept = ds.get("dept_survey") or {}
    if dept:
        out.append(f"【部门（单位）问卷调查（共 {dept.get('total_units', 0)} 个单位）】")
        _dmap = [("了解程度", "awareness"), ("宣传公示满意度", "publicity_satisfaction"),
                 ("补偿安置政策了解", "policy_understanding"), ("关心的主要事项", "concerns"),
                 ("社会稳定风险等级", "risk_assessment"), ("保持稳定信心", "stability_confidence"),
                 ("基本态度", "support_attitude")]
        for title, key in _dmap:
            g = dept.get(key) or {}
            if g:
                out.append(f"  · {title}：" + "、".join(f"{k}（{v}个）" for k, v in g.items()))
    out.append("🔴 以上人数是逐份问卷清点出来的真实值：表9-1/表9-2 **只列以上题目**，"
               "人数与选项一律照抄，不得改动、不得对调、不得增删题目，"
               "更不得出现问卷里没有的题目（如补偿满意度、安置方式满意度）。")
    return "\n".join(out)


# 兼容旧名（report_workflow 等处的历史调用）
merge_verified_survey_into_state = merge_survey_into_state


async def ensure_questionnaire_result(state: dict, llm_service=None,
                                      progress_cb=None) -> Dict[str, Any]:
    """确保 state 里有问卷解析结果：命中缓存即秒回，否则**跑一遍全量视觉解析**。

    🔴 候选**按得分降序逐个尝试**：上一份解析为空（如错把签到表当问卷、
       或那份 PDF 里没有勾选页）就换下一份，而不是直接放弃。
       `summary_*.json` 里 `_empty:true` 的缓存同样跳过。

    ⚠️ 全量解析约 10 分钟（视觉模型 TPM 低，必须串行节流）。因此它的触发点是
    **资料上传时**（`routers/report.py` 的 `_bg_questionnaire`，上传完 PDF 立刻后台跑），
    而不是生成期间 —— 生成期间跑会和逐章 LLM 抢 TPM。
    `node_field_validate` 里只走 `merge_survey_into_state`（吃缓存/兜底数据，不阻塞流水线）。
    """
    if not isinstance(state, dict):
        return {}
    cur = _from_state_result(state)
    if cur:
        return cur
    cands = find_symposium_pdfs(state)
    if not cands:
        logger.info("未找到座谈会 PDF，跳过问卷解析")
        return {}
    from app.services.questionnaire_parser import parse_questionnaire_pdf, load_cached_summary

    for pdf in cands:
        # 缓存里已有一份**有数据**的汇总 → 直接用
        cached = load_cached_summary(pdf)
        if isinstance(cached, dict) and cached.get("total_samples"):
            state["_questionnaire_result"] = cached
            merge_survey_into_state(state)
            return cached
        # 解析这一份；得到空结果（含 `_empty` 缓存）→ 试下一份
        res = await parse_questionnaire_pdf(pdf, llm_service=llm_service, progress_cb=progress_cb)
        if res and res.get("total_samples"):
            state["_questionnaire_result"] = res
            # 解析完立刻合并进 state，本次生成即生效
            merge_survey_into_state(state)
            return res
        logger.info(f"问卷候选解析为空，换下一份: {os.path.basename(pdf)}")
    return {}
