"""勘测定界报告解析 —— 从勘测定界报告的**表格**里抽出结构化数据。

替代旧的 `kd_land_parser.py`（基于坐标网格聚类，对真实 PDF 返回空）。

数据来源优先级：
1. **XLSX**（若用户上传了 Excel 版勘测定界）—— 最可靠
2. **PDF 表格**（pdfplumber）—— 覆盖「地块情况汇总表」与「土地分类面积表」

产出：
    {
      "plots": [{plot_no, location, land_use, total_area_m2, boundary_points, note}],
      "classifications": [{owner_unit, ownership_type, total_area_m2, categories: {...}}],
      "total_area_m2": 全部地块合计,
      "boundary_points": 界址点数合计,
      "land_use": 土地用途（取自汇总表）,
      "location": 土地坐落（取自汇总表）,
      "owner_units": [权属单位…],
      "by_category": {地类名: 面积总计}   # 合计行
    }
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def resolve_material_path(path: str) -> str:
    """把材料登记里的路径还原成**磁盘上的真实路径**。

    🔴 为什么必须有这一步
    ────────────────────────────────────────────────────────────────
    `_uploaded_files` / `_project_materials` 里存的是**相对 storage** 的路径
    （`images/0-勘测定界报告-_157b8cb3.pdf`），而本解析器此前直接 `os.path.exists(path)`
    按 CWD（`backend/`）去找 —— 于是每一次都走「文件不存在」分支：

        WARNING land_survey_parser | 勘测定界文件不存在: images/0-勘测定界报告-_157b8cb3.pdf

    后果不是「少一张表」，而是**正文印着「上表所列拟征收土地总面积…」却根本没有表**
    （第6章 `[SYS_TABLE:6-1]` 渲染不出来）—— 报告自相引用一张不存在的表。

    解析顺序：原样 → `storage/<path>` → `backend/<path>`，命中即返回。
    """
    if not path:
        return path
    if os.path.exists(path):
        return path
    try:
        from app.config import settings
        for base in (settings.STORAGE_DIR, settings.BASE_DIR):
            cand = Path(base) / path
            if cand.exists():
                return str(cand)
    except Exception:
        pass
    return path

# ── 表识别特征 ──
_SUMMARY_KEYS = ("地块号", "土地坐落", "土地用途", "界址点")
_CLASSIFICATION_KEYS = ("被征", "权属类别", "面积总计")

# 地类名（用于把扁平化后的列名归一到标准地类）
_LAND_TYPES = [
    "水田", "水浇地", "旱地", "耕地", "果园", "其他园地", "其它园地", "园地",
    "乔木林地", "竹林地", "灌木林地", "其他林地", "林地",
    "天然牧草地", "人工牧草地", "其他草地", "其它草地", "草地",
    "农村道路", "沟渠", "坑塘水面", "养殖坑塘", "设施农用地", "田坎",
    "商业服务业设施用地", "工业用地", "采矿用地", "物流仓储用地",
    "城镇住宅用地", "农村宅基地", "公用设施用地", "公园与绿地", "广场用地",
    "机关团体新闻出版用地", "科教文卫用地", "特殊用地",
    "铁路用地", "公路用地", "城镇村道路用地", "交通服务场站用地", "农村道路用地",
    "河流水面", "湖泊水面", "水库水面", "内陆滩涂", "水工建筑用地", "沼泽地",
    "空闲地", "盐碱地", "沙地", "裸土地", "裸岩石砾地",
    # 🔴 「村庄」必须收录：实测源表建设用地里有这一列（汪庄组 2867㎡、集体 109㎡），
    #    漏了它那两行就**永远加不平**（表自己都对不上）。
    "村庄",
]
# 汇总类列名（不是具体地类，归到「合计/总面积」）
_TOTAL_COLS = ("面积总计", "总面积", "合计", "小计")

# ═══════════════════════════════════════════════════════════════
# 表格按**第二级**地类出（业主：「统一用勘测定界的表格格式和数据」）
# ═══════════════════════════════════════════════════════════════
# 🔴 源表是三级表头：
#      `农用地 → 耕地 → 水田/水浇地/旱地`
#      `建设用地 → 城镇村及工矿用地 → 农村宅基地/村庄`
#    只按**具体地类**出表会漏掉不认识的名字（实测漏「村庄」→ 少 2867㎡），
#    行合计与各列之和**对不上**，读者一加就发现。
#    按第二级聚合后每行严丝合缝（实测：耕地4098 + 其他农用地4077 + 城镇村及工矿用地2867 = 11042 ✓）。
_L2_LAND_TYPES = (
    "耕地", "园地", "林地", "草地", "其他农用地", "其它农用地",
    "城镇村及工矿用地", "交通运输用地", "水域及水利设施用地",
    "其他土地", "其它土地", "特殊用地",
)


@dataclass
class SurveyPlot:
    plot_no: str = ""
    location: str = ""
    land_use: str = ""
    total_area_m2: Optional[float] = None
    boundary_points: Optional[int] = None
    note: str = ""


@dataclass
class LandClassification:
    owner_unit: str = ""
    ownership_type: str = ""
    total_area_m2: Optional[float] = None
    # 第二级地类（耕地/园地/其他农用地/城镇村及工矿用地…）—— **表格列用它**，
    # 因为按这一级聚合才能保证「行合计 == 各列之和」。
    categories: Dict[str, float] = field(default_factory=dict)
    # 具体地类（水田/旱地/沟渠/农村宅基地…）—— 「主要地类」这类描述文本用它。
    sub_categories: Dict[str, float] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════

def _clean(v) -> str:
    return re.sub(r'\s+', '', str(v or "")).strip()


def _num(v) -> Optional[float]:
    s = _clean(v).replace(",", "")
    if not s or s in ("-", "—", "/"):
        return None
    m = re.match(r'^(\d+(?:\.\d+)?)$', s)
    return float(m.group(1)) if m else None


def _is_empty_row(row: List[str]) -> bool:
    return not any(_clean(c) for c in (row or []))


def _flatten_headers(rows: List[List[str]]) -> List[str]:
    """把多行合并表头扁平化成单行列名（横向+纵向前向填充后拼接）。"""
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    grid = [[_clean(r[c]) if c < len(r) else "" for c in range(ncol)] for r in rows]

    # 纵向：空单元格继承上一行同列
    for ri in range(1, len(grid)):
        for ci in range(ncol):
            if not grid[ri][ci]:
                grid[ri][ci] = grid[ri - 1][ci]
    # 横向：空单元格继承同行左侧（处理合并单元格）
    for ri in range(len(grid)):
        last = ""
        for ci in range(ncol):
            if grid[ri][ci]:
                last = grid[ri][ci]
            elif last:
                grid[ri][ci] = last

    # 拼接去重：同一列在各行的文字合并，重复的去掉
    out = []
    for ci in range(ncol):
        parts, seen = [], set()
        for ri in range(len(grid)):
            p = grid[ri][ci]
            if p and p not in seen:
                seen.add(p)
                parts.append(p)
        out.append("".join(parts))
    return out


def _match_land_type(col: str) -> Optional[str]:
    """列名 → **第二级**地类名（表格列用它，保证「合计 = 各列之和」）。

    例：`农用地耕地水田` → 耕地；`建设用地城镇村及工矿用地农村宅基地` → 城镇村及工矿用地。
    """
    c = re.sub(r'^(农用地|建设用地)', '', col or "")
    if any(t in c for t in _TOTAL_COLS):
        return None          # 「农用地总面积」「建设用地总面积」「合计」都是小计列，不是地类
    for t in _L2_LAND_TYPES:
        if t in c:
            return t
    return None


def _match_sub_land_type(col: str) -> Optional[str]:
    """列名 → **具体**地类名（「主要地类」这类文本用它，读起来更像人话）。

    例：`农用地耕地水田` → 水田。
    """
    c = col or ""
    if any(t in c for t in _TOTAL_COLS) and not any(
            t in c for t in _LAND_TYPES if t not in ("耕地", "园地", "林地", "草地")):
        return None
    for t in _LAND_TYPES:                      # 具体地类优先（列表已按具体→大类排）
        if t in c:
            return t
    return None


# ═══════════════════════════════════════════════════════════════
# 表解析
# ═══════════════════════════════════════════════════════════════

def _find_header_rows(table: List[List[str]]) -> int:
    """返回表头占用的行数（出现第一行「数据行」为止）。"""
    for i, row in enumerate(table):
        cells = [_clean(c) for c in row]
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        # 数据行特征：首列是权属单位/序号，且该行数字占比高
        nums = sum(1 for c in nonempty if _num(c) is not None)
        if i > 0 and nums >= 2 and not any(k in "".join(nonempty) for k in ("地块号", "土地坐落")):
            return i
    return min(3, len(table))


def _parse_summary(table: List[List[str]]) -> List[SurveyPlot]:
    """解析「勘测定界地块情况汇总表」。"""
    hdr_i = _find_header_rows(table)
    headers = _flatten_headers(table[:max(1, hdr_i)])
    idx = {}
    for name, kws in [("plot_no", ("地块号",)), ("location", ("土地坐落", "坐落")),
                      ("land_use", ("土地用途", "用途")),
                      ("area", ("总面积", "面积")), ("bp", ("界址点",)), ("note", ("备注",))]:
        for i, h in enumerate(headers):
            if any(k in h for k in kws):
                idx[name] = i        # 取最后一个匹配列（表头常有合并的重复名）


    plots: List[SurveyPlot] = []
    for row in table[hdr_i:]:
        cells = [_clean(c) for c in row]
        if _is_empty_row(cells):
            continue
        if any(c.startswith(("合计", "总计", "小计")) for c in cells if c):
            continue
        p = SurveyPlot(
            plot_no=cells[idx["plot_no"]] if "plot_no" in idx and idx["plot_no"] < len(cells) else "",
            location=cells[idx["location"]] if "location" in idx and idx["location"] < len(cells) else "",
            land_use=cells[idx["land_use"]] if "land_use" in idx and idx["land_use"] < len(cells) else "",
            note=cells[idx["note"]] if "note" in idx and idx["note"] < len(cells) else "",
        )
        # 地块号必须是真地块号（形如 2026-7 / 2026-10-1），排除表名等噪声
        p.plot_no = re.sub(r'\s*0?0:00:00$', '', p.plot_no).strip()
        if p.plot_no and not re.match(r'^[\w\-—/（）()]{1,20}$', p.plot_no):
            p.plot_no = ""
        if p.plot_no and any(k in p.plot_no for k in ("表", "合计", "单位", "地块情况")):
            p.plot_no = ""
        if "area" in idx and idx["area"] < len(cells):
            p.total_area_m2 = _num(cells[idx["area"]])
        if "bp" in idx and idx["bp"] < len(cells):
            bp = _num(cells[idx["bp"]])
            p.boundary_points = int(bp) if bp is not None else None
        if p.plot_no or p.location:
            plots.append(p)
    return plots


def _parse_classification(table: List[List[str]]) -> List[LandClassification]:
    """解析「土地分类面积表」。"""
    hdr_i = _find_header_rows(table)
    if hdr_i == 0:
        hdr_i = 2
    headers = _flatten_headers(table[:hdr_i])

    # 列 → 地类（同时建两级：第二级进表格，具体地类供「主要地类」文本）
    col_type: Dict[int, Optional[str]] = {}
    col_sub: Dict[int, Optional[str]] = {}
    owner_i = type_i = total_i = None
    for i, h in enumerate(headers):
        if owner_i is None and any(k in h for k in ("被征", "被占", "权属单位", "用地单位")):
            owner_i = i
        if type_i is None and "权属类别" in h:
            type_i = i
        if any(k in h for k in ("面积总计", "总面积", "合计")):
            total_i = i          # 🔴 取最后一个（前者多为「农用地总面积」等分项）
        col_type[i] = _match_land_type(h)
        col_sub[i] = _match_sub_land_type(h)

    rows_out: List[LandClassification] = []
    for row in table[hdr_i:]:
        cells = [_clean(c) for c in row]
        if _is_empty_row(cells):
            continue
        owner = cells[owner_i] if owner_i is not None and owner_i < len(cells) else ""
        if not owner:
            continue
        if owner.startswith(("国有", "集体", "合计", "总计", "小计")):
            continue
        lc = LandClassification(
            owner_unit=owner,
            ownership_type=cells[type_i] if type_i is not None and type_i < len(cells) else "",
            total_area_m2=_num(cells[total_i]) if total_i is not None and total_i < len(cells) else None,
        )
        for i in range(len(cells)):
            if i == total_i:
                continue
            v = _num(cells[i])
            if not v:
                continue
            t = col_type.get(i)
            if t:
                lc.categories[t] = lc.categories.get(t, 0.0) + v
            s = col_sub.get(i)
            if s:
                lc.sub_categories[s] = lc.sub_categories.get(s, 0.0) + v
        if lc.owner_unit:
            rows_out.append(lc)
    return rows_out


def _category_order(table: List[List[str]]) -> List[str]:
    """源表里第二级地类列**从左到右**的顺序（表格列就按它排）。"""
    hdr_i = _find_header_rows(table)
    if hdr_i == 0:
        hdr_i = 2
    order: List[str] = []
    for h in _flatten_headers(table[:hdr_i]):
        t = _match_land_type(h)
        if t and t not in order:
            order.append(t)
    return order


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def _tables_from_pdf(path: str) -> List[List[List[str]]]:
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for t in (page.extract_tables() or []):
                if t:
                    out.append([[str(c or "") for c in row] for row in t])
    return out


def _tables_from_xlsx(path: str) -> List[List[List[str]]]:
    """读 XLSX 并**按表头切分**——一个 sheet 里常混着「汇总表 + 土地分类面积表」。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(_clean(c) for c in r)]
        if rows:
            out.extend(_split_subtables(rows))
    return out


