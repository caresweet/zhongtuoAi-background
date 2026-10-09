"""Master Orchestrator — 主Agent动态编排报告生成。

职责：
1. 接收用户材料 → 检查完整性 → 不齐全则通知前端弹窗
2. 读取公告 → 识别项目信息（公司、项目名、位置、面积）
3. 生成大纲 → 动态决定章节数量和结构
4. 为每个章节Agent编写专属提示词 → 分配材料
5. 逐章Review → 内容错误则分析原因重新生成
6. 生成评审表 + 附件

不再使用固定10章模板，一切由主Agent根据实际材料决定。
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, Any

from app.validation.content_guardrails import AI_BUZZWORDS

_log = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 项目事实卡（ProjectCard）— 每章 agent 唯一事实源
# ═══════════════════════════════════════════════════════════

# 报告正文会直接出现这些字段值，只展示「有真实数据」的项目事实；
# 缺失字段不出现在卡片上，由章节 agent 按 DB32/T4013-2021 规范写
# 「以现状调查确认结果为准」式描述，严禁编造数字。
_PROJECT_CARD_LABELS = {
    "project_name": "项目名称",
    "org_name": "责任单位（稳评责任单位）",
    "decision_unit": "决策主体",
    "implement_unit": "稳评实施单位",
    "location": "项目位置",
    "area_mu": "征收面积（亩）",
    "area_m2": "征收面积（平方米）",
    "land_use": "土地用途",
    "land_acquisition_purpose": "征收目的",
    "doc_reference": "征地预公告文号",
    "compensation_standard": "补偿标准",
    "household_count": "涉及户数",
    "population_count": "涉及人数",
    "total_samples": "问卷总数",
    "survey_total_count": "有效问卷总数",
    "support_rate": "调查支持率",
    "oppose_rate": "反对率",
    "dept_survey_count": "部门意见调查单位数",
    "solicitation_period": "公告期限",
    "assessment_agency": "评估机构",
    "workgroup": "稳评工作组人员及分工",
    # 🔴 **日期类**必须进事实卡：它是每章 agent 的唯一事实源，缺了它们
    #    用户在弹窗里填的日期**永远到不了正文**（实测：用户把 14 个日期全填了，
    #    成稿里只出现 2 次）。业主原话：「我在前台写入的时间内容一个都没有用到」。
    "commission_date": "委托日期",
    "plan_date": "制定评估方案日期",
    "notice_date": "现场公示开始日期",
    "notice_end_date": "现场公示结束日期",
    "survey_date": "实地勘察日期",
    "symposium_date": "座谈会日期",
    "risk_predict_date": "风险预测开始日期",
    "risk_predict_end_date": "风险预测结束日期",
    "report_draft_date": "报告编制开始日期",
    "report_draft_end_date": "报告编制结束日期",
    "review_date": "专家评审开始日期",
    "review_end_date": "专家评审结束日期",
    "plan_approve_date": "方案审核日期",
    "publicity_start_date": "稳评公示开始日期",
    "publicity_end_date": "稳评公示结束日期",
    "report_month": "报告编写年月（YYYY-MM）",
    # 🔴 以下为**资料解析**（预公告/勘测定界/签到表/座谈记录）产出、此前从未进过事实卡的字段。
    #    业主 2026-09-29：「要解析所有的资料，agent 要能识别 PDF 中的资料内容并运用在报告上」——
    #    解析出来却进不了提示词 = 白解析，正文只能写【待填写】或遁词。
    "town": "所属镇（街道）",
    "village": "涉及村（社区）",
    "land_classification": "土地地类构成（勘测定界·分类面积）",
    "land_ownership": "土地权属构成（勘测定界）",
    "boundary_desc": "四至范围（勘测定界/公告）",
    "symposium_date_text": "座谈会日期（中文写法）",
    "symposium_time": "座谈会时间",
    "symposium_location": "座谈会地点",
    "survey_date_text": "问卷调查日期（中文写法）",
    "questionnaire_date": "问卷调查日期（问卷手写）",
    "questionnaire_date_text": "问卷调查日期（中文写法）",
    "dept_questionnaire_date": "部门问卷日期（问卷手写）",
    "review_meeting_date": "专家评审日期",
    "review_meeting_location": "专家评审地点",
    "review_meeting_attendees": "专家评审参会人员",
    "map_plots": "公告附图涉及地块",
    "map_scale": "公告附图比例尺",
    "map_date": "公告附图成图日期",
    "work_scope": "公告载明的工作范围",
    "funding": "资金筹措单位",
    "funding_amount": "资金筹措金额",
    "notice_publish_date": "预公告发布日期",
    "public_demands": "座谈会群众诉求（记录原文）",
    "discussion_text": "座谈会讨论情况（记录原文）",
    "conclusion_text": "座谈会结论（记录原文）",
}

# 🔴 「其他项目残留」禁用片段：**不在代码里写死**，从 data/legacy_fragments.json 读取
#    （运维可随时增删；默认为空）。同时见下方铁律：事实卡未出现的一切具体事实均禁止写入。
def _load_legacy_fragments() -> list:
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "data" / "legacy_fragments.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return []


_OLD_PROJECT_FRAGMENTS = _load_legacy_fragments()


def _load_regulation_config() -> dict:
    """可引用法规白名单配置（按地区）。代码零写死，全部来自 data/regulation_whitelist.json。"""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "data" / "regulation_whitelist.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def build_regulation_whitelist(filled_data: dict) -> str:
    """按项目事实卡里出现的省/市，选取对应的可引用法规清单（省/市可扩展，见 JSON）。"""
    cfg = _load_regulation_config()
    if not cfg:
        return ""
    text = " ".join(
        str(v) for v in (filled_data or {}).values()
        if isinstance(v, (str, int, float)) and not str(v).startswith("_")
    )
    regions = cfg.get("regions") or {}
    # 省级判定：① 直接命中省名；② 通过市名推断所属省
    prov_key = next((k for k in regions if k != "default" and k and k in text), None)
    if not prov_key:
        for k, reg in regions.items():
            if k == "default" or not isinstance(reg, dict):
                continue
            if any(ck and ck != "default" and ck in text for ck in (reg.get("cities") or {})):
                prov_key = k
                break
    prov_key = prov_key or "default"
    reg = regions.get(prov_key) or regions.get("default") or {}
    cities = reg.get("cities") or {}
    city_key = next((k for k in cities if k != "default" and k and k in text), "default")

    parts = ["\n## 📜 可引用法规清单（只能引用以下法规，其余一律不写、绝不编造文号）\n"]
    if cfg.get("national"):
        parts.append("- 国家法律：" + "".join(cfg["national"]) + "\n")
    if reg.get("province"):
        parts.append("- 省级：" + "".join(reg["province"]) + "\n")
    if reg.get("standard"):
        parts.append("- 标准：" + "".join(reg["standard"]) + "\n")
    if cities.get(city_key):
        parts.append("- 市级：" + "".join(cities[city_key]) + "\n")
    return "".join(parts)


# 🔴 各章目标字数（**单一事实源**）—— 按**同类模板报告**各章的实际字数定。
#    食品产业园案卷：编制说明 749 / 编制依据 1168 / 决策事项基本情况 1139 /
#    稳评工作方案 2136 / 各利益相关者 1985 / 四性分析 2130 / 分析预测风险源 2651 /
#    防范化解建议 1935 / 应急预案 3841 / 稳评工作报告 2588。合计 21,376 字。
#    取「模板字数 + 余量」作**上限**，下限取 60%，全篇目标约 24,600 字。
#    ⚠️ 上下限**必须同源** —— 中途只加上限不改下限，出现过第6章
#       「下限 2200 / 上限 1500」这种上限低于下限的矛盾。
_TARGET_WORDS = {
    1: 900, 2: 1400, 4: 2800, 5: 4000, 6: 1500, 7: 2400,
    8: 1400, 9: 1400, 10: 2400, 11: 2800, 12: 1400, 13: 2200,
}


def target_words(chapter_no: int) -> tuple:
    """(下限, 上限) 字数。**所有生成路径共用这一个来源** ——
    `build_chapter_prompt`（LLM 直写章）与 `reference_llm.build_rewrite_prompt`
    （照搬章）都调它。分开各写一份的话，改了一边忘另一边（实测踩过）。
    """
    hi = _TARGET_WORDS.get(int(chapter_no), 2000)
    return max(800, int(hi * 0.6)), hi


# 🔴 稳评「数据字段 → 中文标注」映射（单一事实源）。
#    由 `chapter_catalog.key_labels("stability")` 取用；复垦另有一份复垦口径的映射。
_STABILITY_KEY_LABELS = {
    "org_name": "责任单位（稳评责任单位）",
    "implement_unit": "稳评实施单位",
    "project_name": "项目名称",
    "location": "项目位置",
    "area_mu": "征收面积（亩）",
    "area_m2": "征收面积（平方米）",
    "land_use": "土地用途",
    "compensation_standard": "补偿标准",
    "household_count": "涉及户数",
    "population_count": "涉及人数",
    "total_samples": "问卷总数",
    "support_rate": "支持率",
    "doc_reference": "公告文号",
}


def stability_key_labels() -> dict:
    """返回稳评的「数据字段 → 中文标注」映射副本。"""
    return dict(_STABILITY_KEY_LABELS)


def build_project_card(filled_data: Dict[str, Any]) -> str:
    """构建「项目事实卡」：只含已确认的真实项目事实（唯一事实源）。

    - 只展示有真实值的字段，缺失字段不出现在卡片上
    - 「待补充」值一律过滤，绝不以占位符形式进卡片
    - 每个值都标注来源出处（文档/用户提供/AI分析），供后续人工核验
    """
    from app.validation.content_guardrails import _norm_num

    lines = []
    keys = [
        "project_name", "org_name", "decision_unit", "implement_unit",
        "location", "area_mu", "area_m2", "land_use", "land_acquisition_purpose",
        "doc_reference", "compensation_standard", "household_count",
        "population_count", "total_samples", "survey_total_count",
        "support_rate", "oppose_rate", "dept_survey_count",
        "solicitation_period", "assessment_agency", "workgroup",
        # 🔴 日期（同 `_PROJECT_CARD_LABELS`）：不列在这里 = 用户填的日期
        #    进不了提示词 = 永远写不进正文
        "commission_date", "plan_date",
        "notice_date", "notice_end_date", "survey_date", "symposium_date",
        "risk_predict_date", "risk_predict_end_date",
        "report_draft_date", "report_draft_end_date",
        "review_date", "review_end_date", "plan_approve_date",
        "publicity_start_date", "publicity_end_date", "report_month",
        # 🔴 资料解析产出的字段（此前解析出来但从未进事实卡，见 _PROJECT_CARD_LABELS 注释）
        "town", "village", "land_classification", "land_ownership", "boundary_desc",
        "symposium_location", "symposium_date_text", "symposium_time",
        "survey_date_text", "questionnaire_date", "questionnaire_date_text",
        "dept_questionnaire_date",
        "review_meeting_date", "review_meeting_location", "review_meeting_attendees",
        "map_plots", "map_scale", "map_date", "work_scope",
        "funding", "funding_amount", "notice_publish_date",
    ]
    for k in keys:
        v = filled_data.get(k)
        if v is None or str(v).strip() == "" or str(v).strip() == "【待补充】":
            continue
        label = _PROJECT_CARD_LABELS.get(k, k)
        # 数值类字段：去掉多余的逗号/百分号，保持原文（% 保留以便识别）
        lines.append(f"  {label}：{str(v).strip()}")

    # 🔴 预公告正文摘录：征收目的 / 征收范围 / 工作安排 / 其他事项 / 法律依据
    #    这些是公告的正式表述，写报告时必须据此行文，不能只靠文号与位置。
    _ann_keys = [
        ("legal_basis_cited", "公告引用的法律法规"),
        ("preamble_text", "公告前言（决定启动的表述）"),
        ("purpose_text", "征收目的（公告原文）"),
        ("purpose_legal_basis", "征收目的法定依据"),
        ("scope_text", "征收范围（公告原文）"),
        ("notice_period_text", "公告期限（原文）"),
        ("work_arrangement_text", "工作安排（公告原文）"),
        ("other_matters_text", "其他事项（公告原文）"),
        # 🔴 稳评公示期（用户填开始日 → 系统推算一周），与上面的「公告期限」不是一回事
        ("publicity_period_text", "稳评公示期（公示期一周）"),
        ("contact_name", "公告联系人"),
        ("contact_phone", "公告联系电话"),
        ("attachment_note", "公告附图说明"),
    ]
    _ann_lines = []
    for k, label in _ann_keys:
        v = filled_data.get(k)
        if v is None or str(v).strip() in ("", "【待补充】"):
            continue
        s = str(v).strip()
        if len(s) > 300:
            s = s[:300] + "…"
        _ann_lines.append(f"  {label}：{s}")
    if _ann_lines:
        lines.append("")
        lines.append("  —— 征收土地预公告正文摘录 ——")
        lines.extend(_ann_lines)

    # 🔴 勘测定界明细（权属构成 / 地类构成 / 界址）：此前 `_land_owners`、
    #    `_land_by_category`、`_land_by_sub_category` 解析出来**全仓库没人消费**，
    #    第6章「涉及权属单位、地类构成」与第10章合法性论证只能空对空自圆其说。
    _land_lines = []
    _owners = filled_data.get("_land_owners")
    if isinstance(_owners, (list, tuple)) and _owners:
        _land_lines.append("  土地权属构成（勘测定界）："
                           + "、".join(str(x) for x in _owners if str(x).strip()))
    _cat = filled_data.get("_land_by_category")
    if isinstance(_cat, dict) and _cat:
        _land_lines.append("  地类构成（勘测定界·一级地类，单位㎡）："
                           + "、".join(f"{k}{v}" for k, v in _cat.items() if str(k).strip()))
    _sub = filled_data.get("_land_by_sub_category")
    if isinstance(_sub, dict) and _sub:
        try:
            _top = sorted(((k, v) for k, v in _sub.items() if str(k).strip()),
                          key=lambda kv: -float(kv[1] or 0))[:8]
        except (TypeError, ValueError):
            _top = [(k, v) for k, v in list(_sub.items())[:8] if str(k).strip()]
        _land_lines.append("  主要具体地类（按面积降序，单位㎡）："
                           + "、".join(f"{k}{v}" for k, v in _top))
    _bp = str(filled_data.get("boundary_points") or "").strip()
    if _bp:
        _land_lines.append(f"  界址点数（勘测定界）：{_bp}")
    if _land_lines:
        lines.append("")
        lines.append("  —— 勘测定界明细（第6章 1.5 涉及村组/地类构成、第10章合法性论证用）——")
        lines.extend(_land_lines)

    # 🔴 座谈会记录正文（群众诉求 / 讨论情况 / 结论）——第9章「各方意见汇总」的素材。
    #    此前 `public_demands` / `discussion_text` / `conclusion_text` 由 PDF 解析出来
    #    却**没有任何读取点**，第9章只能写空话。
    _meet_lines = []
    for _k, _label in (("public_demands", "座谈会群众诉求（记录原文）"),
                       ("discussion_text", "座谈讨论情况（记录原文）"),
                       ("conclusion_text", "座谈结论（记录原文）")):
        _v = str(filled_data.get(_k) or "").strip()
        if _v:
            _meet_lines.append(f"  {_label}：{_v[:400]}")
    if _meet_lines:
        lines.append("")
        lines.append("  —— 座谈会记录（第9章 各方意见汇总取材）——")
        lines.extend(_meet_lines)

    # 🔴 问卷调查逐题统计：整块原样贴出。模型看不到分项人数就会自己编
    #    （实测出现过诉求数字对调、身份/年龄/职业三项整表丢失）。
    _dim = filled_data.get("survey_dimension_text")
    if _dim and str(_dim).strip():
        lines.append("")
        lines.extend(str(_dim).strip().splitlines())

    # 🔴 用户补充说明（来自「缺口核对」弹窗）：正文里 agent 自己承认没数据的地方，
    #    用户补了真实内容 —— **必须采纳**，否则那几章重写完还是缺依据。
    _notes = filled_data.get("_supplement_notes")
    if isinstance(_notes, list) and _notes:
        lines.append("")
        lines.append("  —— 用户补充说明（🔴 必须采纳，写入对应章节）——")
        for _it in _notes:
            if not isinstance(_it, dict):
                continue
            _ch, _note = _it.get("chapter", ""), str(_it.get("note") or "").strip()
            if not _note:
                continue
            _about = str(_it.get("about") or "").strip()
            _line = f"  第{_ch}章：{_note}"
            if _about:
                _line += f"（原句：{_about[:60]}）"
            lines.append(_line)

    # 🔴 兜底草稿（用户**跳过**了字段补充，AI 按事实卡拟的规范表述）。
    #    ⚠️ 它**不是事实** —— 所以既不放进上面的事实清单，也不许当成真实数据引用。
    #    用途是让成品「拿到手简单改改就能用」，而不是留一句遁词让用户从零写。
    _fb = filled_data.get("_fallbacks")
    if isinstance(_fb, dict) and _fb:
        lines.append("")
        lines.append("  —— 📝 兜底草稿（用户未提供，AI 拟稿·**可改**）——")
        lines.append("  用法：这些字段用户暂时给不出值，下面是**可以直接用的规范表述**；")
        lines.append("        写正文时把它们当行文素材用（可以润色），但**不要**当成"
                     "已核实的具体数据去做二次推算。")
        for _k, _v in _fb.items():
            if str(_v).strip():
                lines.append(f"  {_PROJECT_CARD_LABELS.get(_k, _k)}：{str(_v).strip()[:200]}")

    if not lines:
        return (
            "## 🔗 本项目事实卡（唯一事实源）\n"
            "尚未掌握任何可确认的项目事实。你只能依据「可用数据」和「用户提供的全部资料」撰写，"
            "所有项目具体数字一律用「以现状调查确认结果为准」式规范文字描述，严禁编造。\n"
        )

    return (
        "## 🔗 本项目事实卡（唯一权威事实源）\n"
        "以下事实来自由用户资料提取并经确认的字段，写本报告时**必须直接采用这些值**。\n"
        + "\n".join(lines)
        + "\n\n"
        "🔴 事实使用铁律：\n"
        "- 事实卡中的值就是本项目的真实数据，与该值不一致的任何数字、名称、文号一律不得出现\n"
        "- 事实卡之外的任何项目数字、区县、街道、村组名称，一律禁止出现（多半来自其他项目或模板）\n"
        "- 用户资料未提供的内容写「以现状调查确认结果为准」等规范文字，严禁编造\n"
    )


# ═══════════════════════════════════════════════════════════
# Outline Generation — master agent decides structure
# ═══════════════════════════════════════════════════════════

async def generate_outline(
    llm,
    filled_data: dict,
    has_survey: bool = False,
    has_review: bool = False,
    has_announcement: bool = False,
) -> dict:
    """主Agent分析材料后生成动态大纲。

    返回: {"chapters": [{"num":1,"title":"...","key_points":[...],"data_needed":[...]}, ...],
            "total": N, "has_review_table": True, "has_appendix": True}
    """
    materials_summary = []
    if has_announcement:
        materials_summary.append("- 征收土地预公告（含项目名称、位置、面积、用途）")
    if has_survey:
        materials_summary.append("- 群众调查问卷及座谈会记录（含支持率、诉求等数据）")
    if has_review:
        materials_summary.append("- 专家评审意见及签到表")

    project_name = filled_data.get("project_name", "某项目")
    location = filled_data.get("location", "")
    area = filled_data.get("area_mu", "")

    prompt = f"""你是社会稳定风险评估报告的主编专家。请根据以下材料决定报告结构。

