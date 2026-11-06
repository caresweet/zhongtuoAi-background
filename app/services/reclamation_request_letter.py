# -*- coding: utf-8 -*-
"""复垦第 12 部分「建设单位申请自然资源部门审查的请示」—— 数据驱动起草。

样本（《1井神安井组复垦报告11.2》第 12 部分）是一页正式请示：建设单位向
自然资源主管部门行文，说明项目情况、方案编制依据与主要结论，请求审查。
原来这一部分没实现（目录从 11 直接跳到 14），业主 2026-11-04 要求补齐。

数据全部取自事实卡（项目名称/单位/位置/面积/投资/复垦方向），**不编**：
抽不到的字段就不写那一句。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _ha(m2: Any) -> str:
    v = _num(m2)
    return f"{v / 10000.0:.4f}" if v > 0 else ""


def _add(doc, text: str, *, size=None, bold: bool = False,
         align=None, indent: bool = True, font: str = "仿宋"):
    from docx.shared import Pt
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    if indent:
        p.paragraph_format.first_line_indent = Pt(24)
    r = p.add_run(text)
    r.font.name = font
    r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), font)
    r.font.size = size or Pt(12)
    r.bold = bold
    return p


def render_request_letter(doc, filled: Dict[str, Any]) -> None:
    """把第 12 部分请示写进文档（调用前已加章标题页）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    f = filled or {}
    project = str(f.get("project_name") or "本项目").strip()
    unit = str(f.get("build_unit") or f.get("project_unit") or "").strip()
    location = str(f.get("location") or "").strip()
    area_ha = _ha(f.get("area_m2"))
    permanent_ha = _ha(f.get("permanent_area_m2"))
    direction = str(f.get("reclaim_direction") or f.get("reclamation_direction") or "").strip()
    service = str(f.get("service_years") or "").strip()
    if re.fullmatch(r'\d+(?:\.\d+)?', service):
        # service_years 是「年」（如 3.0），不是「个月」
        _v = float(service)
        service = f"{_v:g}年" if _v < 12 else f"{_v:g}个月"
    # 🔴 请示里的「估算总投资」指**土地复垦方案投资**，不是主体工程投资。
    #    2026-11-06 测试发现误取了 project_investment（38600 万），必须优先用预算桥。
    invest = 0.0
    _est = f.get("_budget_estimate")
    if isinstance(_est, dict):
        invest = _num(_est.get("dynamic_total") or _est.get("static_total"))
    elif _est is not None:
        invest = _num(getattr(_est, "dynamic_total", None)
                      or getattr(_est, "static_total", None))
    if invest <= 0:
        invest = _num(f.get("project_investment"))
    compiler = str(f.get("implement_unit") or "").strip()

    m = re.search(r'([一-龥]{2,8}市)', location)
    city = m.group(1) if m else ""
    receiver = f"{city}自然资源和规划局" if city else "自然资源主管部门"

    _add(doc, f"关于申请审查《{project}土地复垦方案》的请示",
         size=Pt(16), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False,
         font="黑体")
    doc.add_paragraph()
    _add(doc, f"{receiver}：", indent=False)

    parts = []
    if unit:
        parts.append(f"{unit}拟建设{project}")
    else:
        parts.append(f"本项目建设内容为{project}")
    if location:
        parts.append(f"项目位于{location}")
    if area_ha:
        parts.append(f"临时用地面积{area_ha}公顷")
    if permanent_ha:
        parts.append(f"永久性建设用地{permanent_ha}公顷")
    _add(doc, "，".join(parts) + "。" if parts else "")

    basis = ("根据《土地复垦条例》《土地复垦条例实施办法》和"
             "《土地复垦方案编制规程 第1部分：通则》（TD/T 1031.1-2011）等规定")
    if compiler:
        basis += f"，我单位委托{compiler}编制了《{project}土地复垦方案》"
    else:
        basis += f"，我单位组织编制了《{project}土地复垦方案》"
    _add(doc, basis + "。")

    summary = []
    if direction:
        summary.append(f"复垦方向为{direction}")
    if service:
        summary.append(f"复垦服务年限{service}")
    if invest > 0:
        summary.append(f"估算总投资{invest:.2f}万元")
    if summary:
        _add(doc, "方案确定的" + "，".join(summary) + "。")
    _add(doc, "现将土地复垦方案报上，请予审查。")

    doc.add_paragraph()
    sign = f"{unit}（盖章）" if unit else "（盖章）"
    _add(doc, sign, align=WD_ALIGN_PARAGRAPH.RIGHT, indent=False)
    date = str(f.get("submit_date") or "").strip()
    if not date:
        import datetime as _dt
        _t = _dt.date.today()
        date = f"{_t.year}年{_t.month}月{_t.day}日"
    elif re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', date):
        y, mo, d = date.split("-")
        date = f"{int(y)}年{int(mo)}月{int(d)}日"
    _add(doc, date, align=WD_ALIGN_PARAGRAPH.RIGHT, indent=False)
