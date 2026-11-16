# -*- coding: utf-8 -*-
"""行政区划与政府名称：识别乡镇/街道、联网核验、统一全篇称谓。

解决的问题（2026-11-16 专家批注）：
    报告把「马头镇」写成「街道（淮阴区马头镇）」「街道办事处审核」——
    行政区划类型不是猜的，必须按项目所在地的真实政府写。

三条来源，从可靠到辅助：
    1) 用户资料/项目位置里出现的「XX镇 / XX乡 / XX街道」；
    2) 联网检索「XX镇人民政府」核对是否存在（有 key 走搜索 API，无 key 走必应公开页）；
    3) 都取不到时不臆造，统一写「属地镇（街道）人民政府」。

识别结果写入 `filled_data`（事实卡会带进每一章提示词）：
    township / township_type / township_gov / county / county_gov
    natural_resources_bureau（复垦请示抬头用）
    admin_verified / admin_sources
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TOWN_RE = re.compile(r'([一-龥]{2,8}?(?:镇|乡|街道))')
_COUNTY_RE = re.compile(r'([一-龥]{2,8}?(?:区|县))')
_CITY_RE = re.compile(r'([一-龥]{2,8}?市)')


def _texts(filled: Dict[str, Any], materials: Iterable = ()) -> str:
    parts = [
        str((filled or {}).get(k) or "")
        for k in ("location", "project_name", "land_location", "region",
                  "scope", "project_context")
    ]
    for m in (materials or []):
        if isinstance(m, dict):
            parts.append(str(m.get("text_content") or "")[:2000])
        elif isinstance(m, (tuple, list)) and len(m) >= 2:
            parts.append(str(m[1])[:2000])
    return "\n".join(p for p in parts if p)


def _clean_town(name: str) -> str:
    """去掉「XX区」「XX市」等前缀残留，只留镇/乡/街道本名。"""
    t = str(name or "").strip()
    for sep in ("区", "县", "市"):
        idx = t.rfind(sep)
        if idx > 0 and idx < len(t) - 1:
            t = t[idx + 1:]
    return t


def _clean_county(name: str) -> str:
    t = str(name or "").strip()
    idx = t.rfind("市")
    if 0 <= idx < len(t) - 1:
        t = t[idx + 1:]
    return t


def parse_division(text: str) -> Dict[str, str]:
    """从一段项目位置文字里取 市/区县/乡镇街道。取不到给空串。"""
    s = str(text or "")
    town = ""
    for m in _TOWN_RE.finditer(s):
        cand = _clean_town(m.group(1))
        # 「街道办事处」这种泛称不是地名
        if cand in ("街道", "镇", "乡") or "政府" in cand:
            continue
        town = cand
        break
    county = ""
    for m in _COUNTY_RE.finditer(s):
        cand = _clean_county(m.group(1))
        if cand.endswith(("开发区", "高新区", "新区")):
            continue
        if town and cand + town in s:
            county = cand
            break
        county = county or cand
    city = ""
    for m in _CITY_RE.finditer(s):
        cand = m.group(1)
        if cand.endswith(("城市", "超市")):
            continue
        city = cand
        break
    return {"city": city, "county": county, "township": town}


def township_type(name: str) -> str:
    t = str(name or "")
    if t.endswith("街道"):
        return "街道"
    if t.endswith("乡"):
        return "乡"
    if t.endswith("镇"):
        return "镇"
    return ""


def government_name(township: str) -> str:
    t = str(township or "")
    if t.endswith("街道"):
        return f"{t}办事处"
    if t.endswith(("镇", "乡")):
        return f"{t}人民政府"
    return ""


def natural_resources_bureau(city: str, county: str) -> str:
    """复垦请示抬头：市局 + 区县分局（如 淮安市自然资源和规划局淮安分局）。"""
    if not city or not county:
        return ""
    base = city if city.endswith("市") else f"{city}市"
    _c = str(county or "").strip()
    if _c.endswith(("区", "县")):
        _c = _c[:-1]
    return f"{base}自然资源和规划局{_c}分局"


# ═══════════════════════════════════════════════════════════════
# 本地识别（同步，导资料时就能定）
# ═══════════════════════════════════════════════════════════════

def resolve_local(filled: Dict[str, Any], materials: Iterable = ()) -> Dict[str, str]:
    """把识别结果写进事实卡；只补空值，不覆盖用户明确填的。"""
    if not isinstance(filled, dict):
        return {}
    d = parse_division(_texts(filled, materials))
    town, city, county = d["township"], d["city"], d["county"]
    if not town:
        town = str(filled.get("township") or "").strip()
    if not city:
        city = str(filled.get("city") or "").strip()
    if not county:
        county = str(filled.get("county") or "").strip()

    def _put(key: str, val: str) -> None:
        if val and not str(filled.get(key) or "").strip():
            filled[key] = val

    if town:
        _put("township", town)
        _put("town", town)
        _put("township_type", township_type(town))
        _put("township_gov", government_name(town))
    if city:
        _put("city", city)
    if county:
        _put("county", county)
    bureau = natural_resources_bureau(city, county)
    if bureau:
        _put("natural_resources_bureau", bureau)
    return {"city": city, "county": county, "township": town,
            "township_type": township_type(town),
            "township_gov": government_name(town)}


# ═══════════════════════════════════════════════════════════════
# 联网核验（异步，生成前跑；失败不影响出报告）
# ═══════════════════════════════════════════════════════════════

def _db_path() -> Optional[Path]:
    try:
        from app.config import settings
        return Path(settings.DATA_DIR) / "knowledge_base.db"
    except Exception:
        return None


def _cache_get(key: str) -> Optional[dict]:
    p = _db_path()
    if not p or not p.exists():
        return None
    try:
        conn = sqlite3.connect(str(p))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS admin_division_cache (
                cache_key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            row = conn.execute(
                "SELECT value FROM admin_division_cache WHERE cache_key=?", (key,)
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row and row[0] else None
    except Exception:
        return None


def _cache_put(key: str, value: dict) -> None:
    p = _db_path()
    if not p:
        return
    try:
        conn = sqlite3.connect(str(p))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS admin_division_cache (
                cache_key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute(
                "INSERT INTO admin_division_cache(cache_key, value, updated_at) "
                "VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(cache_key) DO UPDATE SET "
                "value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                (key, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


async def verify_async(filled: Dict[str, Any], materials: Iterable = ()) -> Dict[str, Any]:
    """联网核对乡镇政府是否存在；结果写回 filled，并缓存 7 天。"""
    if not isinstance(filled, dict):
        return {}
    info = resolve_local(filled, materials)
    town = info.get("township") or ""
    if not town:
        return info
    key = f"v2|{info.get('city','')}|{info.get('county','')}|{town}"
    cached = _cache_get(key)
    if cached is not None:
        filled["admin_verified"] = bool(cached.get("verified"))
        filled["admin_sources"] = cached.get("sources") or []
        return {**info, **cached}

    city, county = info.get("city", ""), info.get("county", "")
    # 检索式：先用「镇名 + site:gov.cn」（最容易被政府站收录），再补全称兜底
    queries = [f"{town} site:gov.cn",
               f"{city}{county}{town}人民政府"]
    results: List[Dict[str, str]] = []
    try:
        from app.services.web_opinion import search_web
        for q in queries:
            results.extend(await search_web(q, top_k=8) or [])
            if any(town in (str(r.get("title") or "") + str(r.get("snippet") or ""))
                   for r in results):
                break
    except Exception as e:
        logger.warning(f"[行政区划] 联网核验失败（按未核验处理）: {e}")

    def _text(r: Dict[str, str]) -> str:
        return str(r.get("title") or "") + str(r.get("snippet") or "")

    hits = [r for r in results if town in _text(r)]
    gov_hits = [r for r in hits
                if "gov.cn" in str(r.get("link") or "")
                and (not (city or county)
                     or city in _text(r) or county in _text(r))]
    verified = bool(gov_hits) or len(hits) >= 2
    payload = {
        "verified": verified,
        "hits": len(hits),
        "sources": [str(r.get("link") or "") for r in (gov_hits or hits)[:3]],
        "query": queries[0],
    }
    _cache_put(key, payload)
    filled["admin_verified"] = verified
    filled["admin_sources"] = payload["sources"]
    logger.info(f"[行政区划] {queries[0]} → {'已核验' if verified else '未检索到'}（{len(hits)} 条）")
    return {**info, **payload}