## 项目信息
- 项目名称：{project_name}
- 位置：{location}
- 面积：{area}亩
- 用途：{filled_data.get('land_use', '')}

## 用户已提供材料
{chr(10).join(materials_summary) if materials_summary else '- 基本信息'}

## 任务
根据上述材料的实际内容，决定报告章节结构，并**设计清晰的论证主线**——章节之间要形成逻辑递进，像人写报告一样层层推进。

## 必须包含（根据材料按需组织）
- 项目基本概况（位置、面积、用途等）
- 风险调查分析（如有问卷/座谈数据）
- 评估依据（法律法规，可独立成章或融入各章）
- 风险识别与等级判定
- 防范化解措施
- 评估结论与建议
- 评审表（独立文件）
- 附件（图片、问卷等分类整理）

## 🔴 论证主线要求（最重要）
章节之间必须形成逻辑链条，每章承担一个论证任务，前后承接：
```
概况(是什么) → 评估过程(怎么评估) → 调查(了解什么) → 分析(判断什么)
→ 风险识别(发现什么) → 措施前研判(风险多大) → 措施(怎么化解)
→ 措施后评估(是否有效) → 结论(定论) → 应急预案(兜底)
```
- 每章要**承接前序章节的结论**（如风险识别章要引用调查章的数据）
- 每章要为**后序章节铺垫**（如调查章收集的数据要支撑后续风险分析）

