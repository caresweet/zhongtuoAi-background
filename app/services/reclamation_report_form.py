# -*- coding: utf-8 -*-
"""第11部分「土地复垦方案报告表」—— 样本那种**制式表单**（数据驱动渲染）。

## 长什么样（样本《明远路》p57–p60 逐页核对）

一整张带边框的大表，**最左一列放竖排的分节名**（「项/目/概/况」逐字竖排），
右侧是「标签｜值｜标签｜值」的表单；「主要编制人员」是嵌在里面的子表。
四页分别是：

    ① 项目概况 + 方案编制单位（含主要编制人员子表）
    ② 复垦区土地利用现状 + 复垦责任范围内土地损毁及占用面积 + 复垦土地面积 + 复垦率
    ③ 工作计划及保障措施（一、复垦工作计划；二、实施保障措施）
    ④ 投资估算（测算依据 + 投资估算费用构成）

## 数据从哪来（全部是已有事实源，不编）

| 表单项 | 来源 |
|---|---|
| 项目名称/单位名称/项目位置/项目区面积 | `filled_data`（材料抽取） |
| 图幅号 | `filled_data` 里从勘测定界抽到的 `I50H118163` |
| 投资规模 | `project_investment`（万元） |
| 编制单位名称/法人代表/资质/联系人 | 公司档案（`_company`） |
| 主要编制人员 | 人员库（`personnel_for`） |
| 复垦区土地利用现状 / 复垦土地面积 | `filled_data["land_classification"]`（勘测定界地类，视觉读出） |
| 投资估算费用构成 | `reclamation_budget_bridge` → budget-maker（127.0.0.1:8765）按第6章工程量现算 |
| 工作计划 / 保障措施 | 第8章、第10章正文（摘录，不另编） |

**抽不到的**一律留空、不印「待填写」这类占位符（业主硬约束）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 报告表正文统一字号（业主 2026-10-12：「字体大小还不统一」）
TABLE_TEXT_PT = 10.5


def _txt(v: Any) -> str:
    """取值转字符串；None/空 → 空串（**不印占位符**）。"""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("None", "nan", "—") else s


def _person_rows(personnel: Sequence[dict], unit_name: str, limit: int = 8) -> List[List[str]]:
    """主要编制人员**数据行**（姓名｜职称/职务｜专业｜单位｜签名，签名留给手签）。"""
    out: List[List[str]] = []
    for p in list(personnel or [])[:max(3, limit)]:
        name = _txt(p.get("姓名"))
        if not name:
            continue
        out.append([name, _txt(p.get("职称/职务")), _txt(p.get("专业")), unit_name, ""])
    while len(out) < 3:                      # 样本至少留 3 行（含空行给手填）
        out.append(["", "", "", "", ""])
    return out


def collect_form_data(filled: dict, company: dict, personnel: Sequence[dict]) -> Dict[str, Any]:
    """把各事实源收敛成报告表要用的字段（缺的留空，不猜）。"""
    fd = filled or {}
    co = company or {}
    _area_m2 = 0.0
    try:
        _area_m2 = float(str(fd.get("area_m2") or 0).replace(",", "")) or 0.0
    except Exception:
        _area_m2 = 0.0
    _ha = f"{_area_m2 / 10000.0:.4f}" if _area_m2 else ""

    # 图幅号：材料里有形如 I50H118163 的值
    _sheet = ""
    try:
        import json as _json
        m = re.search(r'\bI?\d{2}[A-Z]\d{6}\b', _json.dumps(fd, ensure_ascii=False))
        if m:
            _sheet = m.group(0)
    except Exception:
        pass

    _invest = _txt(fd.get("project_investment"))
    _invest_u = _txt(fd.get("project_investment_unit")) or "万元"

    lc = fd.get("land_classification") or {}
    return {
        "项目名称": _txt(fd.get("project_name")),
        "单位名称": _txt(fd.get("project_unit")) or _txt(fd.get("land_user_unit")),
        "单位地址": _txt(fd.get("unit_address")),
        "法人代表": "",                    # 材料的「法人代表」属于别的单位，宁缺勿错
        "联系电话": _txt(fd.get("contact_phone")),
        "单位性质": "",
        "项目性质": _txt(fd.get("construction_nature")),
        "项目位置": _txt(fd.get("location")),
        "项目区面积": f"{_ha} 公顷" if _ha else "",
        "投资规模": f"{_invest} {_invest_u}".strip() if _invest else "",
        "图幅号": _sheet,
        "生产年限": (f"{_txt(fd['construct_period'])} 个月"
                     if _txt(fd.get("construct_period")) else ""),
        "服务年限": (f"{_txt(fd['service_years'])} 个月"
                     if _txt(fd.get("service_years")) else ""),
        # 编制单位
        "编制单位名称": _txt(co.get("name")),
        "编制单位法人": _txt(co.get("leader_name")),
        "资质证书名称": _txt(co.get("qualification_text")),
        "资质等级": "",
        "发证机关": "",
        "证书编号": _txt(co.get("business_license_no")),
        "编制单位联系人": _txt(co.get("contact")),
        "编制单位电话": "",
        # 地类
        "地类行": lc.get("rows") or [],
        "地类合计_m2": float(lc.get("合计_m2") or 0),
        "area_m2": _area_m2,
        "单位名称_编制": _txt(co.get("name")),
        "人员行": _person_rows(personnel, _txt(co.get("name"))),
        "永久建设用地_ha": _txt(fd.get("permanent_build_area")) or "",
    }


# ═══════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════

_TWIPS_PER_CM = 566.929


def _fix_table_layout(table, widths_cm: Sequence[float]) -> None:
    """把表格钉成**固定列宽**，并把 `tblGrid` 写成真实列宽。

    🔴 只写 `cell.width` 是**不够的**：python-docx 建的表是 `tblW=auto` +
       **等分**的 `tblGrid` + 没有 `tblLayout fixed`，Word 于是按内容自动调整列宽 ——
       样本量出来的列宽根本不生效，而且合并格会把右边框撑成**锯齿状**
       （实测：`项目名称` 那行比 `法人代表` 那行短一截，业主一眼就看出「格式不对」）。

    正确做法四件套：
      ① `w:tblW w:type="dxa" w:w="<总宽>"` —— **不设这个，Word 会按内容自动收缩整表宽**
         （实测：`方案编制单位` 那张宽度只有 `项目概况` 的 69%，列宽全被重算）
      ② `w:tblLayout w:type="fixed"`（走 python-docx 的 `autofit = False`，按序插入）
      ③ `w:tblGrid` 逐列写死 ④ 每格 `tcW`

    🔴 **不要手写 `w:tblPr` 的子元素再 `append`**：OOXML 里 tblPr 的子元素顺序是
       有约束的（`tblW` 必须排在 `jc`/`tblBorders`/`tblLayout` 之前），乱序 append
       会让 Word/WPS 认为整个 tblPr 非法，把**边框一起丢掉**（实测：表格变成无框）。
       用 `insert_element_before(..., 'w:jc', …)` 按序插。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    table.autofit = False                      # → 按序插入 w:tblLayout type="fixed"
    tbl = table._tbl
    tblPr = tbl.tblPr
    _cols = [int(round(float(w) * _TWIPS_PER_CM)) for w in widths_cm]

    # ① 表宽写死（关键）—— 已有 tblW 就地改属性，没有则按序插入
    _existing = tblPr.findall(qn('w:tblW'))
    if _existing:
        _tw = _existing[0]
    else:
        _tw = OxmlElement('w:tblW')
        tblPr.insert_element_before(
            _tw, 'w:jc', 'w:tblCellSpacing', 'w:tblInd', 'w:tblBorders', 'w:shd',
            'w:tblLayout', 'w:tblCellMar', 'w:tblLook', 'w:tblCaption', 'w:tblDescription')
    _tw.set(qn('w:w'), str(sum(_cols)))
    _tw.set(qn('w:type'), 'dxa')

    # ③ tblGrid 必须是 tbl 的直接子元素，且紧跟 tblPr、在所有 tr 之前
    for g in tbl.findall(qn('w:tblGrid')):
        tbl.remove(g)
    _grid = OxmlElement('w:tblGrid')
    for _w in _cols:
        _gc = OxmlElement('w:gridCol')
        _gc.set(qn('w:w'), str(_w))
        _grid.append(_gc)
    tblPr.addnext(_grid)


