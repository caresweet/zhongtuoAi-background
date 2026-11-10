# -*- coding: utf-8 -*-
"""土地适宜性评价因子：先抽材料 → 再弹窗 → 兜底模板。

业主 2026-11-10：
    「优先从可研和勘测报告中抽取，没有获取到再去弹窗询问用户，
      或者你直接写进模板 excel。」

表4-9「土地适宜性分值」此前用系统默认基准分（坡度 100、覆土 90…），
不是本项目实测，专家评审（2026-11-09）批注「要核实」。本模块提供：

    `extract_suitability_params(filled, materials)`  ← 从可研/勘测/施工组织设计正文抽
    `score_factors(filled)`                          ← 因子取值 → 分值（按表4-8 标准）
    `factor_grade(filled)`                           ← 加权总分 → 一/二/三等地

只填**空值**；抽不到就留给弹窗（`RECLAMATION_NEED_FIELDS`）与模板 Excel。
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

# 弹窗/Excel 字段 → 中文名（报告表与提示词共用）
SUITABILITY_FIELDS = [
    ("slope_grade", "自然坡度（如 <5° / 5-15°）"),
    ("effective_soil_depth_cm", "有效土层厚度（厘米）"),
    ("soil_texture", "土壤质地（中壤土/轻壤土/粘土/砂壤土/砂土）"),
    ("organic_matter", "有机质含量（%）"),
    ("landform", "地貌部位（平地/岗坡地/坡地）"),
    ("irrigation_guarantee", "灌溉保证程度（充分满足/一般满足/无灌溉系统）"),
    ("irrigation_method", "灌溉方式（喷灌/滴灌/地面灌溉/天然降水）"),
    ("drainage_condition", "排水条件（健全/一般/无）"),
    ("road_access", "道路通达性（良好/一般/较差）"),
    ("field_flatness", "田块平整度（非常平坦/比较平坦/高低不平）"),
]

_NUM = r'(\d+(?:\.\d+)?)'


def _put(filled: dict, key: str, value) -> bool:
    if value in (None, ""):
        return False
    if str(filled.get(key) or "").strip():
        return False                      # 已有值（用户填/已确认）不覆盖
    filled[key] = str(value).strip()
    return True


def _first(patterns, text: str):
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            return m
    return None


# ── 各因子的抽取正则 ────────────────────────────────────────────
_SLOPE = (
    re.compile(r'(?:自然)?坡度[^。；，\d]{0,6}?[<＜]\s*' + _NUM + r'\s*[°度]'),
    re.compile(r'坡度[^。；，\d]{0,8}?' + _NUM + r'\s*[~～\-—至]\s*' + _NUM + r'\s*[°度]'),
    re.compile(r'地面坡度[^。；，\d]{0,8}?' + _NUM + r'\s*[°度]'),
    re.compile(r'(?:小于|低于|[<＜])\s*' + _NUM + r'\s*[°度]'),
)
_DEPTH = (
    re.compile(r'有效土层厚度[^。；，\d]{0,6}?' + _NUM + r'\s*(cm|厘米)'),
    re.compile(r'(?:覆土|覆土层|表土)[^。；，\d]{0,8}?厚度[^。；，\d]{0,4}?'
               + _NUM + r'\s*(cm|厘米)'),
)
_TEXTURE = re.compile(r'土壤质地[^。；，]{0,8}?(中壤土|轻壤土|壤土|粘土|黏土|砂壤土|'
                      r'重粘土|砂土|砂砾土)')
_TEXTURE_BARE = re.compile(r'(中壤土|轻壤土|砂壤土|重粘土|砂砾土|粘土|黏土|砂土)')
_ORGANIC_PCT = re.compile(r'有机质(?:含量)?[^。；，%\d]{0,6}?' + _NUM + r'\s*[%％]')
_ORGANIC_GKG = re.compile(r'有机质(?:含量)?[^。；，\d]{0,6}?' + _NUM + r'\s*g\s*/\s*kg',
                          re.I)
_LANDFORM = re.compile(r'(?:地貌部位|地形地貌|地貌)[^。；，]{0,10}?(平地|岗坡地|坡地|平原)')
_LANDFORM_BARE = re.compile(r'地形(?:平坦|平缓|较平坦)|平原区|地处平原')
_IRRI_GUARANTEE = re.compile(
    r'灌溉(?:保证程度|条件|保证)[^。；，]{0,12}?(充分满足|一般满足|无灌溉系统|'
    r'有良好的灌溉系统|有灌溉系统|无灌溉)')
_IRRI_METHOD = re.compile(r'(?:灌溉方式|灌水方式)[^。；，]{0,8}?(喷灌|滴灌|喷滴灌|'
                          r'地面灌溉|漫灌|天然降水)')
_IRRI_METHOD_BARE = re.compile(r'(喷灌|滴灌|地面灌溉|漫灌|天然降水)')
_DRAINAGE = re.compile(r'排水(?:条件|保证程度|体系)[^。；，]{0,14}?'
                       r'(健全|良好|一般|无排水体系|无排水)')
_ROAD = re.compile(r'(?:道路通达性|道路状况|道路条件|交通条件)[^。；，]{0,12}?'
                   r'(良好|一般|较差)')
_ROAD_BARE = re.compile(r'道路[^。；，]{0,6}?(良好|一般|较差)')
_FLATNESS = re.compile(r'(?:田块平整|田面平整|田块条件)[^。；，]{0,10}?'
                       r'(非常平坦|比较平坦|高低不平)')
_FLATNESS_BARE = re.compile(r'(?:田块|田面)[^。；，]{0,6}?'
                            r'(非常平坦|比较平坦|高低不平)')


def _slope_grade(text: str) -> str:
    m = None
    for _p in _SLOPE:
        m = _p.search(text)
        if m:
            break
    if not m:
        return ""
    nums = [float(x) for x in m.groups() if x]
    v = max(nums) if nums else 0.0
    # 「小于 5°」「<5°」 → 上界；用于选分级档
    _less = (m.re is _SLOPE[0]) or (m.re is _SLOPE[3]) or any(
        k in (m.group(0) or "") or k in text[max(0, m.start() - 8):m.start()]
        for k in ("小于", "低于", "<", "＜"))
    if _less:
        return "<5°" if v <= 5 else "5-15°" if v <= 15 else "15-25°" if v <= 25 else ">25°"
    if v < 5:
        return "<5°"
    if v < 15:
        return "5-15°"
    if v < 25:
        return "15-25°"
    return ">25°"


def _organic_pct(text: str) -> str:
    m = _ORGANIC_PCT.search(text)
    if m:
        return m.group(1)
    m = _ORGANIC_GKG.search(text)
    if m:
        return str(round(float(m.group(1)) / 10.0, 2))
    return ""


def extract_suitability_params(filled: dict, materials: Iterable[Tuple[str, str]]) -> List[str]:
    """从材料正文抽取适宜性因子（只填空值），返回写入的键。"""
    added: List[str] = []
    if not isinstance(filled, dict):
        return added
    items = []
    for m in (materials or []):
        if isinstance(m, dict):
            items.append(str(m.get("text_content") or ""))
        elif isinstance(m, (tuple, list)) and len(m) >= 2:
            items.append(str(m[1] or ""))
    text = "\n".join(items)
    if not text.strip():
        return added

    if _put(filled, "slope_grade", _slope_grade(text)):
        added.append("slope_grade")
    m = _first(_DEPTH, text)
    if m and _put(filled, "effective_soil_depth_cm", m.group(1)):
        added.append("effective_soil_depth_cm")
    m = _TEXTURE.search(text) or _TEXTURE_BARE.search(text)
    if m and m.group(1) not in ("砂土", "砂砾土"):   # 裸词「砂土」易误伤，只在带语境时取
        if _put(filled, "soil_texture", m.group(1)):
            added.append("soil_texture")
    if _put(filled, "organic_matter", _organic_pct(text)):
        added.append("organic_matter")
    m = _LANDFORM.search(text)
    if m:
        _lf = "平地" if m.group(1) == "平原" else m.group(1)
        if _put(filled, "landform", _lf):
            added.append("landform")
    elif _LANDFORM_BARE.search(text) and _put(filled, "landform", "平地"):
        added.append("landform")
    m = _IRRI_GUARANTEE.search(text)
    if m:
        _v = ("充分满足" if "充分" in m.group(1) or "良好" in m.group(1)
              else ("无灌溉系统" if "无" in m.group(1) else "一般满足"))
        if _put(filled, "irrigation_guarantee", _v):
            added.append("irrigation_guarantee")
    m = _IRRI_METHOD.search(text) or _IRRI_METHOD_BARE.search(text)
    if m and _put(filled, "irrigation_method", m.group(1)):
        added.append("irrigation_method")
    m = _DRAINAGE.search(text)
    if m:
        _v = ("健全" if ("健全" in m.group(1) or "良好" in m.group(1))
              else ("无" if "无" in m.group(1) else "一般"))
        if _put(filled, "drainage_condition", _v):
            added.append("drainage_condition")
    m = _ROAD.search(text) or _ROAD_BARE.search(text)
    if m and _put(filled, "road_access", m.group(1)):
        added.append("road_access")
    m = _FLATNESS.search(text) or _FLATNESS_BARE.search(text)
    if m and _put(filled, "field_flatness", m.group(1)):
        added.append("field_flatness")
    if added:
        logger.info(f"[适宜性因子] 材料抽取写入 {len(added)} 项：{added}")
    return added


# ── 因子取值 → 分值（按表4-8 分级标准）───────────────────────────
def _num(v, default=0.0) -> float:
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def score_factors(filled: dict) -> Dict[str, int]:
    """把 filled 里的因子取值换成表4-8 的百分制分值；取不到的因子不返回。"""
    f = filled or {}
    out: Dict[str, int] = {}

    g = str(f.get("slope_grade") or "")
    if g:
        out["自然坡度"] = (100 if ("<5" in g or "0-5" in g)
                           else 80 if "5-15" in g
                           else 60 if "15-25" in g else 40)
    d = _num(f.get("effective_soil_depth_cm"))
    if d > 0:
        out["有效土层厚度"] = 100 if d >= 30 else 90 if d >= 20 else 70 if d >= 5 else 40
    t = str(f.get("soil_texture") or "")
    if t:
        out["土壤质地"] = (90 if t in ("中壤土", "轻壤土", "壤土")
                           else 80 if t in ("粘土", "黏土", "砂壤土")
                           else 70 if t in ("重粘土", "砂土") else 40)
    o = _num(f.get("organic_matter"))
    if o > 0:
        out["有机质含量"] = (90 if o >= 0.9 else 80 if o >= 0.7
                             else 70 if o >= 0.64 else 60)
    lf = str(f.get("landform") or "")
    if lf:
        out["地貌部位"] = 100 if lf in ("平地", "平原") else 80 if lf == "岗坡地" else 60
    ig = str(f.get("irrigation_guarantee") or "")
    if ig:
        out["灌溉保证程度"] = 90 if "充分" in ig else 80 if "一般" in ig else 60
    im = str(f.get("irrigation_method") or "")
    if im:
        out["灌溉方式"] = (90 if ("喷" in im or "滴" in im)
                           else 80 if "地面" in im or "漫" in im else 50)
    dr = str(f.get("drainage_condition") or "")
    if dr:
        out["排水保证程度"] = 90 if dr in ("健全", "良好") else 80 if dr == "一般" else 60
    ra = str(f.get("road_access") or "")
    if ra:
        out["道路通达性"] = 90 if ra == "良好" else 70 if ra == "一般" else 40
    ff = str(f.get("field_flatness") or "")
    if ff:
        out["田块平整"] = 90 if "非常" in ff else 80 if "比较" in ff else 60
    return out


def grade_of(total: float) -> str:
    """表4-8 备注：一等地 80—100；二等地 60—80；三等地 <60。"""
    return "一等地" if total >= 80 else ("二等地" if total >= 60 else "三等地")