## 输出JSON格式
```json
{{
  "chapters": [
    {{"num": 1, "title": "根据内容自拟标题", "key_points": ["本重要点1", "本重要点2"], "data_needed": ["需要的字段key"], "depends_on": [依赖的章节号], "argument_note": "本章在论证链中的任务：承接什么、为后序铺垫什么"}},
    ...
  ],
  "has_review_table": true,
  "has_appendix": true,
  "has_legal_basis": true,
  "argument_flow": "整份报告的论证主线一句话说明（从概况到结论的逻辑路径）",
  "note": "说明为什么这样设计（基于什么材料特征）"
}}
```

## 说明
- 章节数根据材料丰富度决定，通常5-8章
- 材料多 → 可以细分章节；材料少 → 合并相关主题
- 标题要具体，不能所有项目都一样（如用"XX地块征收概况"而非"项目基本情况"）
- data_needed写真实的字段key（project_name, org_name, location, area_mu, land_use, compensation_standard, household_count, total_samples等）
- depends_on写本章依赖的前序章节号（如第6章措施前评分依赖第3章调查、第5章风险识别）
- argument_note写本章的论证任务（承接什么结论、为后序铺垫什么）"""

    try:
        response = await asyncio.wait_for(
            llm.chat_with_reasoning(messages=[{"role": "user", "content": prompt}], max_tokens=2000, temperature=0.3),
            timeout=60.0
        )
        content = response.get("content", "")
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            outline = json.loads(json_match.group())
            logger.info(f"Outline generated: {len(outline.get('chapters', []))} chapters")
            return outline
    except Exception as e:
        logger.warning(f"Outline generation failed: {e}")

    # Fallback：直接按 chapter_definitions 的十四章结构生成
    #   （原先是手写一份十章列表，章节结构改过后就成了过期数据）
    try:
        from app.agent.chapter_definitions import (
            ALL_CHAPTERS, get_chapter_title, get_chapter_data_requirements)
        _chapters = []
        for n in ALL_CHAPTERS:
            req = get_chapter_data_requirements(n)
            _need = [k for k, *_ in (req.get("required") or [])] + \
                    [k for k, *_ in (req.get("recommended") or [])]
            _chapters.append({"num": n, "title": get_chapter_title(n),
                              "key_points": [], "data_needed": _need})
        return {"chapters": _chapters, "has_review_table": True, "has_appendix": True,
                "has_legal_basis": True, "note": "按 chapter_definitions 十四章结构生成"}
    except Exception:
        return {"chapters": [], "has_review_table": True, "has_appendix": True,
                "has_legal_basis": True, "note": "章节定义读取失败"}


# ═══════════════════════════════════════════════════════════
# Chapter Prompt Builder — master writes prompt for each chapter
# ═══════════════════════════════════════════════════════════

def get_table_format_reference(chapter_num: int) -> str:
    """从模板表格注册表提取本章表格的列结构，仅作为格式参考。

    只暴露「列结构 + 表格用途」，不暴露固定内容。章节 agent 根据实际数据
    决定写哪些表格、填什么内容——有数据才写，没数据不写。

    Returns:
        格式参考文本（markdown），无表格时返回空串。
    """
    try:
        from app.services.table_registry import TABLE_REGISTRY
    except Exception:
        return ""

    lines = []
    for name, tdef in TABLE_REGISTRY.items():
        if tdef.get("chapter") != chapter_num:
            continue
        columns = tdef.get("columns", [])
        desc = tdef.get("description", name)
        # 清洗列名里的换行符
        clean_cols = [c.replace('\n', '') for c in columns]
        col_str = " | ".join(clean_cols)
        lines.append(f"  - 「{desc}」列结构：{col_str}")

    if not lines:
        return ""

    # 🔴 第9章的两张问卷统计表**由系统渲染**：题面必须与《稳评问卷调查表》原件逐字一致、
    #    人数必须与逐页清点结果一致（业主评审核验点）。模型只输出标记，不得手写 ——
    #    实测手写会把「您是」写成「您的身份」、把部门原件题面概括成「对决策的了解程度」。
    if chapter_num == 9:
        return (
            "## 📐 本章表格（🔴 系统渲染，正文只放标记，不要手写表格）\n"
            + "\n".join(lines) + "\n"
            "🔴 上面两张表**不要用 markdown 手写**，也不要在正文里写「表9-1 …」表题行"
            "（系统自带规范表题，重复写会出现一表两题）：\n"
            "  - 个人问卷统计表 → 单独占一行输出 `[TABLE:ch9_public_survey]`\n"
            "  - 单位（部门）问卷统计表 → 单独占一行输出 `[TABLE:ch9_dept_survey]`"
            "（本次没有部门/单位问卷时才省略这行）\n"
            "标记前后各要有分析文字，形成「引导语 + 标记 + 解读句」的承接写法。\n"
        )

    return (
        "## 📐 表格格式参考（仅格式参考，数量与内容由你根据数据决定）\n"
        "以下列结构来自历史模板，供你参考表头和列安排。是否写表格、写几张、填什么内容，"
        "完全由你根据「可用数据」决定：有数据支撑才写表格，没有数据就写文字描述 + 【待补充】。\n"
        + "\n".join(lines) + "\n"
    )


def build_chapter_prompt(
    chapter_def: dict,
    filled_data: dict,
    image_guide: str = "",
    previous_chapters: dict = None,
    feedback: str = None,
    materials_summary: str = "",
    outline_context: dict = None,
    state: dict = None,
    domain: str = "stability",
) -> str:
    """主Agent为单个章节编写专属提示词。

    `domain` 决定「结构类」注入项（字数区间 / 二级标题规范 / 表格参考 / 字段中文
    标注 / 身份首句 / 评分块 / 逐章特定要求）取哪一套 —— 一律经
    `app.agent.chapter_catalog` 按域解析。末段「写作要求」块按域择一：复垦取
    catalog 给的复垦文本，稳评沿用本函数内联的那份（逐字不变）。
    默认 "stability"：不传时提示词逐字等于改动前（稳评护栏）。
    """
    from app.agent import chapter_catalog as _catalog

    num = chapter_def.get("num", 0)
    title = chapter_def.get("title", f"第{num}章")
    key_points = chapter_def.get("key_points", [])
    data_needed = chapter_def.get("data_needed", [])
    depends_on = chapter_def.get("depends_on", []) or []
    argument_note = chapter_def.get("argument_note", "") or ""

    # 🔴 本项目事实卡：每章都带同一份「唯一事实源」，根治旧项目串数据
    project_card = build_project_card(filled_data)

    # 🔴 字段中文标注（按域取）：让 LLM 明确知道每个字段对应报告里的哪个小节
    _KEY_LABELS = _catalog.key_labels(domain)
    data_lines = []
    pdf_table_data = ""
    # 🔴 直接从 filled_data 里找 _pdf_table_data（不管是否在 data_needed 里）
    if filled_data.get("_pdf_table_data"):
        pdf_table_data = str(filled_data["_pdf_table_data"])
    for key in data_needed:
        val = filled_data.get(key, "")
        if not val:
            continue
        label = _KEY_LABELS.get(key, key)
        data_lines.append(f"  {label}: {val}")

    # 🔴 论证主线：本章在报告中的逻辑位置
    arg_flow = ""
    if argument_note:
        arg_flow += f"\n**本章论证任务**：{argument_note}\n"
    if depends_on:
        dep_titles = []
        if outline_context:
            for ch in (outline_context.get("chapters", []) if isinstance(outline_context, dict) else []):
                if isinstance(ch, dict) and ch.get("num") in depends_on:
                    dep_titles.append(f"第{ch['num']}章{ch.get('title','')}")
        if dep_titles:
            arg_flow += f"**必须承接前序章节**：{'、'.join(dep_titles)} 的结论和数据。引用前序章节识别的风险因素、评分、调查数据时，数字必须与前序一致，不得矛盾。\n"
    if outline_context:
        overall = outline_context.get("argument_flow", "")
        if overall:
            arg_flow += f"\n**整份报告论证主线**：{overall}\n"

    # 🔴 身份首句（按域取）：稳评逐字不变；复垦用其域配置的专业口吻，避免复垦提示词里
    #    顶着「稳评专家」的身份写复垦方案。
    _persona = _catalog.report_persona(domain)

    prompt = f"""{_persona}
撰写报告第{num}章「{title}」。

{project_card}

## 🧭 本章在报告中的逻辑位置（必须遵循，保证整篇逻辑连贯）
{arg_flow if arg_flow else '（本章是报告开篇，负责总览项目基本情况）'}

## 📋 用户提供的全部资料（必须在报告中充分使用）
{materials_summary if materials_summary else '（用户未上传资料）'}

