# -*- coding: utf-8 -*-
"""第4章「土地复垦方向可行性分析」的**系统渲染表**（确定性，不经 LLM）。

样本《明远路》第 4 章有 13 张表（表4-1…表4-13）。我们此前只渲染了 3 张
（表4-4 临时用地土地利用现状统计表 / 表4-6 评价单元土地损毁情况表 /
表4-10 评价单元复垦方向表），其余 10 张全部缺失 —— 这是「样本 134 页、
我们只有 67 页」的一处主因。

本模块只补**能从已有事实源确定性推导**的那几张，一张都不编：

| 表 | 章内落点 | 数据来源 |
|---|---|---|
| 表4-8  土地复垦主导限制因素的耕地、林地、草地等级标准 | 4.4 | **规范标准值**，逐格照样本（表土剥离相关的定级尺度） |
| 表4-11 临时用地表土资源平衡分析表 | 4.5 | 面积×剥离厚度（表土剥离量 = 需剥离地类面积 × 0.2m） |
| 表4-12 临时用地水资源分析表 | 4.5 | 临时用地面积 / 耕地面积 / 复垦方向 |
| 表4-13 临时用地复垦前复垦后结构对比表 | 4.7 | `land_classification` 逐地类 + 占比 |

🔴 为什么放在「系统渲染」而不是「提示词里让模型写」：实测第 4 章的字数与页数
   一直不达标，而这几张表的每一格都是**可以算出来的**（`0.2689 公顷 × 0.2m =
   537.8 m³`）。让模型写，它要么漏、要么把「合计」串列（业已修过一次）。
   确定性的东西就用确定性的办法落。

数据形态见 `land_class_extract.extract_land_classification`：
    `{"rows": [{"一级地类": "耕地（01）", "二级地类": "水田（0101）", "面积_m2": 478.0}, …],
      "合计_m2": 8070.0, "pages": [4, 5]}`
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 需剥离表土的地类（一级类）。依据：样本第4章「本项目涉及耕地0.0478公顷，
# 林地0.2211公顷，需剥离表土…面积0.2689公顷」—— 耕地 + 林地。
_STRIP_TOP_CLASSES = ("耕地", "林地", "园地", "草地")


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _class_name(full: str) -> str:
    """「耕地（01）」→「耕地」（去掉括号里的编码，用于关键字匹配）。"""
    return re.sub(r'[（(].*?[)）]', '', str(full or "")).strip()


def _pct(part_m2: float, total_m2: float) -> str:
    """占比：两位小数、去尾零（样本印的是 5.92 / 0.92 / 27.4 / 33.79 这种写法）。"""
    if total_m2 <= 0:
        return ""
    return f"{part_m2 / total_m2 * 100:.2f}".rstrip('0').rstrip('.')


# ═══════════════════════════════════════════════════════════════
# 各表
# ═══════════════════════════════════════════════════════════════

def table_4_8() -> dict:
    """表4-8 土地复垦主导限制因素的耕地、林地、草地等级标准（规范标准值）。

    逐格取自样本第 4 章（p26–p27）—— 这是**定额/标准表**，与项目无关，照搬即可。
    样本里末行坡度区间印作「>36」，是明显的排版笔误（其余各行是 <5/5—15/15—25/25—35），
    这里按 25—35 之后的自然边界写 **>35**。
    """
    return {
        "caption": "表4-8 土地复垦主导限制因素的耕地、林地、草地等级标准",
        "headers": ["限制因素", "分级指标", "耕地等级", "林地等级", "草地等级"],
        "rows": [
            ["土壤侵蚀（%）", "<10", "1", "1", "1"],
            ["", "10—30", "2", "1", "1"],
            ["", "30—50", "3", "2", "2"],
            ["", ">50", "N", "3", "3"],
            ["坡度（°）", "<5", "1", "1", "1"],
            ["", "5—15", "2", "1", "1"],
            ["", "15—25", "3", "2", "2"],
            ["", "25—35", "N", "2", "3"],
            ["", ">35", "N", "3", "3"],
            ["覆土厚度（cm）", ">40", "1", "1", "1"],
            ["", "30—40", "2", "1", "1"],
            ["", "<30", "3", "2", "3"],
            ["土壤有机质（g/kg）", ">10", "1", "1", "1"],
            ["", "6—10", "2", "1", "1"],
            ["", "<6", "3", "2", "3"],
            ["土壤质地", "壤土", "1", "1", "1"],
            ["", "粘土、砂土", "2", "1", "1"],
            ["", "砂质、砾质", "N", "N", "3"],
            ["", "石质", "N", "N", "N"],
        ],
    }



# ═══════════════════════════════════════════════════════════════
# 表4-9 土地适宜性分值 —— 逐项打分，不做「全是 90 分」
# ═══════════════════════════════════════════════════════════════
# 权重取样本表4-8 的三级因子权重（和 = 1.00）。
_EVAL_WEIGHTS: Dict[str, float] = {
    "自然坡度": 0.10, "有效土层厚度": 0.11, "土壤质地": 0.12,
    "有机质含量": 0.12, "地貌部位": 0.10, "灌溉保证程度": 0.11,
    "灌溉方式": 0.08, "排水保证程度": 0.09, "道路通达性": 0.08,
    "田块平整": 0.09,
}
# 复垦后**基础分值**（按表4-8 分级标准对项目实际条件取值）：
#   平原 <5° → 100；覆土 30cm → 90；壤土 → 90；有机质 0.7~0.8% → 80；
#   平地 → 100；灌溉一般满足 → 80；地面灌溉 → 80；排水体系一般 → 80；
#   道路良好 → 90；田块比较平坦 → 80。
_EVAL_BASE: Dict[str, int] = {
    "自然坡度": 100, "有效土层厚度": 90, "土壤质地": 90,
    "有机质含量": 80, "地貌部位": 100, "灌溉保证程度": 80,
    "灌溉方式": 80, "排水保证程度": 80, "道路通达性": 90,
    "田块平整": 80,
}
# 损毁程度对**可恢复性因子**的折减（重度损毁要拆硬化层、重新覆土，恢复难度大）
_DAMAGE_DEDUCT = {
    "重度": {"有效土层厚度": 10, "有机质含量": 10, "灌溉保证程度": 10,
             "排水保证程度": 10, "田块平整": 10},
    "中度": {"有效土层厚度": 5, "有机质含量": 5, "灌溉保证程度": 5,
             "排水保证程度": 5, "田块平整": 5},
    "轻度": {},
}


def _eval_units(filled: dict, title: str = "") -> List[Tuple[str, float, str]]:
    """评价单元 → [(名称, 面积m², 损毁程度)]。

    有硬化/压占面积就拆成「硬化压占区 / 未硬化扰动区」（面积来自本项目），
    否则退回单一「临时用地」单元。**不编地块。
    """
    f = filled or {}
    # 🔴 评价单元**只来自用户**（每次跟甲方沟通确定，名称/个数/面积都在变）。
    #    没有分区数据就不出表，不拿「硬化压占区/未硬化扰动区」这类固定单元顶替。
    try:
        from app.services.reclamation_zones import zones_from_filled
        _z = zones_from_filled(f)
        if _z:
            return [(str(x.get("name") or ""), _num(x.get("area_m2")),
                     str(x.get("degree") or "重度")) for x in _z]
    except Exception:
        pass
    return []


def table_4_9(filled: dict, title: str = "") -> Optional[dict]:
    """表4-9 土地适宜性分值：按表4-8 的分级标准逐项赋分 + 加权求和。

    🔴 专家 2026-11-03：「表格数据完全靠自主编撰，分数要合理一些。」
       本表不再所有因子一律 90 分：
       · 每个因子按**表4-8 的分级标准**对项目实际条件取值（见 `_EVAL_BASE`）；
       · 按**损毁程度**对可恢复性因子折减（重度 −10、中度 −5）；
       · 分值 = Σ(因子分 × 权重)，权重采用复垦适宜性评价常用权重，可复核。
    """
    units = _eval_units(filled, title)
    if not units:
        return None
    # 🔴 优先用**项目实际因子**（可研/勘测/弹窗/Excel 抽出来的），
    #    抽不到的因子才回落到默认基准分，并按损毁程度折减
    #    （业主 2026-11-10：「优先从可研和勘测报告中抽取，没有再弹窗/模板」）。
    try:
        from app.services.reclamation_suitability import score_factors, grade_of
        _real = score_factors(filled or {})
    except Exception:
        _real, grade_of = {}, (lambda t: "一等地" if t >= 80 else
                               ("二等地" if t >= 60 else "三等地"))
    rows = []
    for name, area, degree in units:
        deduct = _DAMAGE_DEDUCT.get(degree, {})
        scores = {}
        for _k in _EVAL_WEIGHTS:
            if _k in _real:
                scores[_k] = int(_real[_k])          # 项目实测/用户填的值，原样用
            else:
                scores[_k] = max(0, min(100, _EVAL_BASE[_k] - deduct.get(_k, 0)))
        total = round(sum(scores[k] * _EVAL_WEIGHTS[k] for k in _EVAL_WEIGHTS), 2)
        grade = grade_of(total)
        rows.append([name] + [str(scores[k]) for k in _EVAL_WEIGHTS]
                    + [f"{total:.2f}", grade])
    return {
        "caption": "表4-9 土地适宜性分值",
        "headers": ["评价单元", "自然坡度", "有效土层厚度", "土壤质地",
                    "有机质含量", "地貌部位", "灌溉保证程度", "灌溉方式",
                    "排水保证程度", "道路通达性", "田块平整", "分值", "等级"],
        "rows": rows,
    }


def _lc(filled: dict) -> Tuple[List[dict], float]:
    """取地类行与合计（平方米）。"""
    lc = ((filled or {}).get("land_classification") or {})
    rows = lc.get("rows") if isinstance(lc, dict) else []
    rows = rows or []
    total = _num(lc.get("合计_m2")) if isinstance(lc, dict) else 0.0
    if total <= 0:
        total = sum(_num(r.get("面积_m2")) for r in rows)
    return rows, total


def table_ownership(filled: dict) -> Optional[dict]:
    """临时用地土地权属状况统计表（模板表4-6 口径）。

    行=权属单位，列=合计 + 各二级地类面积。地类表里有「权属」列就按权属拆行，
    没有就整表一行（取 filled["land_ownership"]，仍没有写「—」）。
    """
    lc = ((filled or {}).get("land_classification") or {})
    rows_in = lc.get("rows") or []
    if not rows_in:
        return None
    classes = []
    for r in rows_in:
        name = str(r.get("二级地类") or "").strip()
        if name and name not in classes:
            classes.append(name)
    if not classes:
        return None
    default_owner = str((filled or {}).get("land_ownership") or "—").strip() or "—"
    owners = {}
    for r in rows_in:
        owner = str(r.get("权属") or "").strip() or default_owner
        owners.setdefault(owner, {c: 0.0 for c in classes})
        cls = str(r.get("二级地类") or "").strip()
        if cls in owners[owner]:
            owners[owner][cls] += _num(r.get("面积_m2"))
    out = []
    for owner, vals in owners.items():
        total = sum(vals.values())
        out.append([owner] + [f"{total / 10000.0:.4f}"] +
                   [f"{vals[c] / 10000.0:.4f}" if vals[c] else "" for c in classes])
    return {
        "caption": "表4-O 临时用地土地权属状况统计表",
        "headers": ["权属单位", "合计"] + [f"{c}（hm²）" for c in classes],
        "rows": out,
    }


def _land_label(r: dict) -> str:
    """地类名称及编码：一级 + 二级 + 编码（编码按知识库制式表查）。"""
    l1 = str(r.get("一级地类") or "").strip()
    l2 = str(r.get("二级地类") or "").strip()
    code = ""
    try:
        from app.services.land_summary_excel import form_classes
        for c in form_classes():
            if str(c.get("二级地类")) == l2:
                code = str(c.get("二级编码") or "")
                if not l1:
                    l1 = str(c.get("一级地类") or "")
                break
    except Exception:
        pass
    name = " ".join(x for x in (l1, l2) if x)
    return f"{name}（{code}）" if code else name


def _land_status_table(filled: dict, source_key: str, caption: str) -> Optional[dict]:
    """按制式《土地分类面积汇总表》出「一级/二级/面积/占比」+ 小计 + 合计。

    模板表4-3/表4-4 的格式是：首列一级地类纵向合并、第二列二级地类、
    每个一级地类后跟一行「小计」，末尾「合计」跨前两列。以前拉平成 3 列，
    格式与模板不一致（业主 2026-11-05）。
    """
    lc = ((filled or {}).get(source_key) or {})
    rows_in = lc.get("rows") if isinstance(lc, dict) else []
    rows_in = [r for r in (rows_in or []) if _num(r.get("面积_m2")) > 0]
    if not rows_in:
        return None
    try:
        from app.services.land_summary_excel import class_sort_key
        rows_in.sort(key=lambda r: (class_sort_key(r.get("二级地类")),
                                    class_sort_key(r.get("一级地类"))))
    except Exception:
        pass
    total = _num(lc.get("合计_m2")) if isinstance(lc, dict) else 0.0
    if total <= 0:
        total = sum(_num(r.get("面积_m2")) for r in rows_in)

    out = []
    group_name, group_sum, group_open = None, 0.0, False
    for r in rows_in:
        l1 = str(r.get("一级地类") or "").strip() or "—"
        if group_open and l1 != group_name:
            out.append([group_name, "小计", f"{group_sum / 10000.0:.4f}",
                        _pct(group_sum, total)])
            group_sum = 0.0
        group_name = l1
        group_open = True
        m2 = _num(r.get("面积_m2"))
        group_sum += m2
        out.append([l1, _land_label(r).split(" ", 1)[-1] if " " in _land_label(r)
                    else _land_label(r), f"{m2 / 10000.0:.4f}", _pct(m2, total)])
    if group_open:
        out.append([group_name, "小计", f"{group_sum / 10000.0:.4f}",
                    _pct(group_sum, total)])
    out.append(["合计", "合计", f"{total / 10000.0:.4f}", "100"])
    return {"caption": caption,
            "headers": ["地类名称及编码", "地类名称及编码",
                        "面积（hm²）", "占比（%）"],
            "rows": out, "land_groups": True}


def table_4_2(filled: dict) -> Optional[dict]:
    """表4-4 临时用地土地利用现状统计表（制式表口径，3 列 + 合计）。"""
    return _land_status_table(filled, "land_classification",
                              "表4-4 临时用地土地利用现状统计表")


def table_4_3_permanent(filled: dict) -> Optional[dict]:
    """表4-3 主体项目用地土地利用现状统计表（数据来自主体用地汇总表）。"""
    return _land_status_table(filled, "permanent_land_classification",
                              "表4-3 主体项目用地土地利用现状统计表")


def table_damage_inventory(filled: dict) -> Optional[dict]:
    """表4-2 临时用地拟损毁情况统计表（按功能分区合并同类项 + 地块个数 + 逐个地块面积）。

    业主 2026-11-05 实测：模型把「施工场站」拆成两行重复写、地块个数一律写 1；
    随后又比对模板指出「只算了总计和各个模块的总计，没有岔开」—— 模板 表4-2
    的面积是**两栏**：左「分区小计」（纵向合并），右「逐个地块面积」（一行一个）。

    数据来源（基础资料 Sheet「临时用地功能分区」，**同名分区写多行**即为逐个地块）：
        施工场站 0.4200 / 施工场站 0.5600  → 小计 0.9800，2 个地块
    没有逐地块数据时退回单行（只出小计），地块个数仍取备注「地块数=N」或留「—」。
    """
    try:
        from app.services.reclamation_zones import zones_from_filled
        zones = zones_from_filled(filled)
    except Exception:
        zones = []
    if not zones:
        return None
    _counts = (filled or {}).get("zone_plot_counts") or {}
    order, groups = [], {}
    for z in zones:
        name = str(z.get("name") or "").strip() or "—"
        if name not in groups:
            order.append(name)
            groups[name] = {"area": 0.0, "counts": [], "plots": [],
                            "degree": str(z.get("degree") or "").strip()}
        groups[name]["area"] += _num(z.get("area_m2"))
        groups[name]["plots"].extend(
            _num(a) for a in (z.get("plot_areas_m2") or []) if _num(a) > 0)
        cnt = z.get("plot_count")
        if cnt in (None, "", 0):
            cnt = _counts.get(name)
        try:
            if cnt not in (None, ""):
                groups[name]["counts"].append(int(cnt))
        except (TypeError, ValueError):
            pass

    # 任一分区有逐个地块面积 → 用模板的「小计 + 地块面积」两栏结构
    has_plots = any(len(g["plots"]) > 1 for g in groups.values())
    rows: List[list] = []
    group_sizes: List[int] = []
    for i, name in enumerate(order, 1):
        g = groups[name]
        cnt = sum(g["counts"]) if g["counts"] else "—"
        plots = g["plots"]
        _total = f"{g['area'] / 10000.0:.4f}"
        if has_plots:
            if len(plots) > 1:
                if cnt == "—":
                    cnt = len(plots)
                rows.append([str(i), name, _total, f"{plots[0] / 10000.0:.4f}",
                             str(cnt), g["degree"] or "—"])
                for a in plots[1:]:
                    rows.append(["", "", "", f"{a / 10000.0:.4f}", "", ""])
                group_sizes.append(len(plots))
            else:
                rows.append([str(i), name, _total,
                             f"{plots[0] / 10000.0:.4f}" if plots else "",
                             str(cnt), g["degree"] or "—"])
                group_sizes.append(1)
        else:
            rows.append([str(i), name, _total, str(cnt), g["degree"] or "—"])
    total = sum(_num(z.get("area_m2")) for z in zones)
    if has_plots:
        rows.append(["合计", "", f"{total / 10000.0:.4f}", "", "", ""])
        return {"caption": "表4-D 临时用地拟损毁情况统计表",
                "headers": ["序号", "地块名称", "面积（hm²）", "", "地块个数", "损毁程度"],
                "rows": rows, "plot_groups": True, "group_sizes": group_sizes}
    rows.append(["合计", "", f"{total / 10000.0:.4f}", "", ""])
    return {"caption": "表4-D 临时用地拟损毁情况统计表",
            "headers": ["序号", "地块名称", "面积（hm²）", "地块个数", "损毁程度"],
            "rows": rows}


def table_4_7(filled: dict, title: str = "") -> Optional[dict]:
    """表4-7 评价单元土地损毁情况表（样本 p23：单元/类型/损毁方式/面积/程度/复垦情况）。

    🔴 2026-11-02 线上实测：这张表模型只写了表头和空行 —— 面积与原地类明明
       就在勘测定界地类表里。改由系统出数：面积取地类合计，损毁方式/程度取事实卡，
       取不到按**临时用地施工**的样本口径（压占/重度/待复垦）。
    """
    rows_in, total = _lc(filled)
    if total <= 0:
        return None
    _kind = str((filled or {}).get("damage_type") or "").strip() or "压占"
    _degree = str((filled or {}).get("damage_degree") or "").strip() or "重度"
    # 有用户给的功能分区 → 逐分区出（与表1-1 同一份数据）
    try:
        from app.services.reclamation_zones import zones_from_filled
        _zones = zones_from_filled(filled)
    except Exception:
        _zones = []
    if _zones:
        rows = []
        for i, z in enumerate(_zones, 1):
            rows.append([str(i), str(z.get("name") or ""),
                         str(z.get("method") or _kind),
                         f"{_num(z.get('area_m2')) / 10000.0:.4f}",
                         str(z.get("degree") or _degree), "待复垦"])
        _zsum = sum(_num(z.get("area_m2")) for z in _zones)
        rows.append(["合计", "", "", f"{_zsum / 10000.0:.4f}", "", ""])
        return {
            "caption": "表4-7 评价单元土地损毁情况表",
            "headers": ["评价单元", "单元类型", "损毁类型", "面积（hm²）",
                        "损毁程度", "复垦情况"],
            "rows": rows,
        }
    # 🔴 没有用户提供的评价单元就不出表 —— 不用「临时用地」这种固定单元顶替
    return None


def table_4_10(filled: dict, title: str = "") -> Optional[dict]:
    """表4-10 评价单元复垦方向表（样本 p27：单元/占用原土地类型/面积/措施/方向）。

    占用原土地类型与复垦方向都取地类表的二级地类（本项目按恢复原地类设计），
    措施按样本口径写「工程措施、生物化学措施」——这三项都是可确定推出的。
    """
    rows_in, total = _lc(filled)
    if total <= 0:
        return None
    _types = "、".join(str(r.get("二级地类") or "").strip()
                      for r in rows_in if str(r.get("二级地类") or "").strip())
    _types = _types or "原地类"
    # 评价单元只来自用户（表1-1 的同一份分区）；没给就不出表
    try:
        from app.services.reclamation_zones import zones_from_filled
        _zones = zones_from_filled(filled)
    except Exception:
        _zones = []
    if not _zones:
        return None
    def _direction(z) -> str:
        """复垦方向是**一个地类方向**，不是原土地类型清单。

        业主 2026-11-04 指出：原实现把「水田、农村道路、沟渠」直接抄进
        「复垦方向」列，属于类型错误 —— 那是占用前的现状，不是复垦后的方向。
        优先取用户填的 `reclaim_direction`；没填就按原地类归并到主导方向。
        """
        d = str(z.get("reclaim_direction") or "").strip()
        if d:
            return d
        t = str(z.get("land_types") or "")
        if "水田" in t or "耕地" in t:
            return "水田"
        if "旱地" in t:
            return "旱地"
        if "林地" in t:
            return "林地"
        if "坑塘" in t or "沟渠" in t:
            return "水域及水利设施用地"
        return "原地类"

    rows = []
    for i, z in enumerate(_zones, 1):
        _t = str(z.get("land_types") or "").strip() or _types
        rows.append([str(i), str(z.get("name") or ""), _t,
                     f"{_num(z.get('area_m2')) / 10000.0:.4f}",
                     "工程措施、生物化学措施", _direction(z)])
    _zsum = sum(_num(z.get("area_m2")) for z in _zones)
    rows.append(["合计", "", "", f"{_zsum / 10000.0:.4f}", "", ""])
    return {
        "caption": "表4-10 评价单元复垦方向表",
        "headers": ["序号", "评价单元", "占用原土地类型", "面积（hm²）",
                    "主要复垦措施", "复垦方向"],
        "rows": rows,
    }


def table_4_11(filled: dict) -> Optional[dict]:
    """表4-11 临时用地表土资源平衡分析表（= 剥离量 × 回填量，可算）。

    样本口径：剥离面积 = 需剥离表土的地类面积（耕地 + 林地 = 0.0478 + 0.2211
    = 0.2689 公顷），剥离/覆土厚度 0.2 m，土方量 = 面积 × 厚度
    （0.2689 × 10000 × 0.2 = 537.8 m³）。「合计」行的两个量相等即「剥覆平衡」。
    """
    rows_in = ((filled or {}).get("land_classification") or {}).get("rows") or []
    _strip_m2 = sum(_num(r.get("面积_m2")) for r in rows_in
                    if _class_name(r.get("一级地类")) in _STRIP_TOP_CLASSES)
    if _strip_m2 <= 0:
        return None
    # 🔴 厚度只取本项目填的值（cm 或 m 两种写法都认）；没填就不出这张表，
    #    不拿范文的 0.2/0.3 顶替（业主 2026-11-03）。
    _tv = _num((filled or {}).get("topsoil_thickness"))
    if _tv <= 0:
        return None
    _thick = _tv / 100.0 if _tv > 1.0 else _tv
    _ha = _strip_m2 / 10000.0
    _vol = round(_strip_m2 * _thick, 1)
    return {
        "caption": "表4-11 临时用地表土资源平衡分析表",
        "headers": ["名称", "面积（公顷）", "剥离表土厚度（m）", "可供土方量（m³）",
                    "覆土面积（公顷）", "覆厚度（m）", "土方需求量（m³）"],
        "rows": [
            ["临时用地（复垦前）", f"{_ha:.4f}", f"{_thick:.1f}", f"{_vol:.1f}", "", "", ""],
            ["临时用地（复垦后）", "", "", "", f"{_ha:.4f}", f"{_thick:.1f}", f"{_vol:.1f}"],
            ["合计", "", "", f"{_vol:.1f}", "", "", f"{_vol:.1f}"],
        ],
    }


def table_4_12(filled: dict) -> Optional[dict]:
    """表4-12 临时用地水资源分析表。

    样本口径：类型 = 临时用地；面积 = 临时用地合计面积；水源 = 周边水源。
    🔴 「可复垦耕地面积」取**剥离表土面积**（耕地+园地+林地+草地），与表4-11
       同一口径 —— 用「耕地面积」会漏掉复垦为旱地的园地（专家 2026-11-09：
       表4-11 是 2.5900，本表写 2.2800 / 方向只写「旱地」，前后打架）。
    🔴 复垦方向取分区表里用户填的方向（去重），不再取最后一个耕地二级类。
    """
    lc = ((filled or {}).get("land_classification") or {})
    rows_in = lc.get("rows") or []
    _total_m2 = _num(lc.get("合计_m2")) or sum(_num(r.get("面积_m2")) for r in rows_in)
    if _total_m2 <= 0:
        # 兜底取临时用地总面积字段
        _total_m2 = _num((filled or {}).get("area_m2"))
    if _total_m2 <= 0:
        return None
    _farm_m2 = sum(
        _num(r.get("面积_m2")) for r in rows_in
        if _class_name(r.get("一级地类")) in _STRIP_TOP_CLASSES)
    _dirs: List[str] = []
    try:
        from app.services.reclamation_zones import zones_from_filled
        for _z in zones_from_filled(filled) or []:
            _d = str((_z or {}).get("reclaim_direction") or "").strip()
            if _d and _d not in _dirs:
                _dirs.append(_d)
    except Exception:
        pass
    if not _dirs:
        for r in rows_in:
            if _class_name(r.get("一级地类")) == "耕地":
                _d = _class_name(r.get("二级地类"))
                if _d and _d not in _dirs:
                    _dirs.append(_d)
    return {
        "caption": "表4-12 临时用地水资源分析表",
        "headers": ["类型", "面积（公顷）", "可复垦耕地面积（公顷）",
                    "复垦方向", "水源"],
        "rows": [["临时用地", f"{_total_m2 / 10000.0:.4f}",
                  f"{_farm_m2 / 10000.0:.4f}" if _farm_m2 else "—",
                  "、".join(_dirs) or "耕地", "周边水源"]],
    }


def table_4_13(filled: dict) -> Optional[dict]:
    """表4-13 临时用地复垦前复垦后结构对比表。

    逐地类两列（复垦前/复垦后）+ 各自占比 —— 数据全部来自 `land_classification`。
    复垦后按样本口径与复垦前一致（本项目复垦方向为恢复原地类）。
    """
    lc = ((filled or {}).get("land_classification") or {})
    rows_in = lc.get("rows") or []
    _total_m2 = _num(lc.get("合计_m2")) or sum(_num(r.get("面积_m2")) for r in rows_in)
    if not rows_in or _total_m2 <= 0:
        return None
    out: List[List[str]] = []
    for r in rows_in:
        _m2 = _num(r.get("面积_m2"))
        _name = f"{r.get('一级地类') or ''} {r.get('二级地类') or ''}".strip()
        _p = _pct(_m2, _total_m2)
        out.append([_name, f"{_m2 / 10000.0:.4f}", _p,
                    f"{_m2 / 10000.0:.4f}", _p])
    out.append(["合计", f"{_total_m2 / 10000.0:.4f}", "100",
                f"{_total_m2 / 10000.0:.4f}", "100"])
    return {
        "caption": "表4-13 临时用地复垦前复垦后结构对比表",
        "headers": ["地类名称及编码", "复垦前面积（公顷）", "复垦前占比（%）",
                    "复垦后面积（公顷）", "复垦后占比（%）"],
        "rows": out,
    }


# ═══════════════════════════════════════════════════════════════
# 落点：小节号 → 表
# ═══════════════════════════════════════════════════════════════
# 键 = 二级小节号（`## 4.5 水土资源平衡分析` 里的 4.5）。
# 装配器在新小节标题出现时，把**归属更早小节**的表先落下去（见
# `report_assembler._add_reclamation_ch4_tables` / `_flush_ch4_tables`）。
# 值与 `_add_reclamation_ch4_tables` 里的引导语一对一。

_SECTION_OF: Dict[str, str] = {
    "表4-2": "4.2",
    "表4-7": "4.3",
    "表4-8": "4.4",
    "表4-9": "4.4",
    "表4-10": "4.4",
    "表4-11": "4.5",
    "表4-12": "4.5",
    "表4-13": "4.7",
    "表4-Q": "4.4",
    "表4-O": "4.2",
    "表4-P": "4.2",
    "表4-D": "4.1",
}

# 表前的引导语（样本惯例：表前必有一句引出语，禁止表格「悬空」）。
_LEAD_IN: Dict[str, str] = {
    "表4-2": "本项目临时用地范围内各地类的面积及占总面积比例见表4-2。",
    "表4-7": "本项目临时用地各评价单元的损毁方式、损毁面积与损毁程度见表4-7。",
    "表4-8": "本项目土地复垦适宜性评价以土壤侵蚀、坡度、覆土厚度、土壤有机质含量、"
             "土壤质地等作为评价指标，各限制因素对应的耕地、林地、草地等级标准见表4-8。",
    "表4-9": "依据表4-8 的分级标准，结合各评价单元复垦后的实际条件逐项赋分，"
             "按复垦适宜性评价常用权重加权计算各单元土地适宜性分值，见表4-9。",
    "表4-10": "根据适宜性评价结果，本项目各评价单元按恢复原地类确定复垦方向，"
              "复垦方向与主要复垦措施见表4-10。",
    "表4-11": "根据项目区土地利用现状，需剥离表土的地类主要为耕地和林地，"
              "剥离后的表土集中堆存并全部回覆于复垦单元。临时用地表土资源平衡"
              "分析见表4-11。",
    "表4-12": "复垦后耕地的用水问题主要表现在水源与地块间的输送，项目涉及地块"
              "周边灌溉系统完善，可满足复垦后耕地灌溉用水需求。临时用地水资源"
              "分析见表4-12。",
    "表4-13": "本项目拟复垦土地面积以复垦前土地利用现状为基础，复垦方向为恢复"
              "原地类，复垦前后土地利用结构对比见表4-13。",
    "表4-Q": "依据适宜性评价结果，各评价单元的土地质量等级见表4-10。",
    "表4-O": "本项目临时用地土地权属状况见表4-6。",
    "表4-P": "本项目主体项目用地土地利用现状见表4-3。",
    "表4-D": "本项目临时用地拟损毁情况见表4-2。",
}


_PLAN_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "表4-2": ("临时用地", "土地利用现状"),
    "表4-P": ("主体项目用地", "土地利用现状"),
    "表4-7": ("评价单元", "损毁"),
    "表4-8": ("等级标准",),
    "表4-9": ("适宜性分值",),
    "表4-10": ("复垦方向",),
    "表4-11": ("表土", "平衡"),
    "表4-12": ("水资源",),
    "表4-13": ("复垦前", "复垦后"),
    "表4-Q": ("土地质量等级",),
    "表4-O": ("权属",),
    "表4-D": ("拟损毁",),
}


def _norm(s: str) -> str:
    return re.sub(r'\s+', '', str(s or ""))


def _match_plan(key: str, plan) -> Optional[dict]:
    kws = _PLAN_KEYWORDS.get(key, ())
    for e in (plan or []):
        t = _norm(e.get("title"))
        if kws and all(k in t for k in kws):
            return e
    return None


def _clean_plan_title(title: str) -> str:
    t = re.sub(r'\s+', ' ', str(title or "")).strip()
    t = re.sub(r'\s*单位[:：].*$', '', t).strip()
    return t


def table_quality_grade(filled: dict, title: str = "") -> Optional[dict]:
    """临时用地土地质量等级（模板表4-10：评价单元 / 适宜分项 / 土地质量等级）。

    分值直接取表4-9 的加权总分与等级，不另算。
    """
    t9 = table_4_9(filled, title)
    if not t9:
        return None
    rows = []
    for r in t9.get("rows") or []:
        if len(r) < 3:
            continue
        rows.append([r[0], r[-2], r[-1]])
    if not rows:
        return None
    return {
        "caption": "表4-Q 临时用地土地质量等级",
        "headers": ["评价单元", "适宜分项", "土地质量等级"],
        "rows": rows,
    }


def build_ch4_tables(filled: dict, title: str = "",
                     plan: Optional[List[dict]] = None) -> List[Tuple[str, dict]]:
    """产出 `[(小节号, {caption, headers, rows, lead}), …]`，按小节号升序。

    取不到数据的表**整张不出**（宁可少一张，不印空表 —— 业主硬约束）。
    `title` 是报告标题，用于推评价单元名（如「施工便道」）。
    `plan` 是**当前模板**的第四章表格清单（`template_table_copy.chapter_table_plan`）：
    有它时，只出模板里有的表，表号/表题按模板走（明远与井神的表号本来就不一样）。
    """
    cands: List[Tuple[str, Optional[dict]]] = [
        ("表4-D", table_damage_inventory(filled)),
        ("表4-P", table_4_3_permanent(filled)),
        ("表4-2", table_4_2(filled)),
        ("表4-O", table_ownership(filled)),
        ("表4-7", table_4_7(filled, title)),
        ("表4-8", table_4_8()),
        ("表4-9", table_4_9(filled, title)),
        ("表4-Q", table_quality_grade(filled, title)),
        ("表4-10", table_4_10(filled, title)),
        ("表4-11", table_4_11(filled)),
        ("表4-12", table_4_12(filled)),
        ("表4-13", table_4_13(filled)),
    ]
    out: List[Tuple[str, dict]] = []
    for key, tbl in cands:
        if not tbl:
            logger.info(f"[表4-x] {key} 数据不足，整张不渲染")
            continue
        tbl = dict(tbl)
        if plan is not None:
            hit = _match_plan(key, plan)
            if not hit:
                logger.info(f"[表4-x] {key} 模板第四章没有这张表，跳过：{tbl.get('caption')}")
                continue
            tbl["caption"] = f"表4-{hit['no']} {_clean_plan_title(hit.get('title'))}".strip()
        _lead = _LEAD_IN.get(key, "")
        if plan is not None and tbl.get("caption"):
            _m = re.match(r'表4-(\d+)', str(tbl["caption"]))
            if _m:
                _lead = re.sub(r'表4-\d+', f'表4-{_m.group(1)}', _lead)
        tbl["lead"] = _lead
        out.append((_SECTION_OF.get(key, "4.7"), tbl))
    out.sort(key=lambda kv: kv[0])
    return out
