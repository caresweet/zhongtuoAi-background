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
# 表土剥离厚度（m）。样本第4章：「项目区表土层厚度为20cm左右」。
_DEFAULT_STRIP_THICKNESS_M = 0.2


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


def _land_ha(r: dict) -> float:
    return _num(r.get("面积_m2")) / 10000.0


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
    _thick = _num((filled or {}).get("topsoil_thickness")) or _DEFAULT_STRIP_THICKNESS_M
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

    样本口径：类型 = 临时用地；面积 = 临时用地合计面积；可复垦耕地面积 = 耕地面积；
    复垦方向 = 耕地二级类（水田/水浇地/旱地，取不到就写「耕地」）；水源 = 周边水源。
    """
    lc = ((filled or {}).get("land_classification") or {})
    rows_in = lc.get("rows") or []
    _total_m2 = _num(lc.get("合计_m2")) or sum(_num(r.get("面积_m2")) for r in rows_in)
    if _total_m2 <= 0:
        # 兜底取临时用地总面积字段
        _total_m2 = _num((filled or {}).get("area_m2"))
    if _total_m2 <= 0:
        return None
    _farm_m2, _dir = 0.0, "耕地"
    for r in rows_in:
        if _class_name(r.get("一级地类")) == "耕地":
            _farm_m2 += _num(r.get("面积_m2"))
            _sec = _class_name(r.get("二级地类"))
            if _sec:
                _dir = _sec
    return {
        "caption": "表4-12 临时用地水资源分析表",
        "headers": ["类型", "面积（公顷）", "可复垦耕地面积（公顷）",
                    "复垦方向", "水源"],
        "rows": [["临时用地", f"{_total_m2 / 10000.0:.4f}",
                  f"{_farm_m2 / 10000.0:.4f}" if _farm_m2 else "—", _dir, "周边水源"]],
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
    "表4-8": "4.4",
    "表4-11": "4.5",
    "表4-12": "4.5",
    "表4-13": "4.7",
}

# 表前的引导语（样本惯例：表前必有一句引出语，禁止表格「悬空」）。
_LEAD_IN: Dict[str, str] = {
    "表4-8": "本项目土地复垦适宜性评价以土壤侵蚀、坡度、覆土厚度、土壤有机质含量、"
             "土壤质地等作为评价指标，各限制因素对应的耕地、林地、草地等级标准见表4-8。",
    "表4-11": "根据项目区土地利用现状，需剥离表土的地类主要为耕地和林地，"
              "剥离后的表土集中堆存并全部回覆于复垦单元。临时用地表土资源平衡"
              "分析见表4-11。",
    "表4-12": "复垦后耕地的用水问题主要表现在水源与地块间的输送，项目涉及地块"
              "周边灌溉系统完善，可满足复垦后耕地灌溉用水需求。临时用地水资源"
              "分析见表4-12。",
    "表4-13": "本项目拟复垦土地面积以复垦前土地利用现状为基础，复垦方向为恢复"
              "原地类，复垦前后土地利用结构对比见表4-13。",
}


def build_ch4_tables(filled: dict) -> List[Tuple[str, dict]]:
    """产出 `[(小节号, {caption, headers, rows, lead}), …]`，按小节号升序。

    取不到数据的表**整张不出**（宁可少一张，不印空表 —— 业主硬约束）。
    """
    cands: List[Tuple[str, Optional[dict]]] = [
        ("表4-8", table_4_8()),
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
        tbl["lead"] = _LEAD_IN.get(key, "")
        out.append((_SECTION_OF.get(key, "4.7"), tbl))
    out.sort(key=lambda kv: kv[0])
    return out