## 本章要点
{chr(10).join(f'- {p}' for p in key_points)}

## 可用数据
{chr(10).join(data_lines) if data_lines else '（从项目资料和专业知识中获取）'}

## 📊 PDF 提取的表格数据（真实数据，填表时直接用这些数据，不要编造）
{pdf_table_data if pdf_table_data else '（无提取的表格数据）'}

## 前序章节关键信息（必须与前序结论保持一致）
{_summarize_previous(previous_chapters) if previous_chapters else '（第一章，无前序内容）'}
"""

    # 🔴 RAG 素材**不在这里注入** —— 由 `chapter_base.act()` 统一注入。
    #    原来这里注入一次、`chapter_base._format_rag_for_prompt` 又注入一次，
    #    而两次检索调的是**同一个** `rag_service.retrieve_for_chapter`：
    #    界面路径上等于同一份素材检索两遍、塞两遍（prompt 多出近 6000 字），
    #    脚本路径却只有一遍 —— 又一处「两条路径产出不一样」。
    #    现在统一到 agent 侧（那条路两条路径都会走），这里的 `rag_context` 形参
    #    已无消费者，一并删掉。

    if feedback:
        prompt += f"\n## ⚠️ 上一版问题\n{feedback}\n请修正上述问题后重新撰写。"

    # Scoring chapters: add scoring guidelines (扣分制, 严禁百分制)
    scoring_guidance = ""
    if _catalog.has_scoring_guidance(domain) and \
            any(kw in title for kw in ['风险', '评分', '等级', '研判', '措施']):
        scoring_guidance = """
## 评分规则（严格遵守 — DB32/T4013-2021 扣分制）
- 采用**扣分制**：按合法性/合理性/可行性/可控性四大类测评项目逐项扣分，稳评总分 = 各项目扣分之和，扣分越少风险越低。严禁使用「0-100 打分、≥80 分=低风险、措施前 60-85 分」这类百分制口径。
- 支持率/反对率**只能取「🔗 本项目事实卡」的真实值**，严禁推算、假设、编造与之不符的数值。
- 措施后总分应低于措施前总分（风险下降），正文分数必须与系统评分参考完全一致，不得自行重算或改写。
- 🔴 **每项扣分依据的文字由你依本项目事实卡撰写**（系统只给权重、评分标准与已算出的分数）；依据要落到本项目的具体事实上，不得沿用其他项目的话术。
"""
        if num == 12:   # 第12章 确定风险等级
            try:
                from app.services.scoring_service import scoring_service
                _scoring_text = scoring_service.format_for_llm(scoring_service.build_scoring_report(filled_data, {}))
                scoring_guidance += f"\n## 系统评分参考（唯一可用的分数来源，正文数字必须与之一致）\n{_scoring_text}\n"
            except Exception:
                pass
    # ── 字数：**一个来源推出上下限** ──────────────────────────────
    # 🔴 原来是「只给下限的一条 if 链」，模型一律往多里写 ——
    #    全本 33,926 字，是同类模板报告（食品产业园案卷）21,376 字的 **1.59 倍**。
    #    现在按**模板各章的实际字数**定「目标字数」= 上限，下限取 60%。
    #    ⚠️ 上下限**必须同源**：中途只加上限、不改下限，出现过第6章
    #       「下限 2200 / 上限 1500」这种上限低于下限的矛盾，模型无所适从。
    min_words, max_words = _catalog.target_words(domain, num)

    prompt += scoring_guidance

    # 🔴 章节结构规范（二级 + 三级标题）。
    #    此前 `build_chapter_prompt` **从未调用** `format_sections_for_prompt`，
    #    导致 `chapter_definitions` 里定义的三级标题（4.2.1 / 8.4.1 / 9.2.1 …）
    #    从没进过提示词，LLM 只能自己编「（一）（二）」式小标题。
    try:
        _sec = _catalog.sections_for_prompt(domain, num)
        if _sec:
            prompt += "\n" + _sec + "\n"
    except Exception as _se:
        logger.warning(f"章节结构规范注入失败 ch{num}: {_se}")

    # 🔴 表格格式参考（仅格式，内容与数量由 agent 根据数据决定；按域取）
    table_ref = _catalog.table_format_reference(domain, num)
    if table_ref:
        prompt += "\n" + table_ref + "\n"

    # 🔴 把**上传材料里与本章相关的原文**带进来（四至/地上物/周边/沟通结论…）。
    #    那些事实在材料正文里，此前只抽了字段、正文没用，于是本章只能写空话。
    if state:
        try:
            from app.services.material_excerpt import inject_excerpt
            prompt = inject_excerpt(num, state, prompt)
        except Exception as _me:
            logger.warning(f"[材料摘录] 第{num}章注入失败: {_me}")

    # ── 末段「写作要求」块：稳评 / 复垦各一套，互不掺和 ──
    # 🔴 复垦：经 catalog 取复垦口径的要求文本（不含稳评规范/口径与稳评 DB 反馈）。
    # 🔴 稳评：沿用下面这份内联正文 —— 逐字保持不变（稳评护栏）。
    _req_block = _catalog.requirements_block(domain, min_words, max_words)
    if _req_block:
        prompt += _req_block
    else:
        # 🔴 旧项目禁用块（提前拼好）：Python 3.11 的 f-string 表达式内不能含反斜杠，
        # 若在下方大 f-string 里内嵌带 \n 的 f-string 会触发 SyntaxError。
        _old_project_block = ""
        if data_lines:
            _frag_lines = "\n".join(f"  - {f}" for f in _OLD_PROJECT_FRAGMENTS)
            _old_project_block = (
                "## 🚫 其他项目残留（铁律）\n"
                "🔴 通用铁律：**凡「🔗 本项目事实卡」中未出现的具体事实**——村组/社区/乡镇/街道名、区县市名、"
                "项目文号、日期、百分比、金额、地块编号——一律视为**其他项目残留**，"
                "在本章正文、表格、图注、附件说明中**都不得出现**，需要引用时改写为通称（如「拟征收地块」「项目所在地」）。\n"
                "项目具体名称、区划层级（可能是街道/镇/乡/开发区/园区/林场等，以事实卡为准）、文号、日期、"
                "占比、金额一律以「🔗 本项目事实卡」为准。\n"
                + (f"以下片段已确认来自其他项目，尤其禁止：\n{_frag_lines}\n" if _frag_lines else "")
            )
        # 🔴 可引用法规白名单：**不写死**，按项目所在地区从 data/regulation_whitelist.json 选取
        _regulation_whitelist = build_regulation_whitelist(filled_data)
        # 🔴 逐章「本章特定要求」（稳评专有的逐章结构说明，如第3章=江苏省统一制式评审表）
        _ch_specific = _get_chapter_specific_requirements(num)
        prompt += f"""
## 要求
- 🔴 本章必须达到{min_words}字以上
- 🔴 **本章不要超过 {max_words} 字** —— 这是硬上限（同类模板报告本章就是这么多）。
  宁可写精，不要注水：同一件事不换说法重复说、不堆「依据《XX》」式引经据典、
  不写与本章标题无关的内容、不写「有利于…」「为…提供基础」这类没有事实的收尾句。
  写完自己数一下，超了就删到 {max_words} 字以内。
- 组织内容所依据的规范与本项目的评估依据，一律见本报告「评估依据」章与第十章参考文献清单；**规范全称（如 DB32/T4013-2021）只允许在上述依据类章节出现一次，本章除非确属依据内容，否则用"本规范/上述标准"指代**
- 每个要点至少写2-3段（150-300字/段），详细深入分析
- 🔴 本节内容必须与标题相符（一票否决）：每个小节标题写什么，正文就写什么具体内容，禁止写"XX需要负责/XX应当具备"这种与标题无关的空话。例如：
    * 标题「稳评责任单位」→ 正文必须写：责任单位具体名称（取「🔗 本项目事实卡」的"责任单位"）+ 其在该项目中的具体职责
    * 标题「稳评实施单位」→ 正文直接写实施单位名称（取「🔗 本项目事实卡」的「稳评实施单位」）。只写单位名称即可，不要写资质、专业能力、人员等说明。**事实卡里没有实施单位时写「待填写」，不得写入任何具体公司名**
    * 标题「征收位置」→ 正文必须写具体位置（取「🔗 本项目事实卡」的"项目位置"），不写泛泛的"位置合理"
{_ch_specific}
- 🔴 表格铁律（一票否决）：表格不固定，由你根据实际数据灵活设计。**有数据支撑才写表格，没有数据就写规范文字描述（不得出现【待补充】占位符），绝不写空表格或编造数据填表**。规则：
    * 用 markdown 表格语法（| 列1 | 列2 |）写表格，格式参考下面的「表格格式参考」
    * 每张表格必须每个单元格都有真实数据来源；🔴「事实卡」里有实数的**必须填实数**，不得用"以现状调查确认结果为准"搪塞；只有该项数据事实卡确实没有时才用规范文字，**不得写【待补充】**
    * 禁止手写"表X-X XXX"这种孤立的表格标题（表格标题紧跟表格内容，不单独成段）
    * 🔴 表格必须有表题（表X-X 表名），表题写在**表格上方**（表上题下：表题在上、图题在下）；有量纲/单位的列必须在**表头注明单位**（如「总面积(㎡)」「征收面积(亩)」「人数(人)」「占比(%)」），不得出现「总面积()」这类无单位的空括号列
    * 没有问卷数据 → 不写调查统计表；没有勘测数据 → 不写勘测定界表
    * 填表数据直接取自上面「PDF 提取的表格数据」，不要抄范文里的数据{'；🔴 勘测定界汇总表与土地分类面积表（第6章 表6-1）**由系统从勘测定界报告就地渲染**，正文用「引导语 + [SYS_TABLE:6-1] + 解读句」承接即可，**不要手写这两张表、不要写它们的表号**' if num == 6 else ''}
