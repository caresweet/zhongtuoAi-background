"""表格另拼 —— 模板里没有真实数据、必须现算现插的表格与图片。

模板 docx 自带的 7 张表（评审表 / 风险等级判定 / 评分表 …）就地填充时已处理或在
`DocxSlotFiller` 里登记了锚点。本模块负责**模板里没有、或需要用本项目的真实数据重建**的：

| 另拼对象 | 数据来源 |
|---|---|
| 表3-1 土地分类面积表 | `filled["_extracted_tables"]`（勘测定界 PDF 解析结果） |
| 表5-1/5-2 问卷统计 | `table_generation_service.generate_table_3_1/3_3` |
| 附件图片 | `image_catalog` 分类结果 |

插入方式：定位锚点段落 → 在其后 `addnext` 插入新表格（不破坏前后版式）。
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, List, Optional

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

FONT_SONGTI = "宋体"
FONT_TABLE = "宋体"   # 对齐参照件：表内文字为宋体（原为仿宋_GB2312）


# ═══════════════════════════════════════════════════════════════
# 基础设施
# ═══════════════════════════════════════════════════════════════

def find_paragraph(doc, keyword: str, *, after: int = 0):
    """在 doc.paragraphs[after:] 中找第一个包含 keyword 的段落。"""
    for i in range(after, len(doc.paragraphs)):
        p = doc.paragraphs[i]
        if keyword and keyword in p.text:
            return i, p
    return -1, None


def _doc_of(paragraph: Paragraph):
    """稳健地取到顶层 Document。

    段落的 `_parent` 可能是 Document，也可能是 `_Cell`（表格内段落），
    后者的 `add_table` 签名不同（需要 width）。统一走 `part.document`。
    """
    try:
        return paragraph.part.document
    except Exception:
        return paragraph._parent


def insert_table_after(paragraph: Paragraph, headers: List[str], rows: List[List[str]],
                       *, title: str = "", font_size=Pt(10.5)):
    """在 `paragraph` 之后插入一张表格（带表名，表名在表上方）。

    Returns:
        新建的 docx 表格对象。
    """
    doc = _doc_of(paragraph)

    # 表名（在表上方）
    title_p = doc.add_paragraph()
    if title:
        run = title_p.add_run(title)
        run.font.name = FONT_SONGTI
        run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_SONGTI)
        run.font.size = Pt(12)
        # 🔴 参照件表名不加粗（与 report_assembler._add_table_caption 统一）
        run.bold = False
    paragraph._p.addnext(title_p._p)

    tbl = None
    try:
        tbl = doc.add_table(rows=len(rows) + 1, cols=len(headers), style='Table Grid')
    except TypeError:
        # 某些容器（如单元格）的 add_table 不接受 style 关键字
        tbl = doc.add_table(rows=len(rows) + 1, cols=len(headers))
        try:
            tbl.style = 'Table Grid'
        except Exception:
            pass
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    def _set(cell, text, bold=False):
        cell.text = ""
        para = cell.paragraphs[0]
        run = para.add_run(str(text))
        run.font.name = FONT_TABLE
        run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_TABLE)
        run.font.size = font_size
        run.bold = bold

    for j, h in enumerate(headers):
        _set(tbl.cell(0, j), h, bold=True)
    for i, row in enumerate(rows, start=1):
        for j in range(len(headers)):
            _set(tbl.cell(i, j), row[j] if j < len(row) else "")

    title_p._p.addnext(tbl._tbl)
    return tbl


def delete_table(tbl) -> None:
    """删除一个 docx 表格（连同其上方紧邻的表名段落）。"""
    el = tbl._tbl
    prev = el.getprevious()
    el.getparent().remove(el)
    # 表名段落（表格正上方、以「表」开头）一并删除，避免留下孤零零的表名
    if prev is not None and prev.tag.endswith('}p'):
        txt = "".join(prev.itertext()).strip()
        if re.match(r'^表\s*\d', txt) or txt.startswith("土地分类面积表"):
            prev.getparent().remove(prev)


# ═══════════════════════════════════════════════════════════════
# 表3-1 土地分类面积表（勘测定界）
# ═══════════════════════════════════════════════════════════════

def inject_land_table(doc, chapter_no: int, extracted_tables: List[Dict[str, Any]],
                      *, offset: int = 1) -> bool:
    """把勘测定界报告的表插入第3章（（五）用地范围规模）之后，**按报告原顺序**。

    勘测定界报告通常是「地块情况汇总表 → 土地分类面积表」两张；插完第一张后，
    后续表接在它后面（否则后插的会跑到前面、编号倒挂）。

    Returns:
        True = 至少插入了一张表。
    """
    from app.services.docx_slot_filler import chapter_span

    def _is_land(t: Any) -> bool:
        if not isinstance(t, dict) or not t.get("headers"):
            return False
        hstr = " ".join(str(h) for h in t["headers"])
        return any(k in hstr for k in ("耕地", "园地", "林地", "权属",
                                       "分类面积", "被征", "地块号", "土地坐落"))

    land = [t for t in (extracted_tables or []) if _is_land(t)]
    if not land:
        return False
    # 报告原顺序：汇总表在前、分类面积表在后
    land.sort(key=lambda t: 0 if t.get("kind") == "land_summary" else 1)

    s, e = chapter_span(doc, chapter_no, offset=offset)
    if s < 0:
        return False

    anchor_p = None
    for p in doc.paragraphs[s:e]:
        if "勘测定界" in p.text or "红线图" in p.text:
            anchor_p = p
            break
    if anchor_p is None:
        for p in reversed(doc.paragraphs[s:e]):
            if p.text.strip():
                anchor_p = p
                break
    if anchor_p is None:
        return False

    # 模板里若已有示例表，先删掉，避免双表
    for t in list(doc.tables):
        try:
            hdr = " ".join(c.text.strip() for c in t.rows[0].cells)
        except Exception:
            continue
        if "土地分类" in hdr or ("权属" in hdr and "面积总计" in hdr):
            delete_table(t)

    inserted = 0
    for t_def in land:
        headers = [str(h) for h in t_def["headers"]]
        rows = [[str(c) for c in r] for r in (t_def.get("rows") or [])]
        _cap = str(t_def.get("_rendered_caption") or "表3-1 土地分类面积表")
        # 单位只加在「面积类」表上（汇总表含界址点数，不适用）
        if t_def.get("kind") == "land_classification" and "单位" not in _cap:
            _cap += "（单位：平方米）"
        tbl = insert_table_after(anchor_p, headers, rows, title=_cap)
        inserted += 1
        # 下一张接在本张之后
        last_p = _doc_of(anchor_p).add_paragraph()
        tbl._tbl.addnext(last_p._p)
        anchor_p = last_p

    if inserted:
        logger.info(f"已另拼勘测定界表 {inserted} 张："
                    f"{[t.get('_rendered_caption') for t in land]}")
    return inserted > 0


# ═══════════════════════════════════════════════════════════════
# 表5-1 / 5-2 问卷统计
# ═══════════════════════════════════════════════════════════════

def inject_survey_tables(doc, chapter_no: int, state: Dict[str, Any],
                         *, offset: int = 1) -> int:
    """把问卷统计表插到第5章（（六）利益相关者诉求）之后。

    Returns:
        插入的表数量。
    """
    from app.services.docx_slot_filler import chapter_span
    from app.services.table_generation_service import TableGenerationService

    s, e = chapter_span(doc, chapter_no, offset=offset)
    if s < 0:
        return 0

    anchor_p = None
    for p in reversed(doc.paragraphs[s:e]):
        if p.text.strip():
            anchor_p = p
            break
    if anchor_p is None:
        return 0

    # 模板里若已有示例问卷统计表，先删
    for t in list(doc.tables):
        try:
            hdr = " ".join(c.text.strip() for c in t.rows[0].cells)
        except Exception:
            continue
        if "调查内容" in hdr and ("选次" in hdr or "比例" in hdr or "个数" in hdr):
            delete_table(t)

    n = 0
    specs = [
        ("表5-1 公众意见调查统计汇总表", "generate_table_3_1"),
        ("表5-2 单位问卷调查统计汇总表", "generate_table_3_3"),
    ]
    for title, fn_name in specs:
        try:
            data = getattr(TableGenerationService, fn_name)(state)
            headers = data.get("headers") or []
            rows = data.get("rows") or []
            if not headers or not rows:
                continue
            tbl = insert_table_after(anchor_p, headers, rows, title=title)
            # 下一张接在本张之后
            last_p = doc.add_paragraph()
            tbl._tbl.addnext(last_p._p)
            anchor_p = last_p
            n += 1
        except Exception as ex:
            logger.warning(f"{title} 生成失败: {ex}")
    if n:
        logger.info(f"已另拼问卷统计表 {n} 张")
    return n


# ═══════════════════════════════════════════════════════════════
# 附件图片
# ═══════════════════════════════════════════════════════════════

def inject_appendix_images(doc, image_catalog: Dict[str, Any], *,
                           max_per_category: int = 30, max_total: int = 300) -> int:
    """把分类图片按「附件N 类别」挂到附件章（每行一张、正常尺寸）。

    Args:
        doc: 文档。
        image_catalog: `image_catalog.build_image_catalog()` 的返回。
        max_per_category: 每类上限（防「报告 700 多页」）。
        max_total: 附件图片总数上限。

    Returns:
        实际插入的图片数。
    """
    from app.services.docx_slot_filler import chapter_span
    from docx.shared import Cm

    # 附件章 = 正文最后一章（第11章）→ 模板第12章
    s, e = chapter_span(doc, 11)
    if s < 0:
        logger.warning("未找到附件章，跳过图片挂载")
        return 0

    by_cat: Dict[str, List[str]] = {}
    for img in (image_catalog.get("catalog") or []):
        cat = str(img.get("category") or "其他资料图片")
        path = img.get("path")
        if path:
            by_cat.setdefault(cat, []).append(path)

    # 插到附件章最后一个非空段落之后
    anchor_p = None
    for p in reversed(doc.paragraphs[s:e]):
        if p.text.strip():
            anchor_p = p
            break
    if anchor_p is None:
        return 0

    total = 0
    for cat, paths in by_cat.items():
        if total >= max_total:
            break
        cap = min(len(paths), max_per_category, max_total - total)
        if cap <= 0:
            continue
        head = doc.add_paragraph()
        r = head.add_run(f"附件：{cat}")
        r.font.name = FONT_SONGTI
        r._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_SONGTI)
        r.font.size = Pt(14); r.bold = True
        anchor_p._p.addnext(head._p)
        anchor_p = head

        for path in paths[:cap]:
            try:
                pic_p = doc.add_paragraph()
                pic_p.alignment = 1  # center
                run = pic_p.add_run()
                run.add_picture(path, width=Cm(14))
                anchor_p._p.addnext(pic_p._p)
                anchor_p = pic_p
                total += 1
            except Exception as ex:
                logger.warning(f"附件图片插入失败 {path}: {ex}")

    logger.info(f"附件已挂载 {total} 张图片（{len(by_cat)} 类）")
    return total
