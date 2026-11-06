# -*- coding: utf-8 -*-
"""从勘测定界的「土地分类面积表」**视觉读出**各地类面积。

## 为什么要走视觉

复垦第4章的表4-2「临时用地土地利用现状统计表」与第6章工程量测算都要各地类面积，
而这份数据就在勘测定界报告里 —— 但**文本层读不出来**：

    实测（明远佳苑临时用地勘测定界报告）PDF 文本层把单元格边界丢了：
        集体 74.02211.02763.02580.02727.0 5307.0 8070.0
    （`74.0 | 2211.0 | 2763.0 | 2580.0 | 2727.0` 五个数字连成一串）

四条路都试过，全不通：`land_survey_parser` 返回 `{}`；`pdfplumber.extract_tables()`
逐页一张表都认不出；`pdf_data_extractor` 是 LLM/视觉驱动（不是确定性解析）；
坐标聚类只能还原表头，数据行仍是一个连体词。

所以这里让视觉模型把表读出来 —— **只读印着的数字**，读不清的行整行丢弃
（宁缺勿错：错了会一路传到工程量与投资估算）。

## 产物

按样本表4-4 的口径：

    {"rows": [{"一级地类": "耕地（01）", "二级地类": "水田（0101）", "面积_m2": 478.0}, …],
     "合计_m2": 8070.0, "pages": [4, 5]}

单位统一折算成 **平方米**（表里写公顷就 ×10000）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 🔴 哪些材料名可能是「勘测定界成果」——决定要不要跑视觉读数。
#    2026-11-02 实测：井神那批文件叫「勘界图-已去除林地.pdf」「井神现状图…」
#    「宗地图 Model (1).pdf」，一条都不含「勘测定界/勘测」→ 候选为空，地类全程没读，
#    报告表的「复垦区土地利用现状/复垦土地面积」两张表只剩 0.0000。
_LAND_CLASS_NAME_KWS = (
    "勘测定界", "勘测", "勘界", "现状图", "宗地图", "地类", "土地利用现状",
    # 制式《土地分类面积汇总表》（业主 2026-11-05 提供）
    "土地分类面积", "分类面积", "面积汇总表",
)


def is_land_class_material(name: str) -> bool:
    """材料名像不像「勘测定界成果」（含土地分类面积表的那类文件）。"""
    return any(k in str(name or "") for k in _LAND_CLASS_NAME_KWS)


def read_survey_meta_xlsx(path: str) -> Dict[str, str]:
    """从勘界 Excel 的《勘测界定表》读封面级事实（土地座落/单位地址/单位性质…）。

    表是「标签 | 值 | 标签 | 值」的一行四格形态，标签在左、值在右邻格。
    只返回读到非空值的键，读不到就空着（**不猜**）。
    """
    p = Path(str(path or ""))
    if not p.exists() or p.suffix.lower() not in (".xlsx", ".xlsm"):
        return {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(p), data_only=True)
    except Exception:
        return {}
    ws = next((w for w in wb.worksheets if "勘测界定表" in str(w.title or "")), None)
    if ws is None:
        ws = next((w for w in wb.worksheets if "勘测定界表" in str(w.title or "")), None)
    if ws is None:
        return {}
    _KEYS = {
        "土地座落": "location",
        "单位地址": "unit_address",
        "单位性质": "unit_nature",
        "联系电话": "contact_phone",
        "联系人": "contact_name",
        "界址点数": "plot_count",
    }
    out: Dict[str, str] = {}
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row]
        for i, c in enumerate(cells):
            key = _KEYS.get(c.replace(" ", ""))
            if not key or key in out:
                continue
            # 值一定在**紧邻右格**：往后找第一个非空会串到下一个标签
            # （实测「单位地址｜（空）｜联系电话｜（空）」会读出 unit_address=联系电话）
            val = cells[i + 1].strip() if i + 1 < len(cells) else ""
            if val and val not in ("无", "/", "—"):
                out[key] = val
    return out


def read_land_class_xlsx(path: str) -> Dict[str, Any]:
    """从勘测定界报告 **Excel** 的《土地分类面积表》确定性读地类面积。

    🔴 2026-11-02：井神材料里就有 `井神临时用地勘界报告.xlsx`，表结构是三段表头：

        权属单位 | 农用地（耕地→水田/旱地/水浇地、园地、林地、草地…）
                 | 建设用地（商业/工业/宅基地…） | 未利用地 | 合计

    以前管线只认 PDF 视觉读数，Excel 完全没用上 —— 报告表「复垦区土地利用现状」/
    「复垦土地面积」两张表只剩 0.0000，第6章工程量与投资估算直接跳过。
    Excel 是**确定性的**、零 token，应当优先于视觉。

    返回与 `extract_land_classification` 同构：
        `{"rows": [...], "合计_m2": float, "pages": [], "source": "xlsx", "权属单位": str}`
    """
    p = Path(str(path or ""))
    if not p.exists() or p.suffix.lower() not in (".xlsx", ".xlsm"):
        return {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(p), data_only=True)
    except Exception as e:
        logger.warning(f"[地类] 读取 Excel 失败 {p.name}: {e}")
        return {}
    ws = next((w for w in wb.worksheets if "土地分类面积" in str(w.title or "")), None)
    if ws is None:
        return {}
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    if not grid:
        return {}

    def _t(v) -> str:
        return str(v).strip().replace("\n", "") if v is not None else ""

    def _num(v) -> float:
        try:
            s = str(v).replace(",", "").strip()
            return float(s) if s else 0.0
        except Exception:
            return 0.0

    # 表头定位：第一层含「农用地」，第二层含「耕地/园地/林地」，第三层是叶子（水田…）
    r1 = next((i for i, row in enumerate(grid) if any(_t(c) == "农用地" for c in row)), None)
    if r1 is None:
        return {}
    r2 = r1 + 1
    while r2 < len(grid) and not any(_t(c) in ("耕地", "园地", "林地") for c in grid[r2]):
        r2 += 1
    if r2 >= len(grid):
        return {}
    r3 = r2 + 1
    if r3 >= len(grid):
        return {}
    row1, row2, row3 = grid[r1], grid[r2], grid[r3]

    # 合并单元格的值只存在左上角 → 用 merged_cells 把「农用地 / 耕地」按列展开
    from openpyxl.utils import range_boundaries
    l1: Dict[int, str] = {}
    l2: Dict[int, str] = {}
    for rng in ws.merged_cells.ranges:
        min_c, min_r, max_c, max_r = range_boundaries(str(rng))
        val = _t(ws.cell(min_r, min_c).value)
        if not val:
            continue
        if min_r <= r1 + 1 <= max_r:
            for c in range(min_c, max_c + 1):
                l1.setdefault(c, val)
        if min_r <= r2 + 1 <= max_r:
            for c in range(min_c, max_c + 1):
                l2.setdefault(c, val)
    for c, v in enumerate(row1, start=1):
        if _t(v):
            l1.setdefault(c, _t(v))
    for c, v in enumerate(row2, start=1):
        if _t(v):
            l2.setdefault(c, _t(v))

    # 叶子列：第三层给出最细地类（水田/旱地/…），没有就用第二层（其他草地/农村道路…）
    leaves: Dict[int, str] = {}
    for c in range(2, max(len(row3), len(row2)) + 1):
        leaf = _t(row3[c - 1]) if c - 1 < len(row3) else ""
        if not leaf:
            leaf = _t(row2[c - 1]) if c - 1 < len(row2) else ""
        if leaf and leaf != "合计":
            leaves[c] = leaf
    if not leaves:
        return {}

    # 数据行：优先取第一列写着「合计」的那行（避免各村分列重复相加），没有就取第一个有数的行
    data_row = None
    for row in grid[r3 + 1:]:
        if row and _t(row[0]) == "合计" and any(_num(v) > 0 for v in row[1:]):
            data_row = row
    if data_row is None:
        for row in grid[r3 + 1:]:
            if row and any(_num(v) > 0 for v in row[1:]):
                data_row = row
                break
    if data_row is None:
        return {}

    rows: List[dict] = []
    for c, leaf in sorted(leaves.items()):
        a = _num(data_row[c - 1]) if c - 1 < len(data_row) else 0.0
        if a <= 0:
            continue
        rows.append({"一级地类": l1.get(c, ""), "二级地类": leaf, "面积_m2": a})
    if not rows:
        return {}

    # 合计列：第一层写着「合计」的那一列；没有就按行求和
    total = 0.0
    for c, v in l1.items():
        if v == "合计":
            total = _num(data_row[c - 1]) if c - 1 < len(data_row) else 0.0
            break
    if total <= 0:
        total = sum(r["面积_m2"] for r in rows)
    # 权属单位：数据行第一列（取「合计」行之外的权属行，如「XX村村民委员会」）
    owner = _t(data_row[0]) if data_row and _t(data_row[0]) != "合计" else ""
    if not owner:
        for row in grid[r3 + 1:]:
            _c0 = _t(row[0]) if row else ""
            if _c0 and _c0 != "合计" and "单位" not in _c0:
                owner = _c0
                break
    return {"rows": rows, "合计_m2": round(total, 2), "pages": [],
            "source": "xlsx", "权属单位": owner}

# 命中即认为是「土地分类面积表」所在页
_PAGE_KW = ("土地分类面积表",)
# 兜底判据（表名被印章/折痕盖住时）：地类 + 合计 + 耕地 同页
_PAGE_KW_SOFT = ("耕地", "地类", "合计")


def candidate_pages(pdf_path: str) -> List[int]:
    """按**文本层**定位「土地分类面积表」所在页（0 基）。找不到返回空表。

    文本层虽然抽不准表内容，但「哪一页有这张表」是可靠的 —— 用不着让视觉逐页试。

    🔴 两个必须排除的坑（实测踩过）：
      · **目录页**也印着「土地分类面积表」三个字（带 `………` 前导点），
        当成正文页会把目录送进视觉；
      · **勘测定界表**那页同样有「耕地/地类/合计」（那是它的地类小计列，
        不是土地分类面积表），两页一起读会把同一批数字读两遍、合计翻倍。
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return []
    try:
        import fitz
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    def _is_toc(t: str) -> bool:
        return t.count("…") >= 2 or t.count("⋯") >= 2

    hard: List[int] = []
    soft: List[int] = []
    try:
        for i in range(doc.page_count):
            raw = doc[i].get_text()
            t = raw.replace(" ", "").replace("\n", "")
            if _is_toc(t) or len(t) < 120:
                continue
            if any(k in t for k in _PAGE_KW):
                hard.append(i)
            elif all(k in t for k in _PAGE_KW_SOFT):
                soft.append(i)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return hard or soft

