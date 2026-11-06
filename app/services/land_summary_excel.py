# -*- coding: utf-8 -*-
"""《土地分类面积汇总表》（80 列制式表）→ 地类面积行。

表样（业主提供的 `4_土地分类面积汇总表空白(1).xls`）：

    被征(用)地单位 | 权属类别 | 面积总计 | 湿地(00) | 耕地(01) | 种植园地(02) | …
                                       合计  水田 水浇地 旱地 …
    集体合计 …
    国有合计 …
    合计 …

模板第 4 章的表4-3（主体项目用地土地利用现状）与表4-4（临时用地土地利用现状）
就是拿这张表**按地类汇总**出来的：地类名称及编码 / 面积 / 占比。
以前只认「勘界报告」那种三段表头，这种 80 列汇总表读不出来，表4-3 直接缺失、
表4-4 只能用功能分区的面积，看着和表4-1 像。

本模块只做**读表**：`.xls`（xlrd）/`.xlsx`（openpyxl）都支持；
「主体/永久」还是「临时」由文件名或工作表名判断，判断不了默认临时用地。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _grid_xls(path: Path) -> List[List[Any]]:
    import xlrd
    wb = xlrd.open_workbook(str(path), formatting_info=False)
    sh = wb.sheet_by_index(0)
    return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]


def _grid_xlsx(path: Path) -> List[List[Any]]:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _s(v: Any) -> str:
    return str(v).replace("\n", "").replace("\r", "").strip() if v is not None else ""


def _num(v: Any) -> float:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def detect_kind(path: str | Path) -> str:
    """按文件名/工作表名判断是主体用地还是临时用地。"""
    name = Path(str(path)).stem
    if any(k in name for k in ("主体", "永久")):
        return "permanent"
    return "temp"


def read_land_summary(path: str | Path) -> Optional[Dict[str, Any]]:
    """读 80 列《土地分类面积汇总表》→ `{kind, rows, 合计_m2, 权属单位}`。"""
    p = Path(str(path or ""))
    if not p.exists() or p.suffix.lower() not in (".xls", ".xlsx", ".xlsm"):
        return None
    try:
        grid = _grid_xls(p) if p.suffix.lower() == ".xls" else _grid_xlsx(p)
    except Exception as e:
        logger.info(f"[地类汇总] 打开失败 {p.name}: {e}")
        return None
    if not grid:
        return None

    # 表头：第一个含「面积总计」的行
    r1 = next((i for i, row in enumerate(grid)
               if any("面积总计" in _s(c) for c in row)), None)
    if r1 is None:
        return None
    row1 = grid[r1]
    row2 = grid[r1 + 1] if r1 + 1 < len(grid) else []
    if not any("合计" in _s(c) for c in row2):
        return None

    # 一级地类：row1 的类别在合并单元格里只出现一次，向右补齐
    l1: Dict[int, str] = {}
    cur = ""
    for c, v in enumerate(row1):
        s = _s(v)
        if s and s not in ("被征(用)地单位", "权属类别", "面积总计", "备注"):
            cur = re.sub(r'[（(].*?[)）]', '', s).strip()
        l1[c] = cur
    # 叶子列：row2 有名字的列；「合计」列是二级小计，跳过
    leaves: Dict[int, str] = {}
    for c, v in enumerate(row2):
        s = _s(v)
        if not s or s == "合计" or "单位" in s:
            continue
        # 二级地类名（去掉编码）；若一级没有细分，也用类别名
        leaves[c] = re.sub(r'[（(].*?[)）]', '', s).strip()

    if not leaves:
        return None

    def _label(row) -> str:
        # 制式表第一列是空的，「被征(用)地单位/权属类别」在第 2 列
        return _s(row[0]) or (_s(row[1]) if len(row) > 1 else "")

    # 数据行：优先「合计」整行；否则 集体+国有 相加
    data = None
    for row in grid[r1 + 2:]:
        if row and _label(row) == "合计":
            data = row
            break
    if data is None:
        parts = [row for row in grid[r1 + 2:]
                 if row and _label(row) in ("集体合计", "国有合计")]
        if not parts:
            return None
        data = ["合计"] + [sum(_num(r[c]) for r in parts)
                          for c in range(1, len(row1))]

    rows: List[Dict[str, Any]] = []
    for c, leaf in sorted(leaves.items()):
        if c >= len(data):
            continue
        a = _num(data[c])
        if a <= 0:
            continue
        rows.append({"一级地类": l1.get(c, ""), "二级地类": leaf, "面积_m2": a})
    if not rows:
        return None

    total = 0.0
    for c, v in enumerate(row1):
        if _s(v) == "面积总计":
            total = _num(data[c]) if c < len(data) else 0.0
            break
    if total <= 0:
        total = sum(r["面积_m2"] for r in rows)
    return {
        "kind": detect_kind(p),
        "rows": rows,
        "合计_m2": round(total, 2),
        "权属单位": "",
        "source": "summary_xls",
    }


_FORM_CACHE: Optional[Dict[str, Any]] = None
_FORM_TITLE = "土地分类面积汇总表（制式空白表）"


def _parse_form_classes(path: Path) -> List[Dict[str, str]]:
    """从制式表读「一级地类(编码) → 二级地类(编码)」的有序清单。"""
    try:
        grid = _grid_xls(path) if path.suffix.lower() == ".xls" else _grid_xlsx(path)
    except Exception:
        return []
    r1 = next((i for i, row in enumerate(grid)
               if any("面积总计" in _s(c) for c in row)), None)
    if r1 is None or r1 + 1 >= len(grid):
        return []
    row1, row2 = grid[r1], grid[r1 + 1]

    def _split(t: str):
        m = re.match(r'^(.*?)[（(]\s*([0-9A-Za-z]+)\s*[)）]$', t)
        return (m.group(1), m.group(2)) if m else (t, "")

    cols: Dict[int, str] = {}
    cur = ""
    for c, v in enumerate(row1):
        t = _s(v)
        if t and t not in ("被征(用)地单位", "权属类别", "面积总计", "备注"):
            cur = t
        cols[c] = cur
    out: List[Dict[str, str]] = []
    seen = set()
    for c, v in enumerate(row2):
        t = _s(v)
        if not t or t == "合计" or "单位" in t:
            continue
        parent = cols.get(c, "")
        pname, pcode = _split(parent)
        lname, lcode = _split(t)
        key = (pname, lname)
        if key in seen:
            continue
        seen.add(key)
        out.append({"一级地类": pname, "一级编码": pcode,
                    "二级地类": lname, "二级编码": lcode})
    return out


def form_classes() -> List[Dict[str, str]]:
    """知识库里那张制式《土地分类面积汇总表》的地类顺序（每次用都查库）。"""
    global _FORM_CACHE
    if _FORM_CACHE is not None:
        return list(_FORM_CACHE.get("classes") or [])
    classes: List[Dict[str, str]] = []
    try:
        import sqlite3
        db = Path(__file__).resolve().parents[2] / "data" / "knowledge_base.db"
        c = sqlite3.connect(str(db))
        row = c.execute(
            "select file_path, structured_data_json from knowledge_documents"
            " where title=? and is_active is not 0 limit 1", (_FORM_TITLE,)).fetchone()
        c.close()
        if row:
            import json as _json
            p = Path(__file__).resolve().parents[2] / "storage" / row[0]
            if p.exists():
                classes = _parse_form_classes(p)
            if not classes and row[1]:
                data = _json.loads(row[1] or "{}")
                for cat in data.get("categories") or []:
                    for leaf in cat.get("leaves") or []:
                        classes.append({"一级地类": cat.get("name", ""),
                                        "一级编码": cat.get("code", ""),
                                        "二级地类": leaf.get("name", ""),
                                        "二级编码": leaf.get("code", "")})
    except Exception as e:
        logger.info(f"[地类汇总] 知识库取制式表跳过: {e}")
    _FORM_CACHE = {"classes": classes}
    return list(classes)


def class_sort_key(name: str) -> int:
    """地类排序键：按制式表顺序；表里没有的排最后。"""
    n = str(name or "").strip()
    for i, c in enumerate(form_classes()):
        if c.get("二级地类") == n or c.get("一级地类") == n:
            return i
    return 999