def _compact_columns(rows: List[List[str]]) -> List[List[str]]:
    """去掉**整列为空**的列。

    Excel 版勘测定界常把表格铺在很宽的区域（几十列），其中大量列全空；
    不压缩会把表头前向填充搞乱（把「序号」填到相邻空列上）。
    """
    if not rows:
        return rows
    ncol = max(len(r) for r in rows)
    keep = [ci for ci in range(ncol) if any(_clean(r[ci]) for r in rows if ci < len(r))]
    return [[(r[ci] if ci < len(r) else "") for ci in keep] for r in rows]


def _split_subtables(rows: List[List[str]]) -> List[List[List[str]]]:
    """把若干行按「表头行」切分成多张子表。

    表头行特征：整行文字里含表名关键词（地块号/被征/权属类别/面积总计）。
    """
    marks = ("地块号", "土地坐落", "被征", "被占", "权属类别", "面积总计")
    starts = [0]
    for i, row in enumerate(rows[1:], start=1):
        txt = "".join(_clean(c) for c in row)
        if any(k in txt for k in marks) and len(txt) < 200:
            # 上一条若还是表头（连续表头行）则不切
            prev = "".join(_clean(c) for c in rows[i - 1]) if i > 0 else ""
            if not any(k in prev for k in marks):
                starts.append(i)
    starts = sorted(set(starts))
    out = []
    for j, s in enumerate(starts):
        e = starts[j + 1] if j + 1 < len(starts) else len(rows)
        seg = rows[s:e]
        seg = [r for r in seg if any(_clean(c) for c in r)]     # 去空行
        if not seg:
            continue
        seg = _compact_columns(seg)
        # 压缩后至少要有一行像表头，否则丢弃（多为噪声片段）
        head_txt = "".join(_clean(c) for c in seg[0])
        if not any(k in head_txt for k in marks) and not any(
                k in head_txt for k in ("序号", "单位", "面积", "坐落", "用途")):
            continue
        out.append(seg)
    return out