- 🔴 表格承接写法（模板惯例，一票否决）：正文凡涉及表格，**表前必须先写一句引导语**（如"……如下表所示：""各……统计情况见表"），**表后用 1～2 句对表数据做解读/结论**（如"从上表可见，六组面积最大……"），禁止让表格"悬空"——无引导、无解读、突然出现。
    * 系统自动渲染的表（勘测定界汇总表、土地分类表等）：在本章有此类表时，正文在相应数据语境用「引导语 + [SYS_TABLE:X-X] + 解读句」承接（[SYS_TABLE:X-X] 是系统标记，表由系统就地渲染，正文不手写该表、不写表号）。没有 [SYS_TABLE] 对应表就只用规范文字描述数据，不要强行引表。
    * 🔴 **问卷统计表是另一套标记**：第9章表9-1/表9-2 由系统渲染，正文只用 `[TABLE:ch9_public_survey]` / `[TABLE:ch9_dept_survey]`（详见第九章特定要求），**既不要手写这两张表，也不要给它们套 [SYS_TABLE]**。
    * 你手写的 markdown 表格同样必须满足「引导语 + 表 + 解读」，不要把表格当孤立附件堆在正文。
    * 正文提到位置、现场、公示、座谈等实景内容时，可用一句话自然交代有图佐证（如"拟征收地块位置见附图""现场调查照片见附件"），**严禁写 `![图]`、`【图片待插入】`、图号编号**等占位文字。
- 🔴 数据铁律（一票否决）：所有数字（份数、人数、户数、百分比、评分）只能来自「可用数据」和「用户提供的全部资料」。
    * **「事实卡 / 可用数据 / 资料」里已有的数字，必须直接写出具体数值**（如问卷份数、涉及户数、征收面积、界址点数），**严禁用"以现状调查确认结果为准""以实际调查统计为准"等占位话术代替已掌握的真实数字**。
    * 只有确实没有的数字才用规范文字描述（"以现状调查确认结果为准"），严禁输出【待补充】占位符。严禁推算、估计、假设任何数值。例如：
    * 用户没给问卷份数 → 不写具体数字，写"问卷发放与回收情况以现状调查确认结果为准"
    * 用户没给支持率 → 不写具体百分比，写"群众支持情况以问卷调查汇总结果为准"
    * 用户没给户数 → 不写具体数字，写"涉及户数以现状调查确认结果为准"
    * 用户没给年龄分布 → 不写具体分布，写"被调查对象年龄结构详见问卷调查统计分析"
- 🔴 缺失处理：用户未提供的数据 → 仍然写出完整的章节结构（含标题），在内容处用规范公文文字描述（"以XX为准/待XX完成后补充确认"），**不得出现【待补充】占位符符号**。不能因为数据缺失就跳过整个小节或留空
- 🔴 图片（位置示意图/公示/座谈/现场等）：由系统按图片库统一放置并按其尺寸规范排版。**正文不要写任何图片标记、`![图注]`、图号编号或"该部分图片见附件"等占位文字**。
- 🔴 时间逻辑（全局）：本项目征地预公告（文号与发布时间取「🔗 本项目事实卡」，未提供则泛化处理）。**所有稳评调查、评估时间必须在预公告发布之后**，严禁出现早于预公告的评估时间。各阶段时间应依次递进。
- 🔴 数据克制（全局，一票否决）：同一批数据（总面积/亩数/户数/界址点数/各权属地类明细/评分）**只在首次需要处完整写一次**；再次提及一律用"上述地块/该项目/前述合计"等指代，**禁止全文反复整段罗列同一组数字**。列出数字后尽量带一句人话解读（如"约合XX亩""约占…""相对集中"），不要干巴巴堆数字。
- 🔴 规范引用收敛（全局）：任一规范全称（DB32/T4013-2021、DB32/T4937-2024 等）**全文仅在首次/依据类章节写全称一次**，其后一律用"本规范/上述标准/该规范"指代；禁止每章反复出现"按照DB32/T4013-2021规范要求"句式。
- 🔴 去模板腔（全局）：禁止"本项目位于…。总面积为…。涉及…。界址点…。"这种填空式流水账；避免每句都以"本项目/经…/综上所述"开头，句子长短结合，读起来像有经验的工程师手写。
- 🔴 删除重复（全局）：项目位置、总面积、权属、地类等概况信息**每章只写一次**，禁止在同一章或跨章大段重复粘贴相同内容；引用前序数据时用简洁语句承接。
- 🔴🔴 不写思考依据（全局，一票否决）：正文**只写结论和事实，不交代自己是怎么写的**。禁止出现「相关经验仅作为工作方法参考」「本章不再重复罗列」「决策名称与文号一致，便于对应」「后续调查均围绕该名称对应的地块进行，不涉及其他地块」这类编排说明、写作交代、自我解释。小节标题下面直接写实质内容。
- 🔴🔴 不虚构现场作业（全局，一票否决）：资料里没有的现场细节一律不写。禁止「评估组在走访时向群众解释…」「调查人员在三圩社区入户时向群众说明…」这类**资料无法证明发生过**的具体言行；也不要写「参考了历年同类项目经验」「本区已完成的成片开发案例」这类无从查证的先例。只写签到表、问卷、座谈记录、勘测定界成果能证明的事。
- 🔴🔴 有数据就必须写实数（全局，一票否决）：凡「事实卡」里有真实数值的（户数、问卷份数、支持率、面积、日期…），**必须写具体数字**，禁止用「以现状调查确认结果为准」「以实际统计为准」等模糊话术搪塞。只有该数据事实卡确实没有、且用户也未提供时，才用中性表述并进缺口清单。
- 🔴 模板只作依据不作范本（全局）：允许沿用模板的**结构与规范表述**，但**不得整段照抄模板示例文字**；每一段都要落到本项目的事实上，读起来通顺自然。
- 🔴 正文禁写「界址点数」（全局，一票否决）：界址点数量只出现在表格里（第6章 表6-1「勘测定界地块情况汇总表」的「界址点数」列），**任何章节的正文都不得出现「界址点221个」这类表述**。
- 🔴🔴 术语规范（DB32/T4013-2021 §6.3，全局，一票否决）：
  * 一律写**「社会稳定风险评估」**，正文中不得简称「稳评」（**章节标题、表格表头、以及专有名称如「社会稳定风险评估评审表」「社会稳定风险评估责任单位」可以保留**）；
  * 一律写**「拟征收土地」**，不得使用「征地」（「征地补偿」等已固定的政策文件名与法定表述除外，如《江苏省被征地农民社会保障办法》）；
  * **法律名称必须写全称**，不得简写（如写《中华人民共和国土地管理法》，不写《土地管理法》）；
  * **量化数据保留一位小数**（如 326342 平方米写作 326342.0 平方米无必要——此处指**百分比、亩数、得分等计算结果**一律保留一位小数，如 100.0%、489.5 亩、12.5 分）。
{_old_project_block}{_regulation_whitelist}- 🔴 法规引用：法规名称、文号只能来自上面的「可引用法规清单」或「评估依据」，清单之外的一律不写，绝不编造文号（严禁自行编造〔XXXX〕N号）
- 禁用AI套词：{', '.join(AI_BUZZWORDS)}
- 写短句，像老工程师写报告
- 涉及村组：根据实际材料中的村组名称
{_get_learning_hints()}
{_get_chapter_antipatterns(num)}
{_get_expert_skill_hints(num)}
"""
    return prompt


def _get_chapter_specific_requirements(num: int) -> str:
    """章节特定要求：针对用户/专家反复指出的结构性问题，注入对应章节。

    章节结构照参照件《白马湖农场2号风电项目社会稳定风险评估报告》十四章。
    项目颗粒信息一律取自「🔗 本项目事实卡」，本函数内不硬编码任何具体项目的数字与名称。
    """
    if num == 1:
        return """
## 📐 第一章「编制说明」特定要求
本章是全文开篇，读者据此判断「这次稳评该不该做、按什么规矩做」。**内容必须充实**（本章不少于 1800 字），
按下列顺序组织：

### 一、前言（不设标题，正文直接开头，3～4 段）
- 第 1 段：写**为什么必须做这次稳评** —— 引据中办发〔2021〕11号、苏办发〔2021〕15号及《淮安市重大决策
  社会稳定风险评估工作运行机制》，说明本项目属于必须开展社会稳定风险评估的重大决策事项。
- 第 2 段：写**受托背景** —— 受〔责任单位〕委托，本公司承担〔项目名〕的社会稳定风险评估工作；
  一并交代本项目的文号、位置、面积、用途（取事实卡）。
- 第 3 段：写**评估工作如何开展** —— 工作组通过资料收集、现场踏勘、问卷调查、座谈走访、风险分析研判等方式
  开展工作，形成评估报告，供决策机关参考。
- 第 4 段：写**致谢** —— 对属地党委政府、相关部门及基层组织在走访调查与资料收集中的指导和协助表示感谢
  （协助单位取事实卡；事实卡没有就写「属地有关部门和基层组织」，**不要编具体单位名**）。

### 二、1.1 稳评目的（3 条，**用（一）（二）（三）编号**）
每条写 2～3 段：先讲该目的的政策依据与内涵，再结合本项目的征收范围、涉及群众、调查安排说明本次稳评要回答什么问题。
- （一）以人民为中心，妥善解决群众诉求；
- （二）稳评结论作为决策重要依据，实现预知预警预防；
- （三）将稳评科学嵌入重大决策程序环节。

### 三、1.2 稳评原则（4 条，**用（一）（二）（三）（四）编号**）
每条**先单独一行写「（一）原则名称」**，再另起一段写该原则的含义与本项目的落实做法。
格式严格为：
```
（一）客观公正性原则

（正文段：稳评所用资料、数据全面真实有效；本次评估采用问卷调查与部门调查相结合，覆盖不同群体……）

（二）科学评价性原则

（正文段：……）
```
四条依次为：（一）客观公正性原则 /（二）科学评价性原则 /（三）全面系统性原则 /（四）权责统一性原则。

