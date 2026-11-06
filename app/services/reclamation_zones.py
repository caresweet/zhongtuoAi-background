# -*- coding: utf-8 -*-
"""临时用地功能分区（表1-1「临时用地拟损毁土地情况表」）。

业主 2026-11-03：
    「表1-1 临时用地拟损毁土地情况表中的临时用地名称，是每次跟甲方沟通有多少
      地区，让用户提供地区名称还有对应的大小，总计的 2.3248 是正确的。」

所以分区名称/面积**不编**：弹窗问用户（`land_use_zones`，一行一个），
解析成 `filled["_land_use_zones"] = [{name, area_m2, method, degree}]`，
表1-1 与表4-9 都用这一份。合计取各分区之和；与 23248 对不上只告警、不改数。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NUM_UNIT = re.compile(
    r'^(?P<name>.*?)[\s:：,，、]+(?P<num>\d+(?:\.\d+)?)\s*'
    r'(?P<unit>公顷|hm2|hm²|ha|平方米|㎡|m2|m²|亩)?\s*(?P<rest>.*)$', re.I)
_BARE = re.compile(
    r'^(?P<name>[^\d]+?)(?P<num>\d+(?:\.\d+)?)\s*'
    r'(?P<unit>公顷|hm2|hm²|ha|平方米|㎡|m2|m²|亩)?\s*(?P<rest>.*)$', re.I)
_LEAD_NO = re.compile(r'^(?:\(\d+\)|（\d+）|\d+[.、）)])\s*')


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _area_m2(num: float, unit: str) -> float:
    u = (unit or "").lower()
    if u in ("公顷", "hm2", "hm²", "ha"):
        return num * 10000.0
    if u == "亩":
        return num * 666.6667
    if u:
        return num
    # 无单位：小于 20 按公顷（0.2310），否则按平方米（2310）
    return num * 10000.0 if num < 20 else num


def _clean_name(s: str) -> str:
    t = _LEAD_NO.sub("", str(s or "").strip())
    return t.strip(" -—:：,，、")


def _method_degree(name: str) -> tuple[str, str]:
    if "井场" in name:
        return "挖损和压占", "重度"
    if any(k in name for k in ("表土堆放", "堆土", "表土堆存")):
        return "压占", "中度"
    return "压占", "重度"


def parse_zones(text: str) -> List[Dict[str, Any]]:
    """「井场 0.2310公顷；施工场站 1.1950」→ 分区列表。解析不出的行跳过。"""
    out: List[Dict[str, Any]] = []
    for raw in re.split(r'[\n；;]+', str(text or "")):
        line = raw.strip()
        if not line:
            continue
        m = _NUM_UNIT.match(line) or _BARE.match(line)
        if not m:
            continue
        name = _clean_name(m.group("name"))
        if not name or len(name) > 20:
            continue
        num = _num(m.group("num"))
        if num <= 0:
            continue
        area = _area_m2(num, m.group("unit") or "")
        # 用户在分区行里可继续写损毁方式/程度（可选项）；没写才按名称推断
        rest = (m.groupdict().get("rest") or "").strip()
        _d = next((d for d in ("重度", "中度", "轻度") if d in rest), "")
        if "挖损" in rest and "压占" in rest:
            _m = "挖损和压占"
        elif "挖损" in rest:
            _m = "挖损"
        elif "压占" in rest:
            _m = "压占"
        else:
            _m = ""
        # 行尾其余文字当作「占用原土地类型」（可选，如「水田」「水田、沟渠」）
        for _kw in ("挖损和压占", "挖损", "压占", "重度", "中度", "轻度"):
            rest = rest.replace(_kw, "")
        land_types = rest.strip(" ，,、;；:：")
        method, degree = _method_degree(name)
        out.append({"name": name, "area_m2": round(area, 4),
                    "method": _m or method, "degree": _d or degree,
                    "land_types": land_types})
    return out


def zones_from_filled(filled: dict) -> List[Dict[str, Any]]:
    """优先取已解析的 `_land_use_zones`；否则现解析 `land_use_zones` 文本。"""
    f = filled or {}
    z = f.get("_land_use_zones")
    if isinstance(z, list) and z:
        return [x for x in z if isinstance(x, dict) and x.get("area_m2")]
    parsed = parse_zones(str(f.get("land_use_zones") or ""))
    if parsed:
        f["_land_use_zones"] = parsed
    return parsed


def stockpile_area_m2(zones: List[Dict[str, Any]]) -> float:
    return sum(_num(z.get("area_m2")) for z in zones or []
               if any(k in str(z.get("name") or "") for k in ("表土堆放", "堆土", "表土堆存")))


def table_1_1(filled: dict) -> Optional[dict]:
    """表1-1 临时用地拟损毁土地情况表。没有分区数据 → 整张不出。"""
    zones = zones_from_filled(filled)
    if not zones:
        return None
    _f = filled or {}
    total = sum(_num(z.get("area_m2")) for z in zones)
    _declared = _num((_f.get("land_classification") or {}).get("合计_m2")) \
        or _num(_f.get("area_m2"))
    if _declared > 0 and abs(total - _declared) > 1.0:
        logger.warning(f"[表1-1] 分区分项合计 {total:.4f} ㎡ 与临时用地总面积 "
                       f"{_declared:.4f} ㎡ 不一致（按用户提供值出表，不自动改数）")
    rows = [[z["name"], f"{_num(z['area_m2']) / 10000.0:.4f}",
             z.get("method") or "压占", z.get("degree") or "重度", "待复垦"]
            for z in zones]
    rows.append(["合计", f"{total / 10000.0:.4f}", "", "", ""])
    return {
        "caption": "表1-1 临时用地拟损毁土地情况表",
        "headers": ["临时用地名称", "面积（hm²）", "损毁方式", "损毁程度", "复垦情况"],
        "rows": rows,
    }