def _vertical_label(cell, text: str) -> None:
    """段名竖排：逐字一行（样本就是「项/目/概/况」这样排的）。

    🔴 三条业主点名的口径（2026-10-12）：
      ① **字号统一** —— 早先按字数分 11/9/7.5 三档，一列里几个段名字号不一，很显眼；
      ② **格子居中** —— 段名要落在格子的正中央（水平居中 + **垂直居中**），
         不能顶在格子上沿；
      ③ 末尾不加换行（多一行空行会把整节撑高）。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_ALIGN_VERTICAL
    from docx.shared import Pt
    try:
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    except Exception:
        pass
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # 🔴 不能逐字 add_run(ch + "\n")：末尾那个换行会在格底多留一行空行，整行更高。
    r = p.add_run("\n".join(str(text)))
    r.font.size = Pt(TABLE_TEXT_PT)
    r.bold = False
    try:
        p.paragraph_format.line_spacing = Pt(TABLE_TEXT_PT * 1.15)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
    except Exception:
        pass


def _fill_cell(cell, text: str, *, bold: bool = False, size: float = TABLE_TEXT_PT,
               align_center: bool = True) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_ALIGN_VERTICAL
    from docx.shared import Pt
    from docx.oxml.ns import qn
    try:
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    except Exception:
        pass
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if align_center else WD_ALIGN_PARAGRAPH.LEFT
    for r in list(p.runs):
        r.text = ""
    r = p.add_run(text or "")
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = "宋体"
    if r._element.rPr is not None and r._element.rPr.rFonts is not None:
        r._element.rPr.rFonts.set(qn('w:eastAsia'), "宋体")


def _set_row_heights(table, cm: float) -> None:
    """给每行一个**最小行高**（样本主表单行距 30pt ≈ 1.05cm）。

    🔴 业主口径：「单元格的高度基本上就是等于标题字体高度，看上去不舒服」——
       默认行高紧贴文字，行与行糊在一起。`atLeast` 只兜底，内容多的行照样撑开。
    """
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.shared import Cm
    for r in table.rows:
        try:
            r.height = Cm(cm)
            r.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
        except Exception:
            pass


def _merge_equal_runs(table, grid_col: int, first_row: int = 0) -> None:
    """把**同一列里连续相同的值**竖向合并（业主：「主要编制人员的单位也没有合并同类项」）。

    只并「连续且相同且非空」的格；遇到不同的值就断开。`grid_col` 是**表格栅格列号**。
    """
    n = len(table.rows)
    i = max(0, int(first_row))
    while i < n:
        txt = table.cell(i, grid_col).text.strip()
        if not txt:
            i += 1
            continue
        j = i + 1
        while j < n and table.cell(j, grid_col).text.strip() == txt:
            j += 1
        if j - i > 1:
            try:
                table.cell(i, grid_col).merge(table.cell(j - 1, grid_col))
            except Exception:
                pass
        i = j


def _section_table(doc, section: str, rows: List[List[Tuple[str, int]]],
                   widths_cm: Sequence[float], *, left_align_long: bool = True,
                   row_h_cm: float = 1.05, merge_run_cols: Sequence[int] = (),
                   merge_from_row: int = 0,
                   vmerge_specs: Sequence[Tuple[int, int, int]] = ()):
    """一个分节：最左列竖排段名（跨该节所有行），右侧是 `(文本, 跨列数)` 的表单行。

    列数 = `len(widths_cm)`（首列是段名列，其余是表单列）。跨列数不足末尾的行，
    最后一格自动撑满。

    `merge_run_cols`：需要「合并同类项」的**栅格列号**（如主要编制人员的「单位」列）。
    `vmerge_specs`：需要**竖向合并**的 `(栅格列号, 起始行, 结束行)`
        （样本 p58 那张表：`(1,0,1)` 把「类型」跨到表头第二行；`(1,2,4)` 把「损毁」
        跨 挖损/压占/小计 三行）。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Cm
    ncols = max(2, len(widths_cm))
    n = max(1, len(rows))
    t = doc.add_table(rows=n, cols=ncols)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 🔴 固定列宽（只写 cell.width 会被 Word 的自动调整覆盖）—— 见 `_fix_table_layout`
    _fix_table_layout(t, widths_cm)
    for i, w in enumerate(widths_cm):
        for r in t.rows:
            r.cells[i].width = Cm(w)
    # 段名列（合并后竖排）
    if n > 1:
        t.cell(0, 0).merge(t.cell(n - 1, 0))
    for ri, row in enumerate(rows):
        ci = 1
        for text, span in row:
            span = max(1, min(int(span), ncols - ci))
            cell = t.cell(ri, ci)
            if span > 1:
                cell = cell.merge(t.cell(ri, ci + span - 1))
            _fill_cell(cell, text,
                       align_center=not (left_align_long and len(str(text)) > 40))
            ci += span
        if ci <= ncols - 1:               # 尾格撑满，避免右边的空白格
            t.cell(ri, ci - 1).merge(t.cell(ri, ncols - 1))
    for _c in merge_run_cols:
        _merge_equal_runs(t, int(_c), first_row=merge_from_row)
    # 🔴 竖向合并（表头跨行 / 行标签跨行）——要在横向合并**与填字之后**做
    _vmerge_cells(t, vmerge_specs)
    _set_row_heights(t, row_h_cm)
    _vertical_label(t.cell(0, 0), section)
    return t


