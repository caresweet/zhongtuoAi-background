# -*- coding: utf-8 -*-
"""把 PDF 投标文件**重建**成可编辑的 Word（不是 pdf2docx 那种按坐标切碎）。

业主 2026-11-06：pdf2docx 转换件把合并单元格/甘特图/竖排文字拆成单字格，
不能用。这里改成「结构重建」：

  · 文字按段落重排，按字号/加粗识别一~三级标题；
  · 表格用 PyMuPDF 的表格识别取出单元格，重新画 Word 表格（列宽按比例、
    行高自适应，不再裁字/竖排）；
  · 扫描页（证书/身份证）整页转图插入；
  · 目录按识别出的标题重新生成（带点线、右对齐页码）；
  · 每页一个分页，页序与 PDF 基本对应。

用法：
    python scripts/pdf_to_clean_docx.py --pdf 众拓测绘-投标文件.pdf --out 众拓测绘-投标文件_重建.docx
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _set_font(run, name="宋体", size=12, bold=False):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    run.font.name = name
    run._element.rPr.rFonts.set(qn('w:eastAsia'), name)
    run.font.size = Pt(size)
    run.bold = bold


def _p(doc, text: str, *, size=12, bold=False, align=None, indent=True, name="宋体"):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "right":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    else:
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(3)
    if indent and align != "center":
        p.paragraph_format.first_line_indent = Cm(0.74)
    r = p.add_run(text)
    _set_font(r, name=name, size=size, bold=bold)
    return p


def _heading(doc, text: str, level: int):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    p = doc.add_paragraph(style=f"Heading {min(max(level, 1), 3)}")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if level == 1 else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(8 if level == 1 else 4)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    _set_font(r, name="黑体", size=16 if level == 1 else 14 if level == 2 else 12, bold=True)
    return p


def _clean(text: str) -> str:
    t = str(text or "").replace(" ", " ")
    t = re.sub(r'[ \t]+', " ", t)
    return t.strip()


def _join_lines(lines) -> str:
    out = ""
    for ln in lines:
        s = _clean(ln)
        if not s:
            continue
        if out and re.search(r'[，。；：、）】]$', out) is None and not re.match(r'^[（(]\d', s):
            out += s
        else:
            out += ("\n" if out else "") + s
    return out.strip()


def _table(doc, rows, widths_cm, font_pt=9):
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Cm
    ncols = max((len(r) for r in rows), default=0)
    if not ncols:
        return
    t = doc.add_table(rows=len(rows), cols=ncols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    for ri, row in enumerate(rows):
        for ci in range(ncols):
            cell = t.cell(ri, ci)
            cell.text = ""
            txt = re.sub(r'\s*\n\s*', '', str(row[ci] if ci < len(row) else ""))
            txt = _clean(txt)
            r = cell.paragraphs[0].add_run(txt)
            _set_font(r, size=font_pt, bold=(ri == 0))
            cell.paragraphs[0].alignment = 1
    # 列宽按**内容长度**分配：长文本列（项目名称等）拿更多宽度，避免挤成竖排
    weights = []
    for ci in range(ncols):
        lens = [len(_clean(str(r[ci]))) for r in rows if ci < len(r)]
        weights.append(max(4, max(lens or [4])))
    total = sum(weights) or 1
    for ci, w in enumerate(weights):
        cw = max(1.4, 16.0 * w / total)
        for row in t.rows:
            row.cells[ci].width = Cm(cw)
    return t


def _toc(doc, entries):
    """目录：点线 + 右对齐页码（制表位）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("目  录")
    _set_font(r, name="黑体", size=18, bold=True)
    for level, text, page in entries:
        q = doc.add_paragraph()
        q.paragraph_format.tab_stops.add_tab_stop(
            Cm(15.5), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        q.paragraph_format.left_indent = Cm(0.0 if level <= 1 else 0.7)
        q.paragraph_format.space_after = Pt(2)
        r = q.add_run(f"{text}\t{page}")
        _set_font(r, size=12 if level <= 1 else 10.5, bold=(level == 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=130)
    args = ap.parse_args()

    import fitz
    from docx import Document
    from docx.shared import Cm, Pt

    pdf = Path(args.pdf).expanduser()
    out = Path(args.out).expanduser()
    src = fitz.open(str(pdf))
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.5)
    sec.top_margin = sec.bottom_margin = Cm(2.3)

    # 封面（跳过 PDF 第 1 页，重建）
    cover = src[0].get_text()
    lines = [_clean(x) for x in cover.splitlines() if _clean(x)]
    title = "投标文件"
    project = next((x for x in lines if x.startswith("项目名称") or "地块" in x), "")
    pno = next((x for x in lines if "项目编号" in x), "")
    date = next((x for x in lines if "日期" in x), "")
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph(); p.alignment = 1
    r = p.add_run(title); _set_font(r, size=26, bold=True)
    for _ in range(3):
        doc.add_paragraph()
    for txt in (pno, project, date):
        if txt:
            _p(doc, txt, size=16, bold=True, indent=False)
    doc.add_page_break()

    # 目录：直接解析 PDF 第 2 页的目录（人工稿目录只有 8 个部分）
    # 第 1 页封面、第 2 页原目录都由本脚本重建，跳过
    body_pages = range(2, src.page_count)
    toc_entries = []
    try:
        t2 = src[1].get_text()
        for m in re.finditer(r'[（(]\s*([一二三四五六七八九十]+)\s*[)）]\s*([^\n.．]{2,40}?)[.．]{3,}\s*(\d+)', t2):
            toc_entries.append((1, f"（{m.group(1)}）{_clean(m.group(2))}", int(m.group(3))))
    except Exception:
        toc_entries = []
    if toc_entries:
        _toc(doc, toc_entries)
        doc.add_page_break()
    else:
        _toc(doc, [])
        doc.add_page_break()
    for pi in body_pages:
        page = src[pi]
        d = page.get_text("dict")
        text_len = len(page.get_text().strip())
        tables = []
        try:
            for t in page.find_tables().tables:
                tables.append((t.bbox, t.extract(), t.col_count))
        except Exception:
            pass

        # 扫描页：文字极少且无表格 → 整页转图
        if text_len < 120 and not tables:
            pix = page.get_pixmap(dpi=args.dpi)
            doc.add_picture(io.BytesIO(pix.tobytes("png")), width=Cm(16))
            doc.add_page_break()
            continue

        # 收集文字块（排除表格区域）
        def _in_table(bbox):
            x0, y0, x1, y1 = bbox
            for tb, _, _ in tables:
                tx0, ty0, tx1, ty1 = tb
                if x0 >= tx0 - 2 and y0 >= ty0 - 2 and x1 <= tx1 + 2 and y1 <= ty1 + 2:
                    return True
            return False

        items = []
        for b in d.get("blocks", []):
            bbox = b.get("bbox", (0, 0, 0, 0))
            if b.get("type") == 1:  # 图片
                if text_len < 600:      # 图多的页整页转图更稳
                    continue
                items.append((bbox[1], bbox[0], "img", b))
                continue
            if _in_table(bbox):
                continue
            lines = []
            mx = 0
            bold = False
            for ln in b.get("lines", []):
                txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
                if _clean(txt):
                    lines.append(txt)
                for sp in ln.get("spans", []):
                    mx = max(mx, sp.get("size", 0))
                    bold = bold or bool(sp.get("flags", 0) & 16)
            text = _join_lines(lines)
            if not text:
                continue
            # 去掉纯页码
            if re.fullmatch(r'[-—\s]*\d{1,3}[-—\s]*', text):
                continue
            items.append((bbox[1], bbox[0], "text", (text, mx, bold)))
        for tb, rows, ncol in tables:
            items.append((tb[1], tb[0], "table", (rows, tb, ncol)))
        items.sort(key=lambda x: (round(x[0], 1), x[1]))

        # 正文字号
        sizes = [it[3][1] for it in items if it[2] == "text"]
        body = Counter(round(s, 1) for s in sizes).most_common(1)[0][0] if sizes else 12

        for _, _, kind, data in items:
            if kind == "text":
                text, mx, bold = data
                if len(text) <= 40 and (mx >= body + 4 or (bold and mx >= body + 2)):
                    _heading(doc, text, 1 if mx >= body + 6 else 2)
                elif len(text) <= 60 and bold and mx >= body + 1:
                    _heading(doc, text, 3)
                else:
                    _p(doc, text, size=12)
            elif kind == "table":
                rows, bbox, ncol = data
                widths = [max(1.0, (bbox[2] - bbox[0]) / max(ncol, 1))] * ncol
                _table(doc, rows, widths)
                doc.add_paragraph()
            elif kind == "img":
                # 页内小图：整页图更可靠，这里忽略，避免重复
                pass
        doc.add_page_break()

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    print("SAVED", out, "pages", src.page_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
