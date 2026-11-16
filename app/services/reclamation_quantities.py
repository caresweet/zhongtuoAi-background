# -*- coding: utf-8 -*-
"""第6章「工程量测算」的**确定性模型**（不经 LLM）。

## 为什么要有这个

复垦报告里的钱 = 工程量 × 综合单价。单价查定额（`reclamation_budget_bridge`），
工程量此前是**模型自己写的** —— 同一个会话连跑两次，一次有「混凝土面层拆除/垫层清除」、
一次没有，工程施工费 11.93 万 vs 1.47 万，报告表也跟着变。
业主 2026-10-26：「每次生成的报告计算出来的数字都应该是一样的」。
所以工程量改成**按勘测定界地类和固定设计参数算**，模型只负责把结论写成人话。

## 公式从哪来

公式是行业通用算式；**设计参数一律取本项目资料/弹窗**，不再套任何范文：

    表土剥离      = 可剥离地类面积 × 剥离厚度（项目给，cm）
    表土回填      = 表土剥离量
    混凝土面层拆除 = 硬化面积 × 面层厚度（项目给，cm）      ┐ 结构做法＝
    垫层清除      = 硬化面积 × 垫层厚度（项目给，cm）      │ 混凝土
    垃圾外运      = 面层拆除 + 垫层清除                    ┘
    垫层清理      = 硬化面积 × 结构层厚度（项目给，cm）     ┐ 结构做法＝
    垃圾外运      = 垫层清理量                              ┘ 碎石垫层+钢板
    土地翻耕      = 可剥离地类面积（公顷）
    田面平整      = 可剥离地类面积（100m²，折算 0.10m）
    有机肥        = 可剥离地类面积 × 15 t/ha
    复合肥        = 可剥离地类面积 × 0.75 t/ha
    沟渠疏通      = 沟渠面积 × 1.5

🔴 业主 2026-11-03：「不要默认值都是用范文的……每次新报告的都不一样。」
   所以 `topsoil_thickness` / `hard_surface_type` / `hard_area_m2` /
   `surface_thickness_cm` / `cushion_thickness_cm` 缺失时：
   · 缺厚度 → 该分项**不进入工程量**（不拿 0.2/0.3 顶替）；
   · 缺结构做法/层厚 → 清理工程整块不出；
   · 硬化面积缺失才回落复垦区总面积（那是本项目自己的地类数据）。
   缺口在弹窗（`RECLAMATION_NEED_FIELDS`）里问，问不到就留白。

取不到地类数据（`land_classification` 为空）→ 返回空，第6/7章保持原样。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 设计参数（可研/初设口径，见模块头注释的出处）
# ═══════════════════════════════════════════════════════════════
# 🔴 设计参数**不设范文默认值**（业主 2026-11-03：「每次新报告的都不一样」）。
#    厚度、结构做法、硬化面积全部由本项目资料/弹窗给；拿不到就**不编**，
#    对应分项不进入工程量（表6-1/7-3 会缺这一项，缺口写进日志/缺口清单）。
LEVEL_THICKNESS_M = 0.10        # 田面平整折算厚度（算法口径，不是项目做法）
DITCH_FACTOR = 1.5              # 沟渠疏通 = 沟渠面积 × 1.5
FERT_ORGANIC_T_PER_HA = 15.0    # 有机肥 t/ha
FERT_COMPOUND_T_PER_HA = 0.75   # 复合肥 t/ha

# 复垦验收费（四(三)）—— 人工稿《井神 11.2》§7.2.4 的取费口径：
#   土壤污染监测、耕地质量评定各按 2.57 元/㎡ 计（23248 ㎡ ≈ 5.97 万，报告写 6.00 万）；
#   复垦验收报告编制 5.5 万元/项。
# 🔴 不给这一项时，第 7 章「监测与管护费」只有 1.94 万，而 7.2.4 正文又写着
#    「4.2 万/4.8 万/1 万」—— 前后对不上（2026-11-03 对照人工稿实测）。
SOIL_TEST_YUAN_PER_M2 = 2.57
QUALITY_TEST_YUAN_PER_M2 = 2.57
ACCEPTANCE_REPORT_WAN = 5.5

# 需剥离表土、需培肥的地类（一级类）—— 有机肥量与剥离面积同口径（15 t/ha）
_STRIP_CLASSES = ("耕地", "林地", "园地", "草地")

# 注：服务年限/生产年限不设默认值 —— 服务年限由弹窗问，生产年限取材料里的用地期限。


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
def _strip_thickness_m(filled: dict) -> float:
    """剥离/覆土厚度（m）。用户在弹窗按**厘米**填（30），材料里可能是 0.3 m。

    🔴 取不到返回 0 —— 不拿任何范文的厚度顶替。
    """
    v = _num((filled or {}).get("topsoil_thickness"))
    if v <= 0:
        return 0.0
    return v / 100.0 if v > 1.0 else v


def _hard_area_m2(filled: dict, total_m2: float) -> float:
    """硬化（压占）面积：只认本项目填的值；没填返回 0（不拿总面积顶替）。

    井神安井组：2.0048 ha = 临时用地 2.3248 − 表土堆放区 0.3200 —— 用总面积会
    多算 0.32 ha 的垫层清理。
    """
    return _num((filled or {}).get("hard_area_m2"))


def _surface_type(filled: dict) -> str:
    """结构做法：concrete / gravel / ""（未给 → 不编清理分项）。"""
    t = str((filled or {}).get("hard_surface_type") or "")
    if "混凝土" in t:
        return "concrete"
    if any(k in t for k in ("碎石", "钢板", "垫层")):
        return "gravel"
    return ""


def _surface_thickness_m(filled: dict) -> float:
    """结构层厚度（m）：碎石垫层厚度 / 混凝土面层厚度。取不到返回 0。"""
    v = _num((filled or {}).get("surface_thickness_cm"))
    return v / 100.0 if v > 0 else 0.0


def _cushion_thickness_m(filled: dict) -> float:
    """混凝土垫层厚度（m）。取不到返回 0。"""
    v = _num((filled or {}).get("cushion_thickness_cm"))
    return v / 100.0 if v > 0 else 0.0


def _excavation_area_m2(filled: dict) -> float:
    """挖损面积（㎡）：优先取显式参数，否则取功能分区里损毁方式含「挖损」的分区合计。

    管道开挖带、沟槽、基坑这类以挖为主的单元，必须单独计量开挖/回填；
    只算「表土剥离 + 垫层清理」会漏掉槽体土方（2026-11-16 专家批注）。
    """
    v = _num((filled or {}).get("excavation_area_m2"))
    if v > 0:
        return v
    try:
        from app.services.reclamation_zones import zones_from_filled
        zones = zones_from_filled(filled)
    except Exception:
        zones = []
    return sum(_num(z.get("area_m2")) for z in zones
               if "挖损" in str(z.get("method") or ""))


def _excavation_depth_m(filled: dict) -> float:
    """平均开挖深度（m）。取不到返回 0 —— 不拿经验深度顶替。"""
    return _num((filled or {}).get("excavation_depth_m"))


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

    _strip_t = _strip_thickness_m(filled)
    strip_m2 = _area_of(rows, level="一级", names=_STRIP_CLASSES)
    hard_m2 = _hard_area_m2(filled, total_m2)
    ditch_m2 = _area_of(rows, level="二级", names=("沟渠",), contains=True)
    _surf = _surface_type(filled)
    _surf_t = _surface_thickness_m(filled)
    _cush_t = _cushion_thickness_m(filled)

    out: List[dict] = []

    def add(section: str, name: str, unit: str, qty: float) -> None:
        if qty <= 0:
            return
        out.append({"section": section, "name": name, "unit": unit, "qty": _r(qty)})

    # （一）土壤剥离 —— 面积 × 厚度（厚度没给就不出这一项，不用范文顶替）
    _strip_m3 = strip_m2 * _strip_t if _strip_t > 0 else 0.0
    if _strip_m3 > 0:
        add("土壤剥离工程", "表土剥离", "100m³", _strip_m3 / 100.0)

    # （二）清理 —— 结构做法与层厚都来自本项目设计参数：
    #       碎石垫层+钢板 → 只清垫层；混凝土面层+垫层 → 拆面层 + 清垫层
    if _surf == "concrete" and hard_m2 > 0 and _surf_t > 0 and _cush_t > 0:
        _pave_m3 = hard_m2 * _surf_t
        _cushion_m3 = hard_m2 * _cush_t
        add("清理工程", "混凝土面层拆除", "100m³", _pave_m3 / 100.0)
        add("清理工程", "垫层清除", "100m³", _cushion_m3 / 100.0)
        add("清理工程", "垃圾外运", "100m³", (_pave_m3 + _cushion_m3) / 100.0)
    elif _surf == "gravel" and hard_m2 > 0 and _surf_t > 0:
        _cushion_m3 = hard_m2 * _surf_t
        add("清理工程", "垫层清理", "100m³", _cushion_m3 / 100.0)
        add("清理工程", "垃圾外运", "100m³", _cushion_m3 / 100.0)

    # （三）平整 —— 翻耕（公顷）/ 回填 / 田面平整（100m²）
    #   田面平整只覆盖复垦为耕地的部分（井神样表：0.1936ha，不是全部临时用地）
    add("平整工程", "土地翻耕", "公顷", strip_m2 / 10000.0)
    if _strip_m3 > 0:
        add("平整工程", "表土回填", "100m³", _strip_m3 / 100.0)
    add("平整工程", "田面平整", "100m²", strip_m2 / 100.0)

    # （四）挖损工程 —— 沟槽/基坑开挖与回填
    #     挖损面积优先取显式参数，否则取功能分区中损毁方式含「挖损」的分区合计；
    #     开挖深度必须来自本项目资料，取不到就不出这两项（不拿经验深度顶替）。
    _exc_m2 = _excavation_area_m2(filled)
    _exc_d = _excavation_depth_m(filled)
    if _exc_m2 > 0 and _exc_d > 0:
        _exc_m3 = _exc_m2 * _exc_d
        # 表土已按「面积 × 剥离厚度」单列在土壤剥离工程，槽体开挖量扣掉表土层，
        # 避免同一层土在两张表里重复计价。
        _top_in_exc = min(_exc_m3, _exc_m2 * _strip_t) if _strip_t > 0 else 0.0
        _exc_net = _exc_m3 - _top_in_exc
        add("挖损工程", "沟槽开挖", "100m³", _exc_net / 100.0)
        add("挖损工程", "沟槽回填", "100m³", _exc_net / 100.0)

    # （五）恢复水利 —— 沟渠疏通 = 沟渠面积 × 1.5
    add("恢复水利工程", "沟渠疏通", "100m³", ditch_m2 * DITCH_FACTOR / 100.0)

    # （六）生物化学 —— 培肥按可剥离地类（耕地+林地）面积（15 t/ha 口径）
    _farm_m2 = strip_m2
    add("生物化学工程", "土壤培肥（有机肥）", "吨",
        _farm_m2 / 10000.0 * FERT_ORGANIC_T_PER_HA)
    add("生物化学工程", "土壤培肥（复合肥）", "吨",
        _farm_m2 / 10000.0 * FERT_COMPOUND_T_PER_HA)
    return out


def build_quantity_table(filled: dict) -> Optional[dict]:
    """第6章 表6-1「项目复垦工程量汇总表」（含分部行）。取不到地类 → None。

    🔴 有用户上传的投资估算 Excel 时，`_quantity_rows` 已是**那份 Excel 的工程量**，
       这里直接用它 —— 表6-1 与表7-3 必须是同一批数量，否则两张表对不上。
    """
    rows = filled.get(QUANTITY_ROWS_KEY)
    if not isinstance(rows, list) or not rows:
        rows = build_quantity_rows(filled)
    if not rows:
        return None
    _cn = ["（一）", "（二）", "（三）", "（四）", "（五）", "（六）",
           "（七）", "（八）", "（九）", "（十）"]
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
    hard_m2 = _hard_area_m2(filled, total_m2)
    ditch_m2 = _area_of(rows, level="二级", names=("沟渠",), contains=True)
    _strip_t = _strip_thickness_m(filled)
    _surf = _surface_type(filled)
    _surf_t = _surface_thickness_m(filled)
    _cush_t = _cushion_thickness_m(filled)
    _p = []
    if strip_m2 and _strip_t > 0:
        _p.append(f"表土剥离 {strip_m2 * _strip_t:.1f} m³"
                  f"（剥离面积 {strip_m2 / 10000.0:.4f} 公顷 × {_strip_t:.2f} m）、"
                  f"表土回填 {strip_m2 * _strip_t:.1f} m³"
                  f"（剥离厚度按 {_strip_t * 100:.0f} cm 计）")
    if _surf == "concrete" and hard_m2 > 0 and _surf_t > 0 and _cush_t > 0:
        _p.append(f"混凝土面层拆除 {hard_m2 * _surf_t:.1f} m³"
                  f"（硬化面积 {hard_m2 / 10000.0:.4f} 公顷 × {_surf_t * 100:.0f} cm）、"
                  f"垫层清除 {hard_m2 * _cush_t:.1f} m³"
                  f"（× {_cush_t * 100:.0f} cm）、"
                  f"垃圾外运 {hard_m2 * (_surf_t + _cush_t):.1f} m³")
    elif _surf == "gravel" and hard_m2 > 0 and _surf_t > 0:
        _p.append(f"垫层清理 {hard_m2 * _surf_t:.1f} m³"
                  f"（硬化面积 {hard_m2 / 10000.0:.4f} 公顷 × {_surf_t * 100:.0f} cm）、"
                  f"垃圾外运 {hard_m2 * _surf_t:.1f} m³")
    _exc_m2 = _excavation_area_m2(filled)
    _exc_d = _excavation_depth_m(filled)
    if _exc_m2 > 0 and _exc_d > 0:
        _exc_m3 = _exc_m2 * _exc_d
        _top_in_exc = min(_exc_m3, _exc_m2 * _strip_t) if _strip_t > 0 else 0.0
        _exc_net = _exc_m3 - _top_in_exc
        _p.append(f"沟槽开挖 {_exc_net:.1f} m³、沟槽回填 {_exc_net:.1f} m³"
                  f"（挖损面积 {_exc_m2 / 10000.0:.4f} 公顷 × 开挖深度 {_exc_d:.2f} m）")
    if ditch_m2:
        _p.append(f"沟渠疏通 {ditch_m2 * DITCH_FACTOR:.1f} m³")
    _p.append(f"土地翻耕 {strip_m2 / 10000.0:.4f} 公顷、田面平整 {strip_m2 / 10000.0:.4f} 公顷")
    if strip_m2:
        _p.append(f"土壤培肥（有机肥 {strip_m2 / 10000.0 * FERT_ORGANIC_T_PER_HA:.4f} 吨、"
                  f"复合肥 {strip_m2 / 10000.0 * FERT_COMPOUND_T_PER_HA:.4f} 吨）")
    return "；".join(_p) + "。"


# ═══════════════════════════════════════════════════════════════
# 写回 filled_data（提示词/事实卡/报告表/表7 共用一份）
# ═══════════════════════════════════════════════════════════════
QUANTITY_ROWS_KEY = "_quantity_rows"     # 内部键：装配器渲染表6-1 用
BUDGET_KEY = "_budget_estimate"          # 内部键：装配器渲染表7-3/7-4 与报告表用


def enrich_filled_data(filled: dict, *, endpoint: str = "", cost_excel: str = "") -> bool:
    """在**生成前**把工程量与投资估算算好、写回 `filled_data`。

    写入的都是 `RECLAMATION_CARD_KEYS` 里已有的键（事实卡会带进每一章提示词），
    再加两个下划线开头的内部键（装配器渲染表用）。返回是否写入了工程量。

    🔴 用户上传了《投资估算 Excel》（第七章的管道费用表）时，**以它为准**：
       表6-1 的工程量、表7-3/7-4/7-5 的数都从这份 Excel 来（业主 2026-11-05），
       不再走内置单价库 / budget-maker；没上传才用原来的推算。
    """
    _wb = None
    _path = str(cost_excel or filled.get("cost_estimate_excel_path") or "").strip()
    if _path:
        try:
            from app.services.reclamation_cost_excel import read_cost_workbook
            _wb = read_cost_workbook(_path)
            if _wb:
                filled["_cost_workbook"] = _wb
                logger.info(f"[投资估算Excel] {_path}：工程施工费 "
                            f"{(_wb.get('engineering') or {}).get('total_wan')} 万元")
            else:
                logger.warning(f"[投资估算Excel] {_path} 读不出工程施工费，回落内置估算")
        except Exception as e:
            logger.warning(f"[投资估算Excel] 读取失败（回落内置估算）: {e}")

    rows = None
    if _wb:
        try:
            from app.services.reclamation_cost_excel import quantities_from_workbook
            rows = quantities_from_workbook(_wb)
        except Exception:
            rows = None
    if not rows:
        rows = build_quantity_rows(filled)
    if not rows:
        logger.info("[工程量] 没有地类数据，工程量与投资估算不预计算")
        return False
    filled[QUANTITY_ROWS_KEY] = rows
    if _wb:
        try:
            from app.services.reclamation_cost_excel import summary_text
            filled["earthwork_volume"] = summary_text(_wb) or build_summary(filled)
        except Exception:
            filled["earthwork_volume"] = build_summary(filled)
    else:
        filled["earthwork_volume"] = build_summary(filled)
    # 缺哪些设计参数就明确记一笔 —— 对应分项不会进工程量（不拿范文顶替）
    _missing = [k for k in ("topsoil_thickness", "hard_surface_type",
                            "hard_area_m2", "surface_thickness_cm")
                if not str(filled.get(k) or "").strip()]
    # 有挖损分区却没有开挖深度 → 沟槽开挖/回填两项出不来，必须显式记缺口
    try:
        from app.services.reclamation_zones import zones_from_filled
        _has_exc = any("挖损" in str(z.get("method") or "")
                       for z in zones_from_filled(filled))
    except Exception:
        _has_exc = False
    if _has_exc and _excavation_depth_m(filled) <= 0:
        _missing.append("excavation_depth_m")
    if _missing:
        logger.warning(f"[工程量] 缺设计参数，对应分项不出：{_missing}")

    # 🔴 服务年限是**本项目**的口径，不套范文默认值（业主 2026-11-03）。
    #    弹窗问到了就补单位；没问到时留空，第1/8章与报告表显示「待补充」。
    #    🔴 裸数字的口径：复垦基础资料 Excel 填的是**年**（如 3.0），
    #       弹窗/设计抽取可能是**月**（如 36）。<12 按年换算，否则按月。
    _svc = str(filled.get("service_years") or "").strip()
    if _svc and re.fullmatch(r'\d+(?:\.\d+)?', _svc):
        _v = float(_svc)
        _months = _v * 12 if _v < 12 else _v
        filled["service_years"] = f"{_months:g} 个月"
    # 生产年限可直接取临时用地期限（材料里的用地时间），这是项目自身数据
    if not str(filled.get("construct_period") or "").strip():
        _lm = _num(filled.get("land_use_months"))
        if _lm > 0:
            filled["construct_period"] = f"{_lm:.0f} 个月"

    try:
        from app.services.reclamation_budget_bridge import estimate_for_report
        _lc = filled.get("land_classification") or {}
        _ha = _num(_lc.get("合计_m2") if isinstance(_lc, dict) else 0) / 10000.0 \
            or _num(filled.get("area_m2")) / 10000.0
        _area_m2 = _ha * 10000.0
        # 四(三) 复垦验收费：土壤污染监测 + 耕地质量评定（各 2.57 元/㎡）+ 验收报告 5.5 万
        _acceptance = round(
            (_area_m2 * SOIL_TEST_YUAN_PER_M2 + _area_m2 * QUALITY_TEST_YUAN_PER_M2) / 10000.0
            + ACCEPTANCE_REPORT_WAN, 2)
        filled["_acceptance_fee_wan"] = _acceptance
        est = None
        if _wb:
            # 用户上传的投资估算 Excel 是**唯一口径**（含价差预备费，业主 2026-11-05）
            from app.services.reclamation_cost_excel import to_budget_estimate
            est = to_budget_estimate(_wb, area_m2=_area_m2)
        if est is None:
            _m = re.search(r'\d+(?:\.\d+)?', str(filled.get("service_years") or ""))
            est = estimate_for_report(
                rows, area_ha=_ha, service_months=float(_m.group(0)) if _m else 0.0,
                project_name=str(filled.get("project_name") or ""), endpoint=endpoint,
                acceptance_fee=_acceptance)
        if est:
            filled[BUDGET_KEY] = est.to_dict()      # 落库要 JSON，存 dict 不存 dataclass
            filled["construction_cost"] = f"{est.construction:.2f} 万元"
            filled["other_cost"] = f"{est.other:.2f} 万元"
            filled["dynamic_total_investment"] = (
                f"{est.dynamic_total:.2f} 万元" if est.dynamic_total
                else f"{est.static_total:.2f} 万元（静态）")
            # 🔴 第 1 章 1.2.5 与第 7 章正文要引用四项费用。只给零散字段时，
            #    模型会把模板里的旧金额删掉、只留「监测与管护费万元」这种空壳
            #    （2026-11-04 实测）。这里给一句可直接落文的汇总，金额全部来自估算。
            _apply_investment_summary(filled, est)
    except Exception as e:                                  # 不影响出报告
        logger.warning(f"[工程量] 投资估算预计算失败（报告照出，金额留空）: {e}")
    return True

def _apply_investment_summary(filled: dict, est) -> None:
    """把投资估算压成第 1/7 章可直接落文的四项费用汇总。"""
    _reserve = getattr(est, "reserve", 0.0) or (
        getattr(est, "basic_reserve", 0.0)
        + getattr(est, "price_reserve", 0.0)
        + getattr(est, "risk", 0.0))
    filled["monitor_care_cost"] = f"{est.monitor_care:.2f} 万元"
    filled["reserve_cost"] = f"{_reserve:.2f} 万元"
    filled["investment_summary"] = (
        f"静态总投资{est.static_total:.2f}万元，"
        f"动态总投资{(est.dynamic_total or est.static_total):.2f}万元，"
        f"其中工程施工费{est.construction:.2f}万元、"
        f"其他费用{est.other:.2f}万元、"
        f"监测与管护费{est.monitor_care:.2f}万元、"
        f"预备费{_reserve:.2f}万元")
