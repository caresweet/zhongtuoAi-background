# -*- coding: utf-8 -*-
"""报告「数据来源与计算口径」—— 每个关键数字都要能说清从哪来、怎么算的。

用户反馈（2026-11-16）：「数据有很多没有依据，不知道是怎么算出来的」。
这里把事实卡里的关键字段连同 `field_sources` 记录的来源，整理成一张
可直接进报告的说明表；工程量另有 `reclamation_quantities.build_derivation_table`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 字段 → (报告里的名称, 典型来源文件)
_FIELDS: Dict[str, List[tuple]] = {
    "stability": [
        ("project_name", "项目名称", "项目资料"),
        ("area_m2", "征收面积", "勘测定界报告"),
        ("area_mu", "征收面积（亩）", "勘测定界报告"),
        ("land_classification", "地类构成", "勘测定界报告"),
        ("household_count", "涉及户数", "现状调查/村组提供"),
        ("population_count", "涉及人数", "现状调查/村组提供"),
        ("total_samples", "问卷份数", "问卷调查统计表"),
        ("support_rate", "调查支持率", "问卷调查统计表"),
        ("oppose_rate", "反对率", "问卷调查统计表"),
        ("compensation_standard", "补偿标准", "征地补偿安置方案"),
        ("doc_reference", "公告文号", "征收土地预公告"),
        ("implement_unit", "稳评实施单位", "公司档案"),
    ],
    "reclamation": [
        ("project_name", "项目名称", "项目资料"),
        ("area_m2", "临时用地面积", "勘测定界报告"),
        ("land_classification", "地类构成", "勘测定界报告"),
        ("hard_area_m2", "硬化/压占面积", "施工组织设计"),
        ("topsoil_thickness", "表土剥离厚度", "施工组织设计/可研"),
        ("excavation_area_m2", "挖损面积", "功能分区/施工组织设计"),
        ("excavation_depth_m", "平均开挖深度", "施工组织设计"),
        ("service_years", "复垦服务年限", "用地期限+复垦期+管护期"),
        ("project_investment", "项目总投资", "项目批复/可研"),
        ("dynamic_total_investment", "复垦动态总投资", "投资估算表"),
    ],
    "bidding": [],
}

_MISSING = {"", "【待补充】", "待补充", "None", "null"}


def _short(v: Any) -> str:
    if isinstance(v, dict):
        rows = v.get("rows")
        if isinstance(rows, list) and rows:
            total = v.get("合计_m2") or sum(
                float(r.get("面积_m2") or 0) for r in rows if isinstance(r, dict))
            return f"{len(rows)} 个地类，合计 {float(total) / 10000.0:.4f} 公顷"
        return "见正文"
    s = str(v if v is not None else "").strip()
    return s if s not in _MISSING else ""


def build_source_rows(domain: str, filled: Dict[str, Any]) -> List[List[str]]:
    """→ [[数据项, 取值, 来源], …]；没有可用字段返回空列表。"""
    from app.services.field_sources import label_of

    rows: List[List[str]] = []
    for key, label, doc in _FIELDS.get(domain or "stability", []):
        val = _short((filled or {}).get(key))
        if not val:
            continue
        src = label_of(filled, key)
        note = f"{src}（{doc}）" if src else f"项目资料（{doc}）"
        rows.append([label, val[:60], note])
    return rows


def source_table(domain: str, filled: Dict[str, Any]) -> Optional[dict]:
    rows = build_source_rows(domain, filled)
    if not rows:
        return None
    return {
        "caption": "数据来源与计算口径说明",
        "headers": ["数据项", "取值", "来源 / 依据"],
        "rows": rows,
    }


def table_as_markdown(tbl: Optional[dict], title: str = "") -> str:
    """把 {caption, headers, rows} 转成 markdown 表；空表返回空串。"""
    if not tbl or not tbl.get("rows"):
        return ""
    head = tbl.get("headers") or []
    lines = []
    if title:
        lines += [f"**{title}**", ""]
    lines.append("| " + " | ".join(str(h) for h in head) + " |")
    lines.append("| " + " | ".join("---" for _ in head) + " |")
    for r in tbl["rows"]:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines) + "\n"


def as_markdown(domain: str, filled: Dict[str, Any]) -> str:
    rows = build_source_rows(domain, filled)
    if not rows:
        return ""
    lines = ["**数据来源与计算口径说明**", "",
             "| 数据项 | 取值 | 来源 / 依据 |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return "\n".join(lines) + "\n"
