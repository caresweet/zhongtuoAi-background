# -*- coding: utf-8 -*-
"""全文生成后的跨章一致性检查 + 修正。

用户要求（2026-11-16）：全文生成完成后，有关联的资料/数据要上下文一致。
口径：
    · 事实卡里有权威值的（面积、户数、问卷份数、支持率、总投资），全文统一到权威值；
    · 没有权威值但多章重复的（四至、公告期、运距），按**多数一致**为准，
      少数派改写为多数派；
    · 改不了的（项目名称变体、可疑项目文号）只报不改，写进不一致清单。
入口：`report_assembler.assemble()` 在章节修补之后、装配之前调用。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_mu(filled: Dict[str, Any]) -> float:
    mu = _num((filled or {}).get("area_mu"))
    if mu > 0:
        return mu
    m2 = _num((filled or {}).get("area_m2"))
    return m2 / 666.6667 if m2 > 0 else 0.0


def _chapters(chapters: Dict[Any, Dict[str, Any]]):
    for k in sorted(chapters or {}, key=lambda x: str(x)):
        ch = chapters.get(k)
        if isinstance(ch, dict) and ch.get("markdown"):
            yield k, ch


def _sub_all(chapters, pattern: str, repl_fn) -> Tuple[int, List[Dict]]:
    """对全文做带标签的数值替换；repl_fn(match)->(new_text, issue_msg|None)。"""
    n = 0
    issues: List[Dict] = []
    for no, ch in _chapters(chapters):
        md = ch["markdown"]

        def _one(m):
            nonlocal n
            new, msg = repl_fn(m)
            if msg:
                issues.append({"chapter": no, "message": msg,
                               "before": m.group(0), "after": new})
            if new != m.group(0):
                n += 1
            return new

        ch["markdown"] = re.sub(pattern, _one, md)
    return n, issues


def _labeled_value(chapters, label_pattern: str, unit: str,
                   actual: float, tol: float = 0.05) -> Tuple[int, List[Dict]]:
    """把「标签 + 数字 + 单位」统一到 actual。"""
    if actual <= 0:
        return 0, []
    pat = rf'({label_pattern}\s*[为是：:]?\s*)(\d+(?:\.\d+)?)(\s*{unit})'

    def _repl(m):
        val = float(m.group(2))
        if abs(val - actual) <= max(tol, actual * 0.001):
            return m.group(0), None
        _fmt = f"{actual:.1f}" if unit == "%" else f"{actual:g}"
        return (f"{m.group(1)}{_fmt}{m.group(3)}",
                f"{m.group(1).strip()}应为 {_fmt}{unit}，正文写 {val:g}{unit}")

    return _sub_all(chapters, pat, _repl)


def _majority(chapters, pattern: str, label: str,
              normalize=lambda s: s) -> Tuple[int, List[Dict]]:
    """多章重复的结构化文字按多数派统一。"""
    found: Dict[str, List[Tuple[str, str]]] = {}
    for no, ch in _chapters(chapters):
        for m in re.finditer(pattern, ch["markdown"]):
            key = normalize(m.group(0))
            found.setdefault(key, []).append((no, m.group(0)))
    if len(found) <= 1:
        return 0, []
    counts = Counter({k: len(v) for k, v in found.items()})
    best, cnt = counts.most_common(1)[0]
    if cnt < 2:
        return 0, []
    n = 0
    issues: List[Dict] = []
    for key, hits in found.items():
        if key == best:
            continue
        for no, raw in hits:
            issues.append({"chapter": str(no), "message":
                           f"{label}与其他章节不一致（多数为「{best}」）",
                           "before": raw, "after": best})
            ch = chapters.get(no) or {}
            if ch.get("markdown"):
                ch["markdown"] = ch["markdown"].replace(raw, best)
                n += 1
    return n, issues


def _boundary_pattern() -> str:
    return r'东至[^。；\n]{1,40}?北至[^。；\n]{1,30}'


def _notice_pattern() -> str:
    return (r'(?:公告期|公告期限|公示期|公示期限)[^。；\n]{0,6}?'
            r'\d{4}年\d{1,2}月\d{1,2}日\s*至\s*\d{4}年\d{1,2}月\d{1,2}日'
            r'[^。；\n]{0,6}?共?\s*\d+\s*天')


def _haul_pattern() -> str:
    return r'运距[^。；\n]{0,8}?\d+(?:\.\d+)?\s*(?:km|公里)'


def check_and_fix(chapters: Dict[Any, Dict[str, Any]], filled: Dict[str, Any],
                  domain: str = "stability") -> Tuple[List[Dict], List[str]]:
    """执行一致性检查并就地修正。返回 `(问题清单, 修正日志)`。"""
    issues: List[Dict] = []
    fixes: List[str] = []
    f = filled or {}

    def _run(label: str, fn, *a):
        try:
            n, iss = fn(*a)
        except Exception as e:                      # 单类失败不影响其他检查
            logger.warning(f"[一致性] {label}检查失败: {e}")
            return
        issues.extend(iss)
        if n:
            fixes.append(f"{label}统一 {n} 处")

    # ── 有权威值的数值 ──
    _mu = _to_mu(f)
    _run("征收面积", _labeled_value, chapters,
         r'(?:征收|征地|拟征收|临时用地|用地)面积', "亩", _mu)
    _hh = _num(f.get("household_count"))
    _run("涉及户数", _labeled_value, chapters, r'(?:涉及户数|涉及|被征收户数)', "户", _hh)
    _sp = _num(f.get("total_samples") or f.get("survey_total_count"))
    _run("问卷份数", _labeled_value, chapters, r'(?:发放|回收|调查)问卷', "份", _sp)
    _sr = _num(f.get("support_rate"))
    _run("支持率", _labeled_value, chapters, r'支持率', "%", _sr)
    _dyn = _num(str(f.get("dynamic_total_investment") or "").replace("万元", ""))
    if domain == "reclamation" and _dyn > 0:
        # 只统一「复垦动态/总投资」，不碰「项目总投资」（主体工程投资，另一个口径）
        _run("复垦动态总投资", _labeled_value, chapters,
             r'(?:复垦动态总投资|复垦总投资|动态总投资)', "万元", _dyn)

    # ── 多章重复的结构化文字：按多数派 ──
    _run("四至", _majority, chapters, _boundary_pattern(), "四至范围",
         lambda s: re.sub(r'\s+', '', s))
    _run("公告期", _majority, chapters, _notice_pattern(), "公告期",
         lambda s: re.sub(r'\s+', '', s))
    if domain == "reclamation":
        _run("运距", _majority, chapters, _haul_pattern(), "运距")

    # ── 只报不改 ──
    _dir = str(f.get("reclamation_direction") or "").split("、")[0].strip()
    if domain == "reclamation" and _dir and _dir != "水田":
        for no, ch in _chapters(chapters):
            if re.search(r'(以保证|复垦为|恢复为|复垦后为)\s*水田', ch["markdown"]):
                issues.append({"chapter": no, "message":
                               f"复垦方向为{_dir}，正文仍出现「水田」措施表述",
                               "before": "水田", "after": ""})

    if issues:
        logger.warning("[一致性] 发现 %d 处跨章不一致：%s", len(issues),
                       "；".join(i["message"] for i in issues[:6]))
    if fixes:
        logger.info("[一致性] 已修正：%s", "；".join(fixes))
    return issues, fixes