def _vmerge_cells(table, specs: Sequence[Tuple[int, int, int]]) -> None:
    """把若干**栅格列上的连续行**竖向并成一格。

    `specs` 每项 `(栅格列号, 起始行, 结束行)`。样本 p58 那张
    「复垦责任范围内土地损毁及占用面积」就是靠它：
      · 「类型」跨表头两行 (1, 0, 1)
      · 「损毁」跨 挖损/压占/小计 三行 (1, 2, 4)

    🔴 必须在**横向合并与填字之后**做，否则合并出来的格子会被后续 `_fill_cell` 覆盖。
    """
    for _c, _r0, _r1 in specs:
        _c, _r0, _r1 = int(_c), int(_r0), int(_r1)
        try:
            _a, _b = table.cell(_r0, _c), table.cell(_r1, _c)
            if _a._tc is _b._tc:
                continue
            _txt = _a.text.strip()
            _m = _a.merge(_b)
            # `merge` 会把被并方的段落一并搬进来（哪怕它是空的），清掉多余的段
            for _p in list(_m.paragraphs)[1:]:
                _p._p.getparent().remove(_p._p)
            _fill_cell(_m, _txt)
        except Exception as e:                       # pragma: no cover
            logger.warning(f"[报告表] 竖向合并失败(列{_c}, 行{_r0}-{_r1}): {e}")


