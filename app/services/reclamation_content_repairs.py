# -*- coding: utf-8 -*-
"""复垦章装配前的内容修补（按 2026-11-11 专家批注）。

覆盖：
  · 单位 hm → hm²（含表头「单位：hm」）
  · 小节号断号（4.1.3 前缺 4.1.2）、重复小节内容（1.2.6 抄了 1.2.1）
  · 服务年限/管护期与基础资料不一致（批注：12个月→6个月）
  · 复垦方向应恢复原地类、复垦面积应为临时用地面积（批注：原地类 / 2.68 公顷）
  · 剥离厚度统一 30cm（批注：统一成30cm）
  · 管护费/动态投资误用主体工程总投资 21500 万元（批注：错误 / 4.23）
  · 「见表3-1」但全文没有表3-1（补一张土地利用现状表）
  · 「见表7-1」但全文没有表7-1（删除指向，改按定额计取）
  · 硬化面积 1.1000 公顷没有来源（补一句依据）
  · 表/图编号跳号、重号（章内顺序重排，引用同步改）
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Tuple

_HEAD = re.compile(r'^(#*\s*)(\d+)\.(\d+)(?:\.(\d+))?\s+')
_CAPTION = re.compile(r'^(\s*)(表|图)\s*(\d+)\s*[-—－]\s*(\d+)\s*(.*)$')


# ── 单位 ─────────────────────────────────────────────────────
def fix_units(md: str) -> Tuple[str, int]:
    """hm / hm2 → hm²（表头「单位：hm」也要改）。"""
    n = 0
    out = md
    out, k1 = re.subn(r'(?i)\bhm\s*2\b', 'hm²', out)
    out, k2 = re.subn(r'(?i)\bhm(?!²)(?![a-zA-Z0-9])', 'hm²', out)
    out, k3 = re.subn(r'单位[：:]\s*hm²?', '单位：hm²', out)
    return out, k1 + k2 + k3


# ── 小节号断号 ───────────────────────────────────────────────
def fix_heading_numbers(md: str) -> Tuple[str, int]:
    lines = md.splitlines()
    changes = 0
    lvl2 = 0
    lvl3: Dict[int, int] = {}
    for i, ln in enumerate(lines):
        m = _HEAD.match(ln)
        if not m:
            continue
        pre, ch, a, b = m.group(1), m.group(2), m.group(3), m.group(4)
        if b is None:
            # 只重排二级：1,3 → 1,2
            new = f"{ch}.{int(a)}"
            if int(a) != lvl2 + 1:
                new = f"{ch}.{lvl2 + 1}"
            lvl2 = int(new.split('.')[1])
            lvl3[lvl2] = 0
            rest = ln[len(m.group(0)):]
            lines[i] = f"{pre}{new} {rest}"
            if lines[i] != ln:
                changes += 1
        else:
            k = int(a) or lvl2
            lvl3[k] = lvl3.get(k, 0) + 1
            new = f"{ch}.{k}.{lvl3[k]}"
            rest = ln[len(m.group(0)):]
            lines[i] = f"{pre}{new} {rest}"
            if lines[i] != ln:
                changes += 1
    return "\n".join(lines), changes


# ── 重复小节内容 ─────────────────────────────────────────────
def dedupe_sections(md: str) -> Tuple[str, int]:
    import difflib
    paras = [p for p in md.split("\n\n")]
    kept: List[str] = []
    kept_long: List[str] = []
    removed = 0
    for p in paras:
        s = p.strip()
        if len(s) >= 200 and kept_long:
            dup = False
            for q in kept_long[-40:]:
                if difflib.SequenceMatcher(None, s, q).ratio() >= 0.85:
                    dup = True
                    break
            if dup:
                removed += 1
                continue
        kept.append(p)
        if len(s) >= 200:
            kept_long.append(s)
    if removed == 0:
        return md, 0
    return "\n\n".join(kept), removed


# ── 服务年限/管护期 ─────────────────────────────────────────
def _num(v) -> float:
    m = re.search(r'\d+(?:\.\d+)?', str(v or ""))
    return float(m.group(0)) if m else 0.0


def fix_service_period(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    use = _num(filled.get("land_use_months") or filled.get("land_use_period_months"))
    rec = _num(filled.get("reclaim_period_months"))
    care = _num(filled.get("care_period_months"))
    total = _num(filled.get("service_months") or filled.get("service_years"))
    if not total and use and rec and care:
        total = use + rec + care
    if not total:
        return md, 0
    n = 0
    out = md
    if care:
        out, k = re.subn(r'管护期\s*\d+(?:\.\d+)?\s*个月', f"管护期{care:g}个月", out)
        n += k
    out, k = re.subn(r'(服务年限)([^。；，]{0,12}?)\d+(?:\.\d+)?\s*个月',
                     lambda m: f"{m.group(1)}{m.group(2)}{total:g}个月", out)
    n += k
    # 结束年月 = 用地开始 + 总月数 - 1；只改「服务年限…从X到Y」这一句，
    # 不动「2027年2月至2028年6月安排动态投资」这类阶段日期（批注只针对服务年限）。
    start = str(filled.get("land_use_start") or "")
    m = re.match(r'(\d{4})-(\d{1,2})', start)
    if m and total:
        y, mo = int(m.group(1)), int(m.group(2))
        idx = y * 12 + (mo - 1) + int(round(total)) - 1
        ey, em = divmod(idx, 12)
        em += 1
        out, k = re.subn(
            r'(服务年限[^。；]{0,20}?从\s*)\d{4}\s*年\s*\d{1,2}\s*月\s*[到至]\s*\d{4}\s*年\s*\d{1,2}\s*月',
            rf'\g<1>{y}年{mo}月到{ey}年{em}月', out)
        n += k
    return out, n


# ── 复垦方向 / 面积 ─────────────────────────────────────────
def _land_targets(filled: Dict[str, Any]) -> Tuple[str, float, Dict[str, float]]:
    rows = []
    lc = filled.get("land_classification")
    if isinstance(lc, dict):
        rows = lc.get("rows") or []
    names = []
    total = 0.0
    per: Dict[str, float] = {}
    for r in rows:
        t = str((r or {}).get("二级地类") or (r or {}).get("地类") or "")
        a = _num((r or {}).get("面积_m2")) / 10000.0
        total += a
        if t:
            per[t] = per.get(t, 0.0) + a
            if t not in names:
                names.append(t)
    area = total if total else _num(filled.get("area_m2")) / 10000.0
    return "、".join(names), area, per


def fix_restoration(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    names, area_ha, per = _land_targets(filled)
    n = 0
    out = md
    if names:
        out, k = re.subn(
            r'复垦方向(?:确定)?为\s*[^。；\n]{0,50}',
            f"复垦方向为恢复原地类（{names}）", out)
        n += k
    if area_ha:
        out, k = re.subn(
            r'(?<!旱地)(?<!园地)(?<!耕地)(?<!林地)(?<!水田)复垦面积\s*\d+(?:\.\d+)?\s*公顷',
            f"复垦面积{area_ha:.4f}公顷", out)
        n += k
    # 按地类写各复垦面积（批注：9.1 把「复垦旱地面积」写成 2.0，实际旱地只有 1.4）
    for name, a in per.items():
        out, k = re.subn(rf'复垦{name}面积\s*\d+(?:\.\d+)?\s*公顷',
                         f"复垦{name}面积{a:.4f}公顷", out)
        n += k
    return out, n


# ── 剥离厚度 ─────────────────────────────────────────────────
def fix_strip_depth(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    depth = _num(filled.get("topsoil_thickness") or filled.get("strip_depth_cm")) or 30.0
    n = 0
    out, k1 = re.subn(r'剥离厚度\s*\d+(?:\.\d+)?\s*cm', f"剥离厚度{depth:g}cm", md)
    # 「剥离厚度25cm」可能写成「离厚度25cm」被截断，也兜一下
    out, k2 = re.subn(r'(?<!剥)离厚度\s*\d+(?:\.\d+)?\s*cm', f"剥离厚度{depth:g}cm", out)
    return out, k1 + k2


# ── 管护费 / 动态投资 ───────────────────────────────────────
def fix_cost_numbers(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    est = filled.get("_budget_estimate") or {}
    care = float(est.get("care") or 0)
    monitor = float(est.get("monitor") or 0)
    if not care:
        _cm = _num(filled.get("care_period_months"))
        if _cm:
            care = round(1 * _cm * 1000.0 / 10000.0, 2)   # 每村 1 人 · 1000 元/(人·月)
    n = 0
    out = md
    if care:
        out, k = re.subn(r'([^。；\n]*管护[^。；\n]*?共计)\s*[\d.]+\s*万元',
                         rf'\g<1> {care:.2f} 万元', out)
        n += k
    if monitor:
        out, k = re.subn(r'(动态投资)\s*[\d.]+(?:\s*)?万元',
                         rf'\g<1>{monitor:.2f} 万元', out)
        n += k
    return out, n


# ── 表3-1 / 表7-1 指向 ─────────────────────────────────────
def fix_land_table_ref(md: str, land_caption: str = "") -> Tuple[str, int]:
    """「各地类面积见表3-1」：表3-1 实际不存在。

    第4章已有「临时用地土地利用现状统计表」时，把指向改成那张表；
    没有可指的，就把这半句删掉，不留悬空编号（不再另插一张重复表）。
    """
    if "表3-1" not in md:
        return md, 0
    if land_caption:
        out, k = re.subn(r'表\s*3\s*[-—]\s*1', land_caption, md)
        return out, k
    out, k = re.subn(r'[，,；;]?\s*见表\s*3\s*[-—]\s*1[。；]?', '。', md)
    return out, k


def add_table_3_1(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    if "表3-1" not in md or re.search(r'表\s*3\s*[-—]\s*1\s+\S', md):
        return md, 0
    lc = filled.get("land_classification")
    rows = (lc or {}).get("rows") if isinstance(lc, dict) else []
    if not rows:
        return md, 0
    lines = ["", "表3-1 项目区土地利用现状表", "",
             "| 序号 | 一级地类 | 二级地类 | 面积（公顷） | 占比（%） | 权属 |",
             "|---|---|---|---|---|---|"]
    tot = 0.0
    for r in rows:
        a = _num(r.get("面积_m2")) / 10000.0
        tot += a
        lines.append(f"| {r.get('序号') or ''} | {r.get('一级地类') or ''} | "
                     f"{r.get('二级地类') or ''} | {a:.4f} | {r.get('占比') or ''} | "
                     f"{r.get('权属') or ''} |")
    lines.append(f"| 合计 | — | — | {tot:.4f} | 100.00 | — |")
    table = "\n".join(lines)
    # 插在「见表3-1」所在段落之后
    m = re.search(r'[^\n]*见表3-1[^\n]*\n', md)
    if m:
        return md[:m.end()] + table + "\n" + md[m.end():], 1
    return md + "\n" + table, 1


def drop_missing_table_refs(md: str) -> Tuple[str, int]:
    n = 0
    out = md
    for cap in ("表7-1",):
        if cap not in out:
            continue
        has_caption = bool(re.search(rf'{cap}\s+\S', out))
        if has_caption:
            continue
        out, k = re.subn(rf'[，,；;]?\s*见{cap}[。；]?', '', out)
        n += k
    return out, n


# ── 硬化面积来源 ─────────────────────────────────────────────
def explain_hard_area(md: str) -> Tuple[str, int]:
    if "硬化面积" not in md or "施工组织设计" in md:
        return md, 0
    out, k = re.subn(
        r'(硬化面积\s*\d+(?:\.\d+)?\s*公顷)',
        r'\1（依据施工组织设计，为施工场站与施工便道硬化部分）', md, count=1)
    return out, k


# ── 表/图编号 ───────────────────────────────────────────────
def renumber_captions(md: str, chapter: int) -> Tuple[str, int]:
    lines = md.splitlines()
    mapping: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    counters = {"表": 0, "图": 0}
    for i, ln in enumerate(lines):
        m = _CAPTION.match(ln)
        if not m:
            continue
        indent, kind, ch, old, title = m.groups()
        if int(ch) != int(chapter):
            continue
        counters[kind] += 1
        new_no = str(counters[kind])
        old_key = f"{kind}{ch}-{old}"
        if seen.get(old_key):
            # 重号：保留第一个编号，后面的顺延但引用无法无歧义地改
            pass
        else:
            mapping[old_key] = f"{kind}{ch}-{new_no}"
            seen[old_key] = 1
        lines[i] = f"{indent}{kind}{ch}-{new_no} {title}".rstrip()
    out = "\n".join(lines)
    n = len(mapping)
    # 同步引用：先占位再落号，避免「6-3→6-2 又被 6-2→6-1 再改一次」
    for i, (old, new) in enumerate(mapping.items()):
        if old == new:
            continue
        out = out.replace(old, f"\x00{i}\x00")
    for i, (old, new) in enumerate(mapping.items()):
        out = out.replace(f"\x00{i}\x00", new)
    return out, n


# ── 入口 ────────────────────────────────────────────────────
def repair_reclamation_chapters(chapters: Dict[Any, Dict[str, Any]],
                                filled: Dict[str, Any]) -> List[str]:
    logs: List[str] = []
    # 找第4章的「土地利用现状」表号，供第3章「见表3-1」改指向
    _land_caption = ""
    for _ch in (chapters or {}).values():
        if not isinstance(_ch, dict):
            continue
        m = re.search(r'(表\s*\d+\s*[-—]\s*\d+)[^\n]*土地利用现状', str(_ch.get("markdown") or ""))
        if m:
            _land_caption = re.sub(r'\s+', '', m.group(1))
            break
    for key, ch in list(chapters.items()):
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        orig = md
        from app.services.report_content_repairs import fix_truncated_paragraphs
        md, n_tr = fix_truncated_paragraphs(md)
        md, n_u = fix_units(md)
        md, n_d = dedupe_sections(md)
        md, n_s = fix_service_period(md, filled or {})
        md, n_r = fix_restoration(md, filled or {})
        md, n_t = fix_strip_depth(md, filled or {})
        md, n_c = fix_cost_numbers(md, filled or {})
        md, n_a = fix_land_table_ref(md, _land_caption)
        md, n_dr = drop_missing_table_refs(md)
        md, n_e = explain_hard_area(md)
        if md != orig:
            ch["markdown"] = md
            bits = []
            for label, k in (("截断残句", n_tr), ("单位hm²", n_u), ("重复段", n_d),
                             ("服务年限", n_s), ("复垦方向/面积", n_r),
                             ("剥离厚度", n_t), ("费用数字", n_c),
                             ("表3-1指向", n_a), ("删无效表号", n_dr),
                             ("硬化面积来源", n_e)):
                if k:
                    bits.append(f"{label}{k}")
            if bits:
                logs.append(f"第{key}章：" + "、".join(bits))
    return logs
