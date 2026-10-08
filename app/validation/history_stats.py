"""历史统计 —— 为「异常值自动识别」提供历史均值/分位来源。

用户要求（原文）：
> **异常值自动识别**：对通过率等比例数据设定合理区间（如偏离历史均值或行业常模过远），
> 系统自动标记并建议人工复核，防止因输入错误或样本偏差误导结论。

数据来源（按优先级）：
1. `data/history_reports.db` 的 `reports.filled_data_json` —— 结构化事实卡（当前多为空 `{}`）
2. `reports.markdown_content` —— 历史报告正文，按指标正则挖数（**当前实际可用的来源**）
3. 规则自带的 `params.fallback` 常模 —— 样本量不足时兜底

样本量 `< min_n` 时返回 None，由 `rule_checker` 回退到 `fallback`。
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_HISTORY_DB = _BACKEND_DIR / "data" / "history_reports.db"

# 指标 → 从正文挖数的正则（第1个捕获组为数值）
_METRIC_PATTERNS: Dict[str, List[str]] = {
    "support_rate": [r'支持率[^\d%]{0,6}(\d{1,3}(?:\.\d+)?)\s*%',
                     r'支持度为[^\d%]{0,6}(\d{1,3}(?:\.\d+)?)\s*%'],
    "oppose_rate": [r'反对率[^\d%]{0,6}(\d{1,3}(?:\.\d+)?)\s*%'],
    "awareness_rate": [r'知晓率[^\d%]{0,6}(\d{1,3}(?:\.\d+)?)\s*%'],
    "total_samples": [r'(?:发放|回收|有效)[^。\n]{0,10}问卷[^\d]{0,6}(\d{1,5})\s*份',
                      r'问卷[^\d]{0,6}(\d{1,5})\s*份'],
    "household_count": [r'涉及[^。\n]{0,8}农户[^\d]{0,6}(\d{1,5})\s*户',
                        r'[^\d](\d{1,5})\s*户'],
    "population_count": [r'涉及[^。\n]{0,8}人口[^\d]{0,6}(\d{1,5})\s*人'],
    "area_mu": [r'面积[^\d]{0,6}(\d{1,7}(?:\.\d+)?)\s*亩'],
}


def _read_reports() -> List[Tuple[str, str]]:
    """取历史报告的 (filled_data_json, markdown_content)。"""
    if not _HISTORY_DB.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(_HISTORY_DB))
        rows = conn.execute(
            "SELECT COALESCE(filled_data_json,''), COALESCE(markdown_content,'') "
            "FROM reports WHERE status IS NULL OR status != 'error' "
            "ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        conn.close()
        return [(a or "", b or "") for a, b in rows]
    except Exception as e:
        logger.warning(f"读取历史报告失败: {e}")
        return []


def _collect_values(metric_key: str) -> List[float]:
    """收集该指标的历史取值（先结构化字段，再正文正则）。"""
    vals: List[float] = []
    pats = [re.compile(p) for p in _METRIC_PATTERNS.get(metric_key, [])]

    for fd_json, md in _read_reports():
        # 1) 结构化事实卡
        got = False
        if fd_json.strip() and fd_json.strip() != "{}":
            try:
                d = json.loads(fd_json)
                v = d.get(metric_key)
                if v not in (None, "", "【待补充】"):
                    f = _to_float(v)
                    if f is not None:
                        vals.append(f)
                        got = True
            except Exception:
                pass
        if got:
            continue
        # 2) 正文挖数
        for pat in pats:
            m = pat.search(md)
            if m:
                f = _to_float(m.group(1))
                if f is not None:
                    vals.append(f)
                    break
    return vals


def _to_float(v) -> Optional[float]:
    try:
        s = str(v).replace(",", "").replace("%", "").strip()
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=64)
def metric_distribution(metric_key: str) -> Dict[str, Any]:
    """返回该指标的历史分布 `{mean, std, p5, p95, n}`；无数据时 n=0。"""
    vals = _collect_values(metric_key)
    n = len(vals)
    if n == 0:
        return {"mean": None, "std": None, "p5": None, "p95": None, "n": 0}
    vals_sorted = sorted(vals)
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals) if n > 1 else 0.0
    return {
        "mean": mean,
        "std": std,
        "p5": _percentile(vals_sorted, 5),
        "p95": _percentile(vals_sorted, 95),
        "n": n,
    }


def _percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def is_outlier(metric_key: str, value, *, sigma: float = 2.0, min_n: int = 5,
               fallback: Optional[List[float]] = None) -> Tuple[bool, Dict[str, Any]]:
    """判断某指标取值是否为异常值。

    规则：样本量 ≥ min_n 时用「偏离均值 > sigma·σ」或「超出 [P5, P95]」判定；
    样本不足时用 `fallback` 常模区间。

    Args:
        metric_key: 指标名（如 support_rate）。
        value: 待判定的取值。
        sigma: 标准差倍数阈值。
        min_n: 启用历史分布所需的最小样本量。
        fallback: 常模区间 [lo, hi]，样本不足时的兜底。

    Returns:
        (是否异常, 说明 dict)
    """
    v = _to_float(value)
    if v is None:
        return False, {"reason": "metric_missing"}

    dist = metric_distribution(metric_key)
    if dist["n"] >= min_n and dist["std"] is not None:
        mean, std, p5, p95 = dist["mean"], dist["std"], dist["p5"], dist["p95"]
        by_sigma = std > 0 and abs(v - mean) > sigma * std
        by_pct = (p5 is not None and v < p5) or (p95 is not None and v > p95)
        if by_sigma or by_pct:
            return True, {"basis": "history", "mean": round(mean, 2), "std": round(std, 2),
                          "p5": p5, "p95": p95, "n": dist["n"], "value": v}
        return False, {"basis": "history", "n": dist["n"], "value": v}

    if fallback and len(fallback) == 2:
        lo, hi = float(fallback[0]), float(fallback[1])
        if v < lo or v > hi:
            return True, {"basis": "norm_fallback", "range": [lo, hi], "n": dist["n"], "value": v}
        return False, {"basis": "norm_fallback", "n": dist["n"], "value": v}

    return False, {"basis": "none", "n": dist["n"], "value": v}


def clear_cache() -> None:
    """清空历史分布缓存（规则/数据变更后调用）。"""
    metric_distribution.cache_clear()