def parse_land_survey(path: str) -> Dict[str, Any]:
    """解析勘测定界报告（PDF 或 XLSX），返回结构化数据。

    Args:
        path: 勘测定界报告文件路径（.pdf / .xlsx / .xls）。

    Returns:
        结构化字段字典；无法解析时返回空 dict。
    """
    import os
    # 🔴 材料登记里是**相对 storage** 的路径，先还原成真实路径再判断存在性
    path = resolve_material_path(path)
    if not path or not os.path.exists(path):
        logger.warning(f"勘测定界文件不存在: {path}")
        return {}
    ext = os.path.splitext(path)[1].lower()
    try:
        tables = _tables_from_xlsx(path) if ext in (".xlsx", ".xls") else _tables_from_pdf(path)
    except Exception as e:
        logger.warning(f"勘测定界表格提取失败 {path}: {e}")
        return {}

    plots: List[SurveyPlot] = []
    classifications: List[LandClassification] = []
    by_category: Dict[str, float] = {}
    by_sub_category: Dict[str, float] = {}
    # 🔴 表格列的**顺序**也要跟源表一致（业主：「用勘测定界的表格格式」）。
    #    不能靠"各行地类首次出现的顺序"拼——那取决于哪一行先有哪个地类，
    #    同一份材料换个行序就换了列序。
    category_order: List[str] = []

    for t in tables:
        flat = "".join(_clean(c) for row in t[:3] for c in row)
        if any(k in flat for k in _SUMMARY_KEYS) and not any(
                k in flat for k in _CLASSIFICATION_KEYS):
            # 单页可能同时含汇总表与分类表，这里只按特征择一
            if any(k in flat for k in ("地块号", "土地坐落")):
                plots.extend(_parse_summary(t))
        if any(k in flat for k in _CLASSIFICATION_KEYS):
            for _c in _category_order(t):
                if _c not in category_order:
                    category_order.append(_c)
            classifications.extend(_parse_classification(t))

    # 🔴 各地类合计由**明细行累加**得出，**不抄源表的「合计」行**。
    #    实测源表的合计行有合并单元格把「旱地」吞了（页2 合计行旱地空白），
    #    照抄会导致合计 ≠ 各明细之和，报告里那张表自己加不平（差 1343㎡）。
    for _c in classifications:
        for _k, _v in (_c.categories or {}).items():
            by_category[_k] = by_category.get(_k, 0.0) + _v
        for _k, _v in (_c.sub_categories or {}).items():
            by_sub_category[_k] = by_sub_category.get(_k, 0.0) + _v

    # 无汇总表时：从 PDF 页首的「地块号」兜底（部分勘测定界只有分类面积表）
    if not plots and ext not in (".xlsx", ".xls"):
        try:
            import fitz
            doc = fitz.open(path)
            for pg in doc:
                head = pg.get_text().strip().splitlines()[:3]
                for line in head:
                    line = line.strip()
                    if re.fullmatch(r'\d{4}[-—]\d{1,3}(?:[-—]\d{1,3})?', line):
                        plots.append(SurveyPlot(plot_no=line))
                        break
            doc.close()
        except Exception as e:
            logger.warning(f"页首地块号兜底失败: {e}")

    if not plots and not classifications:
        return {}

    # 汇总
    total_area = None
    if plots:
        areas = [p.total_area_m2 for p in plots if p.total_area_m2]
        total_area = sum(areas) if areas else None
    if not total_area and classifications:
        totals = [c.total_area_m2 for c in classifications if c.total_area_m2]
        total_area = sum(totals) if totals else None

    out: Dict[str, Any] = {
        "plots": [p.__dict__ for p in plots],
        "classifications": [c.__dict__ for c in classifications],
        "owner_units": [c.owner_unit for c in classifications],
    }
    if total_area:
        out["area_m2"] = int(total_area) if float(total_area).is_integer() else total_area
    if by_category:
        out["by_category"] = {k: (int(v) if float(v).is_integer() else v)
                              for k, v in by_category.items()}
    if by_sub_category:
        # 具体地类（水田/旱地/…）—— 「主要地类」这类描述文本用
        out["by_sub_category"] = {k: (int(v) if float(v).is_integer() else v)
                                  for k, v in by_sub_category.items()}
    if category_order:
        out["category_order"] = category_order
    if plots:
        p0 = plots[0]
        if p0.land_use:
            out["land_use"] = p0.land_use
        if p0.location:
            out["location"] = p0.location
        bps = [p.boundary_points for p in plots if p.boundary_points]
        if bps:
            out["boundary_points"] = sum(bps)
    return out


