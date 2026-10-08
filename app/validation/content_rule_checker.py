# -*- coding: utf-8 -*-
"""内容规则校验 —— 对**章节正文**跑 `type:"content"` 规则。

与 `rule_checker.py`（跑 `filled_data` 的指标）互补：
- 指标类：问卷份数、支持率、户均人口 → `rule_checker.run_checks()`
- 内容类：占位符残留、模糊话术、无意义说明、编造法规文号、表格缺单位 → 这里

规则仍来自同一份 `data/check_rules.json`（`type == "content"`），业务可改。
部分规则带 `pattern` 字段（正则），没有的走内置判定函数。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.validation.rule_checker import load_rules

logger = logging.getLogger(__name__)

# 内置判定：rule_id → 函数(text) -> 命中列表[(片段, 说明)]
_BUILTIN_CHECKS: Dict[str, Any] = {}


def _register(rule_id):
    def deco(fn):
        _BUILTIN_CHECKS[rule_id] = fn
        return fn
    return deco


@_register("no_fabricated_regulation")
def _check_fabricated_regulation(text: str) -> List[tuple]:
    """编造法规文号：复用 content_guardrails 的白名单校验。"""
    try:
        from app.validation.content_guardrails import find_hallucinated_regulations
        hits = find_hallucinated_regulations(text)
        out = []
        for h in (hits or []):
            if isinstance(h, dict):
                out.append((str(h.get("text") or h.get("regulation") or ""), "疑似编造法规"))
            else:
                out.append((str(h), "疑似编造法规"))
        return out
    except Exception as e:
        logger.warning(f"编造法规校验失败: {e}")
        return []


# 表头含这些列名却没带单位 → 命中「表格缺单位」
_UNIT_NEEDED_COLS = ("面积", "人数", "个数", "份数", "户数", "占比", "比例", "金额", "补偿")
_UNIT_PAT = re.compile(r'(平方米|㎡|m2|亩|公顷|户|人|份|个|%|万元|元)')


@_register("table_unit_required")
def _check_table_unit(text: str) -> List[tuple]:
    """表格缺单位量纲：表头含面积/人数/占比列但整表无单位。"""
    out = []
    for block in re.findall(r'(?:^\|.*\|$\n?)+', str(text or ""), re.M):
        lines = [l for l in block.strip().split("\n") if l.strip().startswith("|")]
        if len(lines) < 2:
            continue
        header = lines[0]
        body = "\n".join(lines[2:]) if len(lines) > 2 else ""
        cols = [c.strip() for c in header.strip("|").split("|")]
        needs = [c for c in cols if any(k in c for k in _UNIT_NEEDED_COLS)]
        if not needs:
            continue
        # 表头或表体任一处出现单位即视为已标注
        if _UNIT_PAT.search(header) or _UNIT_PAT.search(body):
            continue
        out.append((header[:60], f"列「{'、'.join(needs[:3])}」缺单位量纲"))
    return out


def check_text(text: str, rules: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """对一段正文跑全部内容规则。

    Args:
        text: 章节正文（markdown 或纯文本）。
        rules: 规则列表；缺省读 check_rules.json。

    Returns:
        问题条目列表。
    """
    issues: List[Dict[str, Any]] = []
    text = str(text or "")
    for rule in (rules if rules is not None else load_rules()):
        if not rule.get("enabled") or rule.get("type") != "content":
            continue
        label = rule.get("label") or rule.get("metric")
        hits: List[tuple] = []

        # 1) 显式正则
        pat = rule.get("pattern")
        if pat:
            try:
                for m in re.finditer(pat, text):
                    hits.append((m.group(0), label))
            except re.error as e:
                logger.warning(f"规则 {rule.get('id')} 正则非法: {e}")
        # 2) 内置判定
        fn = _BUILTIN_CHECKS.get(str(rule.get("id")))
        if fn:
            hits.extend(fn(text))

        if not hits:
            continue
        issues.append({
            "rule_id": rule.get("id"),
            "metric": label,
            "actual": len(hits),
            "limit": rule.get("value", 0),
            "unit": rule.get("unit", "处"),
            "severity": rule.get("severity", "warn"),
            "basis": rule.get("basis", ""),
            "note": rule.get("note", ""),
            "samples": [h[0][:40] for h in hits[:5]],
            "message": f"{label} 命中 {len(hits)} 处：{'；'.join(h[0][:24] for h in hits[:3])}",
        })
    return issues


def check_chapters(chapters: Dict[Any, Any],
                   rules: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """逐章跑内容规则，问题条目带上章号。"""
    out: List[Dict[str, Any]] = []
    for ch, data in (chapters or {}).items():
        md = data.get("markdown", "") if isinstance(data, dict) else str(data or "")
        for issue in check_text(md, rules):
            issue["chapter"] = ch
            out.append(issue)
    return out
