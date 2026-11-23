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
            # 三级编号必须跟**当前二级**走：原文 1.1 下面写成 1.2.1/1.2.2，
            # 专家批注「1.2.1和1.2.2怎么到这里了」——改成 1.1.1/1.1.2
            k = lvl2 or int(a)
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


def fix_use_period_end(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """「临时用地使用时间 18 个月，从 2027 年 1 月到 2029 年 6 月」—— 结束月算错。

    18 个月的用地区间应是 2027-01～2028-06；模型常把**服务年限总数（30 个月）**
    的结束月（2029-06）抄到使用期上。按「月份 + 起点」重算结束月。
    """
    months = _num(filled.get("land_use_months") or filled.get("land_use_period_months"))
    if not months:
        return md, 0

    def _repl(m):
        head, mo_txt, sep = m.group(1), m.group(2), m.group(3)
        mo = int(float(mo_txt))
        y, mm, conn = int(m.group(4)), int(m.group(5)), m.group(6)
        if mo != int(months):
            return m.group(0)
        idx = y * 12 + (mm - 1) + mo - 1
        ey, em = divmod(idx, 12)
        return f"{head}{mo_txt}个月{sep}{y}年{mm}月{conn}{ey}年{em + 1}月"

    pat = (r'((?:临时用地)?(?:使用时间为|批准使用|批准期限|使用期)[^。；\n]{0,4}?)'
           r'(\d+(?:\.\d+)?)\s*个月([，,]?\s*(?:从|自)?\s*)'
           r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*([到至])\s*(\d{4})\s*年\s*(\d{1,2})\s*月')
    out, n = re.subn(pat, _repl, md)
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


# 正文里的地类叫法与基础资料不同：基础资料按二级地类写「果园/旱地」，
# 正文常写「园地/耕地」。批注 2026-11-12：「沟渠复垦面积 2.6800 公顷（应 0.38）」——
# 只认 `复垦{name}面积` 一种语序还不够，正文实际写的是「沟渠复垦面积」。
_LAND_ALIAS = {
    "果园": ("园地",), "园地": ("果园",),
    "旱地": ("耕地",), "水田": ("耕地",), "耕地": ("旱地", "水田"),
}


def _land_alias_areas(per: Dict[str, float]) -> Dict[str, float]:
    """在二级地类面积上补一层正文别名（耕地=旱地+水田，园地=果园…）。"""
    out: Dict[str, float] = dict(per)
    for name, a in per.items():
        for al in _LAND_ALIAS.get(name, ()):
            out[al] = out.get(al, 0.0) + a
    return out


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
            r'(?<!旱地)(?<!园地)(?<!耕地)(?<!林地)(?<!水田)复垦面积\s*\d+(?:\.\d+)?\s*(?:公顷|hm²|hm2)',
            f"复垦面积{area_ha:.4f}公顷", out)
        n += k
    # 按地类写各复垦面积（批注：9.1 把「复垦旱地面积」写成 2.0，实际旱地只有 1.4；
    # 9.1 又把「沟渠/农村道路复垦面积」都写成总面积 2.68，实际 0.38 / 0.30）。
    for name, a in _land_alias_areas(per).items():
        # 「复垦旱地面积 1.4」「旱地复垦面积 2.68」两种语序都要认；单位公顷/hm² 都认
        for pat, rep in (
                (rf'复垦{name}面积\s*\d+(?:\.\d+)?\s*(?:公顷|hm²|hm2)',
                 f"复垦{name}面积{a:.4f}公顷"),
                (rf'{name}复垦面积\s*\d+(?:\.\d+)?\s*(?:公顷|hm²|hm2)',
                 f"{name}复垦面积{a:.4f}公顷")):
            out, k = re.subn(pat, rep, out)
            n += k
    return out, n


# 农用地一级地类（表土剥离只针对这些；水域/道路/建设用地不剥离）
_AGRI_TOP = {"耕地", "园地", "林地", "草地", "其他农用地"}
_TOP_OF_SECOND = {
    "旱地": "耕地", "水田": "耕地", "水浇地": "耕地",
    "果园": "园地", "茶园": "园地", "其他园地": "园地",
    "乔木林地": "林地", "灌木林地": "林地", "其他林地": "林地",
}


def _land_rows_full(filled: Dict[str, Any]) -> List[Dict[str, Any]]:
    """基础资料《土地分类面积》→ [{top, second, area}]（公顷）。"""
    lc = filled.get("land_classification")
    rows = (lc or {}).get("rows") if isinstance(lc, dict) else []
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        second = str((r or {}).get("二级地类") or (r or {}).get("地类") or "").strip()
        top = str((r or {}).get("一级地类") or "").strip() \
            or _TOP_OF_SECOND.get(second, second)
        a = _num((r or {}).get("面积_m2")) / 10000.0
        if second and a > 0:
            out.append({"top": top, "second": second, "area": a})
    return out


def _subset_for_area(rows: List[Dict[str, Any]], value: float) -> List[Dict[str, Any]]:
    """找出面积之和≈value 的地类组合（本项目 2.0000 = 旱地 1.4 + 果园 0.6）。

    用**实际地类面积**反推这句话在说哪几类，而不是假定「耕地+园地」。
    """
    if not rows or value <= 0:
        return []
    best: List[Dict[str, Any]] = []
    n = len(rows)
    if n > 12:                     # 组合数太大时不做穷举
        tot = sum(r["area"] for r in rows)
        return rows if abs(tot - value) <= 0.02 else []
    for mask in range(1, 1 << n):
        sel = [rows[i] for i in range(n) if mask >> i & 1]
        _s = sum(r["area"] for r in sel)
        if abs(_s - value) <= 0.02 and (not best or len(sel) > len(best)):
            best = sel
    return best


def _class_phrase(rows: List[Dict[str, Any]], style: str) -> str:
    """按句子里原有的叫法给地类列表：一级（耕地/园地）或二级（旱地/果园）。"""
    names: List[str] = []
    for r in rows:
        nm = r["second"] if style == "second" else r["top"]
        if nm and nm not in names:
            names.append(nm)
    if len(names) <= 1:
        return "".join(names)
    return "、".join(names[:-1]) + "和" + names[-1]


def _class_style(text: str) -> str:
    """这句话用的是二级地类（旱地/果园…）还是一级地类（耕地/园地…）。"""
    return "second" if any(k in text for k in _TOP_OF_SECOND) else "top"


def _seg_present(seg: str, style: str, present_top, present_second) -> bool:
    """句子里的地类名是不是本项目真有的（按同一层级比对）。"""
    pool = present_second if style == "second" else present_top
    names = [x.strip() for x in re.split(r'[、和及]', str(seg or "")) if x.strip()]
    return bool(names) and all(x in pool for x in names)


def fix_topsoil_land_types(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """表土剥离对象的地类 —— 批注 2026-11-12：「园地」「耕地和园地」。

    🔴 不能把「旱地」一律改写成「耕地和园地」：是旱地还是耕地、是果园还是园地，
        取决于**本项目的土地分类面积表**。这里只做两件事：
        ① 句子里出现了本项目**没有**的地类（如「耕地和林地」，项目无林地）→
           换成按面积反推出的实际地类；
        ② 句子说的地类**覆盖不全**（2.0000 公顷其实是旱地 1.4 + 果园 0.6）→ 补齐。
        命名口径跟随原句（原句写旱地就写「旱地和果园」，写耕地就写「耕地和园地」）。
    """
    rows = _land_rows_full(filled)
    agri = [r for r in rows if r["top"] in _AGRI_TOP]
    if not agri:
        return md, 0
    present_top = {r["top"] for r in agri}
    present_second = {r["second"] for r in agri}
    # 表土剥离范围：句子里给出的面积能对上哪几类，就按哪几类（找不到才用全部农用地）
    _m = re.search(r'(?:表土剥离主要针对现状的[^。；\n]{0,12}?|需剥离表土的地类主要为[^。；\n]{0,12}?)，?'
                   r'?面积为\s*(\d+(?:\.\d+)?)', md)
    if not _m:
        _m = re.search(r'剥离表土[^。；\n]{0,20}?面积为\s*(\d+(?:\.\d+)?)', md)
    strip_rows = _subset_for_area(agri, float(_m.group(1))) if _m else []
    if not strip_rows:
        strip_rows = agri

    n = 0
    out = md

    # ① 出现本项目不存在的地类 → 整串换成实际地类
    def _fix_list(m):
        nonlocal n
        seg = m.group(2)
        style = _class_style(seg)
        exists = all(
            (nm.strip() in present_second) if style == "second" else (nm.strip() in present_top)
            for nm in re.split(r'[、和及]', seg) if nm.strip())
        if exists:
            return m.group(0)
        n += 1
        return m.group(1) + _class_phrase(strip_rows, style)

    out = re.sub(
        r'(需剥离表土的地类主要为\s*)([^，。；\n]{2,20}?)(?=[，。；])', _fix_list, out)
    out = re.sub(
        r'(需剥离表土的地类为\s*)([^，。；\n]{2,20}?)(?=[，。；])', _fix_list, out)

    # ② 句子里的面积实际覆盖多类，但只写了一类 → 补齐（值不动，只改地类名）
    def _rep_classes(m, _pre: int = 1, _seg: int = 2, _mid: int = 3, _val: int = 4):
        nonlocal n
        seg = m.group(_seg)
        try:
            val = float(m.group(_val))
        except (TypeError, ValueError):
            return m.group(0)
        sel = _subset_for_area(agri, val)
        if not sel:
            return m.group(0)
        style = _class_style(seg)
        if len(sel) <= 1 and _seg_present(seg, style, present_top, present_second):
            return m.group(0)
        phrase = _class_phrase(sel, style)
        if phrase == seg:
            return m.group(0)
        n += 1
        return m.group(_pre) + phrase + m.group(_mid) + m.group(_val)

    out = re.sub(
        r'(表土剥离主要针对现状的\s*)([^，,。；\n]{1,12}?)(\s*[，,]\s*面积为\s*)(\d+(?:\.\d+)?)',
        _rep_classes, out)
    out = re.sub(
        r'(复垦后\s*)([^，,。；\n]{1,12}?)(\s*面积为\s*)(\d+(?:\.\d+)?)',
        _rep_classes, out)

    # 「…面积均为 2.0000hm²，与复垦后旱地面积对应」—— 面积在前、地类在后
    for m in list(re.finditer(
            r'[^。；\n]{0,60}?与复垦后\s*([^，,。；\n]{1,12}?)(?=面积对应)', out)):
        seg = m.group(1)
        vm = None
        for vm in re.finditer(r'(?:均为|为)\s*(\d+(?:\.\d+)?)\s*(?:hm²|公顷|m²)',
                              m.group(0)[:m.start(1) - m.start(0)]):
            pass
        if not vm:
            continue
        sel = _subset_for_area(agri, float(vm.group(1)))
        if not sel:
            continue
        style = _class_style(seg)
        if len(sel) <= 1 and _seg_present(seg, style, present_top, present_second):
            continue
        phrase = _class_phrase(sel, style)
        if phrase != seg:
            out = out[:m.start(1)] + phrase + out[m.end(1):]
            n += 1
    return out, n


# ── 剥离厚度 ─────────────────────────────────────────────────
def fix_zone_plot_counts(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """各功能区的「地块数」按基础资料统计，正文不得与表4-1 冲突。

    批注/业主 2026-11-12：「上下文数据要统一」——城东这版表4-1 写管道开挖带 3 个
    地块、表土堆放区 2 个，正文却都写「1 个地块」。以 `_land_use_zones` 的
    `plot_count` 为准（它同时供表1-1、表4-1 出数），逐区改正文。
    """
    try:
        from app.services.reclamation_zones import zones_from_filled
        zones = zones_from_filled(filled or {})
    except Exception:
        return md, 0
    if not zones:
        return md, 0
    n = 0
    out = md
    total = 0
    for z in zones:
        name = str(z.get("name") or "").strip()
        cnt = int(_num(z.get("plot_count")) or 0)
        if not name or cnt <= 0:
            continue
        total += cnt
        pat = rf'({re.escape(name)}\s*(?:为|共|有)?\s*)\d+\s*个地块'
        out, k = re.subn(pat, rf'\g<1>{cnt}个地块', out)
        n += k
    if total:
        # 「共 N 个地块」「涉及 N 个地块」——按各区合计（表4-1 的合计口径）。
        # 城东这版写「涉及4个地块」（其实是 4 个功能区），而表4-1 按地块数是 8。
        out, k = re.subn(r'((?:共|合计|共计|涉及|分为|划分为)\s*)\d+\s*个地块',
                         rf'\g<1>{total}个地块', out)
        n += k
        # 模型有时写「涉及多个地块」——同样给确数（表4-1 是 8 个）
        out, k = re.subn(r'((?:共|合计|共计|涉及|分为|划分为)\s*)多个\s*地块',
                         rf'\g<1>{total}个地块', out)
        n += k
    return out, n


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
        # 第8章「管护期安排费用 0.60 万元」——批注 2026-11-12：与表8-1 的 1.26 打架。
        # 管护费只认 7.2.4 的 1 人 × 管护月数 × 1000 元/（人·月）。
        out, k = re.subn(r'(管护期安排费用)\s*[\d.]+\s*万元',
                         rf'\g<1>{care:.2f}万元', out)
        n += k
    if monitor:
        # 🔴 这里以前写的是 monitor（复垦监测费 0.37），把「动态投资 57.48 万元」
        #    改成了 0.37 万元。要改的是动态总投资，不是监测费。
        dynamic = float(est.get("dynamic_total") or 0)
        if dynamic:
            out, k = re.subn(r'(动态(?:总)?投资)\s*[\d.]+(?:\s*)?万元',
                             rf'\g<1>{dynamic:.2f} 万元', out)
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
    # 🔴 只改「见表3-1」这种**引用**，绝不能碰表题行：本函数曾把整行
    #    「表3-1 临时用地土地利用现状地类表」的表号也换掉，第3章那张表就
    #    不再被判为「地类表」而漏渲染（2026-11-12 回归）。
    if not re.search(r'见\s*表\s*3\s*[-—]\s*1', md):
        return md, 0
    if land_caption:
        out, k = re.subn(r'见\s*表\s*3\s*[-—]\s*1', f"见{land_caption}", md)
        return out, k
    out, k = re.subn(r'[，,；;]?\s*见\s*表\s*3\s*[-—]\s*1[。；]?', '。', md)
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


# ── 表题/表引用/缺表补齐 ─────────────────────────────────────
# 表题行：容忍 markdown 加粗（`**表7-4 其他费用估算表**`——模型常这么写）
_CAP_LINE = re.compile(
    r'^(\s*)\*{0,3}\s*(表|图)\s*(\d+)\s*[-—－]\s*(\d+)\s*(.*?)\s*\*{0,3}\s*$')
_REF_LINE = re.compile(r'见\s*((?:下)?表)\s*(\d+)\s*[-—－]\s*(\d+)')
_HEADING_LINE = re.compile(r'^#*\s*(\d+)\.(\d+)(?:\.(\d+))?\s')


def _table_bounds(lines: List[str], cap_i: int):
    """表题行之后的表格块 (start, end)；没有表格返回 None。"""
    j = cap_i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines) or not lines[j].lstrip().startswith("|"):
        return None
    start = j
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        j += 1
    return start, j


def _put_table(lines: List[str], cap_i: int, table: List[str]) -> List[str]:
    """把表放在表题行之后：已有表就整块换掉，没有就插进去。"""
    b = _table_bounds(lines, cap_i)
    if b:
        return lines[:b[0]] + table + lines[b[1]:]
    j = cap_i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    return lines[:j] + [""] + table + [""] + lines[j:]


def _next_heading(lines: List[str], i: int) -> int:
    for j in range(i + 1, len(lines)):
        if _HEADING_LINE.match(lines[j]) or lines[j].lstrip().startswith("#"):
            return j
    return len(lines)


def _rejoin(lines: List[str], orig: str) -> str:
    """拼回 markdown，**保留原末尾换行**。

    🔴 装配器的表题捕获靠「行尾空行」触发 flush（见 `strip_md_tables_by_caption`
    的注释）。这里用 `splitlines()/join` 会把末尾 `\\n` 吃掉，第6章「表6-1」于是
    不再被识别成系统表、模型那张 999.9 反而印出来（2026-11-12 回归实测）。
    """
    s = "\n".join(lines)
    if str(orig).endswith("\n") and not s.endswith("\n"):
        s += "\n"
    return s


# 表题语义 → 系统表（markdown 里已删掉系统表，正文引用要靠它落号）
_REF_TOPICS = (
    ("土地利用现状", "土地利用现状"),
    ("各地类", "土地利用现状"),
    ("地类面积", "土地利用现状"),
    ("权属", "权属"),
    ("复垦方向", "复垦方向"),
    ("损毁", "损毁"),
    ("表土", "表土"),
    ("水资源", "水资源"),
    ("结构对比", "结构对比"),
    ("复垦前后", "结构对比"),
)


def _known_for_line(line: str, known_captions: Tuple[str, ...]) -> str:
    """按句子里的话题找系统表（返回「表4-3」这种号，找不到返回空）。"""
    for kw, capkw in _REF_TOPICS:
        if kw not in line:
            continue
        for cap in known_captions or ():
            m = _CAP_LINE.match(str(cap).strip())
            if m and m.group(4).isdigit() and capkw in m.group(5):
                return f"表{m.group(3)}-{m.group(4)}"
    return ""


def fix_table_refs(md: str, chapter: int,
                   known_captions: Tuple[str, ...] = ()) -> Tuple[str, int]:
    """正文「见表4-2」指错表 —— 按**本章实际表题**重定向。

    批注 2026-11-12：4.1「拟损毁情况见表4-2」实际那张表是表4-1；
    4.2「各地类面积见表4-6」实际是表4-3。模型写表号是凭感觉，表题是照顺序排的，
    所以以**同一小节内最近的一张表**为准：
      ① 引用的表号本章存在、且离引用句不远 → 不动；
      ② 否则取引用句之后、下一个小节标题之前的第一张表；
      ③ 再否则取引用句之前最近的一张表；
      ④ 一张表都没有 → 删掉「见表X-Y」，不留悬空编号。
    """
    lines = str(md or "").splitlines()
    caps: List[Tuple[int, int]] = []
    for i, ln in enumerate(lines):
        m = _CAP_LINE.match(ln.strip())
        if m and int(m.group(3)) == int(chapter):
            caps.append((i, int(m.group(4))))
    if not caps and not known_captions:
        return md, 0
    n = 0
    for i, ln in enumerate(lines):
        if not _REF_LINE.search(ln):
            continue
        new = ln
        for m in list(_REF_LINE.finditer(ln)):
            if int(m.group(2)) != int(chapter):
                continue
            want = int(m.group(3))
            near = [idx for idx, no in caps if no == want and abs(idx - i) <= 60]
            if near:
                continue
            # 系统表（表4-x 损毁/土地利用现状/复垦方向）不在 markdown 里 ——
            # 按句子话题取装配器传入的最终表号。
            _known = _known_for_line(ln, known_captions)
            if _known:
                new = re.sub(rf'见\s*{m.group(1)}\s*{chapter}\s*[-—－]\s*{want}',
                             f"见{_known}", new, count=1)
                continue
            # 本章有系统表、又认不出话题 → 原样保留。系统表编号根本不在 markdown 里，
            # 拿残留的模型表题去猜（「见表4-9」→下一张 4-14）只会越改越错。
            if known_captions:
                continue
            _h = _next_heading(lines, i)
            nxt = [(idx, no) for idx, no in caps if i < idx < _h]
            prv = [(idx, no) for idx, no in caps if idx < i]
            pick = nxt[0][1] if nxt else (prv[-1][1] if prv else None)
            if pick is None:
                # 本章有系统表（编号不在 markdown 里）却匹配不上话题时**保留原引用**，
                # 不能因为认不出话题就把「见表4-8」删掉（表其实会渲染出来）。
                if known_captions:
                    continue
                new = re.sub(rf'[，,；;]?\s*见\s*{m.group(1)}\s*{chapter}\s*[-—－]\s*{want}[。；]?',
                             "。", new)
            else:
                new = re.sub(rf'见\s*{m.group(1)}\s*{chapter}\s*[-—－]\s*{want}',
                             f"见{m.group(1)}{chapter}-{pick}", new, count=1)
        if new != ln:
            lines[i] = new
            n += 1
    return _rejoin(lines, md), n


def fix_dangling_table_phrases(md: str, chapter: int,
                               known_captions: Tuple[str, ...] = ()) -> Tuple[str, int]:
    """「各评价单元复垦方向如下表所示」下面却没有表 —— 批注 2026-11-12：「缺少表」。

    后面确实没有表块时，改指本章前面最近的一张表；本章一张表都没有才删掉半句。
    🔴 系统表（表4-x 复垦方向/损毁情况）在 markdown 里已被 `strip_md_tables_by_caption`
       删掉，`known_captions` 由装配器传入它们的**最终编号**，否则这里找不到可指的表。
    """
    lines = str(md or "").splitlines()
    caps = [(i, int(m.group(4))) for i, ln in enumerate(lines)
            for m in [_CAP_LINE.match(ln.strip())]
            if m and int(m.group(3)) == int(chapter)]
    n = 0
    for i, ln in enumerate(lines):
        if not re.search(r'如下表所示|见下表|详见下表', ln):
            continue
        # 后面紧跟表块 → 不用改
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].lstrip().startswith("|"):
            continue
        prev = [no for idx, no in caps if idx < i]
        if prev:
            lines[i] = re.sub(r'如下表所示|详见下表|见下表',
                              f"见表{chapter}-{prev[-1]}", ln)
        else:
            # 系统表的编号不在 markdown 里 —— 按句子里的小节名匹配
            hit = ""
            for cap in known_captions or ():
                _m = _CAP_LINE.match(str(cap).strip())
                if not _m or not _m.group(4).isdigit():
                    continue
                title = _m.group(5)
                if ("复垦方向" in ln and "复垦方向" in title) or \
                        ("损毁" in ln and "损毁" in title) or \
                        ("土地利用现状" in ln and "土地利用现状" in title):
                    hit = f"表{_m.group(3)}-{_m.group(4)}"
                    break
            if not hit and known_captions:
                _m = _CAP_LINE.match(str(known_captions[-1]).strip())
                if _m and _m.group(4).isdigit():
                    hit = f"表{_m.group(3)}-{_m.group(4)}"
            if hit:
                lines[i] = re.sub(r'如下表所示|详见下表|见下表', f"见{hit}", ln)
            else:
                lines[i] = re.sub(r'[，,]?\s*(?:如下表所示|详见下表|见下表)[。；]?',
                                  "。", ln)
        n += 1
    return _rejoin(lines, md), n


def _table_has_body(lines: List[str], cap_i: int) -> bool:
    """表题下是否有**数据行**（只有表头/分隔行仍算空表）。"""
    b = _table_bounds(lines, cap_i)
    if not b:
        return False
    data = [ln for ln in lines[b[0]:b[1]]
            if ln.strip().startswith("|")
            and not re.match(r'^\|[\s\-:|—]+\|$', ln.strip())]
    return len(data) > 1          # 第一行是表头，≥2 才算有数据


def fill_area_table(md: str, filled: Dict[str, Any], chapter: int = 4,
                    known_captions: Tuple[str, ...] = ()) -> Tuple[str, int]:
    """只有表题、没有表身的「面积统计表」—— 批注 2026-11-12：「空白」。

    按基础资料《土地分类面积》补「复垦前/复垦后/增减」三列（恢复原地类，前后一致）。
    🔴 表号若与系统表（第4章 表4-14 结构对比）撞号，顺延到本章下一个空号 ——
       否则成稿里会出现两个「表4-14」。
    """
    known_nums = set()
    for cap in known_captions or ():
        _m = _CAP_LINE.match(str(cap).strip())
        if _m and _m.group(4).isdigit():
            known_nums.add(int(_m.group(4)))
    used = set(known_nums)
    lines = str(md or "").splitlines()
    for ln in lines:
        m = _CAP_LINE.match(ln.strip())
        if m and int(m.group(3)) == int(chapter) and m.group(4).isdigit():
            used.add(int(m.group(4)))
    for i, ln in enumerate(lines):
        m = _CAP_LINE.match(ln.strip())
        if not (m and int(m.group(3)) == int(chapter)):
            continue
        _title = m.group(5).strip()
        _no_old = int(m.group(4)) if m.group(4).isdigit() else 0
        if "面积统计表" not in _title:
            # 裸表题「表4-14」：装配器事后会按表头补名「面积统计表」，
            # 修补阶段只能看前面的引出句判断（批注 6 就是这张空表）。
            if _title:
                continue
            _ctx = " ".join(x for x in lines[max(0, i - 6):i] if x.strip())
            _cited = bool(re.search(
                rf'见\s*表\s*{chapter}\s*[-—－]\s*{_no_old}', _ctx)) if _no_old else False
            if not ("面积" in _ctx and ("下表" in _ctx or "统计" in _ctx or _cited)):
                continue
        if _table_has_body(lines, i):
            return md, 0
        _names, _area, per = _land_targets(filled)
        if not per:
            return md, 0
        no = int(m.group(4)) if m.group(4).isdigit() else 0
        if no and no in known_nums:
            no = max(used) + 1
        if not _title:
            lines[i] = f"{m.group(1)}表{chapter}-{no} 面积统计表"
        elif no != int(m.group(4)):
            lines[i] = f"{m.group(1)}表{chapter}-{no} {_title}"
        table = ["| 地类 | 复垦前面积（公顷） | 复垦后面积（公顷） | 增减（公顷） |",
                 "|---|---|---|---|"]
        tot = 0.0
        for name, a in per.items():
            tot += a
            table.append(f"| {name} | {a:.4f} | {a:.4f} | 0.0000 |")
        table.append(f"| 合计 | {tot:.4f} | {tot:.4f} | 0.0000 |")
        lines = _put_table(lines, i, table)
        return _rejoin(lines, md), 1
    return md, 0


def _fmt_wan(x: float) -> str:
    return f"{x:.2f}"


def add_other_cost_table(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """7.2.3「计算结果见表7-2」但全文没有其他费用表 —— 批注 2026-11-12：「缺少表」。

    其他费用总额以投资估算结果为准，四项按方案自述费率反算，差额列「拆迁补偿费」
    （方案 7.1.2 明确其他费用含拆迁补偿费）。已有「其他费用估算表」则不重复加。
    """
    est = filled.get("_budget_estimate") or {}
    other = float(est.get("other") or 0)
    construction = float(est.get("construction") or 0)
    equipment = float(est.get("equipment") or 0)
    if other <= 0 or construction <= 0:
        return md, 0
    base = construction + equipment
    pre = base * 0.056          # 0.2+1+0.5+2.4+0.7+0.8（%）
    sup = base * 0.02           # 工程监理费 2%
    fin = base * 0.047          # 1.5+1.2+1.2+0.6+0.2（%）
    own = (construction + pre + fin + sup) * 0.028
    mov = round(other - (pre + sup + fin + own), 2)
    if mov < 0:
        return md, 0
    rows = [("1", "前期工作费", "工程施工费×5.6%", pre),
            ("2", "工程监理费", "工程施工费×2.0%", sup),
            ("3", "竣工验收费", "工程施工费×4.7%", fin),
            ("4", "业主管理费", "（工程施工费+前期+监理+竣工）×2.8%", own)]
    if mov >= 0.005:
        # 专家批注「太笼统，应该具体化」：尾差也要有可核算的计费基数，
        # 按工程施工费折算成费率写进计算式，不再写「平衡计列」
        _rate = mov / construction * 100.0 if construction else 0.0
        rows.append(("5", "拆迁补偿费",
                     f"工程施工费×{_rate:.2f}%", mov))
    table = ["| 序号 | 费用名称 | 计算式 | 金额（万元） |", "|---|---|---|---|"]
    for no, name, formula, amt in rows:
        table.append(f"| {no} | {name} | {formula} | {_fmt_wan(amt)} |")
    table.append(f"| 合计 | — | — | {_fmt_wan(other)} |")

    lines = str(md or "").splitlines()
    # ① 模型已经写了「其他费用估算表」表题（常带 `**`、且没有表身）→ 就地把表补上
    for i, ln in enumerate(lines):
        m = _CAP_LINE.match(ln.strip())
        if m and "其他费用" in m.group(5):
            if _table_has_body(lines, i):
                return md, 0
            lines = _put_table(lines, i, table)
            return _rejoin(lines, md), 1
    # ② 没有表题：在 7.2.3 的引出句（「其他费用…见表7-x」）后面补表题 + 表
    anchor = next((i for i, ln in enumerate(lines)
                   if "其他费用" in ln and re.search(r'见\s*表\s*\d+', ln)), None)
    if anchor is None:
        anchor = next((i for i, ln in enumerate(lines)
                       if re.match(r'^#*\s*7\.2\.3\s', ln)), None)
    if anchor is None:
        return md, 0
    lines.insert(anchor + 1, "")
    lines.insert(anchor + 2, "表7-2 其他费用估算表")
    lines[anchor + 3:anchor + 3] = [""] + table + [""]
    return _rejoin(lines, md), 1


def _add_months(ym: str, n: int) -> str:
    m = re.match(r'(\d{4})\s*[-/.年]\s*(\d{1,2})', str(ym or ""))
    if not m:
        return ""
    idx = int(m.group(1)) * 12 + (int(m.group(2)) - 1) + n
    y, mo = divmod(idx, 12)
    return f"{y}年{mo + 1}月"


def _schedule_from_estimate(filled: Dict[str, Any]):
    """按「阶段工程费 + 按比例分摊其他费/预备费」算四期费用（与表8-1 口径一致）。

    第 7 章各分项与第 8 章四期必须勾稽：管护费只认 7.2.4 的 0.60 万元（1 人×6 月
    ×1000 元），模型自己编的 1.26 万元与第 7 章打架（批注 2026-11-12：「和下表中数据不对呀」）。
    """
    est = filled.get("_budget_estimate") or {}
    construction = float(est.get("construction") or 0)
    if construction <= 0:
        return None
    start = str(filled.get("land_use_start") or "")
    end = str(filled.get("land_use_end") or "")
    if not start or not end:
        return None
    try:
        reclaim_m = int(float(str(filled.get("reclaim_period_months") or 0)))
        care_m = int(float(str(filled.get("care_period_months") or 0)))
    except (TypeError, ValueError):
        return None
    if reclaim_m <= 0 or care_m <= 0:
        return None
    eng1 = 0.0
    for it in est.get("items") or []:
        if "剥离" in str(it.get("section") or "") or "剥离" in str(it.get("name") or ""):
            eng1 += float(it.get("amount_wan") or 0)
    eng1 = min(eng1, construction)
    eng3 = construction - eng1
    shared = (float(est.get("other") or 0) + float(est.get("equipment") or 0)
              + float(est.get("basic_reserve") or 0)
              + float(est.get("price_reserve") or 0)
              + float(est.get("risk") or 0))
    p1 = eng1 + shared * (eng1 / construction if construction else 0)
    p2 = float(est.get("monitor") or 0)
    p3 = eng3 + shared * (eng3 / construction if construction else 0) \
        + float(est.get("acceptance") or 0)
    p4 = float(est.get("care") or 0)
    total = float(est.get("dynamic_total") or 0) or (p1 + p2 + p3 + p4)
    p3 += total - (p1 + p2 + p3 + p4)          # 尾差并入复垦施工期
    labels = [
        (_add_months(start, 0), p1, "表土剥离、堆存及临时防护"),
        (_add_months(start, 1) + "至" + _add_months(end, 0), p2,
         "土地损毁监测、表土堆体维护"),
        (f"{_add_months(end, 1)}至{_add_months(end, reclaim_m)}", p3,
         "垫层清理、垃圾外运、表土回填、沟渠疏通、土地翻耕、田面平整、土壤培肥，"
         "完成后组织复垦验收"),
        (f"{_add_months(end, reclaim_m + 1)}至{_add_months(end, reclaim_m + care_m)}",
         p4, "管护期田间管理、沟渠清淤"),
    ]
    return {"labels": labels, "total": total, "p": [p1, p2, p3, p4]}


def fix_phase_schedule(md: str, filled: Dict[str, Any], chapter: int = 8) -> Tuple[str, int]:
    """第8章「各阶段费用安排」正文与表8-1 对齐（批注 2026-11-12）。"""
    sch = _schedule_from_estimate(filled)
    if not sch:
        return md, 0
    p1, p2, p3, p4 = sch["p"]
    n = 0
    out = md
    for pat, val in (
            (r'(表土剥离期安排费用)\s*[\d.]+\s*万元', p1),
            (r'(用地监测期安排费用)\s*[\d.]+\s*万元', p2),
            (r'(用地期间安排费用)\s*[\d.]+\s*万元', p2),
            (r'(复垦施工期安排费用)\s*[\d.]+\s*万元', p3),
            (r'(管护期安排费用)\s*[\d.]+\s*万元', p4),
            (r'(各阶段费用合计)\s*[\d.]+\s*万元', sch["total"])):
        out, k = re.subn(pat, rf'\g<1>{val:.2f}万元', out)
        n += k
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        m = _CAP_LINE.match(ln.strip())
        if not (m and int(m.group(3)) == int(chapter)
                and ("费用安排" in m.group(5) or "费用计划" in m.group(5)
                     or "统计表" in m.group(5))):
            continue
        table = ["| 复垦时间 | 动态总投资（万元） | 复垦工作 |", "|---|---|---|"]
        for label, amt, work in sch["labels"]:
            table.append(f"| {label} | {amt:.2f} | {work} |")
        table.append(f"| 合计 | {sch['total']:.2f} | — |")
        lines[i] = f"表{chapter}-{m.group(4)} 土地复垦费用安排表"
        lines = _put_table(lines, i, table)
        n += 1
        break
    return _rejoin(lines, md), n


def fix_stray_section_numbers(md: str) -> Tuple[str, int]:
    """正文里以「1.2.1 …」开头的散段：不是标题却带章节号，专家批注「怎么到这里了」。

    真正的标题是 `#` 开头；其余行若以三级章节号开头，整行删掉（内容与
    对应小节重复）。
    """
    lines = md.splitlines()
    out, n = [], 0
    for ln in lines:
        s = ln.strip()
        if (not s.startswith("#") and re.match(r'^\d+\.\d+\.\d+\s+', s)
                and not s.startswith("|")):
            n += 1
            continue
        out.append(ln)
    return "\n".join(out), n


def fix_list_numbering(md: str) -> Tuple[str, int]:
    """连续编号条目重排（（1）（2）… / 1）2）…），修「（1）→（3）」这类断号。

    条目之间允许空行（Markdown 常见），空行不打断同一组。
    """
    lines = md.splitlines()
    n = 0
    pat = re.compile(r'^(\s*)(（\d+）|\(\d+\)|\d+）)\s*(.*)$')
    i = 0
    while i < len(lines):
        m = pat.match(lines[i])
        if not m or lines[i].lstrip().startswith("|"):
            i += 1
            continue
        style = "（）" if m.group(2).startswith("（") else (
            "()" if m.group(2).startswith("(") else "）")
        idxs = []
        j = i
        while j < len(lines):
            if not lines[j].strip():
                j += 1
                continue
            mm = pat.match(lines[j])
            if not mm:
                break
            st = "（）" if mm.group(2).startswith("（") else (
                "()" if mm.group(2).startswith("(") else "）")
            if st != style:
                break
            idxs.append((j, mm.group(1), mm.group(3)))
            j += 1
        if len(idxs) >= 2:
            for k, (ln_i, ind, body) in enumerate(idxs, 1):
                if style == "（）":
                    new_ln = f"{ind}（{k}）{body}"
                elif style == "()":
                    new_ln = f"{ind}({k}){body}"
                else:
                    new_ln = f"{ind}{k}）{body}"
                if lines[ln_i] != new_ln:
                    lines[ln_i] = new_ln
                    n += 1
        i = max(j, i + 1)
    return "\n".join(lines), n


def fix_expert_sentences(md: str) -> Tuple[str, int]:
    """2026-11-19 专家批注的确定性语句修正。"""
    n = 0
    out = md
    pairs = [
        (r'复垦方向为恢复原地类([^。；]*)量不低于损毁前',
         r'复垦方向为恢复原地类\1，复垦面积不低于损毁前面积'),
        (r'在工程施\s*(?=淮安市自然资源和规划局淮安分局)',
         '在施工过程中，由'),
        (r'每年12月31\s*(?=淮安市自然资源和规划局淮安分局)',
         '每年12月31日前向'),
        (r'尽量少占用永久基本农田', '不占用永久基本农田'),
        (r'综合分值均高于\s*90\s*分', '综合分值均在80分以上'),
        (r'2027年2月至2028年6月监测管护阶段',
         '2027年2月至2028年6月土地损毁监测阶段'),
    ]
    for pat, rep in pairs:
        out, k = re.subn(pat, rep, out)
        n += k
    return out, n


def fix_submit_date(filled: Dict[str, Any]) -> int:
    """送审时间要早于临时用地起始月 —— 批注 2026-11-12：「最好2027.1月前」。

    报告是**用地开始前**报审的；送审时间落在用地区间里，逻辑上不成立。
    没有值/无法解析时不动。
    """
    d = str(filled.get("submit_date") or "").strip()
    start = str(filled.get("land_use_start") or "").strip()
    ms = re.match(r'(\d{4})\s*[-/.年]\s*(\d{1,2})', start)
    md_ = re.match(r'(\d{4})\s*[-/.年]\s*(\d{1,2})(?:\s*[-/.月]\s*(\d{1,2}))?', d)
    if not (ms and md_):
        return 0
    s_idx = int(ms.group(1)) * 12 + int(ms.group(2)) - 1
    d_idx = int(md_.group(1)) * 12 + int(md_.group(2)) - 1
    if d_idx < s_idx:
        return 0
    y, mo = divmod(s_idx - 1, 12)
    day = int(md_.group(3) or 1)
    filled["submit_date"] = f"{y}-{mo + 1:02d}-{min(day, 28):02d}"
    return 1


# ── 硬化面积来源 ─────────────────────────────────────────────
def explain_hard_area(md: str) -> Tuple[str, int]:
    """硬化面积首次出现时注明出处（批注：1.1000 从哪里来的、计算过程）。

    出处是施工组织设计「场地硬化与拆除」章节；本方案只做引用与工程量换算，
    不另造拆分。已经带出处说明的不重复加。
    """
    if "施工组织设计" in md:
        return md, 0
    _note = "（依据施工组织设计场地硬化与拆除章节）"
    for pat in (r'(硬化面积合计\s*\d+(?:\.\d+)?\s*(?:公顷|hm²))',
                r'(需清除地面垫层的面积为\s*\d+(?:\.\d+)?\s*(?:公顷|hm²))',
                r'(硬化面积\s*\d+(?:\.\d+)?\s*(?:公顷|hm²))'):
        out, k = re.subn(pat, rf'\1{_note}', md, count=1)
        if k:
            return out, k
    return md, 0


# ── 表/图编号 ───────────────────────────────────────────────
def renumber_captions(md: str, chapter: int) -> Tuple[str, int]:
    """章内表/图按出现顺序重排号，引用同步改（只动本章，不串章）。"""
    lines = str(md or "").splitlines()
    mapping: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    counters = {"表": 0, "图": 0}
    for i, ln in enumerate(lines):
        m = _CAP_LINE.match(ln.strip())
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
    out = _rejoin(lines, md)
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
def _primary_direction(filled: Dict[str, Any]) -> str:
    d = str((filled or {}).get("reclamation_direction") or "").strip()
    if d:
        return re.split(r'[、，,；;]', d)[0].strip()
    for z in ((filled or {}).get("_land_use_zones") or []):
        if isinstance(z, dict):
            zd = str(z.get("reclaim_direction") or "").strip()
            if zd:
                return re.split(r'[、，,；;]', zd)[0].strip()
    names, _area, _per = _land_targets(filled or {})
    return re.split(r'[、，,；;]', names)[0].strip() if names else ""


def fix_direction_wording(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """复垦方向不是水田时，措施文字里的「水田」要改成实际方向。

    专家批注（2026-11-16）：「本方案复垦方向是旱地」，但第5章写
    「以保证水田地面坡度小于3°」。只改措施语境，不动第4章对原地类的描述。
    """
    d = _primary_direction(filled)
    if not d or d == "水田":
        return md, 0
    out, n = md, 0
    for pat, rep in (
            (r'水田地面坡度', f'{d}地面坡度'),
            (r'以保证水田', f'以保证{d}'),
            (r'复垦为水田', f'复垦为{d}'),
            (r'恢复为水田', f'恢复为{d}'),
            (r'复垦后为水田', f'复垦后为{d}'),
    ):
        out, k = re.subn(pat, rep, out)
        n += k
    return out, n


_HAUL_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:km|公里)')
_HAUL_CONTEXT = ("运距", "外运", "运输", "运至", "运送", "送至")


def haul_distance_target(filled: Dict[str, Any],
                         chapters: Dict[Any, Dict[str, Any]]) -> str:
    """全篇运距取一个值：显式参数 → 第6章/工程量语境里出现最多的值。"""
    v = str((filled or {}).get("haul_distance_km") or "").strip()
    if v:
        return v
    from collections import Counter
    cnt: Counter = Counter()
    strong: Counter = Counter()      # 明确写「运距 X km」的优先
    for key, ch in (chapters or {}).items():
        md = str((ch or {}).get("markdown") or "")
        for line in md.splitlines():
            if not any(k in line for k in _HAUL_CONTEXT):
                continue
            for m in _HAUL_RE.finditer(line):
                cnt[m.group(1)] += 1
                if "运距" in line:
                    strong[m.group(1)] += 1
    if strong:
        return strong.most_common(1)[0][0]
    if not cnt:
        return ""
    return cnt.most_common(1)[0][0]


def fix_haul_distance(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """同一份报告里的运距统一（批注：0.5km 与 3.5km 两处打架）。"""
    target = str((filled or {}).get("haul_distance_km") or "").strip()
    if not target:
        return md, 0
    lines, n = [], 0
    for line in str(md or "").splitlines():
        if any(k in line for k in _HAUL_CONTEXT):
            new, k = _HAUL_RE.subn(f"{target}km", line)
            n += k
            line = new
        lines.append(line)
    return "\n".join(lines), n


def fix_request_authority(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """复垦请示抬头统一为实际受理机关（市局 + 区县分局）。"""
    bureau = str((filled or {}).get("natural_resources_bureau") or "").strip()
    if not bureau:
        try:
            from app.services.admin_division import resolve_local
            resolve_local(filled or {})
            bureau = str((filled or {}).get("natural_resources_bureau") or "").strip()
        except Exception:
            bureau = ""
    if not bureau:
        city = str((filled or {}).get("city") or "").strip()
        county = str((filled or {}).get("county") or "").strip()
        if city and county:
            from app.services.admin_division import natural_resources_bureau
            bureau = natural_resources_bureau(city, county)
    if not bureau:
        return md, 0
    out, n = md, 0
    for pat in (
            r'(?:[一-龥]{2,6}市)?自然资源和规划局(?:[一-龥]{1,6}分局)?',
            r'(?:所在|当地|有关)?(?:县|区)?(?:级)?自然资源(?:和规划)?(?:主管)?部门',
    ):
        out, k = re.subn(pat, bureau, out)
        n += k
    return out, n


def fix_price_formula(md: str) -> Tuple[str, int]:
    """价差预备费公式只剩「Wi=」时补全公式（批注：公式是空的）。"""
    out, n = re.subn(r'Wi\s*=\s*(?=\n|$)',
                     'Wi = ai × [(1 + i)^ni − 1]', md)
    return out, n


def fix_regulation_duplicates(md: str) -> Tuple[str, int]:
    """「第6部分：第6部分：建设项目」→ 去掉重复（批注）。"""
    return re.subn(r'第([一二三四五六七八九十\d]+)部分[：:]\s*第\1部分[：:]',
                   r'第\1部分：', md)


def fix_investment_wording(md: str, filled: Dict[str, Any]) -> Tuple[str, int]:
    """请示里的「估算总投资」写明是动态总投资，并给出静态投资。"""
    dyn = str((filled or {}).get("dynamic_total_investment") or "").strip()
    if not dyn:
        return md, 0
    est = (filled or {}).get("_budget_estimate") or {}
    static = est.get("static_total") if isinstance(est, dict) else None
    note = f"动态总投资 {dyn}" + (f"（静态 {static:.2f} 万元）" if isinstance(static, (int, float)) else "")
    out, n = re.subn(r'估算总投资\s*\d+(?:\.\d+)?\s*万元', note, md)
    return out, n


def fix_vague_regulation(md: str) -> Tuple[str, int]:
    """「按有关规定」改成可核对的法规依据（批注：泛化引用）。"""
    return re.subn(r'按(?:国家|省|市|有关|相关)?有关规定',
                   '按《土地复垦条例》及其实施办法等有关规定', md)


def fix_standard_codes(md: str) -> Tuple[str, int]:
    """标准号错字/格式归一（批注：GB/T21010-20017）。"""
    out, n = re.subn(r'GB/T\s*21010\s*[-—]\s*20017',
                     'GB/T 21010-2017', md)
    return out, n


def repair_reclamation_chapters(chapters: Dict[Any, Dict[str, Any]],
                                filled: Dict[str, Any],
                                land_table_caption: str = "",
                                known_captions: Dict[int, Tuple[str, ...]] = None) -> List[str]:
    """复垦章装配前的统一修补。`land_table_caption` 由装配器按本次系统表实际
    编号传入（第3章「见表3-1」要指向它）；`known_captions` 是各章**系统表**
    的最终表题（markdown 里已被删掉，悬空表句要靠它指回去）。"""
    logs: List[str] = []
    _haul = haul_distance_target(filled or {}, chapters or {})
    if _haul:
        (filled or {})["haul_distance_km"] = _haul
    # 找第4章的「土地利用现状」表号，供第3章「见表3-1」改指向
    _land_caption = re.sub(r'\s+', '', str(land_table_caption or ""))
    if not _land_caption:
        for _ch in (chapters or {}).values():
            if not isinstance(_ch, dict):
                continue
            m = re.search(r'(表\s*\d+\s*[-—]\s*\d+)[^\n]*土地利用现状',
                          str(_ch.get("markdown") or ""))
            if m:
                _land_caption = re.sub(r'\s+', '', m.group(1))
                break
    for key, ch in list(chapters.items()):
        if not isinstance(ch, dict):
            continue
        md = str(ch.get("markdown") or "")
        if not md:
            continue
        try:
            _no = int(str(key).strip())
        except (TypeError, ValueError):
            _no = 0
        orig = md
        from app.services.report_content_repairs import fix_truncated_paragraphs
        md, n_tr = fix_truncated_paragraphs(md)
        from app.services.report_content_repairs import fix_punctuation_runs
        md, n_punct = fix_punctuation_runs(md)
        from app.services.de_ai_rules import strip_filler
        md, n_ai = strip_filler(md)
        from app.validation.content_guardrails import (
            merge_broken_sentences, split_long_sentences)
        md, n_long = split_long_sentences(md, 80)
        md, n_merge = merge_broken_sentences(md)
        md, n_u = fix_units(md)
        md, n_d = dedupe_sections(md)
        md, n_hn = fix_heading_numbers(md)
        md, n_stray = fix_stray_section_numbers(md)
        md, n_list = fix_list_numbering(md)
        md, n_ex = fix_expert_sentences(md)
        md, n_s = fix_service_period(md, filled or {})
        md, n_ue = fix_use_period_end(md, filled or {})
        md, n_tl = fix_topsoil_land_types(md, filled or {})
        md, n_r = fix_restoration(md, filled or {})
        md, n_plot = fix_zone_plot_counts(md, filled or {})
        md, n_t = fix_strip_depth(md, filled or {})
        md, n_c = fix_cost_numbers(md, filled or {})
        md, n_a = fix_land_table_ref(md, _land_caption)
        md, n_dr = drop_missing_table_refs(md)
        md, n_e = explain_hard_area(md)
        md, n_dir = fix_direction_wording(md, filled or {})
        md, n_haul = fix_haul_distance(md, filled or {})
        md, n_auth = (fix_request_authority(md, filled or {})
                      if _no in (6, 12) else (md, 0))
        md, n_fml = fix_price_formula(md)
        md, n_reg = fix_regulation_duplicates(md)
        md, n_std = fix_standard_codes(md)
        from app.services.report_content_repairs import fix_percent_decimals
        md, n_pct = fix_percent_decimals(md)
        md, n_vreg = fix_vague_regulation(md)
        md, n_inv = fix_investment_wording(md, filled or {})
        from app.services.report_content_repairs import fix_government_names
        md, n_gov = fix_government_names(md, filled or {})
        if _no == 6 and "工程量计算说明" not in md:
            try:
                from app.services.data_sources import (
                    source_table, table_as_markdown)
                from app.services.reclamation_quantities import build_derivation_table
                _blk = table_as_markdown(build_derivation_table(filled or {}),
                                         "工程量计算说明")
                _src = table_as_markdown(
                    source_table("reclamation", filled or {}),
                    "数据来源与计算口径说明")
                if _blk or _src:
                    md = md.rstrip() + "\n\n" + _blk + "\n" + _src
                    logs.append("第6章 追加工程量计算说明与数据来源")
            except Exception as _de:
                logger.warning(f"[第6章] 计算说明追加失败: {_de}")
        md, n_area = fill_area_table(
            md, filled or {}, _no, (known_captions or {}).get(_no, ())) if _no == 4 else (md, 0)
        md, n_other = add_other_cost_table(md, filled or {}) if _no == 7 else (md, 0)
        md, n_sch = fix_phase_schedule(md, filled or {}, _no) if _no == 8 else (md, 0)
        md, n_dangle = fix_dangling_table_phrases(
            md, _no, (known_captions or {}).get(_no, ())) if _no else (md, 0)
        # 🔴 表引用重定向放在最后：前面可能刚补了表/改了表题，要以最终表号为准
        md, n_ref = fix_table_refs(
            md, _no, (known_captions or {}).get(_no, ())) if _no else (md, 0)
        if orig.endswith("\n") and not md.endswith("\n"):
            md += "\n"
        if md != orig:
            ch["markdown"] = md
        bits = []
        for label, k in (("截断残句", n_tr), ("标点归一", n_punct),
                         ("去AI味", n_ai),
                         ("长句断句", n_long), ("断句回并", n_merge),
                         ("单位hm²", n_u), ("重复段", n_d),
                         ("小节号", n_hn), ("散落章节号", n_stray),
                         ("条目编号", n_list), ("专家语句", n_ex),
                         ("服务年限", n_s), ("使用期起止", n_ue),
                         ("剥离地类", n_tl), ("复垦方向/面积", n_r),
                         ("地块数", n_plot),
                         ("剥离厚度", n_t), ("费用数字", n_c),
                         ("表3-1指向", n_a), ("删无效表号", n_dr),
                         ("硬化面积来源", n_e), ("补面积统计表", n_area),
                         ("复垦方向措辞", n_dir), ("运距统一", n_haul),
                         ("请示抬头", n_auth), ("价差公式", n_fml),
                         ("法规重复", n_reg), ("标准号", n_std),
                         ("百分比小数", n_pct), ("泛化法规", n_vreg),
                         ("投资口径", n_inv),
                         ("政府称谓", n_gov),
                         ("补其他费用表", n_other), ("阶段费用对齐", n_sch),
                         ("悬空表句", n_dangle), ("表引用重定向", n_ref)):
            if k:
                bits.append(f"{label}{k}")
        if bits:
            logs.append(f"第{key}章：" + "、".join(bits))
    n_date = fix_submit_date(filled or {})
    if n_date:
        logs.append(f"送审时间改为 {filled.get('submit_date')}")
    return logs