def _kv(lab: str, val: str) -> List[Tuple[str, int]]:
    """一行完整的两格（标签跨 2 格、值跨 3 格）—— 样本 p57：标签 139.7→279.1、值 279.1→526.3。"""
    return [(lab, 2), (val, 3)]


def _kv2(l1: str, v1: str, l2: str, v2: str) -> List[Tuple[str, int]]:
    """一行四个格（标签跨 2 格 ｜ 值 ｜ 标签 ｜ 值）。"""
    return [(l1, 2), (v1, 1), (l2, 1), (v2, 1)]


def _staff_rows(person_rows: Sequence[Sequence[str]]) -> List[List[Tuple[str, int]]]:
    """主要编制人员：**本表的一批行**（不是内嵌表）。

    🔴 业主口径（2026-10-12）：「主要编制人员是方案编制单位的一个内置标题，
       占了一整行，但不会看上去像是另起了一个表格」。所以它是外层表栅格里的行，
       不是 `cell.add_table()` 出来的子表 —— 子表在 Word 里会多出一圈独立的边框。

    入参是 `collect_form_data` 的「人员行」（姓名｜职称/职务｜专业｜单位｜签名）。
    返回：表头行 + 数据行（每行 5 个「跨 1 格」的单元）。
    """
    out: List[List[Tuple[str, int]]] = [
        [("姓名", 1), ("职称/职务", 1), ("专业", 1), ("单位", 1), ("签名", 1)],
    ]
    for r in list(person_rows or []):
        _vals = list(r) + [""] * 5
        out.append([(str(_vals[i] or ""), 1) for i in range(5)])
    while len(out) < 4:                      # 表头 + 至少 3 行（留空行给手填/手签）
        out.append([("", 1), ("", 1), ("", 1), ("", 1), ("", 1)])
    return out


def _land_rows(data: Dict[str, Any]) -> List[List[Tuple[str, int]]]:
    """表A「复垦区土地利用现状」—— 样本 p58 的结构（一级｜二级｜小计｜已损毁｜拟损毁｜占用）。

    样本里临时用地各行的值同时落在「小计」和「拟损毁」两列（0.0478 在两处），
    已损毁 / 占用留空；合计行为 `合计 | 小计 | — | 拟损毁 | —`。
    """
    out: List[List[Tuple[str, int]]] = [
        [("土地类型", 2), ("面积（公顷）", 4)],
        [("一级地类", 1), ("二级地类", 1), ("小计", 1),
         ("已损毁", 1), ("拟损毁", 1), ("占用", 1)],
    ]
    _total = 0.0
    for r in (data.get("地类行") or []):
        _ha = float(r.get("面积_m2") or 0) / 10000.0
        _total += _ha
        out.append([(_txt(r.get("一级地类")), 1), (_txt(r.get("二级地类")), 1),
                    (f"{_ha:.4f}", 1), ("", 1), (f"{_ha:.4f}", 1), ("", 1)])
    out.append([("合计", 2), (f"{_total:.4f}", 1), ("", 1), (f"{_total:.4f}", 1), ("", 1)])
    return out


