# -*- coding: utf-8 -*-
"""可研 / 项目资料 Excel 的「标签 → 值」扫描（先查可研，最后才弹窗）。

业主 2026-11-10：
    「是可研 excel 然后最后才是弹窗。」

用户上传的可行性研究/项目概况 Excel 不是《复垦基础资料》模板，`read_intake_workbook`
认不出；这里按**标签**扫描任意工作表：命中标签就取右侧（或下方）单元格的值，
只填空值，不覆盖用户已填/已确认。抽不到的字段才留给弹窗。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 标签（含常见写法）→ filled_data 键
LABEL_MAP: Dict[str, str] = {
    # 项目基本情况
    "项目名称": "project_name",
    "建设单位": "build_unit",
    "用地单位": "project_unit",
    "项目位置": "location",
    "土地座落": "location",
    "单位地址": "unit_address",
    "法人代表": "legal_rep",
    "法定代表人": "legal_rep",
    "联系电话": "contact_phone",
    "单位性质": "unit_nature",
    "项目性质": "construction_nature",
    "批复文号": "doc_reference",
    "项目批复文号": "doc_reference",
    # 稳评常用
    "决策主体": "decision_unit",
    "稳评责任单位": "org_name",
    "责任单位": "org_name",
    "土地用途": "land_use",
    "涉及户数": "household_count",
    "补偿标准": "compensation_standard",
    "补偿安置方案文号": "compensation_doc",
    "资金筹措单位": "funding",
    # 面积/投资/年限
    "临时用地面积": "area_m2",
    "项目总投资": "project_investment",
    "项目区面积": "project_area_m2",
    "服务年限": "service_years",
    "土地复垦方案服务年限": "service_years",
    # 设计参数
    "表土剥离厚度": "topsoil_thickness",
    "剥离厚度": "topsoil_thickness",
    "硬化结构": "hard_surface_type",
    "硬化面积": "hard_area_m2",
    "压占面积": "hard_area_m2",
    "面层厚度": "surface_thickness_cm",
    "垫层厚度": "cushion_thickness_cm",
    # 土地适宜性评价因子（表4-9）
    "自然坡度": "slope_grade",
    "有效土层厚度": "effective_soil_depth_cm",
    "土壤质地": "soil_texture",
    "有机质含量": "organic_matter",
    "有机质": "organic_matter",
    "地貌部位": "landform",
    "地貌类型": "landform",
    "灌溉保证程度": "irrigation_guarantee",
    "灌溉条件": "irrigation_guarantee",
    "灌溉方式": "irrigation_method",
    "排水条件": "drainage_condition",
    "排水保证程度": "drainage_condition",
    "道路通达性": "road_access",
    "道路条件": "road_access",
    "田块平整度": "field_flatness",
    "田块平整": "field_flatness",
}

_NUM = re.compile(r'(\d+(?:\.\d+)?)')
_TEXT_KEYS = {"project_name", "build_unit", "project_unit", "location",
              "unit_address", "legal_rep", "contact_phone", "unit_nature",
              "construction_nature", "doc_reference", "hard_surface_type"}


def _norm_label(v) -> str:
    return re.sub(r'\s+', '', str(v or "")).strip("：: ")


def _value_right_or_below(ws, row: int, col: int, max_row: int, max_col: int):
    """标签右侧第一个非空单元格；右侧没有则取正下方。"""
    for c in range(col + 1, min(max_col, col + 6) + 1):
        v = ws.cell(row=row, column=c).value
        if v not in (None, ""):
            return v
    if row < min(max_row, row + 3):
        v = ws.cell(row=row + 1, column=col).value
        if v not in (None, ""):
            return v
    return None


def _area_m2(raw: str) -> str:
    m = _NUM.search(raw)
    if not m:
        return ""
    v = float(m.group(1))
    if "公顷" in raw or "hm" in raw.lower() or "ha" in raw.lower():
        v *= 10000.0
    elif "亩" in raw:
        v *= 666.6667
    return f"{v:g}"


def _normalize_text_value(key: str, raw) -> str:
    from app.services.reclamation_suitability import normalize_factor

    s = str(raw).strip()
    if not s:
        return ""
    if key in ("area_m2", "project_area_m2", "hard_area_m2"):
        return _area_m2(s)
    if key in ("project_investment",):
        m = _NUM.search(s)
        return m.group(1) if m else ""
    if key in ("topsoil_thickness", "surface_thickness_cm", "cushion_thickness_cm",
               "effective_soil_depth_cm"):
        m = _NUM.search(s)
        if not m:
            return ""
        v = float(m.group(1))
        if key == "effective_soil_depth_cm":
            if re.search(r'\d\s*(m|米)\b', s) and "cm" not in s and "厘米" not in s:
                v *= 100.0
        return f"{v:g}"
    if key == "service_years":
        m = _NUM.search(s)
        if not m:
            return ""
        v = float(m.group(1))
        if "年" in s and "月" not in s:
            v *= 12.0
        return f"{v:g}"
    if key in ("slope_grade", "soil_texture", "landform", "organic_matter",
               "irrigation_guarantee", "irrigation_method",
               "drainage_condition", "road_access", "field_flatness"):
        return normalize_factor(key, s)
    return s


def scan_workbook(path: str | Path, filled: dict) -> List[str]:
    """扫描一个可研/项目资料 Excel，把标签值写进 filled（只填空值）。"""
    p = Path(path)
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        return []
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(p), data_only=True)
    except Exception as e:
        logger.warning(f"[可研Excel] 打开失败 {p.name}: {e}")
        return []

    added: List[str] = []
    try:
        for ws in wb.worksheets:
            max_row, max_col = ws.max_row or 0, ws.max_column or 0
            if not max_row or not max_col:
                continue
            for row in range(1, max_row + 1):
                for col in range(1, max_col + 1):
                    label = _norm_label(ws.cell(row=row, column=col).value)
                    if not label or len(label) > 16:
                        continue
                    key = None
                    for lb, k in LABEL_MAP.items():
                        if label == lb or label.startswith(lb):
                            key = k
                            break
                    if not key:
                        continue
                    if str(filled.get(key) or "").strip():
                        continue                    # 已有值不覆盖
                    raw = _value_right_or_below(ws, row, col, max_row, max_col)
                    val = _normalize_text_value(key, raw)
                    if not val:
                        continue
                    filled[key] = val
                    added.append(key)
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if added:
        logger.info(f"[可研Excel] {p.name} 写入 {len(added)} 项：{sorted(set(added))}")
    return added