### 四、1.3 稳评理由（**本节必须写，不得省略**；用（一）（二）（三）（四）编号）
说明本项目**为什么需要开展社会稳定风险评估**，按下列四点各写一段，段首用（一）（二）（三）（四）：
- 法律政策依据：《中华人民共和国土地管理法》第四十七条要求开展拟征收土地现状调查和社会稳定风险评估；
  中办发〔2021〕11号、苏办发〔2021〕15号关于重大决策应评尽评的要求；
- 本项目属性：本项目属土地征收决策，直接关系被征地农村集体经济组织及其成员的切身利益，
  属于必须评估的重大决策事项；
- 现实必要性：征收范围涉及多个权属单位与地类，群众对补偿标准、社会保障名额等高度关注；
  通过评估可提前识别风险、把矛盾化解在决策实施之前；
- 结论运用：评估结论作为本项目决策的重要依据，未通过评估或未落实防范化解措施的不得实施。

🔴 全章禁止写项目概况的展开（那在第6章）；禁止编造法规名称与文号；
🔴 正文一律**两端对齐**，二级标题下的正文用（一）（二）（三）编号，与全报告体例一致。
"""
    if num == 2:
        return """
## 📐 第二章「编制依据」特定要求
- 2.1 国家法律法规和文件：按 法律→行政法规→部门规章 顺序列出，**每条尽量带施行/修正年份**。
- 2.2 稳评编制依据：稳评专门规范与文件（DB32/T4013-2021、DB32/T4937-2024、《江苏省重大决策社会稳定风险评估办法》、《关于加强新形势下重大决策社会稳定风险评估机制建设的实施意见》等）。
- 2.3 相关文件：本项目实际取得的文件（公告/批复、用地预审与选址意见书、勘测定界报告、红线图等）。
- 🔴 法规名称与文号**只能取「可引用法规清单」**，清单之外一律不写，绝不编造文号。
"""
    if num == 3:
        return """
## 📐 第三章「社会稳定风险评估评审表」特定要求
- 🔴 本章是**江苏省统一制式评审表**，由系统按模板就地渲染成 22 行制式表格，**不需要你撰写正文**。
- 如被要求输出正文，请只输出一行："（本章为江苏省统一制式评审表，由系统渲染）"。
"""
    if num == 4:
        return """
## 📐 第四章「社会稳定风险评估工作报告」特定要求
- 本章是**全文的总报告**，5 个小节：4.1 项目基本情况 / 4.2 四性分析 / 4.3 不稳定风险因素 / 4.4 稳评结论及工作建议 / 4.5 参与人员签字。
- 🔴 4.1 项目基本情况：项目名、决策主体、责任单位/实施单位、位置、用地范围与规模（㎡与亩）、用途与征收目的、资金筹措、实施周期。
  数据**全部取事实卡与公告原文**，不要写任何机电参数类内容（本项目是土地征收，不是工程建设项目）。
- 🔴 4.2 四性分析**按下面四个三级标题逐一展开，每个三级标题 2～4 段**（这是行业通行的行文骨架，参照知识库同类土地征收案卷）。
  **输出格式必须是 markdown 三级标题 `#### 4.2.1 合法性评估`，不要用「（一）合法性评估」这种加粗写法**：

  #### 4.2.1 合法性评估 依次写四层：
    ① 法律法规相符性 —— 本项目实施符合哪些法律法规（名称只能取「📚 检索到的真实素材」里的，禁止自行编造）；
    ② 地方规划相符性 —— 符合本地国民经济和社会发展规划、国土空间规划等（**规划名称与内容只能来自检索素材**，
       素材里没有提到的规划一律不要写，更不许编一个像样的规划名）；
    ③ 征收目的合法 —— 依《中华人民共和国土地管理法》第四十五条，说明本项目属于哪种法定情形；
    ④ 土地征收程序合法 —— 已取得的前置要件（预公告文号与公告期限、勘测定界、现状调查等，取事实卡）。
    每层结尾一句「因此，本决策……合法/合法合规」收束。
  #### 4.2.2 合理性评估：① 是区域经济社会发展/规划实施的需要；② 对本项目用地功能（如商业服务业、
    交通、公共服务等，取事实卡土地用途）的作用；③ 符合绝大多数群众的根本利益（引用第9章问卷支持率）。
  #### 4.2.3 可行性评估：① 资金来源可靠；② 各级政府支持；③ 基层组织和群众认同。
  #### 4.2.4 可控性评估：① 安全可控性；② 发生群体性事件、集体上访、个人极端事件的可能性；
    ③ 引发其他社会治安问题（含舆论）的可能性；最后给出「风险可控」的结论。
- 🔴 **四性分析的每一个论断都必须有依据**：法规/规划名称取自检索素材，项目数据取自事实卡，
  群众意见取自第9章调查数据。**凡是素材与事实卡里都没有的，不要写**——宁可少写一句，
  也不许编造法规名称、规划名称、先行项目或数字。
- 4.3 列主要不稳定风险因素及等级（表4-1），风险项与第11章一致。
  🔴 **条数与名称必须与第11章逐一对应**，既不得多写也不得少写（实测曾出现第4章写 4 类、
  第11章写 5 类、第13章写 6 类，评审一眼就看出前后打架）。
- 🔴 **本章不要再放量化评分表**：表12-2 量化指标评价体系 / 表12-3 群众意见分析
  已在本报告**第12章**给出，4.4 只写**文字结论**（总分、风险等级判定依据、结论）。
  需要指向表格时写「详见第12章表12-2」，**不得复制一份表到本章** ——
  复制出来的那份得分列是空的（措施后分没算），权重口径还会与第12章冲突。
- 🔴 4.4 必须写明：总分、风险等级判定依据、结论（决策/项目是否可实施）、若干条工作建议；
  风险等级**取系统权威值**（见评分参考），不得自行判定。
- 🔴 引用的面积、支持率、问卷数等数据**必须与第6、9、12章完全一致**。
- 4.5 写明编写单位、编写时间，并按事实卡「稳评工作组人员及分工」列出参与人员姓名与职务（留签字空行）。
- 🔴 **两个日期不要混用**：事实卡的「稳评公示期」（公示期一周，由用户填开始日后系统推算）≠
  「公告期限」（征收土地预公告的期限）。写稳评公示就用「稳评公示期」，写预公告就用「公告期限」；
  **缺哪一个就不要写具体日期**（写「以实际公示记录为准」），不得用另一个顶替，更不得自行编日期。
"""
    if num == 5:
        return """
## 📐 第五章「突发性事件或不稳定因素处置应对工作预案」特定要求
- 12 个小节全部要写实：总则、指导思想、工作原则、组织领导、职责任务、工作流程、预警机制、应急响应、信息报送和处理、后期处置、保障措施、奖惩责任追究。
- 🔴 应急响应等级统一用中文格式：「一级响应（特别重大事件）」「二级响应（较大事件）」「三级响应（一般事件）」，全文编号一致。
- 🔴 组织领导取事实卡的责任单位与项目所在地，成立处置应对工作领导小组并下设办公室，明确协调联络/政策宣传/应急救援/后勤保障/资金统筹分工。
- 🔴 **「组织领导」必须写出领导小组的构成名单：组长 / 副组长 / 成员的「姓名 ＋ 职务」**，
  不能只写「由××政府分管领导担任组长、相关职能部门负责人为成员」这类泛称就了事
  （业主 2026-09-28 反馈：没有具体名单）。
  名单属甲方人事、事实卡与材料里推不出来时，**就地留明确占位**：
  `【待填写：领导小组组长、副组长、成员名单（姓名·职务）】`
  —— 交付前由我方按正式文件补全。**严禁编造人名**，也严禁用泛称蒙混过去。
- 🔴 预警机制要写清信息搜集报送机制、信息报告内容、预警级别划分。
"""
    if num == 6:
        return """
## 📐 第六章「项目基本情况」特定要求
- 6.1 项目概况：项目名称、责任单位/建设单位、实施单位；项目位置与四至；用地范围与规模（㎡与亩、权属构成、地类构成）；用途；资金筹措；实施周期。
- 6.2 利益相关者情况：与项目有利益关系的各类群体的**数量范围**（涉及户数、权属单位、集体经济组织及其成员）以及各类群体的知晓参与情况。
- 🔴 用地范围与规模（6.1）：先写一句引导语（如"项目用地基本情况如下表所示"），随后**单独一行**输出 `[SYS_TABLE:6-1]`（系统据此就地渲染土地分类面积表，不要手写该表、不要写表号），表后再用一句解读收束（如总面积与亩数换算、哪一权属主体面积最大）。
- 🔴 **正文禁止写"界址点数"及其数值**（多余，表格中已含）。
- 🔴 涉及户数：有事实卡真实数据就直接写具体数字，**禁止写"以现状调查确认结果为准"等占位话术**；确实缺失时才用规范话术。
- 🔴 项目位置、面积、权属只在本章完整写一次，后续章节用"上述地块/该项目"指代。
- 🔴 事实卡「征收土地预公告正文摘录」里的**征收目的原文、征收范围原文、公告期限、工作安排原文**
  必须在本章用足（逐段引述并按本项目情况展开），这是本章最主要的实质素材，不要只写一句话概括。
- 🔴 6.2 各类利益相关群体要**逐类分开写**（土地所有权人/使用权人、农村集体经济组织及其成员、
  村民委员会、其他利害关系人）：各是谁、与本项目的利益关系、数量范围、知情与参与渠道、
  意见反馈途径，每类 1～2 段。
"""
    if num == 7:
        return """
## 📐 第七章「稳评工作方案」特定要求
- 6 个小节：7.1 指导思想 / 7.2 评估方法 / 7.3 评估内容 / 7.4 评估程序及步骤 / 7.5 评估时限（过程） / 7.6 工作计划。
- 7.4 评估程序及步骤：九个步骤依次写清（明确目的→成立评估组制定方案→收集审阅资料→听取各方意见→全面评估论证→确定风险等级→编制报告→专家评审修改→正式提交）。
- 7.6 工作计划：稳评小组分工＋调研方案（公告公示/现场勘察/座谈会及走访）＋风险预测＋报告编制审核＋评审报送。
  🔴 人员与分工**只能取事实卡「稳评工作组人员及分工」**，照实列（负责人/调研/风险研判与报告编制/数据汇总整理/资料与档案），**不得自行编造人名**。
