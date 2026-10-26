# -*- coding: utf-8 -*-
"""第6章「工程量测算」的**确定性模型**（不经 LLM）。

## 为什么要有这个

复垦报告里的钱 = 工程量 × 综合单价。单价查定额（`reclamation_budget_bridge`），
工程量此前是**模型自己写的** —— 同一个会话连跑两次，一次有「混凝土面层拆除/垫层清除」、
一次没有，工程施工费 11.93 万 vs 1.47 万，报告表也跟着变。
业主 2026-10-26：「每次生成的报告计算出来的数字都应该是一样的」。
所以工程量改成**按勘测定界地类和固定设计参数算**，模型只负责把结论写成人话。

## 公式从哪来（不是拍脑袋）

两个样本的**表6-1 与表7-3 逐格对得上**，而且「大树」在《盐业井神》docx 里留的批注
写明了算法（括号里是批注编号）：

    项目                       公式                        盐业井神实测        明远（本项目样本）反算
    ─────────────────────────────────────────────────────────────────────────────────
    表土剥离    = 可剥离地类面积 × 0.20 m (*)      0.1936ha×0.30=580.8m³   0.2689ha×0.20=537.8m³
    表土回填    = 表土剥离量                        580.8 m³                537.8 m³
    混凝土面层拆除 = 硬化面积 × 0.15 m               2354m²×0.10=235.4m³    2727m²×0.15≈414m³
    垫层清除    = 硬化面积 × 0.10 m (#37)          2354m²×0.08=188.3m³    2727m²×0.10≈276m³
    垃圾外运    = 面层拆除 + 垫层清除                 423.7 m³                ≈693 m³
    土地翻耕    = 可剥离地类面积（公顷）(#39/#40)     0.1936 ha              0.266 ha
    田面平整    = 全部复垦区面积（100m²）            —                       8070m² → 80.7
    有机肥      = 耕地面积 × 15 t/ha (#43)          2.9040 t                4.04 t
    复合肥      = 耕地面积 × 0.75 t/ha (#44)        0.1452 t                0.20 t
    沟渠疏通    = 沟渠面积 × 1.5 (#42)              393m²×1.5=589.5m³       —
    播撒草籽    = 表土堆放区面积 (#35)              0.0257 ha               —

(*) 剥离厚度两个项目不同：盐业 0.30 m（只剥耕地）、明远 0.20 m（剥耕地+林地）。
    厚度属**设计参数**（可研/初设给），不是地类推出来的。本项目按明远的 0.20 m，
    与第4章 表4-11「剥离面积 0.2689 公顷 × 0.2 m = 537.8 m³」同口径 —— 两章数字必须一致。

    批注 #37 写「功能区面积*15cm」，但报出的 188.32 = 2354×0.08，**批注与结果打架**
    （业主认可这条，见 2026-10-08 记录）。本模块按**实算系数 0.10 m**（明远样本）走，
    因为明远的金额反算出来就是 0.10 m。

    田面平整：盐业按 100m³（耕地×0.1m）、明远按 100m²（全复垦区）。本项目取**明远口径**
    （100m²）—— 与业主自己这份报告的表7-3 单位一致。

## 硬化面积取哪块地

明远样本的面层/垫层金额反算出来的硬化面积 ≈ 2727 m² = **交通运输用地（公路用地）**
（差 1.3%，其余地类组合都对不上）。所以：硬化面积 = 交通运输用地面积；
若该项目没有交通运输用地，回落到全部复垦区面积（盐业那种整个功能区都硬化的情形）。

取不到地类数据（`land_classification` 为空）→ 返回 None，**整块不补**，第6/7章保持原样。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 设计参数（可研/初设口径，见模块头注释的出处）
# ═══════════════════════════════════════════════════════════════
STRIP_THICKNESS_M = 0.20        # 表土剥离厚度
PAVE_THICKNESS_M = 0.15         # 混凝土面层拆除厚度
CUSHION_THICKNESS_M = 0.10      # 垫层清除厚度
LEVEL_THICKNESS_M = 0.10        # 田面平整折算厚度（100m² → 100m³ 时用）
DITCH_FACTOR = 1.5              # 沟渠疏通 = 沟渠面积 × 1.5
FERT_ORGANIC_T_PER_HA = 15.0    # 有机肥 t/ha
FERT_COMPOUND_T_PER_HA = 0.75   # 复合肥 t/ha

# 需剥离表土、需培肥的地类（一级类）—— 明远样本的有机肥量 4.04 t 反算出来就是
# 「耕地+林地 0.2689 ha × 15 t/ha」，所以培肥面积与剥离面积同口径
_STRIP_CLASSES = ("耕地", "林地", "园地", "草地")
# 硬化（需拆面层/垫层）的地类（一级类）。明远样本面层/垫层金额反算的硬化面积
# ≈ 2727 m² = **交通运输用地（公路用地）**（差 1.3%）；把村庄 2580 m² 也算进去
# 就对不上了。取不到则回落全部复垦区面积（盐业那种整个功能区都硬化的情形）。
_HARD_CLASSES = ("交通运输用地",)

# 复垦服务年限的默认口径（业主 2026-10-26：「模板中写的年限」）
#   明远样本：临时用地占用 23~24 个月 + 复垦期 1 个月 + 管护期 6 个月 = 30 个月
#   王元佳苑 24 → 30，污水处理厂 24 → 30，宁淮城际 48 → 54（复垦在结束前完成，只加管护 6）
DEFAULT_SERVICE_MONTHS = 30
DEFAULT_CONSTRUCT_MONTHS = 24


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _class_name(full: str) -> str:
    """「耕地（01）」→「耕地」。"""
    return re.sub(r'[（(].*?[)）]', '', str(full or "")).strip()


def _rows(filled: dict) -> List[dict]:
    lc = (filled or {}).get("land_classification") or {}
    rows = lc.get("rows") if isinstance(lc, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def _area_of(rows: Sequence[dict], *, level: str, names: Sequence[str],
             contains: bool = False) -> float:
    """按地类名汇总面积（m²）。`level` = "一级" / "二级"。"""
    key = "一级地类" if level == "一级" else "二级地类"
    out = 0.0
    for r in rows:
        n = _class_name(r.get(key))
        hit = any(k in n for k in names) if contains else any(n == k for k in names)
        if hit:
            out += _num(r.get("面积_m2"))
    return out


def _total_m2(filled: dict, rows: Sequence[dict]) -> float:
    lc = (filled or {}).get("land_classification") or {}
    _t = _num(lc.get("合计_m2")) if isinstance(lc, dict) else 0.0
    return _t or sum(_num(r.get("面积_m2")) for r in rows) or _num((filled or {}).get("area_m2"))


def _r(v: float, nd: int = 4) -> float:
    return round(float(v) + 0.0, nd)


# ═══════════════════════════════════════════════════════════════
# 工程量
# ═══════════════════════════════════════════════════════════════
def build_quantity_rows(filled: dict) -> List[dict]:
    """→ `[{section, name, unit, qty}]`（与第6章 表6-1 的列一一对应）。

    没有地类明细就返回空 —— 只有总面积算不出剥离/翻耕/培肥，出的表会缺半截。
    """
    rows = _rows(filled)
    if not rows:
        return []
    total_m2 = _total_m2(filled, rows)
    if total_m2 <= 0:
        return []

    strip_m2 = _area_of(rows, level="一级", names=_STRIP_CLASSES)
    hard_m2 = _area_of(rows, level="一级", names=_HARD_CLASSES) or total_m2
    ditch_m2 = _area_of(rows, level="二级", names=("沟渠",), contains=True)

    out: List[dict] = []

    def add(section: str, name: str, unit: str, qty: float) -> None:
        if qty <= 0:
            return
        out.append({"section": section, "name": name, "unit": unit, "qty": _r(qty)})

    # （一）土壤剥离 —— 面积 × 厚度
    _strip_m3 = strip_m2 * STRIP_THICKNESS_M
    add("土壤剥离工程", "表土剥离", "100m³", _strip_m3 / 100.0)

    # （二）清理 —— 面层 / 垫层 / 外运
    _pave_m3 = hard_m2 * PAVE_THICKNESS_M
    _cushion_m3 = hard_m2 * CUSHION_THICKNESS_M
    add("清理工程", "混凝土面层拆除", "100m³", _pave_m3 / 100.0)
    add("清理工程", "垫层清除", "100m³", _cushion_m3 / 100.0)
    add("清理工程", "垃圾外运", "100m³", (_pave_m3 + _cushion_m3) / 100.0)

    # （三）平整 —— 翻耕（公顷）/ 回填 / 田面平整（100m²）
    add("平整工程", "土地翻耕", "公顷", strip_m2 / 10000.0)
    add("平整工程", "表土回填", "100m³", _strip_m3 / 100.0)
    add("平整工程", "田面平整", "100m²", total_m2 / 100.0)

    # （四）恢复水利 —— 沟渠疏通 = 沟渠面积 × 1.5
    add("恢复水利工程", "沟渠疏通", "100m³", ditch_m2 * DITCH_FACTOR / 100.0)

    # （五）生物化学 —— 培肥按可剥离地类（耕地+林地）面积（明远样本 4.04 t 反算口径）
    _farm_m2 = strip_m2
    add("生物化学工程", "土壤培肥（有机肥）", "吨",
        _farm_m2 / 10000.0 * FERT_ORGANIC_T_PER_HA)
    add("生物化学工程", "土壤培肥（复合肥）", "吨",
        _farm_m2 / 10000.0 * FERT_COMPOUND_T_PER_HA)
    return out


def build_quantity_table(filled: dict) -> Optional[dict]:
    """第6章 表6-1「项目复垦工程量汇总表」（含分部行）。取不到地类 → None。"""
    rows = build_quantity_rows(filled)
    if not rows:
        return None
    _cn = ["（一）", "（二）", "（三）", "（四）", "（五）", "（六）", "（七）"]
    out: List[List[str]] = [["一", "土壤重构工程", "", ""]]
    cur, group, sub = None, -1, 0
    for r in rows:
        if r["section"] != cur:
            cur, sub = r["section"], 0
            group += 1
            out.append([_cn[group] if group < len(_cn) else "", cur, "", ""])
        sub += 1
        out.append([str(sub), r["name"], r["unit"], _fmt_qty(r["qty"])])
    return {"caption": "表6-1 项目复垦工程量汇总表",
            "headers": ["序号", "工程或费用名称", "单位", "数量"],
            "rows": out}


def _fmt_qty(v: float) -> str:
    """工程量与样本同口径：保留到小数点后 3~4 位，去掉多余的 0。"""
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def build_summary(filled: dict) -> str:
    """给提示词/事实卡用的一句话工程量清单（模型写正文时照这个用）。"""
    rows = _rows(filled)
    if not rows:
        return ""
    total_m2 = _total_m2(filled, rows)
    if total_m2 <= 0:
        return ""
    strip_m2 = _area_of(rows, level="一级", names=_STRIP_CLASSES)
    hard_m2 = _area_of(rows, level="一级", names=_HARD_CLASSES) or total_m2
    ditch_m2 = _area_of(rows, level="二级", names=("沟渠",), contains=True)
    _p = []
    if strip_m2:
        _p.append(f"表土剥离 {strip_m2 * STRIP_THICKNESS_M:.1f} m³"
                  f"（剥离面积 {strip_m2 / 10000.0:.4f} 公顷 × {STRIP_THICKNESS_M:.2f} m）、"
                  f"表土回填 {strip_m2 * STRIP_THICKNESS_M:.1f} m³"
                  f"（剥离厚度按 {STRIP_THICKNESS_M * 100:.0f} cm 计）")
    _p.append(f"混凝土面层拆除 {hard_m2 * PAVE_THICKNESS_M:.1f} m³"
              f"（硬化面积 {hard_m2 / 10000.0:.4f} 公顷 × {PAVE_THICKNESS_M * 100:.0f} cm）、"
              f"垫层清除 {hard_m2 * CUSHION_THICKNESS_M:.1f} m³"
              f"（× {CUSHION_THICKNESS_M * 100:.0f} cm）、"
              f"垃圾外运 {hard_m2 * (PAVE_THICKNESS_M + CUSHION_THICKNESS_M):.1f} m³")
    if ditch_m2:
        _p.append(f"沟渠疏通 {ditch_m2 * DITCH_FACTOR:.1f} m³")
    _p.append(f"土地翻耕 {strip_m2 / 10000.0:.4f} 公顷、田面平整 {total_m2 / 10000.0:.4f} 公顷")
    if strip_m2:
        _p.append(f"土壤培肥（有机肥 {strip_m2 / 10000.0 * FERT_ORGANIC_T_PER_HA:.4f} 吨、"
                  f"复合肥 {strip_m2 / 10000.0 * FERT_COMPOUND_T_PER_HA:.4f} 吨）")
    return "；".join(_p) + "。"


# ═══════════════════════════════════════════════════════════════
# 写回 filled_data（提示词/事实卡/报告表/表7 共用一份）
# ═══════════════════════════════════════════════════════════════
QUANTITY_ROWS_KEY = "_quantity_rows"     # 内部键：装配器渲染表6-1 用
BUDGET_KEY = "_budget_estimate"          # 内部键：装配器渲染表7-3/7-4 与报告表用


def enrich_filled_data(filled: dict, *, endpoint: str = "") -> bool:
    """在**生成前**把工程量与投资估算算好、写回 `filled_data`。

    写入的都是 `RECLAMATION_CARD_KEYS` 里已有的键（事实卡会带进每一章提示词），
    再加两个下划线开头的内部键（装配器渲染表用）。返回是否写入了工程量。
    """
    rows = build_quantity_rows(filled)
    if not rows:
        logger.info("[工程量] 没有地类数据，工程量与投资估算不预计算")
        return False
    filled[QUANTITY_ROWS_KEY] = rows
    filled["earthwork_volume"] = build_summary(filled)
    filled["topsoil_thickness"] = f"{STRIP_THICKNESS_M:.2f}"

    # 服务年限：材料里没有就按样本口径（占用 + 复垦 1 个月 + 管护 6 个月 = 30 个月）
    # 🔴 必须带单位 —— 事实卡只印「30」时，模型写成「服务年限为 30 年」（实测踩过）。
    if not str(filled.get("service_years") or "").strip():
        filled["service_years"] = f"{DEFAULT_SERVICE_MONTHS} 个月"
        filled.setdefault("_service_years_source", "模板口径（占用期+复垦期1个月+管护期6个月）")
    if not str(filled.get("construct_period") or "").strip():
        filled["construct_period"] = f"{DEFAULT_CONSTRUCT_MONTHS} 个月"

    try:
        from app.services.reclamation_budget_bridge import estimate_for_report
        _lc = filled.get("land_classification") or {}
        _ha = _num(_lc.get("合计_m2") if isinstance(_lc, dict) else 0) / 10000.0 \
            or _num(filled.get("area_m2")) / 10000.0
        _m = re.search(r'\d+(?:\.\d+)?', str(filled.get("service_years") or ""))
        est = estimate_for_report(
            rows, area_ha=_ha, service_months=float(_m.group(0)) if _m else 0.0,
            project_name=str(filled.get("project_name") or ""), endpoint=endpoint)
        if est:
            filled[BUDGET_KEY] = est.to_dict()      # 落库要 JSON，存 dict 不存 dataclass
            filled["construction_cost"] = f"{est.construction:.2f} 万元"
            filled["other_cost"] = f"{est.other:.2f} 万元"
            filled["dynamic_total_investment"] = (
                f"{est.dynamic_total:.2f} 万元" if est.dynamic_total
                else f"{est.static_total:.2f} 万元（静态）")
    except Exception as e:                                  # 不影响出报告
        logger.warning(f"[工程量] 投资估算预计算失败（报告照出，金额留空）: {e}")
    return True
