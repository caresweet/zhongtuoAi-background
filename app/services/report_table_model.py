"""统一报告表格数据模型 (ReportTable) 与单一渲染入口。

目标：把所有「受控表格」（表1-1/3-1/3-3/4-1/5/6-2/6-3/7/8-1/8-2/9）收敛到统一的
数据抽象 `ReportTable`，由唯一的 `render_report_table()` 渲染，避免各处散落不同的
渲染逻辑、以及多级合并表头带来的错位。

模型**不包含合并表头概念**——每张表都是纯字符串二维数组（headers + rows），
从源头杜绝多级表头错位。各类表只是「列模板」不同：

- 表4-1（土地分类，大类列）：headers 用大类列；rows 每行一个权属单位+合计。
- 表3-1（公众意见调查，六维度多行）：headers=[调查内容,选项,个数,比例]；
  rows 由「调查问题段」拼成（标题行+选项行）。
- 表6-2/8-1（评分表，扣分制）：六列 [测评指标,权重,测评项目,评分,评分标准,得分]；
  「得分」列 kind=data source="score"，其余列照抄模板。

用 python-docx 渲染，表题放表格正下方（DB32/T4013-2021 规范）。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union

# ═══════════════════════════════════════════════════════════════════
# 列来源类型
#   fixed       = 照抄模板（表头/项目名/评分标准等静态列）
#   data        = 系统计算（如 "分数"、"个数"、"比例"）
#   structural  = 序号列（1/2/3…）
#   auto        = LLM 可写但强制套模板
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ColTemplate:
    """一列的模板描述。"""

    name: str                       # 列名（如 "测评指标"）
    kind: str = "fixed"             # fixed|data|structural|auto
    source: Optional[str] = None    # data 列的数据来源 key
    align: str = "center"           # center|left
    width: Optional[int] = None     # 列宽（字符数，可选）


@dataclass
class ReportTable:
    """一张统一表示的表格。

    - table_no: 表号（"表3-1"）
    - caption: 表题（"公众意见调查分析表"）
    - section: 所属章节（1/3/4/6/8…）
    - headers: 列名数组（已拍平，不含合并单元格）
    - rows: 数据行（纯字符串二维数组）
    - col_templates: 每列来源/类型，驱动「模板为准+按数据裁剪」
    - total_row: 合计行（可选）
    """

    table_no: str
    caption: str
    section: int
    headers: List[str]
    rows: List[List[str]] = field(default_factory=list)
    col_templates: List[ColTemplate] = field(default_factory=list)
    total_row: Optional[List[str]] = None
    notes: str = ""                 # 备注/数据来源说明（可空）

    def validate(self) -> List[str]:
        """校验表格一致性，返回问题列表（空列表=通过）。"""
        issues = []
        if not self.headers:
            issues.append("表头为空")
        if any(not isinstance(h, str) for h in self.headers):
            issues.append("表头含非字符串")
        ncols = len(self.headers)
        for i, row in enumerate(self.rows):
            if len(row) != ncols:
                issues.append(f"第{i + 1}行列数({len(row)})≠表头列数({ncols})")
            for j, v in enumerate(row):
                if v is None:
                    issues.append(f"第{i + 1}行第{j + 1}列为空值(None)")
        if self.total_row and len(self.total_row) != ncols:
            issues.append(f"合计行列数({len(self.total_row)})≠表头列数({ncols})")
        return issues

    def flatten(self) -> List[List[str]]:
        """返回含表头 + 数据 + 合计行的纯字符串二维数组。"""
        out = [list(self.headers)]
        out.extend([list(r) for r in self.rows])
        if self.total_row:
            out.append(list(self.total_row))
        return out

    @classmethod
    def from_extracted(cls, table_no: str, caption: str, section: int,
                       extracted: dict, col_kinds: Optional[dict] = None) -> "ReportTable":
        """从「提取表字典」（如 land_survey_parser / pdf_data_extractor 输出）构造 ReportTable。

        提取表字典结构：{"headers": [...], "rows": [[...]], "total_row": [...]}
        col_kinds 可选：非 data 列的 kind 覆盖（默认按列序，首列 structural，其余 data）。
        """
        headers = list(extracted.get("headers") or [])
        rows = [list(r) for r in (extracted.get("rows") or [])]
        total_row = list(extracted["total_row"]) if extracted.get("total_row") else None
        # 🔴 首列简化（业主：表6-1「第一列…重复的『朱坝街道三圩社区六组』这种字样」）
        rows, notes = cls._simplify_first_col(rows)
        col_templates = []
        for i, h in enumerate(headers):
            if col_kinds and i in col_kinds:
                kind = col_kinds[i]
            elif i == 0:
                kind = "structural"
            else:
                kind = "data"
            col_templates.append(ColTemplate(h, kind=kind))
        return cls(
            table_no=table_no, caption=caption, section=section,
            headers=headers, rows=rows, total_row=total_row,
            col_templates=col_templates, notes=notes,
        )

    @staticmethod
    def _simplify_first_col(rows: list) -> tuple:
        """首列公共前缀/后缀提取 → (rows, 表注)。

        🔴 **按数据实际判断，不写死任何规范**：勘测定界报告的行数与单位写法
           因项目而异，硬编码格式会害了别的项目。这里只在「所有行确实共享同一
           前缀/后缀、且长度 ≥2 字」时才提取；**提取后会让某行变空的，那行保留原值**。

        例：朱坝街道农民集体 / 朱坝街道三圩社区农民集体 / 朱坝街道三圩社区二组农民集体
            → 朱坝街道 / 三圩社区 / 三圩社区二组
            + 表注「注：表中被征（用）地单位均位于朱坝街道。」
        """
        vals = [str(r[0]).strip() for r in (rows or []) if r and str(r[0]).strip()]
        if len(vals) < 3:
            return rows, ""

        def _common(tail: bool) -> str:
            base, n, k = vals[0], min(len(v) for v in vals), 0
            while k < n:
                ch = base[-1 - k] if tail else base[k]
                if all((v[-1 - k] if tail else v[k]) == ch for v in vals):
                    k += 1
                else:
                    break
            return (base[-k:] if tail else base[:k]) if k else ""

        suf, pre = _common(True), _common(False)
        if len(suf) < 2:
            suf = ""
        if len(pre) < 2:
            pre = ""
        if not suf and not pre:
            return rows, ""

        out = []
        for r in rows:
            if not r:
                out.append(r)
                continue
            v = str(r[0]).strip()
            nv = v[:-len(suf)] if (suf and v.endswith(suf)) else v
            # 🔴 只剩前缀的行（如「朱坝街道农民集体」→「朱坝街道」）保留原样，
            #    否则会印出一个空单元格。
            if pre and nv.startswith(pre) and len(nv) > len(pre):
                nv = nv[len(pre):]
            out.append([nv or v] + list(r[1:]))
        return out, (f"注：表中被征（用）地单位均位于{pre}。" if pre else "")


# ═══════════════════════════════════════════════════════════════════
# 单一渲染入口
# ═══════════════════════════════════════════════════════════════════

# 表格字体（与 report_assembler 保持一致）
try:
    from docx.shared import Pt, Cm
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    # 🔴 按业主给的表格规格：表内容与表题一律**宋体小四**（§6.2 的"五号"偏小，
    #    与四号正文落差过大，视觉突兀）
    _FONT_TABLE = "宋体"
    _FONT_CAPTION = "宋体"
except ImportError:  # pragma: no cover - 无 docx 环境时的兜底
    Pt = Cm = WD_TABLE_ALIGNMENT = WD_ALIGN_PARAGRAPH = qn = None


def render_report_table(doc, table: ReportTable, style: str = "Grid",
                        font_size: float = 12,
                        bold_header: bool = True,
                        center_align: bool = True,
                        caption_below: bool = False):
    """把 ReportTable 渲染成 python-docx 表格。

    Args:
        doc: python-docx Document
        table: ReportTable
        style: 表格样式（默认 "Table Grid"）
        font_size: 正文字号（磅）
        bold_header: 表头是否加粗
        center_align: 是否居中对齐
        caption_below: 表题是否放表格正下方（DB32 规范）
    """
    if Pt is None:
        raise RuntimeError("python-docx 未安装，无法渲染表格")

    issues = table.validate()
    if issues:
        raise ValueError("表格校验失败: " + "; ".join(issues))

    # 🔴 表题放**表格上方**（业主表格规格：表格标题位于表格上方、宋体小四加粗）
    if not caption_below and (table.table_no or table.caption):
        _add_caption_above(doc, f"{table.table_no} {table.caption}".strip())

    nrows = len(table.rows) + (1 if table.total_row else 0) + 1  # +1 表头
    ncols = len(table.headers)
    t = doc.add_table(rows=nrows, cols=ncols, style="Table Grid")
    t.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头
    for ci, h in enumerate(table.headers):
        cell = t.cell(0, ci)
        cell.paragraphs[0].clear()
        r = cell.paragraphs[0].add_run(str(h))
        _set_font(r, font_size, bold=bold_header)
        if center_align:
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 数据行
    for ri, row in enumerate(table.rows):
        for ci in range(ncols):
            val = row[ci] if ci < len(row) else ""
            _write_cell(t.cell(ri + 1, ci), val, font_size, center_align)

    # 合计行
    if table.total_row is not None:
        last = nrows - 1
        for ci in range(ncols):
            val = table.total_row[ci] if ci < len(table.total_row) else ""
            _write_cell(t.cell(last, ci), val, font_size, center_align)

    # 🔴 列宽按内容分配（业主表格规格：铺满版心、首列名称列更宽）。
    #    不设列宽时 Word 会把 10+ 列平均切，数字列挤成一条，视觉很突兀。
    _set_column_widths(t, table.headers)

    # 表题放表格正下方
    if caption_below and (table.table_no or table.caption):
        label = f"{table.table_no} {table.caption}".strip()
        _add_caption_below(doc, label)

    # 🔴 表注（如「注：表中被征（用）地单位均位于朱坝街道。」）——
    #    首列做了公共前缀提取后，必须把提取掉的信息交代清楚，否则读者对不上号。
    if getattr(table, "notes", ""):
        try:
            _p = doc.add_paragraph()
            _p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            _r = _p.add_run(table.notes)
            _r.font.name = _FONT_TABLE
            _r._element.rPr.rFonts.set(qn('w:eastAsia'), _FONT_TABLE)
            _r.font.size = Pt(max(9.0, font_size - 2))
        except Exception:
            pass

    return t


def _set_column_widths(tbl, headers):
    """按列名分配合适的列宽：名称/描述列宽，纯数值列窄；整体铺满版心。"""
    n = len(headers or [])
    if not n:
        return
    tbl.autofit = False
    # 版心宽度 ≈ A4 纵向去掉左右页边距后的可用宽度
    _TOTAL = 15.6  # cm
    _WIDE = ("单位", "坐落", "名称", "权属", "类别", "描述", "风险", "措施", "情形",
             "结论", "项目", "内容", "评分标准", "阶段", "类型")
    _NARROW = ("序号", "权重", "评分", "得分", "个数", "比例", "发生", "等级", "编号")

    widths = []
    for h in (headers or []):
        hs = str(h)
        if any(k in hs for k in _NARROW):
            widths.append(0.06)
        elif any(k in hs for k in _WIDE):
            widths.append(0.14 if widths else 0.18)
        else:
            widths.append(0.09)
    total = sum(widths) or 1.0
    widths = [w / total for w in widths]
    try:
        for ci, w in enumerate(widths):
            for row in tbl.rows:
                row.cells[ci].width = Cm(_TOTAL * w)
    except Exception:
        pass


def _is_numeric(text: str) -> bool:
    """判断单元格内容是否属于「数字类」（数字、百分数、带单位的数值、"—"）。"""
    import re as _re
    t = str(text or "").strip().replace(",", "")
    return bool(t) and bool(_re.fullmatch(r'[-—–]?\d+(\.\d+)?\s*[%㎡亩元万户人份个]*', t))


def _write_cell(cell, text, font_size, center_align):
    """数据单元格：**文字左对齐、数字居中**（业主表格规格）。"""
    cell.paragraphs[0].clear()
    _txt = str(text).replace("\n", " ")
    r = cell.paragraphs[0].add_run(_txt)
    _set_font(r, font_size, bold=False)
    cell.paragraphs[0].alignment = (WD_ALIGN_PARAGRAPH.CENTER if _is_numeric(_txt)
                                    else WD_ALIGN_PARAGRAPH.LEFT)


def _set_font(run, size, bold=False):
    run.font.name = _FONT_TABLE
    try:
        run._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_TABLE)
    except Exception:
        pass
    run.font.size = Pt(size)
    run.bold = bold


def _add_caption_above(doc, text):
    """表题（表格上方，宋体小四**不加粗**，居中 —— 对齐参照件）。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.font.name = _FONT_CAPTION
    try:
        r._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_CAPTION)
    except Exception:
        pass
    r.font.size = Pt(12)
    # 🔴 参照件表名不加粗；与 report_assembler._add_table_caption / table_injector 统一
    r.bold = False


def _add_caption_below(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = _FONT_CAPTION
    try:
        r._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_CAPTION)
    except Exception:
        pass
    r.font.size = Pt(12)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