def _reclaim_area_rows(data: Dict[str, Any]) -> List[List[Tuple[str, int]]]:
    """表C「复垦土地面积」—— 样本 p58 下节（一级｜二级｜已复垦｜拟复垦 + 复垦率）。"""
    out: List[List[Tuple[str, int]]] = [
        [("土地类型", 2), ("面积（公顷）", 2)],
        [("一级地类", 1), ("二级地类", 1), ("已复垦", 1), ("拟复垦", 1)],
    ]
    _total = 0.0
    for r in (data.get("地类行") or []):
        _ha = float(r.get("面积_m2") or 0) / 10000.0
        _total += _ha
        out.append([(_txt(r.get("一级地类")), 1), (_txt(r.get("二级地类")), 1),
                    ("", 1), (f"{_ha:.4f}", 1)])
    out.append([("合计", 2), ("", 1), (f"{_total:.4f}", 1)])
    _rate = 100 if _total > 0 else ""
    # 样本 p58 下节末行：土地复垦率占 一级地类+二级地类 两格，100 占
    # 已复垦+拟复垦 两格（业主 2026-10-12：「100应该占两列」）。
    out.append([("土地复垦率（%）", 2), (f"{_rate}", 2)])
    return out


def render_report_form(doc, data: Dict[str, Any], chapters: Dict[str, Any] | None = None,
                       chapter_no: int = 11) -> None:
    """把第11部分「土地复垦方案报告表」渲染进文档。

    四节对应样本四页；节与节之间**不分页**（样本是连续的表，靠表格自身跨页）。
    """
    from docx.shared import Pt
    # 🔴 列宽**逐列量自样本 PDF 的表格线**（单位 cm，pt÷28.3465）：
    #    · 主表单（样本 p57，竖线 83.1/139.7/187.5/279.1/388.5/453.5/526.3）是
    #      **6 列**：段名(2.00) ｜ c1(1.69) ｜ c2(3.23) ｜ c3(3.86) ｜ c4(2.29) ｜ c5(2.57)
    #      ——`_kv` 标签跨 c1+c2(4.92)、值跨 c3+c4+c5(8.72)；`_kv2` 是 4.92|3.86|2.29|2.57；
    #      主要编制人员的 5 个字段正好是 c1..c5（所以它**就是本表的行**，不用内嵌表）。
    #    · 复垦区土地利用现状 / 损毁占用面积（样本 p58，各 7 / 6 格）
    #    · 复垦土地面积（样本 p58 下节：一级｜二级｜已复垦｜拟复垦）
    #    · 投资估算费用构成（样本 p60 下节：段名｜序号｜名称｜金额，按 15.64cm 等比缩过）
    W6 = (2.00, 1.69, 3.23, 3.86, 2.29, 2.57)
    W_LAND = (2.00, 3.98, 2.48, 1.87, 1.51, 1.61, 2.20)
    W_AREA = (2.00, 3.98, 5.86, 1.61, 2.20)
    W_DAMAGE = (2.00, 1.69, 4.76, 3.38, 1.61, 2.20)
    W_COST = (2.00, 4.53, 4.88, 4.23)

    # 表头
    _p = doc.add_paragraph()
    _p.alignment = 1                     # CENTER
    _r = _p.add_run("土地复垦方案报告表")
    _r.bold = True
    _r.font.size = Pt(16)
    from docx.oxml.ns import qn as _q
    if _r._element.rPr is not None and _r._element.rPr.rFonts is not None:
        _r._element.rPr.rFonts.set(_q('w:eastAsia'), "黑体")

    d = data
    # ① 项目概况
    _section_table(doc, "项目概况", [
        _kv("项目名称", d["项目名称"]),
        _kv("单位名称", d["单位名称"]),
        _kv("单位地址", d["单位地址"]),
        _kv2("法人代表", d["法人代表"], "联系电话", d["联系电话"]),
        _kv2("单位性质", d["单位性质"], "项目性质", d["项目性质"]),
        _kv("项目位置", d["项目位置"]),
        _kv2("项目区面积", d["项目区面积"], "投资规模", d["投资规模"]),
        _kv("项目位置土地利用现状图编号", d["图幅号"]),
        _kv2("生产年限（或建设期限）", d["生产年限"],
             "土地复垦方案服务年限", d["服务年限"]),
    ], W6)

    # ② 方案编制单位 —— 「主要编制人员」是**本表的一段行**（不是内嵌子表）。
    #    🔴 业主 2026-10-12：「主要编制人员是方案编制单位的一个内置标题，占了一整行，
    #       但不会看上去像是另起了一个表格」。早先用 `cell.add_table()` 内嵌，
    #       Word 里多出一圈独立边框，看着就是「另起了一张表」。
    _staff = _staff_rows(d.get("人员行") or [])
    _staff_first = 5 + 1 + 1                       # 上面 5 行 + 标题行 + 表头行
    _section_table(doc, "方案编制单位", [
        _kv("编制单位名称", d["编制单位名称"]),
        _kv("法人代表", d["编制单位法人"]),
        _kv2("资质证书名称", d["资质证书名称"], "资质等级", d["资质等级"]),
        _kv2("发证机关", d["发证机关"], "编号", d["证书编号"]),
        _kv2("联系人", d["编制单位联系人"], "联系电话", d["编制单位电话"]),
        [("主 要 编 制 人 员", 5)],
    ] + _staff, W6, merge_run_cols=(4,), merge_from_row=_staff_first)

    doc.add_page_break()

    # ③ 复垦区土地利用现状 / 损毁及占用 / 复垦土地面积
    _section_table(doc, "复垦区土地利用现状", _land_rows(d), W_LAND, row_h_cm=0.9)
    _ha = d["area_m2"] / 10000.0
    _section_table(doc, "复垦责任范围内土地损毁及占用面积", [
        [("类型", 2), ("面积（公顷）", 3)],
        [("", 2), ("小计", 1), ("已损毁或占用", 1), ("拟损毁或占用", 1)],
        # 「损毁」竖跨 挖损/压占/小计 三行（样本 p58：损毁 的纵向中心落在 挖损 与 小计
        # 之间）；「占用」哪怕本项目没有，也**留一行占位**（业主要求表格结构完整）。
        [("损毁", 1), ("挖损", 1), ("", 1), ("", 1), ("", 1)],
        [("", 1), ("压占", 1), (f"{_ha:.4f}", 1), ("", 1), (f"{_ha:.4f}", 1)],
        [("", 1), ("小计", 1), (f"{_ha:.4f}", 1), ("", 1), (f"{_ha:.4f}", 1)],
        [("占用", 2), ("", 1), ("", 1), ("", 1)],
        [("合计", 2), (f"{_ha:.4f}", 1), ("", 1), (f"{_ha:.4f}", 1)],
    ], W_DAMAGE, row_h_cm=0.9,
        vmerge_specs=((1, 0, 1), (1, 2, 4)))
    _section_table(doc, "复垦土地面积", _reclaim_area_rows(d), W_AREA, row_h_cm=0.9)

    doc.add_page_break()

    # ④ 工作计划及保障措施（摘录第8、10章正文，不另编）
    _section_table(doc, "工作计划及保障措施", [
        [("一、复垦工作计划", 5)],
        [(d.get("工作计划", ""), 5)],
        [("二、实施保障措施", 5)],
        [(d.get("保障措施", ""), 5)],
    ], W6)

    doc.add_page_break()

    # ⑤ 投资估算 —— 样本 p60 是**两节**（各自竖排段名）：
    #    左段「投资估算测算依据」、右段「投资估算费用构成」（序号｜名称｜金额）。
    _cost = d.get("费用构成") or []
    _section_table(doc, "投资估算测算依据", [
        [(d.get("测算依据", ""), 5)],
    ], W6)
    _cost_rows: List[List[Tuple[str, int]]] = [
        [("序号", 1), ("工程或费用名称", 1), ("费用（万元）", 1)],
    ]
    for _no, _name, _val in _cost:
        _cost_rows.append([(str(_no), 1), (str(_name), 1), (str(_val), 1)])
    _section_table(doc, "投资估算费用构成", _cost_rows, W_COST)
