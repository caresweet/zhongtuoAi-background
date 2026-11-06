# -*- coding: utf-8 -*-
"""读取用户提供的《第七章投资估算》Excel（如「副本1管道费用----井神 - 李天伟.xlsx」）。

业主 2026-11-05 口径：
  · 第7章 表7-3 / 表7-4 / 表7-5 的数据**参照用户上传的投资估算 Excel**；
  · 表7-3 = 表6-1 的工程量 × Excel 里的综合单价（Excel 本身是人工按复垦工作估的）；
  · Excel 每个项目都不同，属于**用户资料**的一部分；
  · 价差预备费：Excel 有值就读 Excel，没有才按 5% 公式算。

Excel 结构（样本 Sheet3 / Sheet2）：

    Sheet3  工程施工费估算表
        序号｜工程或费用名称｜单位｜数量｜综合单价（元）｜工程施工费（万元）
        一 / 土壤重构工程 /（一）土壤剥离工程 / 1 表土剥离 … / 合计

    Sheet2  其他费用 + 投资估算
        一 工程施工费 / 二 设备购置费 / 三 其他费用（（一）前期工作费 … （五）业主管理费）
        四 监测与管护费（复垦监测费、管护费、土地污染监测、耕地质量评定、复垦验收报告编制）
        五 预备费（基本预备费、价差预备费、风险金）/ 六 静态总投资 / 七 动态总投资

读不出来就返回 None（调用方回落到内置单价库 / budget-maker，不编数）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _s(v: Any) -> str:
    return str(v or "").replace("\n", "").replace("\r", "").strip()


def _read_grids(path: str | Path) -> List[List[List[Any]]]:
    """所有工作表 → 二维数组（.xls 走 xlrd，.xlsx 走 openpyxl）。"""
    p = Path(path)
    grids: List[List[List[Any]]] = []
    if p.suffix.lower() == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(p))
        for sh in wb.sheets():
            grids.append([[sh.cell_value(r, c) for c in range(sh.ncols)]
                          for r in range(sh.nrows)])
    else:
        from openpyxl import load_workbook
        wb = load_workbook(str(p), data_only=True, read_only=True)
        for ws in wb.worksheets:
            grids.append([[ws.cell(row=r, column=c).value
                           for c in range(1, (ws.max_column or 0) + 1)]
                          for r in range(1, (ws.max_row or 0) + 1)])
        wb.close()
    return grids


# ═══════════════════════════════════════════════════════════════
# 解析
# ═══════════════════════════════════════════════════════════════
def _engineering_cols(header: Sequence[Any]) -> Optional[Dict[str, int]]:
    """表7-3 表头 → 列号。缺「综合单价」的不认。"""
    cols: Dict[str, int] = {}
    for i, v in enumerate(header):
        t = _s(v)
        if not t:
            continue
        if "序号" in t:
            cols.setdefault("no", i)
        elif "名称" in t:
            cols.setdefault("name", i)
        elif t.startswith("单位"):
            cols.setdefault("unit", i)
        elif "数量" in t:
            cols.setdefault("qty", i)
        elif "综合单价" in t:
            cols.setdefault("price", i)
        elif "工程施工费" in t:
            cols.setdefault("amount", i)
    return cols if ("name" in cols and "price" in cols) else None


def _parse_engineering(grid: List[List[Any]]) -> Optional[dict]:
    hdr_i, cols = None, None
    for i, row in enumerate(grid[:30]):
        c = _engineering_cols(row)
        if c:
            hdr_i, cols = i, c
            break
    if hdr_i is None:
        return None

    rows: List[dict] = []
    total: Optional[float] = None
    for row in grid[hdr_i + 1:]:
        def cell(k: str) -> Any:
            j = cols.get(k)
            return row[j] if j is not None and j < len(row) else None

        no, name = _s(cell("no")), _s(cell("name"))
        if not name:
            continue
        unit, qty = _s(cell("unit")), _num(cell("qty"))
        price, amount = _num(cell("price")), _num(cell("amount"))
        if name.replace(" ", "") in ("合计", "总计"):
            total = amount if amount is not None else total
            break
        is_section = not unit and qty is None and price is None
        if not is_section and amount is None and qty is not None and price is not None:
            amount = round(qty * price / 10000.0, 4)
        rows.append({"no": no, "name": name, "unit": unit,
                     "qty": qty, "price": price, "amount_wan": amount,
                     "section": is_section})
    if not rows:
        return None
    # 段小计缺失时用其下分项求和
    for i, r in enumerate(rows):
        if not r["section"] or r["amount_wan"] is not None:
            continue
        sub = 0.0
        for r2 in rows[i + 1:]:
            if r2["section"]:
                break
            sub += float(r2["amount_wan"] or 0.0)
        r["amount_wan"] = round(sub, 4) if sub else None
    if total is None:
        total = round(sum(float(r["amount_wan"] or 0.0)
                          for r in rows if not r["section"]), 4)
    return {"rows": rows, "total_wan": total}


def _pick_name_col(grid: Sequence[Sequence[Any]]) -> int:
    """投资估算表里「名称」所在列：取命中关键字最多的一列。"""
    keys = ("工程施工费", "其他费用", "监测与管护费", "预备费", "静态总投资",
            "动态总投资", "前期工作费", "工程监理费", "竣工验收费", "业主管理费")
    best, best_hits = 1, -1
    for c in range(min(4, max((len(r) for r in grid), default=0))):
        hits = sum(1 for r in grid if c < len(r) and any(k in _s(r[c]) for k in keys))
        if hits > best_hits:
            best, best_hits = c, hits
    return best


def _row_amount(row: Sequence[Any], name_col: int) -> Optional[float]:
    """取该行**最右**的数字（金额列），跳过名称/公式列。"""
    for v in reversed(list(row)):
        if isinstance(v, str) and ("=" in v or "×" in v):
            continue
        n = _num(v)
        if n is not None:
            return n
    return None


_SEC_NO = re.compile(r'^[（(][一二三四五六七八九十]+[)）]$')
_CN_ONLY = re.compile(r'^[一二三四五六七八九十]+$')


def _parse_summary(grid: List[List[Any]]) -> Optional[dict]:
    """Sheet2：一~七 + 其他费用明细 + 监测/预备费明细。

    Sheet2 的列**不固定**（有的行名称在 B 列、有的在 C 列），逐行按内容识别。
    """
    flat: Dict[str, dict] = {}
    order: List[dict] = []
    section = ""
    for row in grid:
        texts = [(i, _s(v)) for i, v in enumerate(row) if _s(v)]
        if not texts:
            continue
        formula = next((t for _, t in texts if "=" in t or "×" in t), "")
        # 名称：跳过纯数字、公式、括号序号（括号序号当 no）
        no, name = "", ""
        for _i, t in texts:
            if "=" in t or "×" in t:
                continue
            if re.fullmatch(r'-?\d+(?:\.\d+)?', t):
                if not no:
                    no = t
                continue
            if _SEC_NO.match(t) or _CN_ONLY.match(t):
                no = t
                continue
            name = t
            break
        if not name:
            continue
        name = name.rstrip("：:").strip()
        amount = _row_amount(row, 0)
        # 金额不能取到「序号」或百分比之类的数字：取最后一个数值
        item = {"no": no, "name": name, "amount_wan": amount,
                "formula": formula, "section": section}

        key = name.replace(" ", "")
        for k in ("工程施工费", "设备购置费", "其他费用", "监测与管护费", "预备费",
                  "静态总投资", "动态总投资"):
            if key.startswith(k):
                # 同名多行时取**有金额**的那一行（Sheet2 有「三 其他费用：」空行）
                if k not in flat or (flat[k].get("amount_wan") is None
                                     and amount is not None):
                    flat[k] = item
                break

        _is_section = bool(_SEC_NO.match(no or "")) and name not in ("",)
        if _is_section:
            section = name
        order.append(item)

    def amt(k: str) -> Optional[float]:
        it = flat.get(k)
        return float(it["amount_wan"]) if it and it["amount_wan"] is not None else None

    if amt("工程施工费") is None:
        return None

    def sub(*name_kws: str) -> Optional[float]:
        # 先精确匹配（「管护费」不能命中「监测与管护费」），再包含匹配
        for kw in name_kws:
            for it in order:
                if it["name"].replace(" ", "") == kw and it["amount_wan"] is not None:
                    return float(it["amount_wan"])
        for kw in name_kws:
            for it in order:
                if kw in it["name"].replace(" ", "") and it["amount_wan"] is not None:
                    return float(it["amount_wan"])
        return None

    _monitor = sub("复垦监测费") or 0.0
    _care = sub("管护费") or 0.0
    _pollution = sub("土地污染监测") or 0.0
    _quality = sub("耕地质量评定") or 0.0
    _acceptance = sub("复垦验收报告编制") or 0.0
    _basic = sub("基本预备费") or 0.0
    _escalation = sub("价差预备费", "差价具备费", "差价预备费") or 0.0
    _risk = sub("风险金", "风险费") or 0.0
    _monitor_care = amt("监测与管护费")
    if _monitor_care is None:
        _monitor_care = round(_monitor + _care + _pollution + _quality + _acceptance, 4)
    _reserve = amt("预备费")
    if _reserve is None:
        _reserve = round(_basic + _escalation + _risk, 4)
    _construction = amt("工程施工费") or 0.0
    _equipment = amt("设备购置费") or 0.0
    _other = amt("其他费用") or 0.0
    _static = amt("静态总投资")
    if _static is None:
        _static = round(_construction + _equipment + _other + _monitor_care
                        + _basic + _risk, 4)
    _dynamic = amt("动态总投资")
    if _dynamic is None:
        _dynamic = round(_static + _escalation, 4)

    return {
        "construction": _construction,
        "equipment": _equipment,
        "other": _other,
        "monitor_care": _monitor_care,
        "reserve": _reserve,
        "static_total": _static,
        "dynamic_total": _dynamic,
        "monitor": _monitor,
        "care": _care,
        "pollution": _pollution,
        "quality": _quality,
        "acceptance": _acceptance,
        "basic_reserve": _basic,
        "escalation": _escalation,
        "risk": _risk,
        "other_rows": order,
    }


def read_cost_workbook(path: str | Path) -> Optional[dict]:
    """读整本投资估算 Excel；不是这种表（读不出工程施工费）就返回 None。"""
    try:
        grids = _read_grids(path)
    except Exception as e:
        logger.info(f"[投资估算Excel] 打开失败 {path}: {e}")
        return None
    eng = None
    summary = None
    for g in grids:
        if eng is None:
            eng = _parse_engineering(g)
        if summary is None:
            summary = _parse_summary(g)
        if eng and summary:
            break
    if not eng:
        return None
    if summary:
        # 工程施工费以明细表为准（汇总表可能是四舍五入后的）
        summary["construction"] = eng["total_wan"]
    return {"engineering": eng, "summary": summary, "source": str(path)}


# ═══════════════════════════════════════════════════════════════
# 表7-3 / 7-4 / 7-5
# ═══════════════════════════════════════════════════════════════
def _f(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return ""


_OTHER_SECTIONS = ("前期工作费", "工程监理费", "拆迁补偿费", "竣工验收费", "业主管理费")


_CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def engineering_table(data: dict) -> Optional[dict]:
    """表7-3 工程施工费估算表（序号｜工程或费用名称｜单位｜数量｜综合单价｜工程施工费）。"""
    eng = (data or {}).get("engineering") or {}
    rows_in = eng.get("rows") or []
    if not rows_in:
        return None
    rows: List[list] = []
    seq = 0
    lvl1 = 0
    lvl2 = 0
    for r in rows_in:
        if r["section"]:
            no = r.get("no") or ""
            if _SEC_NO.match(no):
                lvl2 += 1
                no = no or f"（{_CN_NUM[min(lvl2, 10) - 1]}）"
            elif no:
                lvl1 += 1
                lvl2 = 0
            elif lvl1:
                lvl2 += 1
                no = f"（{_CN_NUM[min(lvl2, 10) - 1]}）"
            else:
                lvl1 += 1
                no = _CN_NUM[min(lvl1, 10) - 1]
            rows.append([no, r["name"], "", "", "", _f(r.get("amount_wan"))])
            seq = 0
            continue
        seq += 1
        rows.append([str(seq), r["name"], r.get("unit") or "",
                     _f(r.get("qty"), 4), _f(r.get("price")),
                     _f(r.get("amount_wan"), 4)])
    rows.append(["合计", "", "", "", "", _f(eng.get("total_wan"), 4)])
    return {"caption": "表7-3 工程施工费估算表",
            "headers": ["序号", "工程或费用名称", "单位", "数量",
                        "综合单价（元）", "工程施工费（万元）"],
            "rows": rows, "keep_blank": True}


def other_cost_table(data: dict) -> Optional[dict]:
    """表7-4 其他费用估算表（序号｜名称｜计算式｜金额）。"""
    sm = (data or {}).get("summary") or {}
    rows_in = sm.get("other_rows") or []
    items = []
    for r in rows_in:
        nm = r["name"].replace(" ", "")
        if nm in _OTHER_SECTIONS:
            items.append(r)
        elif r.get("section") in _OTHER_SECTIONS and _num(r.get("no")) is not None:
            items.append(r)
    if not items and not sm.get("other"):
        return None
    rows: List[list] = []
    for r in items:
        rows.append([r.get("no") or "", r["name"], r.get("formula") or "",
                     _f(r.get("amount_wan"))])
    return {"caption": "表7-4 其他费用估算表",
            "headers": ["其他费用（万元）", "", "", _f(sm.get("other"))],
            "rows": rows, "merge_header": True, "keep_blank": True}


def summary_table(data: dict) -> Optional[dict]:
    """表7-5 土地复垦投资估算表（一~七 + 监测/预备费明细 + 费率）。"""
    sm = (data or {}).get("summary") or {}
    if not sm or sm.get("static_total") is None:
        return None
    monitor_total = sm.get("monitor_care") or 0.0
    reserve_total = sm.get("reserve") or 0.0
    rows: List[list] = [
        ["一", "工程施工费", _f(sm.get("construction")), ""],
        ["二", "设备费", _f(sm.get("equipment") or 0.0), ""],
        ["三", "其他费用", _f(sm.get("other")), ""],
        ["四", "监测与管护费", _f(monitor_total), ""],
        ["（一）", "复垦监测费", _f(sm.get("monitor")), "1.5%[一]"],
        ["（二）", "管护费", _f(sm.get("care")), ""],
        ["（三）", "土地污染监测", _f(sm.get("pollution")), ""],
        ["（四）", "耕地质量评定", _f(sm.get("quality")), ""],
        ["（五）", "复垦验收报告编制", _f(sm.get("acceptance")), ""],
        ["五", "预备费", _f(reserve_total), ""],
        ["（一）", "基本预备费", _f(sm.get("basic_reserve")), "6%[一+二+三+四]"],
        ["（二）", "差价预备费", _f(sm.get("escalation")), ""],
        ["（三）", "风险费", _f(sm.get("risk")), "2%[一]"],
        ["六", "静态总投资", _f(sm.get("static_total")), ""],
        ["七", "动态总投资", _f(sm.get("dynamic_total")), ""],
    ]
    return {"caption": "表7-5 土地复垦投资估算表",
            "headers": ["序号", "工程或费用名称", "费用（万元）", "费率（%）"],
            "rows": rows, "keep_blank": True}


# ═══════════════════════════════════════════════════════════════
# 表8-1 土地复垦费用安排表（业主口径：阶段工程费 + 按比例分摊）
# ═══════════════════════════════════════════════════════════════
_PHASE_WORK = {
    1: "表土剥离、堆存",
    2: "土地的日常监测",
    3: "复垦工作全面展开，包括场地清理，表土回覆，增施化肥等复垦措施，完成后组织复垦验收",
    4: "管护",
}


def _add_months(ym: str, months: int) -> str:
    m = re.match(r'^\s*(\d{4})\D+(\d{1,2})', str(ym or ""))
    if not m:
        return ""
    y, mo = int(m.group(1)), int(m.group(2))
    mo += int(months)
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{y}年{mo}月"


def _phase_label(a: str, b: str = "") -> str:
    return a if (not b or a == b) else f"{a}至{b}"


def phase_schedule_table(data: dict, filled: dict) -> Optional[dict]:
    """表8-1：四个阶段 × 动态投资额。

    · 阶段1 = 首月；阶段2 = 次月至用地结束；阶段3 = 结束后复垦期；阶段4 = 管护期；
    · 工程费按所属分项归阶段（土壤剥离工程→阶段1，其余→阶段3）；
    · 监测费→阶段2、管护费→阶段4、污染/质量/验收→阶段3；
    · 其他费用、基本预备费、价差预备费、风险金 **按两阶段工程费占比分摊**（业主 2026-11-05 选定）；
    · 合计严格等于动态总投资（尾差并入阶段3）。
    """
    fd = filled or {}
    sm = (data or {}).get("summary") or {}
    eng = (data or {}).get("engineering") or {}
    start = str(fd.get("land_use_start") or "").strip()
    end = str(fd.get("land_use_end") or "").strip()
    if not start or not end:
        return None
    try:
        reclaim_m = int(float(str(fd.get("reclaim_period_months") or 0)))
        care_m = int(float(str(fd.get("care_period_months") or 0)))
    except (TypeError, ValueError):
        reclaim_m = care_m = 0
    if reclaim_m <= 0 or care_m <= 0:
        return None

    _start_lbl = _add_months(start, 0)
    _p1_end = _start_lbl
    _p2_start = _add_months(start, 1)
    _p2_end = _add_months(end, 0)
    _p3_start = _add_months(end, 1)
    _p3_end = _add_months(end, reclaim_m)
    _p4_start = _add_months(end, reclaim_m + 1)
    _p4_end = _add_months(end, reclaim_m + care_m)
    labels = {
        1: _phase_label(_start_lbl, ""),
        2: _phase_label(_p2_start, _p2_end),
        3: _phase_label(_p3_start, _p3_end),
        4: _phase_label(_p4_start, _p4_end),
    }

    # 工程费 → 阶段
    eng1 = eng3 = 0.0
    section = ""
    for r in eng.get("rows") or []:
        if r["section"]:
            section = r["name"]
            continue
        amt = float(r.get("amount_wan") or 0.0)
        if "剥离" in section:
            eng1 += amt
        else:
            eng3 += amt
    construction = float(sm.get("construction") or (eng1 + eng3))
    if construction > 0 and abs((eng1 + eng3) - construction) > 0.01:
        eng3 = max(0.0, construction - eng1)

    # 非工程费
    p2 = float(sm.get("monitor") or 0.0)
    p4 = float(sm.get("care") or 0.0)
    p3_extra = (float(sm.get("pollution") or 0.0) + float(sm.get("quality") or 0.0)
                + float(sm.get("acceptance") or 0.0))
    # 其他费用 / 预备费 / 价差 / 风险 按工程费占比分摊
    shared = (float(sm.get("other") or 0.0) + float(sm.get("equipment") or 0.0)
              + float(sm.get("basic_reserve") or 0.0)
              + float(sm.get("escalation") or 0.0)
              + float(sm.get("risk") or 0.0))
    if shared and (eng1 + eng3) > 0:
        _r1 = eng1 / (eng1 + eng3)
        p1 = eng1 + shared * _r1
        p3 = eng3 + shared * (1 - _r1) + p3_extra
    else:
        p1 = eng1
        p3 = eng3 + p3_extra

    dynamic = float(sm.get("dynamic_total") or 0.0) or (
        float(sm.get("static_total") or 0.0) + float(sm.get("escalation") or 0.0))
    if not dynamic:
        dynamic = p1 + p2 + p3 + p4
    # 尾差并入复垦期
    p3 += dynamic - (p1 + p2 + p3 + p4)

    rows = [
        [labels[1], _f(p1), _PHASE_WORK[1]],
        [labels[2], _f(p2), _PHASE_WORK[2]],
        [labels[3], _f(p3), _PHASE_WORK[3]],
        [labels[4], _f(p4), _PHASE_WORK[4]],
    ]
    return {"caption": "表8-1 土地复垦费用安排表            单位：万元",
            "headers": ["复垦时间", "动态总投资", "复垦工作"],
            "rows": rows, "keep_blank": True}


def resolve_cost_excel(filled: dict, materials: Sequence[dict] = (),
                       storage_dir: str | Path | None = None) -> str:
    """在用户资料里找《投资估算 Excel》：先按基础资料里填的文件名，再按常见关键词。"""
    from pathlib import Path as _P
    name = str((filled or {}).get("cost_estimate_excel_name") or "").strip()
    try:
        from app.config import settings
        _dirs = [Path(storage_dir or settings.STORAGE_DIR)]
    except Exception:
        _dirs = [Path(storage_dir)] if storage_dir else []
    if name:
        for d in _dirs:
            for sub in ("", "templates", "materials", "uploads"):
                p = d / sub / name if sub else d / name
                if p.exists() and p.is_file():
                    return str(p)
    _kw = ("费用", "投资估算", "估算表", "管道费用")
    for m in materials or ():
        if not isinstance(m, dict):
            continue
        p = m.get("source_path") or m.get("path") or ""
        nm = m.get("source_name") or m.get("original_name") or _P(str(p)).name
        if not str(p).lower().endswith((".xls", ".xlsx", ".xlsm")):
            continue
        if name and (str(nm) == name or _P(str(nm)).stem == _P(name).stem):
            return str(p)
        if any(k in str(nm) for k in _kw):
            return str(p)
    return ""


def summary_text(data: dict) -> str:
    """给提示词的工程量一句话清单 —— **用 Excel 的数量**，与表6-1/表7-3 同口径。

    不这么写的话，正文会按内置公式说「沟渠疏通 924m³」，而表里是 Excel 的
    「616㎡」，同一份报告两个数（业主会逐项对账）。
    """
    rows = quantities_from_workbook(data)
    if not rows:
        return ""
    parts = [f"{r['name']} {r['qty']:g} {r['unit']}".strip()
             for r in rows if r.get("qty")]
    return "工程量（按用户提供的投资估算表）：" + "、".join(parts)


def quantities_from_workbook(data: dict) -> List[dict]:
    """Excel 明细 → 表6-1 的工程量行（名称/单位/数量 + 所属分项）。

    用户上传的 Excel 就是该项目的工程量口径；表6-1 与表7-3 用同一份，避免两张表打架。
    """
    eng = (data or {}).get("engineering") or {}
    rows: List[dict] = []
    section = ""
    for r in eng.get("rows") or []:
        if r["section"]:
            section = r["name"]
            continue
        if r.get("qty") is None:
            continue
        rows.append({"name": r["name"], "unit": r.get("unit") or "",
                     "qty": float(r["qty"]), "section": section})
    return rows


def to_budget_estimate(data: dict, *, area_m2: float = 0.0):
    """Excel 汇总 → `BudgetEstimate`（报告表、第1/7章正文共用同一份数）。"""
    from app.services.reclamation_budget_bridge import BudgetEstimate
    sm = (data or {}).get("summary") or {}
    if not sm:
        return None
    acceptance = round(float(sm.get("pollution") or 0.0)
                       + float(sm.get("quality") or 0.0)
                       + float(sm.get("acceptance") or 0.0), 4)
    est = BudgetEstimate(
        construction=float(sm.get("construction") or 0.0),
        equipment=float(sm.get("equipment") or 0.0),
        other=float(sm.get("other") or 0.0),
        monitor_care=float(sm.get("monitor_care") or 0.0),
        monitor=float(sm.get("monitor") or 0.0),
        care=float(sm.get("care") or 0.0),
        acceptance=acceptance,
        reserve=float(sm.get("reserve") or 0.0),
        basic_reserve=float(sm.get("basic_reserve") or 0.0),
        price_reserve=float(sm.get("escalation") or 0.0),
        risk=float(sm.get("risk") or 0.0),
        static_total=float(sm.get("static_total") or 0.0),
        dynamic_total=(float(sm["dynamic_total"])
                       if sm.get("dynamic_total") is not None else None),
        source="用户上传的投资估算 Excel",
    )
    if area_m2 > 0:
        est.per_mu = round(est.dynamic_total / (area_m2 / 666.6667), 4) \
            if est.dynamic_total else 0.0
        est.per_ha = round(est.dynamic_total / (area_m2 / 10000.0), 4) \
            if est.dynamic_total else 0.0
    items = []
    for r in ((data or {}).get("engineering") or {}).get("rows") or []:
        if r["section"] or r.get("qty") is None:
            continue
        items.append({"name": r["name"], "unit": r.get("unit") or "",
                      "qty": r["qty"], "price": r.get("price"),
                      "amount_wan": r.get("amount_wan")})
    est.direct_items = items
    return est