_PROMPT = """这是土地勘测定界报告里的一页，其中可能含**土地分类面积表**（按地类逐项列出面积）。

请只做一件事：把表里**印刷着的**内容原样读出来，输出 JSON：

{
  "found": true/false,
  "unit": "平方米" 或 "公顷"（照表里印的单位写，没有就按"平方米"）,
  "rows": [
    {"一级地类": "耕地（01）", "二级地类": "水田（0101）", "面积": "478.0"},
    ...
  ],
  "total": "8070.0"
}

硬性规则（务必遵守）：
1. **只读表里的数字**。读不清、被印章/折痕遮挡的行，**整行不要输出** —— 宁缺勿错。
2. 不要推算、不要补零、不要把两列数字合并。面积是多少就写多少。
3. 「小计」「合计」行不要放进 rows（合计单独放 total）。
4. 一级地类与二级地类都要带上括号里的编码（如「耕地（01）」「水田（0101）」）；
   表里没有编码就只写名称。
5. 本页**没有**这张表时（例如这页是「勘测定界表」「界址点坐标表」）返回 {"found": false}。
"""


def _md5(path: str) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def _parse_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    m = re.search(r'\{.*\}', s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _to_m2(value, unit: str) -> Optional[float]:
    """面积 → 平方米。`unit` 含「公顷」时 ×10000。"""
    try:
        v = float(str(value).replace(",", "").strip())
    except Exception:
        return None
    if "公顷" in str(unit):
        v *= 10000.0
    return round(v, 2)


def _rows_from_payload(payload: dict) -> List[dict]:
    unit = payload.get("unit") or "平方米"
    out: List[dict] = []
    for r in (payload.get("rows") or []):
        if not isinstance(r, dict):
            continue
        a = _to_m2(r.get("面积"), unit)
        if a is None or a <= 0:
            continue
        out.append({"一级地类": str(r.get("一级地类") or "").strip(),
                    "二级地类": str(r.get("二级地类") or "").strip(),
                    "面积_m2": a})
    return out


async def extract_land_classification(pdf_path: str,
                                      llm_service=None,
                                      storage_dir: Optional[str] = None,
                                      ) -> Dict[str, Any]:
    """视觉读出该 PDF 里的地类面积 → `{"rows": [...], "合计_m2": float, "pages": [...]}`。

    读不到（没有这张表 / 视觉不可用 / 解析失败）→ `{}`，**不抛异常**
    （调用方按「没解析到」处理：表留空、不影响出报告）。
    """
    pages = candidate_pages(pdf_path)
    if not pages:
        return {}
    if llm_service is None:
        try:
            from app.services.llm_service import LLMService
            llm_service = LLMService()
        except Exception:
            return {}

    # 复用整页渲染（已是 JPEG、已缓存），按页号取
    try:
        from app.services.pdf_key_images import render_all_pages
        renders = render_all_pages(pdf_path,
                                   out_dir=str(Path(storage_dir) / "images" / "appendix")
                                   if storage_dir else None)
    except Exception as e:
        logger.warning(f"[地类] 整页渲染失败: {e}")
        return {}
    if not renders:
        return {}

    import base64
    rows: List[dict] = []
    used: List[int] = []
    printed_total: Optional[float] = None
    for pno in pages:
        if pno >= len(renders):
            continue
        img = renders[pno]
        cache = None
        if storage_dir:
            cdir = Path(storage_dir) / "land_class_cache"
            try:
                cdir.mkdir(parents=True, exist_ok=True)
                h = _md5(img)
                if h:
                    cache = cdir / f"{h}.json"
                    if cache.exists():
                        payload = json.loads(cache.read_text())
                    else:
                        payload = None
                else:
                    payload = None
            except Exception:
                cache, payload = None, None
        else:
            payload = None

        if payload is None:
            try:
                raw = Path(img).read_bytes()
                b64 = base64.b64encode(raw).decode()
                mt = "image/jpeg" if str(img).lower().endswith((".jpg", ".jpeg")) else "image/png"
                text = await llm_service.chat_with_image(
                    _PROMPT, b64, media_type=mt, max_tokens=2000)
                payload = _parse_json(text) or {"found": False}
            except Exception as e:
                logger.warning(f"[地类] 第{pno + 1}页视觉读取失败: {e}")
                payload = {"found": False}
            if cache is not None:
                try:
                    cache.write_text(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    pass

        if not payload.get("found"):
            continue
        rows.extend(_rows_from_payload(payload))
        _pt = _to_m2(payload.get("total"), payload.get("unit") or "平方米")
        if _pt:
            printed_total = max(printed_total or 0.0, _pt)
        used.append(pno + 1)

    rows = _dedupe_by_area(rows)
    if not rows:
        return {}
    _sum = round(sum(r["面积_m2"] for r in rows), 2)
    # 🔴 表里**印着**的合计最权威（读数漏行时它还能兜住口径）；没有才用求和。
    total = printed_total or _sum
    if printed_total and abs(printed_total - _sum) > max(1.0, printed_total * 0.02):
        logger.warning(f"[地类] 逐行求和 {_sum} 与表内合计 {printed_total} 不符（可能漏读行）")
    logger.info(f"[地类] 视觉读出 {len(rows)} 行地类，合计 {total} ㎡（第{used}页）")
    return {"rows": rows, "合计_m2": total, "pages": used}


def _dedupe_by_area(rows: List[dict]) -> List[dict]:
    """按**面积**去重：同一个面积只留标签最完整的那一行。

    为什么需要：表可能跨页，或视觉把「勘测定界表」的地类小计列也读了出来 ——
    同一批数字会进来两次（实测：两页都读，5 行变 10 行、合计 8070 变 16140）。
    标签完整度按「一级+二级都有」>「只有二级」>「只有一级」排序。
    """
    def _score(r: dict) -> int:
        s = 0
        if r.get("一级地类"):
            s += 2
        if r.get("二级地类"):
            s += 4
        return s

    best: Dict[float, dict] = {}
    for r in rows:
        k = float(r["面积_m2"])
        if k not in best or _score(r) > _score(best[k]):
            best[k] = r
    return sorted(best.values(), key=lambda r: -r["面积_m2"])


def summarize(land_class: Dict[str, Any]) -> str:
    """给提示词/事实卡用的一行摘要（没数据返回空串）。"""
    rows = (land_class or {}).get("rows") or []
    if not rows:
        return ""
    parts = []
    for r in rows:
        name = r.get("二级地类") or r.get("一级地类") or ""
        ha = r.get("面积_m2", 0) / 10000.0
        parts.append(f"{name} {ha:.4f} 公顷")
    return "、".join(parts) + f"；合计 {land_class.get('合计_m2', 0) / 10000.0:.4f} 公顷"
