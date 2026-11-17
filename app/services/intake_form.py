# -*- coding: utf-8 -*-
"""基础资料「弹窗填写」的字段字典与写回。

Excel 模板（稳评数据资料表 / 复垦基础资料）里的成对字段、分区表、土地分类表，
在这里以 JSON schema 暴露给前端弹窗；用户提交后复用各域 `apply_intake()`
写进 `filled_data`，派生逻辑（公顷→平方米、服务年限、分区/地类/预算）不另写一份。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services import reclamation_intake as ri
from app.services import stability_intake as si


def _norm_type(label: str, typ: str = "") -> str:
    if typ in ("text", "number", "date", "month", "phone"):
        return typ
    if any(k in label for k in ("面积", "投资", "厚度", "深度", "长度", "份数",
                                "户数", "人数", "率", "坡度", "含量", "数量",
                                "(月)", "（月）", "(cm)", "（cm）", "(m)", "（m）",
                                "(%)", "（%）", "(公顷)", "（公顷）", "(万元)", "（万元）")):
        return "number"
    if "日期" in label:
        return "date"
    if "年月" in label:
        return "month"
    if "电话" in label:
        return "phone"
    return "text"


def _options(label: str) -> List[str]:
    if label == "项目性质":
        return ["新建", "改扩建", "改建"]
    if label == "复垦方向":
        return ["水田", "旱地", "林地", "其他农用地"]
    if label.startswith("是否占用"):
        return ["是", "否", "不涉及"]
    if label == "硬化结构类型":
        return ["碎石垫层+钢板", "混凝土面层+垫层", "其他"]
    return []


def _stability_schema() -> Dict[str, Any]:
    required = {
        "project_name", "org_name", "location", "area_mu", "area_m2",
        "land_use", "household_count", "doc_reference", "total_samples",
        "support_rate", "leader",
    }
    groups = []
    for title, spec in (("项目基本信息", si._BASE_FIELDS),
                        ("时间节点", si._TIME_FIELDS),
                        ("调查数据", si._SURVEY_FIELDS),
                        ("人员与联系", si._CONTACT_FIELDS)):
        fields = []
        for label, key, typ, help_text in spec:
            fields.append({
                "key": key, "label": label, "type": _norm_type(label, typ),
                "required": key in required, "help": help_text or "",
                "options": _options(label),
            })
        groups.append({"title": title, "fields": fields})
    return {"domain": "stability", "groups": groups, "tables": [],
            "note": "标 * 的为必需项；日期用 YYYY-MM-DD，年月用 YYYY-MM。"}


def _reclamation_schema() -> Dict[str, Any]:
    required_keys = {
        "project_name", "build_unit", "construction_nature", "location", "region",
        "permanent_area_ha", "land_use_period_months", "land_use_start",
        "land_use_end", "reclaim_period_months", "care_period_months",
        "land_location", "land_ownership", "temp_area_ha",
        "topsoil_thickness", "hard_surface_type", "hard_area_ha",
        "occupies_basic_farmland", "occupies_eco_redline", "reclaim_direction",
    }
    specs = [
        ("项目基本信息", ri._BASE_FIELDS, ri._PAIR_MAP),
        ("勘界信息", ri._SURVEY_FIELDS, ri._PAIR_MAP),
        ("设计参数", ri._DESIGN_FIELDS, ri._PAIR_MAP),
        ("投资估算", ri._BUDGET_FIELDS, ri._BUDGET_MAP),
        ("报告表信息", ri._FORM_FIELDS, ri._PAIR_MAP),
    ]
    groups, seen = [], set()
    for title, spec, mapping in specs:
        fields = []
        for row in spec:
            label = row[0]
            key = mapping.get(label)
            if not key or key in seen:
                continue
            seen.add(key)
            required_flag = (row[2] == "是") if title != "投资估算" else False
            fields.append({
                "key": key, "label": label, "type": _norm_type(label),
                "required": required_flag or key in required_keys,
                "help": row[3] or "", "options": _options(label),
            })
        if fields:
            groups.append({"title": title, "fields": fields})

    tables = [
        {"key": "zones", "title": "临时用地功能分区（表1-1/4-7/4-9/4-10 共用）",
         "columns": [
             {"key": "name", "label": "功能区名称", "type": "text", "required": True},
             {"key": "area_ha", "label": "面积(公顷)", "type": "number", "required": True},
             {"key": "method", "label": "损毁方式", "type": "text",
              "options": ["压占", "挖损", "挖损和压占", "塌陷"]},
             {"key": "degree", "label": "损毁程度", "type": "text",
              "options": ["轻度", "中度", "重度"]},
             {"key": "land_types", "label": "原地类", "type": "text"},
             {"key": "reclaim_direction", "label": "复垦方向", "type": "text"},
             {"key": "plot_count", "label": "地块数", "type": "number"},
         ]},
        {"key": "land_rows", "title": "土地分类面积（合计=临时用地面积）",
         "columns": [
             {"key": "level1", "label": "一级地类", "type": "text", "required": True},
             {"key": "level2", "label": "二级地类", "type": "text"},
             {"key": "area_ha", "label": "面积(公顷)", "type": "number", "required": True},
             {"key": "ownership", "label": "权属", "type": "text"},
         ]},
    ]
    return {"domain": "reclamation", "groups": groups, "tables": tables,
            "note": "标 * 的为必需项；功能分区与土地分类面积是两张核心表，"
                    "合计应等于临时用地面积；不填分区时第4/6章按面积参数简化。"}


def form_schema(domain: str) -> Dict[str, Any]:
    return _reclamation_schema() if domain == "reclamation" else _stability_schema()


def _clean(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _float(v: Any, default: float = 0.0) -> float:
    import re as _re
    m = _re.search(r"-?\d+(?:\.\d+)?", _clean(v).replace(",", ""))
    try:
        return float(m.group(0)) if m else default
    except ValueError:
        return default


def _reclamation_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    fields = {k: _clean(v) for k, v in (payload.get("values") or {}).items()
              if _clean(v)}
    zones = []
    for z in payload.get("zones") or []:
        name = _clean(z.get("name"))
        area = _float(z.get("area_ha"))
        if not name or area <= 0:
            continue
        zones.append({
            "name": name,
            "area_m2": round(area * 10000.0, 4),
            "method": _clean(z.get("method")),
            "degree": _clean(z.get("degree")),
            "land_types": _clean(z.get("land_types")),
            "reclaim_direction": _clean(z.get("reclaim_direction")),
            "plot_count": (int(_float(z.get("plot_count")))
                           if _clean(z.get("plot_count")) else None),
        })
    land_rows = []
    for r in payload.get("land_rows") or []:
        lv1 = _clean(r.get("level1"))
        area = _float(r.get("area_ha"))
        if not lv1 or area <= 0:
            continue
        land_rows.append({
            "一级地类": lv1,
            "二级地类": _clean(r.get("level2")),
            "面积_m2": round(area * 10000.0, 2),
            "占比": "",
            "权属": _clean(r.get("ownership")),
        })
    budget = {k: _clean(v) for k, v in (payload.get("budget") or {}).items()
              if _clean(v)}

    # 🔴 Excel 读回时在 `read_intake_workbook` 里做的派生，这里同样要做：
    #    apply_intake 只负责写字段，不会替你换算面积/服务年限/地类汇总。
    def _take_ha(src_key: str, dst_key: str) -> None:
        if fields.get(src_key):
            fields[dst_key] = round(_float(fields.pop(src_key)) * 10000.0, 2)

    _take_ha("permanent_area_ha", "permanent_area_m2")
    _take_ha("temp_area_ha", "area_m2")
    _take_ha("hard_area_ha", "hard_area_m2")
    _take_ha("excavation_area_ha", "excavation_area_m2")
    _take_ha("stockpile_area_ha", "stockpile_area_m2")
    if not fields.get("area_m2") and zones:
        fields["area_m2"] = round(sum(z["area_m2"] for z in zones), 4)
    _months = sum(_float(fields.get(k)) for k in
                  ("land_use_period_months", "reclaim_period_months",
                   "care_period_months"))
    if _months and not _clean(fields.get("service_years")):
        fields["service_years"] = round(_months / 12.0, 1)
        fields["service_months"] = int(round(_months))
    if fields.get("permanent_area_m2") and fields.get("area_m2"):
        fields["project_area_m2"] = round(
            _float(fields["permanent_area_m2"]) + _float(fields["area_m2"]), 2)
    if land_rows:
        fields["land_classification"] = {
            "rows": land_rows,
            "合计_m2": round(sum(r["面积_m2"] for r in land_rows), 2),
        }

    return {"fields": fields, "zones": zones, "land_rows": land_rows,
            "budget": budget, "public": [], "attachments": [],
            "cost_excel_name": ""}


def apply_form(domain: str, filled: Dict[str, Any],
               payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """把弹窗数据写进事实卡。

    Returns: `(写入的键, 缺失的必填标签)`。
    """
    if not isinstance(filled, dict):
        return [], []
    domain = "reclamation" if domain == "reclamation" else "stability"
    if domain == "stability":
        data = {"fields": {k: _clean(v) for k, v in
                           (payload.get("values") or {}).items() if _clean(v)}}
        changed = si.apply_intake(filled, data)
    else:
        changed = ri.apply_intake(filled, _reclamation_payload(payload))

    filled["_intake_source"] = "form"

    # 必填校验（只提示，不阻断保存）
    missing = []
    for group in form_schema(domain).get("groups") or []:
        for f in group.get("fields") or []:
            if f.get("required") and not _clean((payload.get("values") or {}).get(f["key"])):
                missing.append(f["label"])
    if domain == "reclamation":
        zones = _reclamation_payload(payload)["zones"]
        if not zones:
            missing.append("临时用地功能分区（至少 1 行）")
    return changed, missing