- 🔴🔴 日期与天数**只能取事实卡真实值**（委托日期/公示起止/实地勘察/座谈会/报告编制/专家评审等）。事实卡没有的一律**不写具体年月或"X 个工作日"**，用「按委托约定时间组织实施」这类中性表述。
- 🚫 严禁"各阶段工作天数根据项目实际推进情况衔接安排"这类模糊话术，**也严禁编造日期或天数**。
- 🔴 时间逻辑：所有调查、评估时间必须在公告/批复发布之后，各阶段依次递进。
"""
    if num == 8:
        return """
## 📐 第八章「稳评方法与过程」特定要求
- 8.1 风险调查的方法 / 8.2 风险调查的范围及对象 / 8.3 风险调查内容 / 8.4 风险调查过程。
- 🔴 8.4 风险调查过程必须按「①现场公示 ②实地勘察 ③座谈会」三块组织，各块写明：公示点/公示期/公示周期、勘察地点/方式/时间、座谈会地点/时间/参会人员/参会人数/群众意见征询情况。
- 🔴 时间一律取事实卡真实值（缺则由系统询问用户补充），**禁止编造时间**。
- 🔴 现场照片/公示照片/座谈会照片由系统按「两张一行」并排排版，正文用一句话引出即可（如"现场公示与座谈情况见图8-1至图8-3"），不要写图号细节。
- 🔴 调查范围必须覆盖全部直接利益相关者；调查对象要写清户数及分组分解（有数据时）。
- 🔴 8.1 要逐个方法展开：资料收集法、问卷调查法、座谈访谈法、现场勘查法、舆情分析法——
  每个方法写清"怎么做 + 本项目实际做到什么程度（问卷 50 份、部门 2 家、座谈场次、踏勘次数）"。
- 🔴 8.3 风险调查内容按「合法性 / 合理性 / 可行性 / 可控性」四个维度分别列出本项目实际调查了什么，
  并与第10章的四性分析口径一致（本章只写"调查了什么"，结论留给第10章）。
- 🔴 8.4 的每一块（现场公示 / 实地勘察 / 座谈会）都要写足「时间、地点、方式、参与对象、主要过程与结果」五项要素，
  时间一律取事实卡真实值，缺失则按规范话术描述，**不得编造**。
"""
    if num == 9:
        return """
## 📐 第九章「各利益相关者对项目的反映」特定要求
- 9.1 问卷调查情况：个人问卷与单位问卷的发放数、有效回收数、回收率；知晓率、支持率、反对率。
- 🔴🔴 **表9-1、表9-2 由系统渲染，你不得手写这两张表**（这是铁律）：
  - 表9-1 个人问卷统计汇总表 → 在（一）小节内**单独占一行**输出标记 `[TABLE:ch9_public_survey]`
  - 表9-2 单位（部门）问卷统计汇总表 → 同样**单独占一行**输出 `[TABLE:ch9_dept_survey]`
    （本题无单位/部门问卷时**才**省略这一行）
  - **不要写「表9-1 …」「表9-2 …」表题行**，也不要写 markdown 表格（`| 调查内容 | 选项 |…`）——
    系统已自带规范表题；你若再写一遍，会出现「一张表两个表题」的排版事故。
  - 原因：题目文字必须与《稳评问卷调查表》原件**逐字一致**、人数必须与逐页清点结果一致，
    这两项是评审核验点，只能由系统直出。你手写会被概括改写或抄错（实测发生过）。
- 🔴 两张表之间的正文要求：标记前后要有**分析文字**，形成「引导语 + 标记 + 解读句」的承接写法，
  例如先写发放回收与知晓/支持/反对概况，再放标记，表后接着分析诉求分布特征。
  不得「表后无内容」，也不得只放标记不写文字。
- 🔴 统计维度的口径（供你写正文时引用，**不要据此手写表格**）：本项目《稳评问卷调查表》共六项，
  ①您是（本地居民／租住本区／附近上班／路过）②您的年龄（16～35／36～55／56以上）
  ③您的职业（机关事业／企业／待业／其他）④您是否了解本决策（了解／不了解）
  ⑤对该决策是否支持（支持／无所谓／有条件支持／反对／强烈反对）
  ⑥您会采取何种方式解决诉求（调解／诉讼／正常反映／其他）。
  问卷里没有的题目（如补偿标准满意度、安置方式满意度）**正文里也不要提**。
- 🔴 正文引用的每一项人数，一律照抄事实卡「问卷调查统计」里的真实数字，不得自行编造、不得留空、
  不得重新口算或四舍五入（实测出现过表写 7 人、紧邻正文写 2 人的自相矛盾）。
- 9.2 各方意见汇总情况：①政府及相关部门意见；②基层组织意见；③被调查群众诉求；④网络媒体舆论意见。
- 🔴 数据克制：同一组数据只写一次，禁止全文反复整段罗列。
"""
    if num == 10:
        return """
## 📐 第十章「项目合法性、合理性、可行性、可控性分析」特定要求
- 10.1 项目的合法性分析：①符合法律法规要求；②实施主体合法；③项目目的合法；④实施程序合法。
- 10.2 合理性分析：①促进区域发展；②符合群众利益；③实施难度较低；最后总结具备合理性。
- 10.3 可行性分析：①资金来源可靠；②各级政府支持；③基层组织和群众认同；最后总结具有一定可行性。
- 10.4 可控性分析：①安全可控性；②发生群体性事件的可能性；③引发其他社会治安问题的可能性。
- 🔴 项目/征收目的与规划用途的差异必须解释（如果两者都已知且不一致）：公告载明的目的与规划用途属于"法定目的"与"规划用途"两个层面，不构成矛盾；须新增一段专门说明（约 150～250 字）消除审查疑问。
- 🔴 **土地分类面积表（表6-1）已在第6章渲染**，本章**不要再手写、也不要重复渲染该表**；需要引用时用"详见第6章表6-1"一句话承接。
- 🔴 面积/地类数据铁律：正文出现的每一处地类面积、权属主体面积，必须与表6-1的真实数值完全一致。**禁止把总面积套用到每个地类或每个权属主体上**。
- 🔴 禁止无依据断言：不得写"范围内无常住人口""无群众上访""无历史遗留问题"等结论性断言，除非事实卡确有依据。
"""
    if num == 11:
        return """
## 📐 第十一章「分析预测风险源」特定要求
- 🔴 本章小节**由你按本项目实际风险类别自行划分**（参照件按项目类型逐项展开，格式 `### 11.1 项目××方面的风险`）。
- 每个风险小节写清三件事：**风险描述 ＋ 风险分析 ＋ 风险程度判定**（等级用"重大/较大/一般/较小/微小"等规范表述）。
- 🔴 风险类别要与本项目类型相符（土地征收与风电、道路等项目的风险源不同），不得照搬其他项目类型的风险清单。
- 🔴 **禁止出现"征收程序方面的风险"**：前期程序已依法履行的项目，该类风险不列入。
- 🔴 风险因素要与第9章调查到的群众意见、第10章四性分析相互印证，不得凭空提出。
- 🔴 **本章的小节标题就是全篇「主要风险因素清单」，是唯一口径**：你在这里划分出的
  `### 11.x ××方面的风险` 即 N 项主要风险因素；第4章表4-1、第12章表12-4、
  第13章表13-1 的「风险因素/风险类型」列**必须与之逐条同名同序、条数一致**，
  不得新增、不得改名、不得换说法。
  ⚠️ 实测踩过：第11章小节写「土地征收补偿方案/征收决策实施/征地社保名额确定/征收决策资金/周边水系及环境影响」，
  第11章汇总表却另写「补偿标准争议/补偿资金到位/社保安置落实/施工期环境影响/信息公开不充分」，
  第13章表13-1 又冒出「征收程序方面的风险」——三处各一套，评审一眼看出对不上（业主 2026-09-28 反馈）。
- 🔴 **资金与决策主体不得下移**：补偿资金/财政统筹、安置名单确认等**主体一律取事实卡的稳评责任单位（区级）**；
  其下的乡镇（街道）、居委会只是**配合/组织实施单位**，禁止写成「××镇人民政府财政统筹」
  「报××镇人民政府确定」这类把决定权/资金权下移的表述。
  ⚠️ 实测踩过：同一份报告里第6章写「区政府财政统筹」、第11章又写「三河镇人民政府财政统筹」，自相矛盾。
- 🔴 **本章不放表格**，章末也**不要写「如下表所示」「见下表」「从上表可见」这类承接语**。
  风险因素汇总表（含初始风险等级）统一在本报告**第12章**（表12-4）给出，需要指向时
  写「详见第12章表12-4」，不要把表复制到本章。
  ⚠️ 实测踩过两次：模型写了「汇总如下表所示：」紧接「从上表可见……」，**中间根本没有表**
  —— 评审会直接发现正文承诺了一张不存在的表。宁可只写文字结论，也不要留悬空引用。
"""
    if num == 12:
        return """
## 📐 第十二章「确定风险等级」特定要求
- 12.1 风险分析方法：定性与定量相结合；风险等级分**高、中、低**三级；说明发生概率与影响程度的判断方法。
- 12.2 稳评风险等级确定量化指标评价体系：表12-1 直接定性指标（本项目无上述情形则说明）；表12-2 量化指标评价体系（合法性/合理性/可行性/可控性四维度权重与评分标准）；表12-3 群众意见分析；附评分规则与计算公式。
- 12.3 主要风险因素初始风险等级表：表12-4，汇总各项风险等级及初始总得分与等级。
- 🔴 量化评分表表头必须对齐：`测评指标|权重|测评项目|评分|评分标准|得分`，每行只填本行指标，得分不得错位。
- 🔴 评分采用**扣分制**：得分列=扣分值，符合计 0 分、部分符合按档计分、不符合计满该项最高扣分；扣分合计不得超过各维度权重之和 100 分。严禁出现「百分制总分」「80分/90分」等表述。
- 🔴 每张表必须有表题名称（如"表12-2 稳评风险等级确定量化指标评价体系"），**不得只写表号没有名称**。
- 🔴 表内所有单元格文字（含"评分标准"列的长句）字体、字号、行距、列宽一致。
"""
    if num == 13:
        return """
