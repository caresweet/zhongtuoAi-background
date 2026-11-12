# -*- coding: utf-8 -*-
"""稳评《数据资料表》Excel：生成模板、读回、写入事实卡。

与复垦的《复垦基础资料》同一套用法：
    python -m scripts.build_stability_intake          # 生成空白模板
用户填写后上传，系统在生成前读入 `filled_data`（只在本字段未被锁定/确认时写入）。

覆盖报告的**项目事实**（项目/责任单位/位置/面积/用途/户数/补偿/资金）、
**时间节点**、**调查数据**（问卷/支持率/诉求/意见）、**人员与联系**；
风险等级、评分、正文结论等由系统计算/生成，**不在表里填**。
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SHEET_HOME = "说明"
SHEET_BASE = "项目基本信息"
SHEET_TIME = "时间节点"
SHEET_SURVEY = "调查数据"
SHEET_CONTACT = "人员与联系"
SHEET_ATTACH = "附件清单"

SHEETS = [SHEET_HOME, SHEET_BASE, SHEET_TIME, SHEET_SURVEY,
          SHEET_CONTACT, SHEET_ATTACH]

_DATE_KEYS = {
    "commission_date", "plan_date", "plan_approve_date", "notice_publish_date",
    "publicity_start_date", "notice_date", "notice_end_date", "survey_date",
    "symposium_date", "risk_predict_date", "risk_predict_end_date",
    "report_draft_date", "report_draft_end_date", "review_date",
    "review_end_date",
}

# (标签, key, 类型, 说明)
_BASE_FIELDS = [
    ("项目名称", "project_name", "text", "与公告/立项文件一致"),
    ("决策主体", "decision_unit", "text", "如：XX区人民政府"),
    ("稳评责任单位", "org_name", "text", "通常为区/县人民政府或主管部门"),
    ("项目位置", "location", "text", "写到区/街道/村"),
    ("所属地级市", "city", "text", "如：淮安市"),
    ("所属区县", "district", "text", "如：洪泽区"),
    ("所属乡镇/街道", "town", "text", "如：高良涧街道"),
    ("征收面积（亩）", "area_mu", "number", ""),
    ("征收面积（平方米）", "area_m2", "number", "与勘测定界一致"),
    ("土地用途", "land_use", "text", "如：工业用地"),
    ("涉及户数", "household_count", "number", "被征收农户数"),
    ("补偿安置方案文号", "compensation_doc", "text", ""),
    ("资金筹措单位", "funding", "text", "如：区财政统筹"),
    ("界址范围描述", "boundary_desc", "text", "东至…南至…西至…北至…"),
]

_TIME_FIELDS = [
    ("委托日期", "commission_date", "date", "YYYY-MM-DD"),
    ("制定方案日期", "plan_date", "date", ""),
    ("方案审核日期", "plan_approve_date", "date", ""),
    ("公告发布日期", "notice_publish_date", "date", ""),
    ("稳评公示开始日期", "publicity_start_date", "date", "公示期一般 7 天，结束日系统推算"),
    ("现场公示开始日期", "notice_date", "date", ""),
    ("现场公示结束日期", "notice_end_date", "date", ""),
    ("问卷日期", "questionnaire_date", "date", "问卷/调查表上的日期"),
    ("实地勘察日期", "survey_date", "date", ""),
    ("座谈会日期", "symposium_date", "date", ""),
    ("座谈会地点", "symposium_location", "text", ""),
    ("风险预测开始", "risk_predict_date", "date", ""),
    ("风险预测结束", "risk_predict_end_date", "date", ""),
    ("报告编制开始", "report_draft_date", "date", ""),
    ("报告编制结束", "report_draft_end_date", "date", ""),
    ("专家评审开始", "review_date", "date", ""),
    ("专家评审结束", "review_end_date", "date", ""),
    ("报告编写年月", "report_month", "month", "YYYY-MM"),
]

_SURVEY_FIELDS = [
    ("问卷份数", "total_samples", "number", "有效问卷份数"),
    ("群众支持率（%）", "support_rate", "number", "如：100"),
    ("群众反对率（%）", "oppose_rate", "number", "如：0"),
    ("群众知晓率（%）", "awareness_rate", "number", "如：100"),
    ("部门问卷单位数", "dept_survey_count", "number", "参与问卷的部门数"),
    ("座谈会参会人员", "symposium_attendees", "text", "以顿号分隔"),
    ("群众诉求", "villager_demands", "text", "主要诉求摘要"),
    ("部门意见", "dept_opinions", "text", "主管部门意见"),
    ("基层组织意见", "grassroots_opinion", "text", "村/社区意见"),
    ("网络舆情情况", "online_opinion", "text", "有无负面舆情"),
]

_CONTACT_FIELDS = [
    ("稳评工作组组长", "leader", "text", ""),
    ("实施单位联系人", "impl_contact", "text", "我方联系人"),
    ("实施单位联系电话", "impl_phone", "phone", ""),
    ("责任单位负责人", "org_leader", "text", ""),
    ("负责人职务", "org_leader_title", "text", ""),
    ("责任单位联系人", "org_contact", "text", ""),
    ("责任单位联系电话", "org_phone", "phone", ""),
    ("公告联系人", "contact_name", "text", ""),
    ("公告联系电话", "contact_phone", "phone", ""),
]

_ATTACH_HEADERS = ["序号", "材料名称", "是否提供", "备注"]
_ATTACH_NAMES = [
    "征收土地预公告", "勘测定界报告", "群众调查问卷", "座谈会记录及签到表",
    "专家评审意见及签到表", "现场公示照片", "现场勘察照片", "补偿标准文件",
    "项目立项/规划文件", "其他材料",
]


def _norm_label(v: Any) -> str:
    return re.sub(r'\s+', '', str(v or "")).strip("：: ")


def _num(v: Any) -> str:
    m = re.search(r'(\d+(?:\.\d+)?)', str(v or "").replace(",", ""))
    return f"{float(m.group(1)):g}" if m else ""


def _date(v: Any, month_only: bool = False) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m") if month_only else v.strftime("%Y-%m-%d")
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.search(r'(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})', s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r'(\d{4})\s*[年\-/.]\s*(\d{1,2})', s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return f"{y:04d}-{mo:02d}" if month_only else f"{y:04d}-{mo:02d}-01"
    return s


def _convert(kind: str, raw: Any) -> str:
    if raw in (None, ""):
        return ""
    if kind == "number":
        return _num(raw)
    if kind == "date":
        return _date(raw)
    if kind == "month":
        return _date(raw, month_only=True)
    return str(raw).strip()


# ── 生成模板 ─────────────────────────────────────────────────────
def build_intake_workbook(path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="DDEBF7")

    ws0 = wb.active
    ws0.title = SHEET_HOME
    ws0["A1"] = "社会稳定风险评估报告 —— 数据资料表"
    ws0["A1"].font = Font(bold=True, size=14)
    ws0["A3"] = "填写说明"
    ws0["A3"].font = Font(bold=True)
    for i, line in enumerate([
        "1. 只填你掌握的数据；没有的留空，系统会在生成前弹窗逐项询问。",
        "2. 日期统一写 2026-09-03（年-月-日）；报告编写年月写 2026-09。",
        "3. 面积、支持率、户数等只填数字，不要带单位。",
        "4. 风险等级、评分、报告结论由系统计算生成，不需要填。",
        "5. 填完把本 Excel 连同预公告、勘测定界、问卷、照片等材料一起上传。",
    ], start=4):
        ws0[f"A{i}"] = line
    ws0.column_dimensions["A"].width = 90

    def _pairs(name: str, fields, note: str = ""):
        ws = wb.create_sheet(name)
        ws.append(["字段", "值", "说明"])
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = head_fill
            c.border = border
        for label, _k, _t, desc in fields:
            ws.append([label, "", desc])
        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 40
        ws.column_dimensions["C"].width = 40
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.border = border
                c.alignment = Alignment(vertical="center", wrap_text=True)

    _pairs(SHEET_BASE, _BASE_FIELDS)
    _pairs(SHEET_TIME, _TIME_FIELDS)
    _pairs(SHEET_SURVEY, _SURVEY_FIELDS)
    _pairs(SHEET_CONTACT, _CONTACT_FIELDS)

    ws = wb.create_sheet(SHEET_ATTACH)
    ws.append(_ATTACH_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = head_fill
        c.border = border
    for i, name in enumerate(_ATTACH_NAMES, start=1):
        ws.append([i, name, "是/否", ""])
    for col, w in zip("ABCD", (6, 32, 12, 30)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.border = border

    wb.save(str(p))
    return p


# ── 读回 / 写库 ───────────────────────────────────────────────────
def _pairs(ws, fields) -> Dict[str, str]:
    label_to_key = {_norm_label(label): (key, kind)
                    for label, key, kind, _d in fields}
    out: Dict[str, str] = {}
    for r in range(2, (ws.max_row or 0) + 1):
        label = _norm_label(ws.cell(row=r, column=1).value)
        if not label or label not in label_to_key:
            continue
        key, kind = label_to_key[label]
        val = _convert(kind, ws.cell(row=r, column=2).value)
        # 🔴 0 是有效值（反对率 0%、支持率 0%）：旧写法 `if val` 把 0 当空值丢了
        if val is not None and val != "":
            out[key] = val
    return out


def read_intake_workbook(path: str | Path) -> Optional[Dict[str, Any]]:
    """读《稳评数据资料表》；不是这张表（缺工作表）返回 None。"""
    from openpyxl import load_workbook

    p = Path(path)
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        return None
    try:
        wb = load_workbook(str(p), data_only=True)
    except Exception as e:
        logger.warning(f"[稳评资料] 打开失败 {p.name}: {e}")
        return None
    names = set(wb.sheetnames)
    if not ({SHEET_BASE, SHEET_SURVEY} & names):
        return None
    fields: Dict[str, Any] = {}
    if SHEET_HOME in names:
        title = str(wb[SHEET_HOME].cell(row=1, column=1).value or "")
        if "稳评" not in title and "社会稳定" not in title and SHEET_TIME not in names:
            return None
    for sheet, spec in ((SHEET_BASE, _BASE_FIELDS), (SHEET_TIME, _TIME_FIELDS),
                        (SHEET_SURVEY, _SURVEY_FIELDS),
                        (SHEET_CONTACT, _CONTACT_FIELDS)):
        if sheet in names:
            fields.update(_pairs(wb[sheet], spec))

    attachments = []
    if SHEET_ATTACH in names:
        ws = wb[SHEET_ATTACH]
        for r in range(2, (ws.max_row or 0) + 1):
            name = str(ws.cell(row=r, column=2).value or "").strip()
            if not name:
                continue
            provided = str(ws.cell(row=r, column=3).value or "").strip()
            attachments.append({"name": name, "provided": provided,
                                "note": str(ws.cell(row=r, column=4).value or "").strip()})
    return {"fields": fields, "attachments": attachments}


def apply_intake(filled: Dict[str, Any], intake: Dict[str, Any]) -> List[str]:
    """写进事实卡；`_locked_` 的键不覆盖，其余以 Excel 为准。"""
    if not isinstance(filled, dict) or not isinstance(intake, dict):
        return []
    changed: List[str] = []
    for k, v in (intake.get("fields") or {}).items():
        if not k or k.startswith("_"):
            continue
        if str(filled.get("_locked_" + k) or "").lower() in ("1", "true", "yes"):
            continue
        if v in (None, ""):
            continue
        filled[k] = v
        changed.append(k)
    if intake.get("attachments"):
        filled["_intake_attachments"] = intake["attachments"]
        changed.append("_intake_attachments")
    return changed


def apply_intake_from_state(state: Dict[str, Any]) -> int:
    """从会话已上传文件里找《稳评数据资料表》或其他 Excel，读入 filled_data。"""
    from app.domains import domain_of_state
    if domain_of_state(state) != "stability":
        return 0

    paths: List[Path] = []
    seen = set()
    for item in (state.get("_uploaded_files") or []) + (state.get("_project_materials") or []):
        p = item.get("path") if isinstance(item, dict) else item
        if isinstance(item, dict) and not p:
            p = item.get("source_path")
        if not str(p or "").lower().endswith((".xlsx", ".xlsm")):
            continue
        cands = [Path(str(p))]
        try:
            from app.config import settings
            cands.append(Path(settings.STORAGE_DIR) / str(p))
        except Exception:
            pass
        for c in cands:
            if c.exists() and c.is_file() and str(c) not in seen:
                seen.add(str(c))
                paths.append(c)
                break

    filled = state.setdefault("filled_data", {})
    changed = 0
    for p in paths:
        data = read_intake_workbook(p)
        if data is not None:
            _n = apply_intake(filled, data)
            logger.info(f"[稳评资料] 读取《{p.name}》：写入 {len(_n)} 项")
            changed += len(_n)
            continue
        # 不是《稳评数据资料表》→ 当可研/项目资料 Excel 按标签扫（项目名称/位置/面积…）
        try:
            from app.services.reclamation_fs_excel import scan_workbook
            _got = scan_workbook(p, filled)
            if _got:
                logger.info(f"[稳评资料] {p.name} 按标签写入 {len(_got)} 项")
                changed += len(_got)
        except Exception as _fe:
            logger.warning(f"[稳评资料] {p.name} 标签扫描跳过: {_fe}")
    return changed