def to_extracted_tables(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把解析结果转成 `_extracted_tables` 认的表格格式（供组装器直接渲染）。

    **报告里的表就是勘测定界报告里的表**，因此：
    - 顺序与勘测定界报告一致：**地块情况汇总表在前，土地分类面积表在后**
    - 编号顺序递增（表3-1 / 表3-2），只有一张时它就是表3-1
    - 只有「有实质内容」的表才输出（空壳汇总表不输出，避免报告里出现空表）

    Returns:
        `[{headers, rows, raw_markdown, kind, row_count, col_count, _rendered_caption}]`
    """
    if not parsed:
        return []
    out: List[Dict[str, Any]] = []

    # ① 地块情况汇总表（勘测定界报告的第一张表）
    plots = parsed.get("plots") or []
    _meaningful_plots = [p for p in plots if (p.get("location") or p.get("total_area_m2"))]
    if _meaningful_plots:
        # 🔴 「总面积」必须带单位 —— 不带的话读者不知道是 ㎡ 还是 亩
        headers = ["序号", "地块号", "土地坐落", "土地用途", "总面积(㎡)", "界址点数"]
        rows = [[str(i + 1), p.get("plot_no", ""), p.get("location", ""), p.get("land_use", ""),
                 _fmt(p.get("total_area_m2")), _fmt(p.get("boundary_points"))]
                for i, p in enumerate(plots)]
        out.append({
            "headers": headers, "rows": rows,
            "raw_markdown": _rows_to_markdown(headers, rows),
            "row_count": len(rows), "col_count": len(headers),
            "kind": "land_summary",
        })

    # ② 土地分类面积表
    cls = parsed.get("classifications") or []
    if cls:
        # 🔴 列顺序取**源表表头顺序**（`category_order`），不用"各行首次出现顺序"——
        #    后者取决于哪一行先出现哪个地类，换个行序列就换位置。
        cats: List[str] = list(parsed.get("category_order") or [])
        for c in cls:
            for t in (c.get("categories") or {}):
                if t not in cats:
                    cats.append(t)
        headers = ["被征（用）地单位", "权属类别", "面积总计(㎡)"] + [f"{t}(㎡)" for t in cats]
        rows = [[c.get("owner_unit", ""), c.get("ownership_type", ""),
                 _fmt(c.get("total_area_m2"))] +
                [_fmt((c.get("categories") or {}).get(t)) for t in cats]
                for c in cls]
        tot = parsed.get("by_category") or {}
        if tot:
            rows.append(["合计", "", _fmt(parsed.get("area_m2"))] + [_fmt(tot.get(t)) for t in cats])
        out.append({
            "headers": headers, "rows": rows,
            "raw_markdown": _rows_to_markdown(headers, rows),
            "row_count": len(rows), "col_count": len(headers),
            "kind": "land_classification",
        })

    # 顺序编号：与**实际渲染位置**（第6章）一致 —— 原来是「表3-1/表3-2」，
    # 而组装器把这两张表渲染进第6章并重编成「表6-1」，正文引用对不上。
    for i, t in enumerate(out, 1):
        t["_rendered_caption"] = (
            f"表6-{i} {'勘测定界地块情况汇总表' if t['kind'] == 'land_summary' else '土地分类面积表'}")
    return out


def _fmt(v) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (ValueError, TypeError):
        return str(v)


def _rows_to_markdown(headers: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def format_for_prompt(parsed: Dict[str, Any], limit_plots: int = 20) -> str:
    """把解析结果格式化成可注入 prompt 的文本。"""
    if not parsed:
        return ""
    lines = ["## 📐 勘测定界报告（唯一权威地块数据）"]
    for p in (parsed.get("plots") or [])[:limit_plots]:
        lines.append(
            f"- 地块 {p.get('plot_no','')}｜坐落 {p.get('location','')}｜用途 {p.get('land_use','')}"
            f"｜面积 {p.get('total_area_m2','')} ㎡｜界址点 {p.get('boundary_points','')}"
        )
    cls = parsed.get("classifications") or []
    if cls:
        lines.append("土地分类面积（按权属单位，单位：㎡）：")
        types = []
        for c in cls:
            for t in (c.get("categories") or {}):
                if t not in types:
                    types.append(t)
        lines.append("  单位｜权属｜总计｜" + "｜".join(types))
        for c in cls[:20]:
            vals = "｜".join(str((c.get("categories") or {}).get(t, "")) for t in types)
            lines.append(f"  {c.get('owner_unit','')}｜{c.get('ownership_type','')}｜"
                         f"{c.get('total_area_m2','')}｜{vals}")
    if parsed.get("by_category"):
        lines.append("各地类合计：" + "、".join(
            f"{k} {v}" for k, v in parsed["by_category"].items()))
    lines.append("🔴 地类构成与各权属面积**只能**引用上表数值，禁止把分项填成总面积，禁止编造。")
    return "\n".join(lines)


# ── 会话级入口：从「这个会话上传的材料」里找勘测定界并解析 ────────────────
# 🔴 为什么要有它
#    解析勘测定界的代码原来**只**长在 `routers/report.py` 的上传分析分支里，
#    而脚本路径（`scripts/gen_full_report.py --session`）根本不经过那里 ——
#    于是同一条「出报告」的链路，界面跑得到、脚本跑不到（表6-1 时有时无）。
#    另外那段代码遍历的是 `_project_materials`（已解析材料），勘测定界常常**不在**
#    其中（本会话 177 个上传文件里只登记了 2 个），所以连界面路径也漏。
#    统一成这里一个函数：**扫描会话的上传清单**（权威、完整），两条路径都调它。

_LAND_SURVEY_HINTS = ("勘测", "测定界", "定界")


def collect_land_survey(state: dict) -> tuple:
    """扫描会话材料 → 解析勘测定界报告。

    Returns:
        (extracted_tables, parsed)：没找到 / 解析失败时返回 `([], {})`。
    """
    cands: List[tuple] = []
    for u in (state.get("_uploaded_files") or []):
        if isinstance(u, dict):
            cands.append((str(u.get("path") or ""), str(u.get("original_name") or "")))
        else:
            cands.append((str(u), ""))
    for m in (state.get("_project_materials") or []):
        if isinstance(m, dict):
            cands.append((str(m.get("source_path") or ""), str(m.get("source_name") or "")))

    seen = set()
    for path, name in cands:
        if not path or path in seen:
            continue
        seen.add(path)
        if not path.lower().endswith((".pdf", ".xlsx", ".xls")):
            continue
        if not any(k in (name + path) for k in _LAND_SURVEY_HINTS):
            continue
        parsed = parse_land_survey(path)          # 内部会把相对 storage 的路径还原
        if parsed:
            return to_extracted_tables(parsed), parsed
    return [], {}