## 📐 第十三章「防范和化解风险的工作建议」特定要求
- 🔴 本章小节**由你按措施自行划分**（参照件格式 `### 13.1 措施名`），且必须与**第11章列出的风险一一对应**，不得遗漏、不得新增无对应风险的泛泛建议。
- 每条措施写清：具体做法 ＋ 责任主体（取事实卡责任单位及其下属部门/属地）。
- 🔴 通用方向可覆盖：严格履行报批程序、用地管理与复垦保障、资金落实与安全、生态与耕地保护、交通疏导与道路修复、安全防护、信访处置与舆情稳控等——**按本项目实际风险取用，不要照搬**。
- 🔴 稳评责任主体取事实卡的责任单位；若涉及其下乡镇/街道，则为配合实施单位，责任主体表述不得混淆。
- 🔴 表13-1「风险类型」列**必须与第11章各小节标题逐条同名同序、条数一致**（第11章是唯一口径）；
  ⚠️ 尤其**禁止出现「征收程序方面的风险」**——该项目前期程序已依法履行、第11章不列此项，
  第13章表里冒出来就与第11章对不上（实测踩过，业主 2026-09-28 反馈）。
- 🔴 措施要可落地、可核查，禁止"加强领导、高度重视"这类空话。
"""
    if num == 14:
        return """
## 📐 第十四章「附件」特定要求
- 🔴 本章由系统按附件清单挂载实际文件与图片，**不需要你撰写正文**。
"""
    return ""

# Cached learning hints (updated every 5 minutes)
_learning_hints_cache = {"hints": "", "updated": 0}


def _get_expert_skill_hints(chapter_num: int) -> str:
    """加载专家蒸馏的审核 skill + 用户报告要求，注入生成 prompt 预防问题。

    规则型 skill：告诉 LLM「禁止出现某模式」，从源头避免犯错
    文本型 skill：告诉 LLM「应该怎么写」，给出纠正示例
    用户要求（skill_type='user_requirement'）：无条件注入，必须遵守
    """
    try:
        import sqlite3
        from app.config import settings
        db_path = settings.DATA_DIR / "knowledge_base.db"
        if not db_path.exists():
            return ""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # 🔴 用户报告要求（无条件注入，不按章节过滤）
        user_rows = conn.execute(
            "SELECT chapter_num, skill_type, rule_pattern, rule_desc, correction FROM review_skills "
            "WHERE is_active=1 AND skill_type='user_requirement' "
            "ORDER BY id LIMIT 8"
        ).fetchall()
        # 专家 skill（规则 + 文本，按章节）
        rows = conn.execute(
            "SELECT chapter_num, skill_type, rule_pattern, rule_desc, correction FROM review_skills "
            "WHERE is_active=1 AND skill_type != 'user_requirement' AND (chapter_num=0 OR chapter_num=?) "
            "ORDER BY id LIMIT 20",
            (chapter_num,)
        ).fetchall()
        conn.close()

        parts = []
        if user_rows:
            ulines = ["\n## 📋 用户报告要求（必须严格遵守，不可违背）"]
            for r in user_rows:
                corr = f"（提示：{r['correction']}）" if r["correction"] else ""
                ulines.append(f"- {r['rule_desc']}{corr}")
            parts.append("\n".join(ulines))

        if rows:
            lines = ["\n## ⚠️ 历史专家反馈（生成时必须避免，这是专家反复指出的问题）"]
            for r in rows:
                ch = f"第{r['chapter_num']}章 " if r["chapter_num"] else ""
                if r["skill_type"] == "rule" and r["rule_pattern"]:
                    corr = f"（正确写法：{r['correction']}）" if r["correction"] else ""
                    lines.append(f"- 禁止出现「{r['rule_pattern']}」：{r['rule_desc']}{corr}")
                elif r["skill_type"] == "text" and (r["rule_desc"] or r["correction"]):
                    lines.append(f"- {ch}{r['rule_desc']}：{r['correction']}")
            parts.append("\n".join(lines))

        return "\n".join(parts) + "\n" if parts else ""
    except Exception:
        return ""


def _get_learning_hints() -> str:
    """Get cached learning hints for prompt injection. Updates every 5 min."""
    import time as _time
    now = _time.time()
    if now - _learning_hints_cache["updated"] > 300:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't run async in sync context; use latest cache
                return _learning_hints_cache["hints"]
        except RuntimeError:
            pass
    return _learning_hints_cache["hints"]


async def refresh_learning_hints(domain: str = "stability"):
    """Refresh the cached learning hints. Call periodically or on startup."""
    try:
        from app.services.learning_service import learning_service
        hints = await learning_service.build_learning_hints(domain)
        _learning_hints_cache["hints"] = hints
        _learning_hints_cache["updated"] = __import__("time").time()
    except Exception:
        pass


def _summarize_previous(chapters: dict) -> str:
    """Summarize previous chapters for context — 结构化提炼关键结论。

    不只取前200字符，而是用正则提取每章的关键数据点（面积/位置/风险等级/评分/
    风险因素等），让后序章节能承接前序结论，保证逻辑连贯。
    """
    if not chapters:
        return ""
    lines = []
    for num in sorted(chapters.keys()):
        ch = chapters[num]
        md = ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
        title = ch.get("title", "") if isinstance(ch, dict) else ""
        key_facts = _extract_key_facts(md)
        if key_facts:
            lines.append(f"第{num}章「{title}」关键结论：{key_facts}")
        else:
            # 退化为前150字
            summary = md[:150].replace("\n", " ").replace("#", "")
            lines.append(f"第{num}章「{title}」: {summary}...")
    return "\n".join(lines)


def _extract_key_facts(md: str) -> str:
    """从章节 markdown 提取关键数据点（面积/位置/风险等级/评分/风险因素）。"""
    facts = []
    patterns = [
        (r'(?:总面积|面积|规模)[为：:约]*\s*(\d+\.?\d*\s*(?:亩|㎡|平方米))', "面积"),
        (r'(?:位于|坐落|位置)[为：:约]*\s*([^\s，。]{2,20}(?:街道|镇|社区|村))', "位置"),
        (r'(?:风险等级|综合风险|判定为|属于)[为：:约]*\s*(低风险|中风险|高风险)', "风险等级"),
        (r'(?:措施前|措施后)?(?:综合)?(?:得分|评分)[为：:约]*\s*(\d+(?:\.\d+)?)\s*分', "评分"),
        (r'(?:支持率|知晓度)[为：:约]*\s*(\d+\.?\d*)\s*%', "调查率"),
        (r'(?:问卷|调查)[为：:约]*\s*(\d+)\s*份', "问卷数"),
        (r'(?:涉及|共涉及)\s*(\d+)\s*户', "户数"),
        (r'(?:识别出|主要风险)[为：:约]*\s*([^\n。]{5,40}?)(?:等)?风险', "风险因素"),
    ]
    for pattern, label in patterns:
        import re as _re
        m = _re.search(pattern, md)
        if m and not any(label in f for f in facts):
            facts.append(f"{label}:{m.group(1).strip()[:30]}")
    return "；".join(facts[:8]) if facts else ""


_CHAPTER_ANTIPATTERNS = {
    1: """## ⛔ 第1章常见错误（请避免）
❌ "征收面积约500亩" → 征收面积必须与「🔗 本项目事实卡」中的面积完全一致，不得四舍五入、不得用"约"
❌ "涉及农户若干户" → 事实卡有具体户数则写具体数字；没有则写规范文字"涉及户数以现状调查确认结果为准"，严禁编造
❌ "项目位置优越" → 去掉"优越"等主观形容词，只写事实
❌ "资金测算约XX万元" → 用户未提供资金数据时必须写"资金测算以实际方案为准"，不得编造具体金额
""",
    3: """## ⛔ 第3章常见错误（请避免）
❌ "支持率89.5%" → 征地项目原则上100%支持。事实卡有真实支持率则用真实值；否则写"以问卷调查汇总结果为准"，不得编造
❌ "反对率10.5%" → 征地项目反对率一般为0%，不得出现与事实卡矛盾的反对数据
❌ "发放问卷150份" → 填写事实卡给出的问卷/回收份数；没有则写"问卷发放与回收情况以现状调查确认结果为准"
❌ 调查表数据与正文不一致 → 表格和正文的数值必须完全一致
""",
    6: """## ⛔ 第6章常见错误（请避免）
❌ "合法性得分-5分" → 评分不能为负数，所有评分在0-100之间
❌ "总分120分" → 评分不能超过100分
❌ 编造评分项 → 评分项必须来自DB32/T4013-2021标准
❌ 只说总分不说各维度分 → 必须列出合法性/合理性/可行性/可控性各维度得分
""",
    8: """## ⛔ 第8章常见错误（请避免）
❌ 措施后得分低于措施前 → 措施后评分应比措施前高5-15分
❌ 措施前后无变化 → 必须体现措施效果
❌ 编造措施后评分 → 措施后评分必须在措施前基础上合理提升
""",
    4: """## ⛔ 第4章常见错误（请避免）
❌ 编造法规文号 → 只能引用「评估依据」中列出的法规，知识库未提供的法规一律不写
❌ "依据相关法律法规" → 必须引用具体法规名称和文号
❌ 四性分析一句话带过 → 每项分析至少2-3段（150-300字/段）
""",
}


def _get_chapter_antipatterns(chapter_num: int) -> str:
    """Get chapter-specific anti-patterns (negative examples) for the prompt."""
    return _CHAPTER_ANTIPATTERNS.get(chapter_num, "")


