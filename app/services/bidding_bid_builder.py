# -*- coding: utf-8 -*-
"""投标（响应）文件生成器 —— 严格按招标文件要求的格式章节组装。

流程（业主 2026-11-05 口径）：
  1. `bidding_tender.parse_tender` 读懂招标文件（项目信息/资格/人员/评分/格式章节）；
  2. 从公司人员库按最低配置**随机抽人**（负责人1+组员3），带证书图片；
  3. 按招标「投标文件格式」那一章的目录逐项出：投标函、报价文件、资格证明、
     偏离表、评分导读表、服务方案、人员配置清单、其他；
  4. 服务方案等**正文由 LLM 按 5 章 23 节逐节写**（约 9.4 万字），资料类从公司档案取；
  5. 报价由调用方传入（UI 在上传招标文件后弹窗询问）。

不支持的格式（PDF 招标文件）会先尝试文本解析；解析不到格式目录时按同域
「投标响应文件」默认结构出，并在文档里标注需人工核对。
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import io
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[2]
KB = BACKEND / "data" / "knowledge_base.db"
AUTH = BACKEND / "data" / "auth.db"

# 当前投标公司（三家公司共用一套档案库，取图/取证必须按公司隔离）
_ASSET_COMPANY: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "bid_asset_company", default=None)


# ═══════════════════════════════════════════════════════════════
# 基础排版
# ═══════════════════════════════════════════════════════════════
def _set_font(run, name="宋体", size=12, bold=False):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    run.font.name = name
    run._element.rPr.rFonts.set(qn('w:eastAsia'), name)
    run.font.size = Pt(size)
    run.bold = bold


def _p(doc, text: str, *, size=15, bold=False, align="left", indent=True,
       name="宋体", space_after=4):
    """正文段落。默认宋体 15 磅（人工稿正文字号），正文行距 1.5 倍。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                   "right": WD_ALIGN_PARAGRAPH.RIGHT,
                   "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}.get(align, WD_ALIGN_PARAGRAPH.LEFT)
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.first_line_indent = Cm(1.06)      # 15 磅正文两字符
    if size >= 14 and align in ("justify", "left"):
        p.paragraph_format.line_spacing = 1.5
        p.paragraph_format.space_after = Pt(0)
    r = p.add_run(text)
    _set_font(r, name=name, size=size, bold=bold)
    return p


def _h(doc, text: str, level=1, *, toc=True, size=None, font=None):
    """章节标题（照人工稿：**一律不加粗**）。

    人工稿 PDF 实测：部分标题宋体 15、部分标题仿宋 18/15，字重都是常规
    （PDF 字体 flags 无 bold 位）。旧实现全部用黑体加粗，观感比人工稿重。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    # 一级标题挂 Heading 1（目录域抓取）；二/三级挂 Heading 2/3（版式统一用）
    style = ("Heading 1" if (level == 1 and toc)
             else "Heading 2" if level == 2 else
             "Heading 3" if level == 3 else None)
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if level == 1 else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(10 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    # 八个部分用宋体 15；各部分的「1、2、3、」子项用黑体 14（人工稿实测）；
    # 服务方案内的分章/分节由调用方显式传 font="仿宋"
    _font = font or ("宋体" if (level == 1 and toc) else "黑体")
    _size = size or (15 if level == 1 else 14 if level == 2 else 15)
    _set_font(r, name=_font, size=_size, bold=False)
    from docx.shared import RGBColor
    r.font.color.rgb = RGBColor(0, 0, 0)
    return p


def _plan_p(doc, text: str):
    """服务方案正文：仿宋 15pt、固定 28 磅行距、首行缩进 2 字符（对齐人工稿）。"""
    from docx.shared import Cm, Pt
    p = _p(doc, text, size=15, align="justify", name="仿宋")
    p.paragraph_format.line_spacing = Pt(28)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.first_line_indent = Cm(1.06)
    return p


def _p_two(doc, left: str, right: str, *, size=12, indent=0.0,
           right_pos_cm=15.0):
    """一行两段：左文 + 右文（制表位右对齐），用于「投标人名称… 单位：元」。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.first_line_indent = Cm(indent) if indent else None
    p.paragraph_format.tab_stops.add_tab_stop(Cm(right_pos_cm),
                                              WD_TAB_ALIGNMENT.RIGHT)
    r = p.add_run(left)
    _set_font(r, size=size)
    r = p.add_run("\t" + right)
    _set_font(r, size=size)
    return p


def _p_mix(doc, parts, *, size=12, align="justify", indent=True,
           name="仿宋", space_after=4):
    """一段文字里混排**带下划线**的填写值（招标格式模板的空格要保留成下划线）。

    parts: [(文字, 是否加下划线), ...]，例如
        [("根据贵方的", False), (project, True), ("（项目名称）招标文件…", False)]
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    p = doc.add_paragraph()
    p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,
                   "center": WD_ALIGN_PARAGRAPH.CENTER,
                   "right": WD_ALIGN_PARAGRAPH.RIGHT,
                   "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}.get(
                       align, WD_ALIGN_PARAGRAPH.LEFT)
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.first_line_indent = Cm(1.06)
    for text, underline in parts:
        if not text:
            continue
        r = p.add_run(str(text))
        _set_font(r, name=name, size=size)
        if underline:
            r.font.underline = True
    return p


def _image_row(doc, imgs, *, width_cm=6.8):
    """身份证正反面并排（带细边框，无图题），与人工稿一致。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    if not imgs:
        return None
    t = doc.add_table(rows=1, cols=len(imgs))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (_nm, data) in enumerate(imgs):
        cell = t.cell(0, i)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = 1
        try:
            p.add_run().add_picture(io.BytesIO(data), width=Cm(width_cm))
        except Exception as e:
            logger.warning(f"[投标文件] 身份证图片插入失败: {e}")
        for row in t.rows:
            row.cells[i].width = Cm(width_cm)
    return t


_BID_TOP_TITLES = [
    "（一）投标函", "（二）报价文件", "（三）资格证明文件",
    "（四）技术条款及相关要求偏离表", "（五）技术商务部分评分导读表",
    "（六）服务内容的详细说明及服务方案", "（七）项目人员配置清单", "（八）其他",
]

# 评分导读表 9 行的「投标文件对应页码」锚点（顺序与 _score_rows 一致）
_SCORING_ANCHORS = (
    "3. 类似项目业绩",
    "（七）项目人员配置清单",
    "1. 体系认证证书",
    "2. 仪器设备",
    "第一章 对项目的理解",
    "第二章 项目组织实施方案",
    "第三章 质量保障措施",
    "第四章 进度安排",
    "第五章 安全保密保障措施",
)


def _fill_scoring_pages(d, pages: Dict[str, int], anchors=None) -> None:
    """把评分导读表 9 行的页码换成真实页码（与目录同一次分页）。"""
    if not pages:
        return
    anchors = tuple(anchors if anchors is not None else _SCORING_ANCHORS)
    tbl = None
    for t in d.tables:
        if t.rows and any("自评分" in c.text for c in t.rows[0].cells):
            tbl = t
            break
    if tbl is None:
        return
    for i, title in enumerate(anchors, start=1):
        if title not in pages or i >= len(tbl.rows):
            continue
        cell = tbl.rows[i].cells[-2]
        cell.text = ""
        r = cell.paragraphs[0].add_run(str(pages[title]))
        _set_font(r, name="仿宋", size=14)


def _stamp_toc(path, titles=None, anchors=None, style=None) -> bool:
    """把目录域换成**带真实页码的静态条目**（用 LibreOffice 分页）。

    titles 默认丁蜀镇八件套；简式投标文件传入自己的目录。
    目录条目的字体/行距/是否带页码取公司体例（`style["toc"]`）。
    只在机器上有 soffice 时执行；失败就保留目录域，不影响出报告。
    """
    titles = list(titles or _BID_TOP_TITLES)
    anchors = tuple(anchors) if anchors is not None else tuple(_SCORING_ANCHORS)
    import shutil as _sh
    import subprocess
    import tempfile
    from pathlib import Path as _P
    _ts = ((style or {}).get("toc") or {}) if style else {
        "entry_font": "黑体", "entry_size": 14, "line_pt": 31.2,
        "show_page_numbers": True}
    soffice = _sh.which("soffice")
    if not soffice:
        return False
    path = _P(path)
    tmp = _P(tempfile.mkdtemp(prefix="bidtoc_"))
    try:
        # 🔴 量页码用一份**去掉 updateFields 的副本**：否则 LibreOffice 转 PDF 时
        #    会先把 TOC 域自动展开（条目更多），目录多占一页，导致后面所有锚点
        #    页码整体 +1（实测 2026-11-11：检查表页码比实际大 1）。
        try:
            from docx import Document as _MeasureDoc
            from docx.oxml.ns import qn as _qn
            _m = _MeasureDoc(str(path))
            _st_el = _m.settings.element
            _uf = _st_el.find(_qn('w:updateFields'))
            if _uf is not None:
                _st_el.remove(_uf)
            _measure = tmp / "measure.docx"
            _m.save(str(_measure))
        except Exception:
            _measure = path
        subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(tmp), str(_measure)],
            check=True, capture_output=True, timeout=240)
        pdf = tmp / f"{_measure.stem}.pdf"
        if not pdf.exists():
            return False
        import fitz

        def _detect(_pdf) -> Dict[str, int]:
            """按整行标题找页码（0 起的 PDF 页序 = 版心打印页码）。"""
            _src = fitz.open(str(_pdf))
            _wanted = list(titles) + list(anchors)
            _found: Dict[str, int] = {}
            for i in range(2, _src.page_count):
                # 跳过封面/目录页，按**整行等于标题**匹配，避免命中目录条目。
                # ⚠️ LibreOffice 会把「第一章 对项目的理解」中间的空格吃掉，
                #    所以同时比对去空格后的行（实测导致方案章节页码一直填不上）。
                raw_lines = [ln.strip() for ln in _src[i].get_text().splitlines()]
                lines = set(raw_lines) | {ln.replace(" ", "").replace("　", "")
                                          for ln in raw_lines}
                _anchor_set = set(anchors)
                _norm_page = "".join(raw_lines).replace(" ", "").replace("　", "")
                for title in _wanted:
                    if title not in _anchor_set and title in _found:
                        continue          # 目录标题取**首次**出现
                    hit = (title in lines or title.replace(" ", "") in lines)
                    if not hit:
                        # 长标题在 PDF 里会折行（「文件5」与正文可能落在两行）：
                        # 去空格后取前 12 字，在**整页拼起来的文本**里找
                        _key = title.replace(" ", "").replace("　", "")[:12]
                        hit = bool(_key) and _key in _norm_page
                    if hit:
                        # 文件锚点取**最后一次**出现（前面的目录/检查表也印着同样的文字）
                        _found[title] = i
            _src.close()
            return _found

        _found = _detect(pdf)
        pages = {t: _found[t] for t in titles if t in _found}
        anchor_pages = {t: _found[t] for t in anchors if t in _found}
        if len(pages) < len(titles):
            return False

        from docx import Document
        from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
        from docx.shared import Cm, Pt
        d = Document(str(path))
        paras = d.paragraphs
        ph = next((p for p in paras
                   if "右键选择" in p.text or 'TOC \\o' in p._p.xml), None)
        if ph is None:
            # 已有静态目录：只刷新页码
            _existing = [p for p in paras
                         if any(p.text.startswith(t + "\t") for t in titles)]
            if len(_existing) != len(titles):
                return False
            for q, title in zip(_existing, titles):
                for r in list(q.runs):
                    r._element.getparent().remove(r._element)
                _num = (f"\t{pages[title]}"
                        if _ts.get("show_page_numbers") else "")
                r = q.add_run(f"{title}{_num}")
                _set_font(r, name=_ts.get("entry_font") or "宋体",
                          size=_ts.get("entry_size") or 14, bold=False)
                q.paragraph_format.space_after = Pt(0)
                q.paragraph_format.line_spacing = Pt(_ts.get("line_pt") or 33)
            _fill_scoring_pages(d, anchor_pages, anchors)
            _fill_page_tokens(d, {**pages, **anchor_pages})
            d.save(str(path))
            return True
        nxt = ph._p.getnext()
        from docx.text.paragraph import Paragraph
        anchor = Paragraph(nxt, ph._parent) if nxt is not None else None
        for title in titles:
            q = (anchor.insert_paragraph_before() if anchor is not None
                 else ph.insert_paragraph_before())
            # 人工稿目录：黑体 14 磅、点线引导、页码右对齐到版心右边界（3cm 页边距 → 15cm）、段后 2 磅
            _pg = (style or {}).get("page") or {}
            _right = 21.0 - float(_pg.get("margin_left_cm", 2.4)) - float(_pg.get("margin_right_cm", 2.8))
            q.paragraph_format.tab_stops.add_tab_stop(
                Cm(_right), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
            q.paragraph_format.space_after = Pt(0)
            q.paragraph_format.line_spacing = Pt(_ts.get("line_pt") or 33)
            _num = (f"\t{pages[title]}"
                    if _ts.get("show_page_numbers") else "")
            r = q.add_run(f"{title}{_num}")
            _set_font(r, name=_ts.get("entry_font") or "宋体",
                      size=_ts.get("entry_size") or 14, bold=False)
        ph._p.getparent().remove(ph._p)
        _fill_scoring_pages(d, anchor_pages, anchors)
        _fill_page_tokens(d, {**pages, **anchor_pages})
        d.save(str(path))
        # 🔴 第二遍：把目录域换成静态条目后，目录占的页数可能变了（域展开时目录
        #    多占一页 → 后面整体后移一页，检查表页码会比实际大 1，实测 2026-11-11）。
        #    用最终 docx 再量一次分页，只校正页码，不再改版式。
        try:
            subprocess.run(
                [soffice, "--headless", "--norestore", "--convert-to", "pdf",
                 "--outdir", str(tmp), str(path)],
                check=True, capture_output=True, timeout=240)
            _fin = tmp / f"{path.stem}.pdf"
            if _fin.exists():
                _f2 = _detect(_fin)
                _p2 = {t: _f2[t] for t in titles if t in _f2}
                _a2 = {t: _f2[t] for t in anchors if t in _f2}
                if _p2 != pages or _a2 != anchor_pages:
                    logger.info(f"[投标文件] 目录页码二次校正：一={_p2.get(titles[0])} "
                                f"文件1={_a2.get(anchors[0]) if anchors else '-'}")
                    _d2 = Document(str(path))
                    for _q in _d2.paragraphs:
                        for _t in titles:
                            if _q.text.startswith(_t + "\t"):
                                for _r in list(_q.runs):
                                    _r._element.getparent().remove(_r._element)
                                _num = (f"\t{_p2[_t]}"
                                        if _ts.get("show_page_numbers") else "")
                                _r = _q.add_run(f"{_t}{_num}")
                                _set_font(_r, name=_ts.get("entry_font") or "宋体",
                                          size=_ts.get("entry_size") or 14,
                                          bold=False)
                                break
                    _fill_scoring_pages(_d2, _a2, anchors)
                    _fill_page_tokens(_d2, {**_p2, **_a2})
                    # 第一遍已把检查表的 {{…}} 换成数字，第二遍按行内容回填新页码
                    for _t2 in _d2.tables:
                        _hdr = [c.text for c in _t2.rows[0].cells]
                        _pi = next((i for i, h in enumerate(_hdr) if "页码" in h),
                                   None)
                        if _pi is None:
                            continue
                        for _row in _t2.rows[1:]:
                            _cells = _row.cells
                            if len(_cells) < 2:
                                continue
                            _ct = _cells[1].text.strip()
                            for _a in anchors:
                                _name = _a.partition(".")[2]
                                if _a in _a2 and _ct[:10] == _name[:10]:
                                    _cells[_pi].text = str(_a2[_a])
                                    break
                    _d2.save(str(path))
        except Exception as e:
            logger.warning(f"[投标文件] 目录页码二次校正失败: {e}")
        return True
    finally:
        import shutil as _sh2
        _sh2.rmtree(tmp, ignore_errors=True)


def _fill_page_tokens(d, pages: Dict[str, int]) -> None:
    """表格单元格里写 {{章节标题}} 的，换成该章真实页码。"""
    import re as _re
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                txt = cell.text.strip()
                m = _re.match(r'^\{\{(.+?)\}\}$', txt)
                if not m or m.group(1) not in pages:
                    continue
                cell.text = ""
                r = cell.paragraphs[0].add_run(str(pages[m.group(1)]))
                _set_font(r, name="仿宋", size=12)


def _add_toc(doc, style=None) -> None:
    """第二页目录：标题 + TOC 域（打开文档时自动更新页码）。版式取公司体例。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn as _q
    from docx.shared import Pt
    toc = (style or {}).get("toc") or {}
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(toc.get("title") or "目　录")
    if not style:      # 老路径（丁蜀镇全量版）保持黑体 20、不加粗
        _set_font(r, name="黑体", size=20, bold=False)
    else:
        _set_font(r, name=toc.get("title_font") or "宋体",
                  size=toc.get("title_size") or 20,
                  bold=bool(toc.get("title_bold", True)))
    p.paragraph_format.space_before = Pt(7)    # 人工稿标题距上边距约 7 磅
    p.paragraph_format.space_after = Pt(30)

    q = doc.add_paragraph()
    run = q.add_run()
    _set_font(run, size=12)
    begin = OxmlElement('w:fldChar')
    begin.set(_q('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.set(_q('xml:space'), 'preserve')
    instr.text = 'TOC \\o "1-1" \\h \\z \\u'
    sep = OxmlElement('w:fldChar')
    sep.set(_q('w:fldCharType'), 'separate')
    placeholder = OxmlElement('w:t')
    placeholder.text = "右键选择“更新域”生成目录"
    end = OxmlElement('w:fldChar')
    end.set(_q('w:fldCharType'), 'end')
    for el in (begin, instr, sep, placeholder, end):
        run._r.append(el)

    # Word / WPS 打开时自动刷新目录域
    try:
        from docx.oxml import OxmlElement as _OE
        st = doc.settings.element
        if st.find(_q('w:updateFields')) is None:
            uf = _OE('w:updateFields')
            uf.set(_q('w:val'), 'true')
            st.append(uf)
    except Exception:
        pass


def _new_section(doc, landscape: bool):
    """新起一节并设纸张方向（人工稿：偏离表、人员配置清单是横排 A4）。"""
    from docx.enum.section import WD_ORIENT, WD_SECTION
    from docx.shared import Cm
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    if landscape:
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = Cm(29.7), Cm(21.0)
        sec.left_margin = sec.right_margin = Cm(2.5)
        sec.top_margin = sec.bottom_margin = Cm(2.5)
    else:
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
        sec.left_margin = sec.right_margin = Cm(3.0)
        sec.top_margin = sec.bottom_margin = Cm(3.0)
    return sec


def _page_number_footer(doc) -> None:
    """页脚居中页码（封面页不显示）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn as _q
    from docx.shared import Pt
    sec = doc.sections[0]
    sec.different_first_page_header_footer = True
    # 页码从 0 起：封面不显示，目录页印 1，与人工稿一致
    try:
        sectPr = sec._sectPr
        if sectPr.find(_q('w:pgNumType')) is None:
            pn = OxmlElement('w:pgNumType')
            pn.set(_q('w:start'), '0')
            sectPr.append(pn)
    except Exception:
        pass
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    _set_font(run, size=9)
    fld = OxmlElement('w:fldSimple')
    fld.set(_q('w:instr'), 'PAGE')
    r = OxmlElement('w:r')
    t = OxmlElement('w:t')
    t.text = "1"          # 不刷新域的阅读器至少看到页码 1
    r.append(t)
    fld.append(r)
    p._p.append(fld)


def _apply_body_format(doc, style=None) -> None:
    """按公司体例统一正文/标题/表格/页边距（参数全部来自 bidding_style 配置）。

    业主 2026-11-11：「所有的修改规格方面都不要写死，都是会变动的」——
    这里不写死字号边距，只把配置值套到全文；配置见 `storage/bidding_style.json`。
    """
    if not style:
        # 未提供公司体例时保持旧行为（正文 ≤12 磅统一 1.5 倍行距）
        for p in doc.paragraphs:
            sizes = [r.font.size.pt for r in p.runs if r.font.size]
            if p.text.strip() and sizes and max(sizes) <= 12:
                p.paragraph_format.line_spacing = 1.5
                p.paragraph_format.space_after = 0
        return
    import re as _re
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    st = style or {}
    body = st.get("body") or {}
    h1 = st.get("heading1") or {}
    tbl = st.get("table") or {}
    pg = st.get("page") or {}
    # 页边距（所有节，横排只改上下左右，不动纸张方向）
    for sec in doc.sections:
        if pg:
            sec.top_margin = Cm(pg.get("margin_top_cm", 2.5))
            sec.bottom_margin = Cm(pg.get("margin_bottom_cm", 2.5))
            sec.left_margin = Cm(pg.get("margin_left_cm", 2.4))
            sec.right_margin = Cm(pg.get("margin_right_cm", 2.8))
    _LABEL = _re.compile(
        r'^(投标人|致：|地址|邮编|电话|传真|开户|账\s*户|代理人|单位名称|授权单位|'
        r'地\s*址|日\s*期|法定代表人|企业名称|供应商|项目名称|项目投标报价|'
        r'服务期限|项目负责人|是否为小微企业|文件\d|（\d）|[一二三四五六七八九十]+、)')
    _toc_title = (st.get("toc") or {}).get("title") or ""
    _in_toc = False
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        if _toc_title and text == _toc_title:
            _in_toc = True
            continue
        if _in_toc:
            if p.style.name == "Heading 1":
                _in_toc = False
            else:
                continue          # 目录条目：字体/行距由 _stamp_toc 按 toc 配置写入
        # 一级标题：黑体 16 加粗居中（配置）
        if p.style.name == "Heading 1":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                if r.text.strip():
                    _set_font(r, name=h1.get("font", "黑体"),
                              size=h1.get("size", 16), bold=h1.get("bold", True))
            p.paragraph_format.line_spacing = 1.5
            continue
        if p.style.name in ("Heading 2", "Heading 3"):
            _hs = st.get("heading2" if p.style.name == "Heading 2" else "heading3") or {}
            for r in p.runs:
                if r.text.strip():
                    _set_font(r, name=_hs.get("font", "宋体"),
                              size=_hs.get("size", 15), bold=_hs.get("bold", True))
            p.alignment = (WD_ALIGN_PARAGRAPH.CENTER
                           if _hs.get("align") == "center"
                           else WD_ALIGN_PARAGRAPH.LEFT)
            p.paragraph_format.line_spacing = 1.5
            continue
        # 封面/大标题（>16 磅）不动
        sizes = [r.font.size.pt for r in p.runs if r.font.size]
        if sizes and max(sizes) > 16:
            continue
        _all_bold = bool(p.runs) and all(
            r.bold for r in p.runs if r.text.strip())
        for r in p.runs:
            if r.text.strip():
                _set_font(r, name=body.get("font", "宋体"),
                          size=body.get("size", 14), bold=_all_bold)
        centered = p.alignment == WD_ALIGN_PARAGRAPH.CENTER
        if not centered and not _LABEL.match(text):
            p.paragraph_format.first_line_indent = Cm(body.get("first_line_cm", 0.87))
        else:
            p.paragraph_format.first_line_indent = Cm(0)
        # 投标函这类「长信函」建段时已按倍行距排过；保留它（人工稿实测约 22～24 磅），
        # 不再改成 25 磅固定值，否则整封信会多出一行、签名/日期被挤到下一页。
        _ls = p.paragraph_format.line_spacing
        if not isinstance(_ls, float):
            p.paragraph_format.line_spacing = Pt(body.get("line_pt", 25))
        p.paragraph_format.space_after = Pt(0)
    # 表格：宋体、表头加粗（配置）
    for t in doc.tables:
        for ri, row in enumerate(t.rows):
            for cell in row.cells:
                for cp in cell.paragraphs:
                    for r in cp.runs:
                        if r.text.strip():
                            _set_font(r, name=tbl.get("font", "宋体"),
                                      size=tbl.get("size", 12),
                                      bold=(ri == 0 and tbl.get("header_bold", True)))


def _quote_detail_table(doc, items, total_yuan: int, big: str):
    """明细报价表：5 列 + 合计行跨列合并（照人工稿/招标模板）。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    n = len(items)
    t = doc.add_table(rows=1 + n + 2, cols=5)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["序号", "项目名称", "数量", "单价", "分项总报价"]
    for ci, h in enumerate(headers):
        cell = t.cell(0, ci)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        r = cell.paragraphs[0].add_run(h)
        _set_font(r, name="仿宋", size=14)
        cell.paragraphs[0].alignment = 1
    for i, (name, amt) in enumerate(items, 1):
        for ci, val in enumerate((str(i), name, "1", str(amt), str(amt))):
            cell = t.cell(i, ci)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            r = cell.paragraphs[0].add_run(val)
            _set_font(r, name="仿宋", size=14)
            cell.paragraphs[0].alignment = 1
    # 合计：左侧两列纵向合并，右侧三列拆成「小写/大写」两行
    left = t.cell(1 + n, 0).merge(t.cell(2 + n, 1))
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    left.text = ""
    r = left.paragraphs[0].add_run("合计")
    _set_font(r, name="仿宋", size=14)
    left.paragraphs[0].alignment = 1
    for ri, txt in ((1 + n, f"（小写）{total_yuan} 元"), (2 + n, f"（大写）{big}")):
        c = t.cell(ri, 2).merge(t.cell(ri, 4))
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        c.text = ""
        r = c.paragraphs[0].add_run(txt)
        _set_font(r, name="仿宋", size=14)
        c.paragraphs[0].alignment = 1
    t.autofit = False
    for ci, w in enumerate([1.2, 4.9, 2.6, 3.2, 3.1]):
        t.columns[ci].width = Cm(w)
        for row in t.rows:
            row.cells[ci].width = Cm(w)
    t.rows[0].height = Cm(1.38)
    for ri in range(1, n + 1):
        t.rows[ri].height = Cm(1.35)
    t.rows[1 + n].height = Cm(2.7)
    t.rows[2 + n].height = Cm(1.35)
    return t


def _table(doc, headers: Sequence[str], rows: Sequence[Sequence[str]],
           widths: Optional[Sequence[float]] = None, *, font_size=None,
           row_heights: Optional[Sequence[float]] = None,
           header_fill: Optional[str] = None,
           aligns: Optional[Sequence[str]] = None):
    """带表头的表格。

    单元格格式照人工稿 PDF：
      · 字体仿宋，字号随列数——≤6 列用 15 磅，7 列以上用 12 磅（甘特图等宽表）；
      · 表头行可加浅蓝底纹（方案表用 #D9E1F3，表单类表格不加）；
      · 默认横向居中，可按列指定 left（如偏离表/评分表的长文本列）；
      · 全部纵向居中，边框 Table Grid。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm
    if font_size is None:
        font_size = 15 if len(headers) <= 6 else 12
    aligns = list(aligns) if aligns else []
    t = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER

    def _align_of(ci: int) -> int:
        a = aligns[ci] if ci < len(aligns) else "center"
        return {"left": 0, "center": 1, "right": 2}.get(a, 1)

    for ci, h in enumerate(headers):
        cell = t.cell(0, ci)
        cell.text = ""
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        r = cell.paragraphs[0].add_run(str(h))
        # 人工稿 PDF 的表头也是常规字重（仿宋 14～15，无 bold 位），靠底纹/边框区分
        _set_font(r, name="仿宋", size=font_size, bold=False)
        cell.paragraphs[0].alignment = _align_of(ci)
        if header_fill:
            tcPr = cell._tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), header_fill)
            tcPr.append(shd)
    for ri, row in enumerate(rows, 1):
        for ci in range(len(headers)):
            cell = t.cell(ri, ci)
            cell.text = ""
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            val = row[ci] if ci < len(row) else ""
            r = cell.paragraphs[0].add_run(str(val))
            _set_font(r, name="仿宋", size=font_size)
            cell.paragraphs[0].alignment = _align_of(ci)
    if widths:
        t.autofit = False
        for ci, w in enumerate(widths):
            t.columns[ci].width = Cm(w)
            for r in t.rows:
                r.cells[ci].width = Cm(w)
    if row_heights:
        from docx.enum.table import WD_ROW_HEIGHT_RULE
        for i, h in enumerate(row_heights):
            if i < len(t.rows):
                t.rows[i].height = Cm(h)
                # 必须写 hRule=atLeast：只给 height 时 Word 按 auto 处理，行高不生效
                t.rows[i].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    return t


def _grid(doc, rows: Sequence[Sequence[str]],
          widths: Optional[Sequence[float]] = None, *, font_size=15):
    """无表头栅格（关联单位表、地址/联系信息表用）：全部单元格同字号、不加粗。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    if not rows:
        return None
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ri, row in enumerate(rows):
        for ci in range(len(rows[0])):
            cell = t.cell(ri, ci)
            cell.text = ""
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            val = row[ci] if ci < len(row) else ""
            r = cell.paragraphs[0].add_run(str(val))
            _set_font(r, name="仿宋", size=font_size)
            cell.paragraphs[0].alignment = 1
    if widths:
        t.autofit = False
        for ci, w in enumerate(widths):
            t.columns[ci].width = Cm(w)
            for r in t.rows:
                r.cells[ci].width = Cm(w)
    return t


def _related_companies(company_id: int, leader: str) -> str:
    """与本公司同一法定代表人的其他单位（投标函 13 关联单位表按此填）。

    人工稿该表填的是「江苏众拓项目代理咨询有限公司」——同一法定代表人。
    数据从 auth.db 的 companies 表按 leader_name 反查，不写死公司名。
    """
    if not leader:
        return ""
    try:
        c = sqlite3.connect(str(AUTH))
        rows = c.execute(
            "select name from companies where id<>? and leader_name=? "
            "and coalesce(status,'')<>'deleted' order by id",
            (company_id, leader)).fetchall()
        c.close()
        return "、".join(str(r[0]) for r in rows if r[0])
    except Exception:
        return ""


def _center_all_cells(doc) -> None:
    """全篇表格单元格**纵向**居中；横向对齐保留各表自己的设置
    （人工稿：方案表居中，偏离表/评分表的长文本列左对齐）。"""
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _img(doc, data: bytes, width_cm=15.0, caption="", page_break_before=False,
         rotate90=False):
    """插一张图。`rotate90` 用于横放的扫描件（营业执照）：Word 里整页竖排显示。"""
    from docx.shared import Cm
    try:
        doc.add_picture(io.BytesIO(data), width=Cm(width_cm))
    except Exception as e:
        logger.warning(f"[投标文件] 图片插入失败: {e}")
        return None
    para = doc.paragraphs[-1]
    if page_break_before:
        para.paragraph_format.page_break_before = True
    if rotate90:
        from docx.oxml.ns import qn as _q
        for _xfrm in para._p.iter(_q('a:xfrm')):
            _xfrm.set('rot', '16200000')      # 270°（Word 单位为 1/60000 度）
    if caption:
        _p(doc, caption, size=10.5, align="center", indent=False)
    return para


# ═══════════════════════════════════════════════════════════════
# 数据读取
# ═══════════════════════════════════════════════════════════════
def company_name(company_id: int) -> str:
    try:
        c = sqlite3.connect(str(AUTH))
        row = c.execute("select name from companies where id=?", (company_id,)).fetchone()
        c.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _asset_texts(company: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        c = sqlite3.connect(str(KB))
        for title, content in c.execute(
                "select title,content from company_assets where company=? and is_active=1",
                (company,)):
            out[str(title or "")] = str(content or "")
        c.close()
    except Exception:
        pass
    return out


def _asset_names() -> List[str]:
    """公司档案图片名（只取名，不读图，用于按年份/关键词挑材料）。

    按当前投标公司过滤：三家公司各有自己的营业执照/审计报告/证书，不能串。
    """
    try:
        c = sqlite3.connect(str(KB))
        _cid = _ASSET_COMPANY.get()
        sql = "select image_name from asset_images where is_active=1"
        args: tuple = ()
        if _cid is not None:
            sql += " and company_id=?"
            args = (int(_cid),)
        rows = [str(r[0] or "") for r in c.execute(sql + " order by id", args)]
        c.close()
        return rows
    except Exception:
        return []


def _asset_rotate_deg(name: str) -> int:
    """某张档案图需要旋转多少度（扫描件方向不一致，逐图记在 rotate_deg）。"""
    try:
        c = sqlite3.connect(str(KB))
        _cid = _ASSET_COMPANY.get()
        sql = "select rotate_deg from asset_images where image_name=?"
        args: list = [name]
        if _cid is not None:
            sql += " and company_id=?"
            args.append(int(_cid))
        row = c.execute(sql + " order by id limit 1", args).fetchone()
        c.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _asset_images(*keywords: str, limit: int = 12) -> List[Tuple[str, bytes]]:
    out = []
    try:
        c = sqlite3.connect(str(KB))
        _cid = _ASSET_COMPANY.get()
        sql = ("select image_name,image_data from asset_images where is_active=1")
        args: tuple = ()
        if _cid is not None:
            sql += " and company_id=?"
            args = (int(_cid),)
        rows = c.execute(sql + " order by id", args).fetchall()
        c.close()
    except Exception:
        return out
    for name, b64 in rows:
        nm = str(name or "")
        if not any(k in nm for k in keywords):
            continue
        try:
            out.append((nm, base64.b64decode(b64)))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _social_insurance_images(company_id: int, limit: int = 8
                             ) -> List[Tuple[str, bytes]]:
    """最近月份的公司社保材料扫描件（业主每月导入 → company_social_insurance）。"""
    out: List[Tuple[str, bytes]] = []
    try:
        c = sqlite3.connect(str(KB))
        rows = c.execute(
            "select period, image_path from company_social_insurance "
            "where company_id=? and image_path<>'' "
            "order by period desc, id", (company_id,)).fetchall()
        c.close()
    except Exception:
        return out
    _period = ""
    for period, path in rows:
        # 只取最近一个月的材料，避免把历史月份全塞进投标文件
        if _period and period != _period:
            break
        _period = period or _period
        p = Path(str(path or ""))
        if not p.is_absolute():
            p = BACKEND / p
        if p.exists():
            out.append((p.name, p.read_bytes()))
        if len(out) >= limit:
            break
    return out


def _person_images(personnel_id: int) -> List[Tuple[str, bytes]]:
    """某人**招标要求的那类证书**（职称/注册测绘师/执业资格），不要培训证书。

    🔴 业主 2026-11-11（专家意见）：投标文件里放的证书必须是招标文件点名的
       资质，不能拿培训证书之类「凑数」。
    """
    out = []
    _KEEP = "('注册测绘师','职称证书','执业资格证书')"
    _cid = _ASSET_COMPANY.get()
    _csql = " and company_id=?" if _cid is not None else ""
    _cargs: tuple = (int(_cid),) if _cid is not None else ()
    try:
        c = sqlite3.connect(str(KB))
        rows = c.execute(
            "select image_name,image_path from company_personnel_images "
            f"where personnel_id=? and category in {_KEEP}{_csql} "
            "order by case when category='注册测绘师' then 0 "
            "when category='职称证书' then 1 else 2 end, id",
            (personnel_id,) + _cargs).fetchall()
        if not rows:
            # 同一人可能以另一家公司入库（历史数据重复导入），按姓名兜底取证书图；
            # 仍限定在**当前投标公司**，不跨公司借证书。
            rows = c.execute(
                "select i.image_name, i.image_path from company_personnel_images i "
                "join company_personnel p on p.id = i.personnel_id "
                f"where p.name = (select name from company_personnel where id=?) "
                f"and i.category in {_KEEP}{_csql.replace('company_id', 'i.company_id')} "
                "order by case when i.category='注册测绘师' then 0 "
                "when i.category='职称证书' then 1 else 2 end, i.id",
                (personnel_id,) + _cargs).fetchall()
        c.close()
    except Exception:
        return out
    for name, path in rows:
        p = Path(str(path or ""))
        if not p.is_absolute():
            p = BACKEND / p
        if p.exists():
            out.append((str(name), p.read_bytes()))
    return out


# ═══════════════════════════════════════════════════════════════
# 服务方案（agent 写）
# ═══════════════════════════════════════════════════════════════
# 服务方案章节结构（对齐人工稿 232 页稿：5 章 57 节，目标约 8.6 万字）
# 🔴 业主 2026-11-10：「服务方案分节要多，学习人工稿中的表格书写习惯和内容」——
#    人工稿每章 9～14 节、节内配管理表格；这里按人工稿的分节粒度重排，
#    表格由 `bidding_plan_tables` 按小节号插入（见 _SECTION_TABLES）。
_PLAN_CHAPTERS: List[Tuple[str, List[Tuple[str, List[str], int]]]] = [
    ("第一章 对项目的理解", [
        ("1.1 项目基本情况",
         ["项目来源、采购人与项目编号", "各地块位置、规模与建设内容",
          "服务范围与成果形式"], 2000),
        ("1.2 采购需求与工作内容理解",
         ["招标文件第三章项目技术要求逐项解读", "服务边界、配合要求与成果深度",
          "从采购人视角理解核心诉求"], 1800),
        ("1.3 丁蜀镇区位条件与自然地理概况",
         ["地理位置、地形地貌、气候水文", "交通区位与基础设施条件"], 1800),
        ("1.4 宜兴市社会经济与产业发展格局",
         ["经济总量、产业结构与用地需求", "产业空间布局与转型方向"], 1600),
        ("1.5 支撑重大产业项目落地实施",
         ["项目对地方产业培育的意义", "用地保障对项目落地的作用"], 1400),
        ("1.6 促进低空经济产业集聚发展",
         ["航空产业园定位与分期开发", "先导区报批的示范意义"], 1400),
        ("1.7 落实最严格的耕地保护制度",
         ["耕地占补平衡与永久基本农田避让", "节约集约用地要求"], 1400),
        ("1.8 政策法规需求分析",
         ["土地管理法及实施条例要求", "江苏省用地报批政策口径"], 1600),
        ("1.9 技术服务需求分析",
         ["勘测定界、组卷报批的技术要求", "成果深度与审查标准"], 1600),
        ("1.10 各地块报批需求差异化分析",
         ["各地块位置、规模与报批类型对比", "重点难点与特殊关注事项", "预计耗时的差异化安排"], 1800),
        ("1.11 用地报批审批流程与审查要点分析",
         ["县、市、省三级审查流程", "各环节审查要点与常见退件原因"], 1800),
        ("1.12 项目关键风险识别与应对",
         ["要件、权属、政策与进度风险", "风险应对的总体思路"], 1600),
        ("1.13 法律法规依据",
         ["土地管理法、土地管理法实施条例", "江苏省土地管理条例等地方性法规"], 1500),
        ("1.14 部门规章与规范性文件",
         ["自然资源部及江苏省自然资源厅文件", "用地报批组卷与电子报件规范"], 1400),
        ("1.15 技术规范与标准",
         ["勘测定界、土地利用现状分类等技术标准", "成果质量检查验收规定"], 1400),
    ]),
    ("第二章 项目组织实施方案", [
        ("2.1 组织架构设计原则与层级模型",
         ["项目负责人负责制的层级架构", "管理层级与作业组设置", "人员配置与到岗要求"], 1600),
        ("2.2 关键岗位设置与职责分工",
         ["项目负责人、技术负责人、质检、外业、内业职责", "岗位协作与接口管理"], 1500),
        ("2.3 项目管理制度体系",
         ["进度、质量、资料、考勤、保密等制度", "制度执行与考核"], 1400),
        ("2.4 总体技术路线设计",
         ["前期准备—外业调查—内业编制—组卷报批—跟踪批复", "各阶段成果与时间安排"], 1600),
        ("2.5 核心技术方法与作业流程",
         ["权属调查、地类认定、勘测定界方法", "面积汇总与图件编制流程", "坐标与数据标准"], 1800),
        ("2.6 设备配置与技术保障",
         ["测量仪器、无人机、成图软件与工作站配置", "设备检定与维护"], 1400),
        ("2.7 各地块实施方案",
         ["按地块制定差异化实施要点", "多地块并行的资源调配"], 1800),
        ("2.8 前期准备—耕地占补与规划空间指标落实",
         ["耕地占补平衡指标落实", "规划空间与农用地转用计划指标"], 1500),
        ("2.9 前期准备—违法用地及信访情况核查",
         ["违法用地核查与处置", "信访矛盾排查与化解"], 1300),
        ("2.10 征前程序—预公告与现状调查",
         ["征收土地预公告发布", "拟征收土地现状调查与确认"], 1500),
        ("2.11 征前程序—社会稳定风险评估与补偿安置方案",
         ["社会稳定风险评估组织", "补偿安置方案拟定与测算"], 1500),
        ("2.12 征前程序—公告、听证与补偿登记",
         ["补偿安置方案公告与听证", "补偿登记与协议签订"], 1400),
        ("2.13 报批材料组卷与电子报件",
         ["一书四方案及附件编制", "电子报件系统录入与校验", "纸质材料装订归档"], 1600),
        ("2.14 审批跟踪与批复获取",
         ["县、市、省三级审查跟踪", "补正响应与批复取得", "成果移交与后续服务"], 1500),
    ]),
    ("第三章 质量保障措施", [
        ("3.1 质量管理方针与目标体系",
         ["公司质量方针与 ISO9001 体系", "本项目质量目标与量化指标"], 1600),
        ("3.2 质量管理组织架构与职责分工",
         ["质量管理岗位设置与人员", "质量责任体系"], 1400),
        ("3.3 质量管理制度与标准体系",
         ["质量检查验收、地类认定、组卷规范等制度", "标准体系与适用范围"], 1400),
        ("3.4 前期准备阶段质量控制",
         ["基础资料完整性与准确性控制", "政策口径统一"], 1400),
        ("3.5 外业调查核查阶段质量控制",
         ["权属调查与指界质量控制", "地类认定与勘界精度控制"], 1500),
        ("3.6 内业编制与成果审核质量控制",
         ["面积汇总、图件、文本质量控制", "三级校审与过程记录"], 1500),
        ("3.7 核心专项工作质量保障措施",
         ["耕地占补、社保审核、林地报批等专项", "专项成果的质量把关"], 1400),
        ("3.8 质量检查验收标准与成果交付",
         ["检查维度与验收标准", "成果交付与移交管理"], 1500),
        ("3.9 质量持续改进与事后服务",
         ["质量问题整改闭环", "质量例会、经验固化与事后服务"], 1300),
    ]),
    ("第四章 进度安排", [
        ("4.1 编制依据与原则",
         ["合同工期约束与审批时限", "统筹安排、并行推进原则"], 1300),
        ("4.2 总体工期安排与里程碑节点",
         ["总体工期与阶段划分", "里程碑节点与交付物"], 1400),
        ("4.3 工作分解结构与并行策略",
         ["工作包分解与工期分配", "多地块并行的资源与逻辑关系"], 1400),
        ("4.4 前期准备与外业调查阶段进度安排",
         ["启动、资料收集、权属调查、勘界的时间安排", "阶段交付成果"], 1400),
        ("4.5 内业编制与组卷报批阶段进度安排",
         ["面积汇总、图件、文本、组卷的时间安排", "阶段交付成果"], 1400),
        ("4.6 审批跟踪与批复获取阶段进度安排",
         ["逐级审查、补正、批复的时间安排", "阶段交付成果"], 1300),
        ("4.7 项目全周期进度横道图",
         ["横道图编制说明", "关键工作的时间分布"], 1200),
        ("4.8 组织保障与资源保障",
         ["人员、设备、经费保障", "投入阶段与数量"], 1400),
        ("4.9 沟通协调与技术保障",
         ["与采购人、主管部门的沟通机制", "技术支撑与专家咨询"], 1300),
        ("4.10 进度风险识别与应对预案",
         ["要件、权属、审查、资源等进度风险", "应对措施与责任人"], 1400),
    ]),
    ("第五章 安全保密保障措施", [
        ("5.1 安全生产管理架构与制度",
         ["安全生产组织机构与责任制", "安全制度与教育培训"], 1400),
        ("5.2 外业作业安全保障措施",
         ["交通、作业、环境风险辨识", "控制措施与责任人"], 1500),
        ("5.3 内业作业与设备安全保障",
         ["用电、消防、设备安全检查", "检查频次与整改"], 1400),
        ("5.4 数据安全管理体系",
         ["数据分类分级", "存储、传输与访问控制"], 1500),
        ("5.5 信息系统安全与数据备份",
         ["备份策略与执行周期", "系统安全防护"], 1400),
        ("5.6 测绘成果与涉密数据管理",
         ["涉密成果领取、使用、传递、归还与销毁", "涉密介质管理"], 1500),
        ("5.7 保密制度体系与人员管理",
         ["保密制度与保密协议", "岗位保密职责与考核"], 1400),
        ("5.8 资料档案保密管理",
         ["档案分级保管与借阅审批", "复制与销毁管理"], 1300),
        ("5.9 应急预案与处置机制",
         ["应急组织与响应等级", "各类事件处置措施与报告"], 1400),
    ]),
]

# 小节号 → 表键（bidding_plan_tables.PLAN_TABLES）。人工稿每章配 3～9 张表。
_SECTION_TABLES: Dict[str, Tuple[str, ...]] = {
    "1.10": ("plots",),
    "2.1": ("org",), "2.2": ("duties",), "2.3": ("rules",), "2.4": ("route",),
    "2.6": ("equip",), "2.14": ("guarantee",),
    "3.1": ("quality_goals",), "3.2": ("quality_org",),
    "3.3": ("quality_rules",), "3.6": ("qc_stage",), "3.8": ("acceptance",),
    "4.2": ("milestones",), "4.3": ("workpackages",),
    "4.4": ("schedule_prep",), "4.5": ("schedule_draft",),
    "4.6": ("schedule_appr",), "4.7": ("gantt",), "4.8": ("resources",),
    "4.10": ("schedule_risk",),
    "5.1": ("safety_duty",), "5.2": ("field_risk",), "5.3": ("safety_check",),
    "5.4": ("data_class",), "5.5": ("backup",), "5.6": ("confidential_flow",),
    "5.7": ("confidential_duty",), "5.8": ("archive",), "5.9": ("emergency",),
}


def _plan_chunks(chapter) -> List[List[Tuple[str, List[str], int]]]:
    """一小节一次调用 —— 单次 1200～2000 字，57 节并行拼出约 8.6 万字。"""
    return [[sub] for sub in chapter[1]]


async def _gen_chunk(llm, system: str, facts: str, chapter_title: str, chunk,
                     prior: str) -> str:
    outline, target = [], 0
    for sub in chunk:
        full, pts, chars = sub[0], sub[1], sub[2]
        no, title = (full.split(" ", 1) + [""])[:2]
        outline.append(f"## {no} {title}".strip())
        outline.append("要点：" + "；".join(pts))
        _ask = int(chars * 1.05)      # 实测模型基本按目标字数产出；57 节合计约 9.5 万字
        outline.append(f"目标字数：不少于 {_ask} 字")
        target += _ask
    user = (
        f"为下面的政府采购项目撰写投标文件「服务内容的详细说明及服务方案」的"
        f"「{chapter_title}」部分。\n\n{facts}\n"
        f"本次只写以下小节（严格按编号和标题，用 ## 输出）：\n"
        + "\n".join(outline)
        + f"\n\n前文已写小节：{prior}。不要重复前文内容，做好承接。\n"
        f"写作要求：合计不少于 {target} 字；每节 4～6 个完整段落，开头一句总述，"
        "再展开做法、标准、责任人和时间；不使用项目符号，步骤用「1）2）3）」；"
        "结合招标文件采购内容写具体做法，不写空话；"
        "不要用 Markdown 表格（系统会在小节末尾统一插入管理表格），"
        "不得出现「待补充」「以实际为准」等占位语。"
    )
    try:
        # 🔴 关闭思考链：deepseek 推理模型默认把预算耗在 reasoning 上
        #    （实测 12000 tokens 只出 500 字正文），关掉后直接写正文、快得多。
        _res = await asyncio.wait_for(
            llm.chat_with_reasoning(
                messages=[{"role": "user", "content": user}], system=system,
                max_tokens=12000, temperature=0.35, disable_thinking=True),
            timeout=240)
        text = str((_res or {}).get("content") or "").strip()
        if text and len(text) > 500 and "待补充" not in text:
            return text
        return ""
    except Exception as e:
        logger.warning(f"[投标文件] 章节「{chapter_title}」一块生成失败: {e}")
        return ""


def _drop_runaway_sections(text: str, expect_no: str) -> str:
    """截掉模型越界续写的小节。

    实测（丁蜀镇线上测试）：让模型只写「4.1 总体进度计划与编制依据」，
    它把整章 4.2～4.5 也一起写了出来 —— 成品正文里同一个章号出现两遍。
    每块只该有一个小节，遇到第一个编号不符的 2～3 级标题就截断。
    截断后剩得太少（模型一上来就写错号）则原样返回，宁可多不可空。
    """
    if not text or not expect_no:
        return text
    keep: List[str] = []
    for line in str(text).splitlines():
        m = re.match(r'^#{2,3}\s*(\d+(?:\.\d+)*)\s', line.strip())
        if m and not (m.group(1) == expect_no
                      or m.group(1).startswith(expect_no + ".")):
            break
        keep.append(line)
    out = "\n".join(keep).strip()
    return out if len(out) >= 300 else text


async def _write_plan(tender: dict, *, llm=None) -> str:
    """按人工稿 5 章 23 节生成服务方案（约 9.4 万字），各块并行调用 LLM。"""
    try:
        from app.services.llm_service import LLMService
        llm = llm or LLMService()
    except Exception:
        return ""
    scope = (tender.get("scope") or "")[:1800]
    facts = (
        f"项目名称：{tender.get('project_name')}\n"
        f"项目编号：{tender.get('project_no')}\n"
        f"采购人：{tender.get('purchaser')}\n"
        f"代理机构：{tender.get('agency')}\n"
        f"预算/最高限价：{tender.get('budget_wan')} 万元\n"
        f"采购内容：{scope}\n"
    )
    system = ("你是招标投标文件编写专家，替供应商（江苏众拓测绘有限公司）写投标响应文件。"
              "文风平实专业，所有内容必须落在本项目事实上，不编造地名、数字和批文号。")

    sem = asyncio.Semaphore(4)      # 控制并发，避免触发接口限流

    async def _guarded(ch_t, ch_chunk, prior_txt):
        async with sem:
            return await _gen_chunk(llm, system, facts, ch_t, ch_chunk, prior_txt)

    jobs = []          # (chapter_title, chunk, task)
    prior = ""
    for chapter_title, subs in _PLAN_CHAPTERS:
        for chunk in _plan_chunks((chapter_title, subs)):
            jobs.append((chapter_title, chunk,
                         asyncio.create_task(
                             _guarded(chapter_title, chunk, prior))))
            prior = "、".join(str(x[0]) for x in chunk)
    results = list(await asyncio.gather(*(j[2] for j in jobs), return_exceptions=True))
    # 并发失败的块**串行重试一次**（限流/超时多为瞬时问题）
    for i, ((ch_t, chunk, _), text) in enumerate(zip(jobs, results)):
        if not (isinstance(text, str) and text):
            results[i] = await _gen_chunk(llm, system, facts, ch_t, chunk, "")

    parts: List[str] = []
    cur_ch = ""
    for (chapter_title, chunk, _), text in zip(jobs, results):
        if chapter_title != cur_ch:
            parts.append(f"# {chapter_title}\n")
            cur_ch = chapter_title
        if isinstance(text, str) and text:
            # 每块只写一个小节；模型越界续写的别的小节要截掉（见函数注释）
            _no = str(chunk[0][0]).split(" ", 1)[0].strip()
            parts.append(_drop_runaway_sections(text, _no) + "\n")
            # 人工稿在大节末尾配管理表格：按小节号插表（渲染见 _md_to_docx）
            for _tk in _SECTION_TABLES.get(_no, ()):
                parts.append(f"[[table:{_tk}]]\n")
        else:
            logger.warning(f"[投标文件] 「{chapter_title}」有一块没有生成内容")
    return "\n".join(parts)


def _fallback_plan(tender: dict) -> str:
    name = tender.get("project_name") or "本项目"
    return (
        f"## 一、项目理解\n\n{name}为用地报批服务项目，工作内容包含征地报批全流程服务、"
        "勘测定界与报批材料组卷、社会稳定风险评估配合等。服务期限按招标文件要求执行。\n\n"
        "## 二、总体工作方案\n\n按“前期准备—资料收集—勘测定界—组卷报批—成果移交”"
        "五个阶段推进，实行项目负责人负责制，关键节点向采购人书面汇报。\n\n"
        "## 三、质量保障措施\n\n执行三级校审（作业组自检、项目组复核、公司质检），"
        "成果资料按档案管理要求整理移交。\n\n"
        "## 四、进度安排\n\n进场后按采购人要求编制实施计划，周报进度，确保在合同工期内完成。\n\n"
        "## 五、安全保密措施\n\n涉密测绘成果按保密规定管理，作业人员签订保密承诺，"
        "数据不外传、不留存于个人设备。\n"
    )


def _md_to_docx(doc, md: str, ctx: Dict[str, Any] = None):
    """简易 markdown → docx（标题/段落/列表/方案表格）。

    `[[table:key]]` 是 `_write_plan` 按小节插的表标记，这里换成
    「表题 + 三线表」（表头行数据见 bidding_plan_tables）。
    """
    _seen_scope = False
    for line in str(md or "").splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        mt = re.match(r'^\s*\[\[table:([\w_]+)\]\]\s*$', s)
        if mt:
            if ctx is not None:
                _render_plan_table(doc, mt.group(1), ctx)
            continue
        if s.lstrip().startswith("|"):      # 模型自画的 Markdown 表格一律丢弃
            continue
        if s.startswith("###"):
            _h(doc, s.lstrip("#").strip(), 3, size=15, font="仿宋")
        elif s.startswith("##"):
            _h(doc, s.lstrip("#").strip(), 2, size=15, font="仿宋")
        elif s.startswith("#"):
            _h(doc, s.lstrip("#").strip(), 1, toc=False, size=18, font="仿宋")
        elif re.match(r'^\s*[-*]\s+', s):
            body = re.sub(r'^\s*[-*]\s+', '', s)
            # 模型常在每个小节重复一遍「采购内容：…」，只保留第一次
            if body.startswith("采购内容："):
                if _seen_scope:
                    continue
                _seen_scope = True
            _plan_p(doc, "• " + body)
        else:
            _plan_p(doc, re.sub(r'\*\*(.+?)\*\*', r'\1', s))


def _render_plan_table(doc, key: str, ctx: Dict[str, Any]) -> None:
    """按人工稿表式渲染一张方案表：居中表题 + Table Grid + 表后留白。"""
    from app.services.bidding_plan_tables import build_table
    spec = build_table(key, ctx)
    if not spec:
        return
    _p(doc, spec["caption"], size=15, bold=False, align="center",
       indent=False, space_after=2, name="仿宋")
    headers = spec["headers"]
    # 人工稿方案表：仿宋、≤6 列 15 磅、宽表 12 磅，表头浅蓝底纹
    _table(doc, headers, spec["rows"], header_fill="D9E1F3")
    # 表后留一点空，避免下一节标题贴着表格
    gap = doc.add_paragraph()
    gap.paragraph_format.space_after = 0


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ("", "拾", "佰", "仟")
_BIG_UNITS = ("", "万", "亿")


def _upper_yuan(amount_yuan: int) -> str:
    """整数元 → 人民币大写（如 370000 → 叁拾柒万元整）。"""
    if amount_yuan <= 0:
        return "零元整"
    groups = []
    n = amount_yuan
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            if parts and not parts[-1].endswith("零"):
                parts.append("零")
            continue
        s = ""
        zero = False
        for i in range(3, -1, -1):
            d = (g // (10 ** i)) % 10
            if d == 0:
                zero = bool(s)
            else:
                if zero:
                    s += "零"
                    zero = False
                s += _DIGITS[d] + _UNITS[i]
        parts.append(s + _BIG_UNITS[gi])
    text = "".join(parts).rstrip("零")
    return text + "元整"


def _price_text(price_wan: float) -> Tuple[str, str]:
    """(小写, 大写)。"""
    return f"{price_wan:.2f}", _upper_yuan(int(round(float(price_wan) * 10000)))


def _quote_items(project: str, total_yuan: int) -> List[Tuple[str, int]]:
    """按项目名里的地块拆「投标明细报价表」细项；拆不出就一行总价。

    人工稿按地块分项（蜀浦路东西两侧 / 湖光中路南侧 / 航空产业园），
    合计与开标一览表一致。金额按百元取整，尾差放最后一项。
    """
    name = str(project or "").strip()
    name = re.sub(r'项目$', '', name)
    name = re.sub(r'(土地征收|用地)?报批(综合)?服务$', '', name).strip()
    parts = [p.strip() for p in re.split(r'[、及]', name) if p.strip()]
    if not parts:
        parts = ["用地报批服务"]
    items = [f"{p}用地报批服务" for p in parts]
    n = len(items)
    if n == 1:
        return [(items[0], total_yuan)]
    each = (total_yuan // n) // 100 * 100
    amounts = [each] * (n - 1) + [total_yuan - each * (n - 1)]
    return list(zip(items, amounts))


def _auth_fields(texts: Dict[str, str], *, company: Optional[Dict[str, Any]] = None,
                 agent_name: str = "") -> Dict[str, str]:
    """授权书/投标函的填写值。

    🔴 业主 2026-11-11：「只有公司的法人才是固定的陈春，其他都不固定」——
       法定代表人一律取公司档案 `companies.leader_name`（不是某份人工稿里的人），
       被授权人由调用方传入（前端询问），不再随机换人。
    """
    import datetime as _dt
    src = texts.get("授权委托书") or ""
    company = company or {}

    def grab(pat: str, default: str = "") -> str:
        m = re.search(pat, src)
        return (m.group(1) if m else default).strip()

    legal = str(company.get("leader_name") or "").strip() or grab(
        r'法定代表人[：:]\s*([^\n；]+)')
    agent = (agent_name or "").strip() or grab(r'受托人：.*?姓名\s*([^\s；，]+)')
    gender = grab(r'性别[：:]\s*([男女])') or "女"
    birth = grab(r'出生日期[：:]\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)')
    age = ""
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', birth)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        t = _dt.date.today()
        age = str(t.year - y - ((t.month, t.day) < (mo, d)))
    _today = _dt.date.today()
    return {
        "legal": legal,
        "agent": agent,
        "gender": gender,
        "age": age,
        "position": grab(r'职务[：:]\s*([^；\n]+)') or "项目经理",
        "id_no": grab(r'身份证(?:号码)?[：:]\s*([0-9Xx]+)'),
        "mobile": grab(r'(?:联系方式|手机号码)[：:]\s*(\d{5,})'),
        "office": str(company.get("office_phone") or "").strip()
        or grab(r'办公电话[：:]\s*([0-9\-]+)'),
        "address": str(company.get("address") or "").strip()
        or grab(r'地址[：:]\s*([^\n]+)'),
        # 授权书落款「日 期」按人工稿用 2026.7.28 这种点分格式
        "date_dot": f"{_today.year}.{_today.month}.{_today.day}",
    }


def _small_enterprise_facts(texts: Dict[str, str]) -> Dict[str, str]:
    """从公司档案《中小企业声明函》里取从业人数/营业收入/资产总额/企业类型。

    🔴 投标文件里的声明函若留「______」，评审会当成未填写（2026-11-11 实测）。
       这些数值公司档案里本来就有，直接复用，不要空着。
    """
    src = texts.get("中小企业声明函") or ""
    if not src:
        return {}

    def grab(pat: str) -> str:
        m = re.search(pat, src)
        return m.group(1).strip() if m else ""

    return {
        "employees": grab(r'从业人员\s*(\d+)\s*人'),
        "revenue": grab(r'营业收入[为约]?\s*([\d.]+)\s*万元'),
        "assets": grab(r'资产总额[为约]?\s*([\d.]+)\s*万元'),
        "type": grab(r'属于\s*([中小微]{1,2}企业)'),
    }


def _deviation_rows(tender: dict, project: str, duration: int) -> List[List[str]]:
    """偏离表：按人工稿逐条拆行（资格条件/拒绝情形/人员/期限/地点/验收）。

    旧实现把整段资格条件压成一行，评标专家无法逐条对照 —— 人工稿是 6 行。
    """
    quals = [q for q in (tender.get("qualifications") or []) if q]
    if quals:
        circled = "①②③④⑤⑥⑦⑧⑨"
        q_text = "投标人参加本次采购活动应具备下列资格条件：\n" + "\n".join(
            f"{circled[i] if i < len(circled) else f'({i + 1})'}{q}"
            for i, q in enumerate(quals))
    else:
        q_text = ("投标人参加本次采购活动应具备下列资格条件：\n"
                  "①具有独立承担民事责任的能力；\n"
                  "②具有良好的商业信誉和健全的财务会计制度；\n"
                  "③有依法缴纳税收和社会保障资金的良好记录；\n"
                  "④具有履行合同所必需的设备和专业技术能力；\n"
                  "⑤不接受联合体，不接受中标后分包；\n"
                  "⑥良好的信用记录。")
    q_resp = q_text.replace("投标人", "我单位", 1)
    if "测绘资质" in q_text:
        q_resp += "\n（我单位持有乙级测绘资质证书，证书在有效期内。）"

    reject_text = ("拒绝下述供应商参加本次采购活动：①投标人负责人为同一人或者存在"
                   "直接控股、管理关系的不同供应商，不得参加同一合同项下的采购活动；"
                   "②凡为采购项目提供整体设计、规范编制或者项目管理、监理、检测等"
                   "服务的供应商，不得再参加本项目的采购活动。")
    reject_resp = ("我单位无以下情况：①与本项目其他投标人负责人不为同一人，且不存在"
                   "直接控股、管理关系；②未为本项目提供整体设计、规范编制或者项目"
                   "管理、监理、检测等服务。")

    n_staff = max(sum((tender.get("staff_min") or {}).values()), 4)
    staff_text = (f"人员配备要求：中标供应商派专人负责与采购方联系协调，成立专门项目组，"
                  f"项目人员不得少于 {n_staff} 人，主要人员保持稳定。")
    staff_resp = (f"我单位派专人负责对接采购方，成立由项目负责人、技术负责人及专业"
                  f"技术人员组成的项目组，投入人员不少于 {n_staff} 人，人员更换事先"
                  f"报采购人同意。")

    def _same(t: str) -> str:
        return t

    return [
        ["1", project, q_text, q_resp, "完全响应"],
        ["2", project, reject_text, reject_resp, "完全响应"],
        ["3", project, staff_text, staff_resp, "完全响应"],
        ["4", project, f"服务期限：签订合同后 {duration} 日内完成。",
         _same(f"服务期限：签订合同后 {duration} 日内完成。"), "完全响应"],
        ["5", project, "服务地点：采购人指定地点。",
         _same("服务地点：采购人指定地点。"), "完全响应"],
        ["6", project, "验收标准：符合国家相关规范标准及招标文件的要求。",
         _same("验收标准：符合国家相关规范标准及招标文件的要求。"), "完全响应"],
    ]


def _personnel_score(people: List[Dict[str, Any]]) -> str:
    """按评分办法算人员项自评分：负责人（高级 2 + 注册测绘师 2，封顶 4）
    + 组员（高级 2 / 中级 1，封顶 6）。"""
    from app.services.bidding_tender import _title_rank
    if not people:
        return ""
    lead = people[0]
    s = 0
    if _title_rank(lead.get("title")) >= 2 and any(
            k in str(lead.get("major") or "") for k in ("土地", "测绘")):
        s += 2
    if any("注册测绘师" in str(c) for c in (lead.get("certs") or [])):
        s += 2
    s = min(s, 4)
    ms = 0
    for p in people[1:]:
        t = str(p.get("title") or "")
        if "高级" in t:
            ms += 2
        elif "中级" in t or "工程师" in t:
            ms += 1
    return str(s + min(ms, 6))


def _cert_count() -> int:
    """公司档案里体系认证证书的种数（质量/环境/职业健康/测量管理）。"""
    keys = ("质量管理体系认证", "环境管理体系认证", "职业健康安全管理体系认证",
            "测量管理体系认证")
    n = 0
    try:
        c = sqlite3.connect(str(KB))
        names = {str(r[0] or "") for r in c.execute(
            "select image_name from asset_images where is_active=1")}
        c.close()
        n = sum(1 for k in keys if any(k in x for x in names))
    except Exception:
        pass
    return max(n, 1)


def _equip_count() -> int:
    """拟投入设备类别数（评分每类 1 分，最高 5 分）。"""
    cats = ("全站仪", "GNSS接收器", "无人机", "CASS成图软件", "绘图仪")
    try:
        c = sqlite3.connect(str(KB))
        names = {str(r[0] or "") for r in c.execute(
            "select image_name from asset_images where is_active=1")}
        c.close()
        return min(sum(1 for k in cats if any(k in x for x in names)), 5)
    except Exception:
        return 5


def _plan_ctx(tender: dict, company: str, people: List[Dict[str, Any]]) -> Dict[str, Any]:
    """服务方案表格用的上下文（项目事实 + 人员 + 工期 + 地块）。"""
    return {
        "project": tender.get("project_name") or "",
        "purchaser": tender.get("purchaser") or "",
        "company": company,
        "people": people,
        "duration_days": int(tender.get("duration_days") or 90),
        "plots": tender.get("plots") or [],
    }


async def build_bid_document(*, tender_path: str | Path, company_id: int,
                             price_wan: float, out_path: str | Path,
                             seed: Optional[int] = None,
                             personnel: Optional[List[dict]] = None,
                             use_llm: bool = True,
                             agent_name: str = "") -> Path:
    from docx import Document
    from docx.shared import Cm, Pt
    from app.services.bidding_tender import parse_tender, select_bid_personnel

    # 三家公司共用档案库：本次投标只取这家公司的营业执照/证书/人员图片
    _ASSET_COMPANY.set(int(company_id))
    tender = parse_tender(tender_path)
    # 最低评标价法的招标文件（如大冈镇佳富村）格式完全不同：走简式八件套
    if tender.get("bid_format") == "compact":
        return await build_compact_bid_document(
            tender_path=tender_path, company_id=company_id,
            price_wan=price_wan, out_path=out_path, seed=seed,
            personnel=personnel, use_llm=use_llm, agent_name=agent_name)
    co_name = company_name(company_id) or "投标人"
    # 人员配置对齐人工稿：1 名负责人 + 不少于 5 名组员（招标最低 1+3 只是底线；
    # 高级职称组员每个 2 分、最多 6 分，配足 5 名组员才能拿满人员分）
    _staff_min = max(
        [int(v) for v in (tender.get("staff_min") or {}).values()
         if str(v).isdigit()] + [3])
    _members_n = max(_staff_min, 5)
    people = personnel or select_bid_personnel(
        company_id=company_id, seed=seed, members=_members_n,
        require_insured=bool(tender.get("personnel_social_insurance")))
    texts = _asset_texts(co_name)
    small, big = _price_text(price_wan)
    _total_yuan = int(round(float(price_wan) * 10000))
    project = tender.get("project_name") or ""
    no = tender.get("project_no") or ""
    purchaser = tender.get("purchaser") or ""
    agency = tender.get("agency") or ""
    from datetime import date as _date

    # 人工稿日期用阿拉伯数字（「日期：2026 年 11 月 6 日」），不用汉字年号
    _d = _date.today()
    today = f"{_d.year}年{_d.month}月{_d.day}日"

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(3.0)
    sec.top_margin = sec.bottom_margin = Cm(3.0)   # 人工稿页边距 3cm

    # ── 封面（照人工案例逐项对版：60pt 仿宋标题、16pt 仿宋信息、3cm 页边距）──
    # 🔴 封面空行用 12 磅单倍行距：14 磅以上会被 _p 套成 1.5 倍行距，
    #    在 Word 下行高更大，会把「日期」顶到第二页（业主 2026-11-10 截图）。
    def _blank():
        _p(doc, "", size=12, indent=False, space_after=0)

    _blank()
    _blank()
    _t = _p(doc, "投 标 文 件", size=60, bold=False, align="center",
            indent=False, name="仿宋", space_after=0)
    _t.paragraph_format.space_before = Pt(40)
    _blank()
    _blank()
    _p(doc, f"项目编号：{no}", size=16, align="center", indent=False,
       name="仿宋", space_after=0)
    _blank()
    _blank()
    _p(doc, f"项目名称：{project}", size=16, indent=False, name="仿宋",
       space_after=0)
    _blank()
    _p(doc, f"投标人名称：{co_name}", size=16, indent=False, name="仿宋",
       space_after=0)
    _p(doc, "（盖公章）", size=16, indent=False, name="仿宋", space_after=0)
    _blank()
    _p(doc, "法定代表人或其被授权代表人：", size=16, indent=False,
       name="仿宋", space_after=0)
    _p(doc, "（签名）", size=16, indent=False, name="仿宋", space_after=0)
    for _ in range(4):
        _blank()
    _date_p = _p(doc, f"日期：{today}", size=16, align="center", indent=False,
                 name="仿宋", space_after=0)
    _date_p.paragraph_format.space_before = Pt(24)   # 固定在封面下半部
    doc.add_page_break()

    # ── 第二页：目录（照人工稿）──
    _add_toc(doc)
    doc.add_page_break()

    # ── （一）投标函 ──
    _h(doc, "（一）投标函", 1)
    _p(doc, "投标函", size=15, align="center", indent=False)   # 人工稿标题下再居中一行「投标函」
    lines = [
        f"致{purchaser}、{agency}：",
        "我方收到你们招标文件，经仔细阅读和研究，我方决定参加投标。",
        f"1. 我方愿意按照招标文件的一切要求提供本项目服务，投标总价详见《开标一览表》，"
        f"投标报价为人民币（大写）{big}（小写：￥{_total_yuan}.00元）。",
        "2. 我方已对照招标要求，填写了投标服务偏离情况，详见《技术条款及相关要求偏离表》。",
        "3. 我方承诺本次采购项目清单中，若涉及强制采购节能产品或信息安全产品的，"
        "将按照招标文件及法律法规要求提供相关产品。",
        "4. 我方同意按招标文件中的规定，本投标文件的有效期为投标截止之日起的60天（含提交当日）。"
        "在特殊情况下，可与贵方协商延长投标文件的有效期。",
        "5. 如果我方的投标文件被接受，我方将履行招标文件中规定的每一项要求，按期、按质、按量完成服务。",
        "6. 我方愿意根据贵方及评标委员会的要求，提供投标文件中所有材料的原件及相关证明性资料。",
        "7. 我方认为采购人有权确定中标人。除因不可抗力或招标文件认可的情形以外，我方绝不放弃中标资格。",
        "8. 我方愿意按《中华人民共和国民法典》和合同条款履行自己的全部责任。",
        "9. 如果我方中标，我方愿意遵守招标文件和中标通知书的规定，在指定时间和地点与采购人签订合同，"
        "并在签订合同前按招标文件的规定交纳履约保证金。",
        "10. 我方决不提供虚假材料谋取中标，决不采取不正当手段诋毁、排挤其他投标人，"
        "决不与采购人、其它投标人或者采购代理机构恶意串通，决不向采购人、采购代理机构工作人员和评委"
        "进行商业贿赂，决不在采购过程中与采购人进行协商谈判，决不拒绝有关部门监督检查，"
        "如有违反，无条件接受相关管理部门的处罚。",
        "11. 我方承诺一旦中标，提供的服务质量不低于招标文件要求，符合国家相关标准，"
        "并接受采购人的检查与验收要求。",
        "12. 我方不是为本项目提供整体设计、规范编制或者项目管理、监理、检测等服务的投标人。",
    ]
    for ln in lines:
        _p(doc, ln, align="justify")

    # 13/14 照人工稿做成表格（业主 2026-11-10：这两项「都没有表格」）
    _af0 = _auth_fields(texts)
    _contact = str(_af0.get("agent") or "").strip()
    _mobile = str(_af0.get("mobile") or "").strip()
    if not (_contact and _mobile):
        _pc = _company_contact(co_name).split()
        _contact = _contact or (_pc[0] if _pc else "")
        _mobile = _mobile or (_pc[1] if len(_pc) > 1 else "")
    _rel = _related_companies(company_id, str(_af0.get("legal") or ""))

    _p(doc, "13. 关联单位情况说明：", align="justify")
    _grid(doc, [
        ["与我方单位负责人为同一人的其他单位", _rel or "无"],
        ["与我方存在直接控股、管理关系的其他单位", _rel or "无"],
    ], widths=[6.2, 8.8])
    _p(doc, "注：没有请填无。", align="justify")

    _p(doc, "14. 与本投标有关的一切正式往来函电，请联系：", align="justify")
    _grid(doc, [["地址", _company_addr(co_name) or _af0.get("address") or "",
                 "邮编", "223005"]],
          widths=[2.2, 8.3, 2.2, 2.3])
    _grid(doc, [
        ["传真", "无", "办公电话", "0517-83663008"],
        ["联系人", _contact, "手机号码", _mobile],
        ["开户名称", co_name, "", ""],
        ["开户银行", "/", "开户账号", "/"],
    ], widths=[2.5, 5.5, 2.5, 4.5])
    # 落款三行必须同页（否则「日期」会被单独挤到下一页）
    _sig1 = _p(doc, f"投标人（盖章）：{co_name}", indent=False)
    _sig2 = _p(doc, "法定代表人或被授权代表人（签名）：", indent=False)
    _sig1.paragraph_format.keep_with_next = True
    _sig2.paragraph_format.keep_with_next = True
    _p(doc, f"日期：{today}", indent=False)

    # ── （二）报价文件（照招标第六章模板逐项对版）──
    # 用「段前分页」而不是空段落分页：空段落在满页时会多出一张白页
    _h(doc, "（二）报价文件", 1).paragraph_format.page_break_before = True
    _h(doc, "1、开标一览表：", 2)
    _p(doc, "开标一览表", size=14, align="center", indent=False, name="仿宋")
    _p_two(doc, f"投标人名称（盖章）：{co_name}", "单位：元", size=14,
           indent=0.0)
    _table(doc, ["标题", "内容（小写）", "内容（大写）"],
           [["报价", f"{_total_yuan} 元", big]],
           widths=[3.43, 5.33, 8.18], font_size=14,
           row_heights=[1.12, 2.89])
    _sig = _p_two(doc, "投标人签名：", f"日期：{today}", size=14, indent=0.75)
    _sig.paragraph_format.space_before = Pt(18)
    _p(doc, "注：", size=10.5, indent=False)
    _p(doc, "①  投标报价应包含完成本服务项目发生的所有含税费用、支付给员工的工资和"
            "国家强制缴纳的各种社会保障资金，以及投标人认为需要的其他费用等；",
       size=10.5, indent=False)
    _p(doc, "②  每一项服务仅接受一个价格，投标文件应对《开标一览表》中的全部服务进行投标，"
            "只投其中部分服务者，投标无效。", size=10.5, indent=False)

    doc.add_page_break()
    _h(doc, "2、投标明细报价表：", 2)
    _p(doc, "2.1 明细报价表", size=14, align="center", indent=False, name="仿宋")
    _p(doc, f"项目名称：{project}", indent=False)
    _p(doc, f"项目编号：{no}", indent=False)
    _p(doc, "单位：元", indent=False, align="center")
    _items = _quote_items(project, _total_yuan)
    _quote_detail_table(doc, _items, _total_yuan, big)
    _sig = _p(doc, f"投 标 人（签章）：{co_name}", indent=False)
    _sig.paragraph_format.left_indent = Cm(4.5)
    _sig.paragraph_format.space_before = Pt(18)
    _sig2 = _p(doc, "投 标 人（签名）：", indent=False)
    _sig2.paragraph_format.left_indent = Cm(4.5)
    _p(doc, "注：", size=10.5, indent=False)
    _p(doc, "（1）投标人应当根据“第三章 项目技术要求和有关说明”的内容详细填写明细报价表，"
            "须包括本次采购的所有细项；“明细报价表”合计应与“报价一览表”报价一致。",
       size=10.5, indent=False)
    doc.add_page_break()

    # ── （三）资格证明文件 ──
    _h(doc, "（三）资格证明文件", 1)
    af = _auth_fields(texts)
    _h(doc, "1、关于资格的声明函：", 2)
    # 人工稿：声明函标题与正文全用仿宋 14
    _p(doc, "关于资格的声明函", size=14, align="center", indent=False, name="仿宋")
    _p(doc, f"{purchaser}：", size=14, indent=False, name="仿宋")
    _p(doc, f"我公司参加贵单位{project}项目采购活动前三年内在经营活动中没有重大违法记录；"
            "同时，投标时不存在有效期限内的采购严重违法失信行为的处罚记录，未被列入失信名单"
            "或重大税收违法案件当事人名单；且具备履行合同所必需的设备和专业技术能力。"
            "我公司（单位）愿针对本次项目进行投标，投标文件中所有关于投标资格的文件、证明、"
            "陈述均是真实的、准确的。若有违背，我公司（单位）同意监管部门做出的相关决定并"
            "承担由此而产生的一切后果。", size=14, align="justify", name="仿宋")
    _p(doc, "投标人签名：", size=14, indent=False, name="仿宋")
    _p(doc, f"投标人公章：{co_name}", size=14, indent=False, name="仿宋")

    # 法定代表人身份证随《关于资格的声明函》一起提交（模板位置）
    _legal_imgs = _asset_images(f"{af['legal']} 身份证", limit=2)
    if _legal_imgs:
        _p(doc, "法定代表人身份证复印件：", bold=False, indent=False)
        _image_row(doc, _legal_imgs)
    doc.add_page_break()

    _h(doc, "2、法定代表人授权委托书：", 2)
    _p(doc, "法定代表人授权书", size=14, bold=False, align="center",
       indent=False, name="仿宋")
    # 人工稿：授权书正文全用仿宋 12
    _af = dict(size=12, indent=False, name="仿宋")
    _p(doc, f"日期：{today}", size=12, indent=False, align="right", name="仿宋")
    _p(doc, f"{purchaser}、{agency}：", **_af)
    _p(doc, f"{co_name}系中华人民共和国合法企业。法定地址：{af['address']}",
       size=12, align="justify", name="仿宋")
    _p(doc, f"特授权{af['agent']}代表我公司（单位）全权办理针对{project}项目的投标、"
            "参与开标、谈判、签约等具体工作，并签署全部有关的文件、协议及合同。",
       size=12, align="justify", name="仿宋")
    _p(doc, "我公司（单位）对被授权代表人的签名负全部责任。",
       size=12, align="justify", name="仿宋")
    _p(doc, "在撤销授权的书面通知送达你处以前，本授权书一直有效，被授权代表签署的所有"
            "文件（在授权书有效期内签署的）不因授权的撤销而失效。",
       size=12, align="justify", name="仿宋")
    _p(doc, "被授权代表人情况：", **_af)
    _p(doc, f"姓名：{af['agent']}        性别：{af['gender']}        "
            f"年龄：{af['age']}        职务：{af['position']}", **_af)
    _p(doc, f"身份证号码：{af['id_no']}        手机号码：{af['mobile']}", **_af)
    _p(doc, f"办公电话：{af['office']}        传真：无", **_af)
    _p(doc, f"通讯地址：{af['address']}", **_af)
    _p(doc, "被授权代表人签名：", **_af)
    _p(doc, f"单位名称（公章）：{co_name}", **_af)
    _p(doc, f"法定代表人签名：{af['legal']}", **_af)

    # 授权书后贴被授权代表身份证正反面（并排，带细边框，无图题）
    _agent_imgs = _asset_images(f"{af['agent']} 身份证", limit=2)
    if _agent_imgs and af["agent"] != af["legal"]:
        _p(doc, "被授权代表身份证复印件：", bold=False, indent=False)
        _image_row(doc, _agent_imgs)
    doc.add_page_break()

    _h(doc, "3、资格性证明材料复印件：", 2)
    _p(doc, "资格性证明材料", size=14, align="center", indent=False, name="仿宋")
    _p(doc, f"日期：{today}", size=12, indent=False, align="right", name="仿宋")
    _p(doc, "投标人应按以下顺序提供相关材料复印件：", size=12, indent=False, name="仿宋")
    for _item in (
        "①　具有独立承担民事责任的能力：投标人为法人或其他组织的提供营业执照或法人"
        "（登记）证书复印件，投标人为自然人的提供自然人身份证复印件；",
        "②　具有良好的商业信誉和健全的财务会计制度：银行出具的资信证明（若投标人为"
        "法人的须提供其基本开户银行出具的资信证明）或投标人近十二个月中任意一个月份的"
        "财务状况报告（资产负债表和利润表）或由会计师事务所出具的近两年中任意一个年度"
        "的审计报告和所附已审财务报告复印件；",
        "③　有依法缴纳税收的良好记录：投标人依法缴纳近十二个月中任意一个月份税收的"
        "相关材料（提供相关主管部门证明或银行代扣证明）复印件；",
        "④　有依法缴纳社会保障资金的良好记录：投标人依法缴纳近十二个月中任意一个月份"
        "社会保障资金的相关材料（提供相关主管部门证明或银行代扣证明）复印件；",
        "⑤　良好的信用记录：未被“信用中国”网站（www.creditchina.gov.cn）列入失信执行人、"
        "重大税收违法案件当事人名单、政府采购严重违法失信名单，或未在“中国政府采购网”"
        "网站（www.ccgp.gov.cn）被列入政府采购严重违法失信行为记录名单；（若投标人企业"
        "信息已录入以上网站的，无需提供相关证明材料；若投标人企业信息未录入以上网站的，"
        "须在投标文件中提供无不良信用记录承诺书）",
        "⑥　采购人特殊要求：投标单位具有乙级及以上测绘资质。",
    ):
        _p(doc, _item, size=12, indent=False, name="仿宋")
    _p(doc, "投标人签名：", size=12, indent=False, name="仿宋")
    _p(doc, f"投标人公章：{co_name}", size=12, indent=False, name="仿宋")
    doc.add_page_break()

    _asset_block(doc, "营业执照", [("1.营业执照",)], caption="营业执照")
    _asset_block(doc, f"{af['legal']} 身份证", [(f"{af['legal']} 身份证",)],
                 caption="法定代表人身份证复印件")
    if af.get("agent") and af["agent"] != af["legal"]:
        _asset_block(doc, f"{af['agent']} 身份证", [(f"{af['agent']} 身份证",)],
                     caption="被授权代表身份证复印件")
    _asset_block(doc, "财务", [("2024年度财务报告",), ("2025年度审计报告",),
                              ("2025年10—12月资产负债表",),
                              ("2025年10—12月利润表",)], caption="财务状况报告")
    _asset_block(doc, "税收", [("依法缴纳税收证明（2025年10月）",)], caption="依法缴纳税收证明")
    _asset_block(doc, "社保", [("依法社会保障资金证明（2025年11月）",)], caption="社会保障资金证明")
    _asset_block(doc, "社会保险权益记录单", [("江苏省社会保险权益记录单",)],
                 caption="社会保险权益记录单")
    _asset_block(doc, "信用", [("未被列入失信被执行人网站截图",),
                              ("未被列入重大税收违法失信主体",),
                              ("未被列入政府采购严重违法失信行为记录名单",)],
                 caption="信用记录查询截图")
    _asset_block(doc, "测绘资质", [("9.供应商具有有效期内的测绘资质乙级及以上资质证书",)],
                 caption="测绘资质证书", break_after=False)

    # ── （四）偏离表（人工稿是横排 A4，表格更宽）──
    _new_section(doc, True)
    _h(doc, "（四）技术条款及相关要求偏离表", 1)
    _p(doc, "技术条款及相关要求偏离表", size=14, align="center", indent=False, name="仿宋")
    _p(doc, f"投标人名称（盖章）：{co_name}", indent=False)
    _dev_rows = _deviation_rows(tender, project,
                                int(tender.get("duration_days") or 90))
    _table(doc, ["序号", "项目名称", "招标内容", "投标内容", "正偏离/负偏离"],
           _dev_rows, widths=[1.6, 5.4, 7.65, 7.65, 2.4], font_size=12,
           aligns=["center", "center", "left", "left", "center"])

    # ── （五）评分导读表（回纵向 A4）──
    _new_section(doc, False)
    _h(doc, "（五）技术商务部分评分导读表", 1)
    _p(doc, "技术商务部分评分导读表", size=14, align="center", indent=False, name="仿宋")
    _p(doc, f"投标人名称（盖章）：{co_name}", indent=False)
    _p(doc, f"项目编号：{no}", indent=False)

    # 🔴 人工稿是 9 项：4 项客观分 + 5 项方案分（共 55 分）。旧实现只列客观分，
    #    评分专家翻不到方案章节的对应页。页码由 _stamp_toc 按 PDF 分页回填。
    _perf = _performance_rows()
    _perf_score = str(min(len(_perf), 3) * 2) if _perf else "0"
    _cert_n = _cert_count()
    _equip_n = _equip_count()
    _lead = people[0] if people else {}
    _msen = "、".join(str(p.get("name") or "") for p in people[1:]
                     if "高级" in str(p.get("title") or "")) or "—"
    _score_rows = [
        ["1", "投标人自2023年1月1日（含）以来承担过类似征地报批项目的，"
              "每提供一个得2分，本项最高得6分，未提供不得分。",
         f"已提供类似业绩 {len(_perf)} 项", "见投标文件", _perf_score],
        ["2", "1、项目负责人具有土地类或测绘类高级及以上职称的得2分，同时具有注册"
              "测绘师的得2分，本项最高得4分。\n2、项目组人员（除项目负责人外）中具有"
              "国土规划类、土地类或测绘类高级及以上职称的每有一个得2分，具有中级及以上"
              "职称的每有一个得1分，本项最多得6分。人员不重复计分，每人按最高职称计分。"
              "【提供人员证书复印件及社保证明并加盖公章，未提供不得分】",
         f"1.项目负责人{_lead.get('name') or ''}具有{_lead.get('title') or '高级职称'}；\n"
         f"2.项目组高级职称人员：{_msen}；\n3.提供项目组成员社保证明材料",
         "见投标文件", _personnel_score(people)],
        ["3", "具有有效期内的质量管理体系、职业健康安全管理体系、环境管理体系证书、"
              "测量管理体系认证证书的，每提供1个得1分，本项最高得4分。",
         f"提供质量管理体系、职业健康安全管理体系、环境管理体系、测量管理体系"
         f"认证证书（共 {_cert_n} 项）", "见投标文件", str(_cert_n)],
        ["4", "投标人拟投入本项目具备全站仪、GNSS接收机、无人机、CASS成图软件、"
              "水位仪、绘图仪、测高仪，每类得1分，同类设备不重复计分，最高5分。",
         "提供全站仪、GNSS接收机、无人机、CASS成图软件、绘图仪等设备购买发票和照片",
         "见投标文件", str(_equip_n)],
        ["5", "对项目的理解", "提供方案", "见投标文件", "10"],
        ["6", "项目组织实施方案", "提供实施方案", "见投标文件", "15"],
        ["7", "质量保障措施", "提供质量保障措施", "见投标文件", "10"],
        ["8", "进度安排", "提供进度安排", "见投标文件", "10"],
        ["9", "安全保密保障措施", "提供安全保密保障措施", "见投标文件", "10"],
    ]
    _table(doc, ["序号", "评审项目", "响应情况", "投标文件对应页码", "自评分"],
           _score_rows, widths=[1.1, 5.6, 4.0, 2.1, 2.2], font_size=12,
           aligns=["center", "left", "left", "center", "center"])
    doc.add_page_break()

    # ── （六）服务方案 ──
    _h(doc, "（六）服务内容的详细说明及服务方案", 1)
    md = ""
    if use_llm:
        try:
            md = await _write_plan(tender)
        except Exception as e:
            logger.warning(f"[投标文件] 方案生成失败，用内置框架: {e}")
            md = ""
    _md_to_docx(doc, md or _fallback_plan(tender),
                _plan_ctx(tender, co_name, people))

    # ── （七）人员配置清单（人工稿横排 A4）──
    _new_section(doc, True)
    _h(doc, "（七）项目人员配置清单", 1)
    _p(doc, "项目人员配置清单", size=14, align="center", indent=False, name="仿宋")
    _p(doc, f"投标人名称（盖章）：{co_name}", indent=False)
    _p(doc, f"项目编号：{no}", indent=False)
    rows = []
    for i, p in enumerate(people, 1):
        # 证书编号只认人员库的 cert_no；没有就印「—」，**不拿证书名称顶编号**。
        cert_no = str(p.get("cert_no") or "").strip() or "—"
        rows.append([str(i), p.get("name") or "", p.get("gender") or "",
                     str(p.get("age") or ""), p.get("role") or "",
                     p.get("education") or "", p.get("title") or "", cert_no])
    _table(doc, ["序号", "姓名", "性别", "年龄", "岗位名称", "学历", "职称", "资格证书编号"],
           rows, widths=[1.5, 3.0, 2.0, 2.0, 3.5, 2.2, 2.8, 7.7],
           font_size=12)
    _p(doc, "注：上述人员在项目执行过程中如需更换，须经采购人同意；"
            "擅自更换或不到位属违约行为。", size=10.5, indent=False)
    _p(doc, "拟投入人员简历：", bold=False, indent=False)
    for p in people:
        certs = "、".join(p.get("certs") or []) or "—"
        # 人员库缺年龄/学历时不要印成「，岁，学历」——缺项直接跳过
        _bits = [str(p.get("name") or ""), str(p.get("gender") or "")]
        if p.get("age"):
            _bits.append(f"{p['age']}岁")
        if p.get("education"):
            _bits.append(f"{p['education']}学历")
        if p.get("major"):
            _bits.append(f"{p['major']}专业")
        if p.get("title"):
            _bits.append(str(p["title"]))
        _bits.append(f"拟任{p.get('role') or ''}")
        _p(doc, "，".join(x for x in _bits if x) + f"；持有证书：{certs}。",
           align="justify")

    # ── （八）其他（回纵向 A4）──
    _new_section(doc, False)
    _h(doc, "（八）其他", 1)
    _h(doc, "1. 体系认证证书", 2)
    _asset_block(doc, "", [("质量管理体系认证",), ("环境管理体系认证",),
                           ("职业健康安全管理体系认证",),
                           ("测量管理体系认证证书",)], caption="体系认证证书")

    _h(doc, "2. 仪器设备", 2)
    _p(doc, "我方拟投入本项目的主要设备包括：全站仪、GNSS 接收机（RTK）、无人机、"
            "绘图仪、测高仪、水位仪、CASS 成图软件及图形工作站等，满足本项目勘测定界、"
            "外业调查与成果编制需要；自有设备附发票/采购合同及照片，软件附合法授权材料。",
       align="justify")
    _asset_block(doc, "", [("仪器设备-全站仪",), ("仪器设备-无人机",),
                           ("仪器设备-CASS",), ("仪器设备-绘图仪",),
                           ("仪器设备-GNSS",)], caption="仪器设备发票及照片")

    _h(doc, "3. 类似项目业绩", 2)
    _p(doc, "近年承担的类似征地报批项目如下（合同/批复复印件附后）：", align="justify")
    perf = _performance_rows()
    if perf:
        _table(doc, ["序号", "项目名称", "类型", "合同签订时间"],
               [[str(i), a, b, c] for i, (a, b, c) in enumerate(perf, 1)],
               widths=[1.5, 9.0, 3.5, 3.0])
    _asset_block(doc, "", [("1.2024年食品科技产业园",), ("2.井神绿化通道",),
                           ("3.张兴矿区",), ("4.世坤迪地块",),
                           ("5.大王庄路网",), ("6.淮阴区烈士",)], caption="业绩证明")

    _h(doc, "4. 拟投入人员证书及社保证明", 2)
    for p in people:
        pid = p.get("id")
        if pid:
            imgs = _person_images(int(pid))
            if imgs:
                _p(doc, f"{p.get('name')}（{p.get('role')}）", bold=False, indent=False)
                for nm, data in imgs[:6]:
                    _img(doc, data, 14.0, nm)

    _h(doc, "5. 声明与承诺", 2)
    for key in ("具备履行合同所必需设备和专业技术能力声明", "无重大违法记录声明",
                "中小企业声明函", "供应商独立性承诺书", "信用记录承诺书"):
        if texts.get(key):
            _p(doc, key, bold=False, indent=False)
            for para in texts[key].splitlines():
                if para.strip():
                    # 素材里的旧落款日期换成本次投标日期（「出生日期」不动）
                    para = re.sub(
                        r'(?<!出生)日期：\s*\d{4}\s*[年.\-]\s*\d{1,2}\s*[月.\-]\s*\d{1,2}\s*日',
                        f"日期：{today}", para.strip())
                    _p(doc, para, align="justify")

    # 🔴 招标文件明确「不接受联合体」时，补一份非联合体声明（人工稿有这份）
    if tender.get("no_consortium"):
        _p(doc, "非联合体投标声明", bold=False, indent=False)
        _p(doc, f"我单位郑重声明：本项目由{co_name}独立投标，未与其他单位组成联合体，"
                "未以任何形式借用或挂靠其他单位资质；中标后由我单位独立履行合同义务，"
                "不进行转包和违法分包。如有不实，愿承担相应法律责任。", align="justify")

    _h(doc, "6. 单位名称变更材料", 2)
    _p(doc, "淮安纵横测绘有限公司于 2022 年 12 月 1 日名称变更为江苏众拓测绘有限公司，"
            "原公司签订的项目合同、资质证书及相关业绩均由变更后的公司承继。"
            "现将名称变更材料附后。", align="justify")
    _asset_block(doc, "单位名称变更", [("单位名称变更材料",)],
                 caption="单位名称变更材料")

    # 身份证复印件已按招标模板贴在「（三）2. 法定代表人授权委托书」之后

    # 正文 12pt 统一 1.5 倍行距（对齐人工稿）；表格上下左右居中；页脚页码
    _apply_body_format(doc)
    _center_all_cells(doc)
    _page_number_footer(doc)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    try:
        if _stamp_toc(out):
            logger.info("[投标文件] 目录已写入真实页码")
    except Exception as e:
        logger.warning(f"[投标文件] 目录页码写入失败，保留目录域: {e}")
    logger.info(f"[投标文件] 已生成 {out}（{len(people)} 人，报价 {small} 万元）")
    return out


async def build_compact_bid_document(*, tender_path: str | Path, company_id: int,
                                     price_wan: float, out_path: str | Path,
                                     seed: Optional[int] = None,
                                     personnel: Optional[List[dict]] = None,
                                     use_llm: bool = False,
                                     agent_name: str = "") -> Path:
    """最低评标价法项目的投标文件（简式八件套）。

    格式照招标文件第六章：封面 → 目录 → 一 资格性和符合性检查响应对照表 →
    二 资格信誉等证明文件 → 三 投标函 → 四 开标一览表 → 五 报价清单 →
    六 商务条款响应及偏离表 → 七 项目组成员一览表 → 八 信用承诺书 → 附件
    中小企业声明函。不出服务方案（最低价法不评技术分）。
    """
    from docx import Document
    from docx.shared import Cm, Pt
    from datetime import date as _date
    from app.services.bidding_tender import parse_tender, select_bid_personnel
    from app.services.bidding_style import load_bidding_style
    from app.services.company_service import load_company
    from app.config import settings as _settings

    # 版式取公司体例（storage/bidding_style.json 可整份替换）；不写死在代码里
    style = load_bidding_style(_settings.STORAGE_DIR)
    company = await load_company(company_id) or {}
    _ASSET_COMPANY.set(int(company_id))

    tender = parse_tender(tender_path)
    co_name = company_name(company_id) or "投标人"
    # 招标文件是否要求拟派人员提供社保（决定选人范围和是否附参保证明）
    person_si = bool(tender.get("personnel_social_insurance"))
    people = personnel or select_bid_personnel(
        company_id=company_id, seed=seed,
        members=max(int((tender.get("staff_min") or {}).get("项目组成员") or 3), 3),
        require_insured=person_si)
    texts = _asset_texts(co_name)
    af = _auth_fields(texts, company=company,
                      agent_name=agent_name or str(
                          (people[0] if people else {}).get("name") or ""))
    # 中小企业声明数据：优先公司档案字段（companies），其次档案文本
    _se = _small_enterprise_facts(texts)
    _se.update({k: v for k, v in {
        "employees": company.get("employee_count"),
        "revenue": company.get("annual_revenue_wan"),
        "assets": company.get("total_assets_wan"),
        "type": company.get("enterprise_type"),
    }.items() if str(v or "").strip()})
    small, big = _price_text(price_wan)
    total_yuan = int(round(float(price_wan) * 10000))
    project = tender.get("project_name") or ""
    no = tender.get("project_no") or ""
    purchaser = tender.get("purchaser") or ""
    agency = tender.get("agency") or ""
    duration = int(tender.get("duration_days") or 60)
    households = str(tender.get("households") or "")
    max_yuan = tender.get("max_price_yuan") or ""
    _d = _date.today()
    today = f"{_d.year}年{_d.month}月{_d.day}日"
    lead = str((people[0] if people else {}).get("name") or "")
    numerals = "一二三四五六七八九十"
    fmt = tender.get("format_titles") or [
        "资格性和符合性检查响应对照表", "资格信誉等证明文件", "投标函",
        "开标一览表", "报价清单", "商务条款响应及偏离表",
        "项目组成员一览表", "信用承诺书"]
    titles = [f"{numerals[i]}、{t}" for i, t in enumerate(fmt[:10])]
    t_check, t_qual, t_letter, t_open, t_quote, t_dev, t_team, t_credit = (
        titles + [""] * 8)[:8]

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    pg = style["page"]
    sec.left_margin, sec.right_margin = Cm(pg["margin_left_cm"]), Cm(pg["margin_right_cm"])
    sec.top_margin, sec.bottom_margin = Cm(pg["margin_top_cm"]), Cm(pg["margin_bottom_cm"])

    def _blank():
        _p(doc, "", size=12, indent=False, space_after=0)

    # ── 封面（按公司体例：字号/字体/是否印「正本」「盖公章」都由 bidding_style 决定）──
    cv = style["cover"]
    for _ in range(3):
        _blank()
    _t = _p(doc, cv.get("title") or "投 标 文 件", size=cv["title_size"],
            bold=cv["title_bold"], align="center", indent=False,
            name=cv["title_font"], space_after=0)
    _t.paragraph_format.space_before = Pt(40)
    if cv.get("show_zhengben"):
        _p(doc, "（正本）", size=cv["line_size"], align="center", indent=False,
           name=cv["line_font"], space_after=0)
    for _ in range(3):
        _blank()
    _date_txt = _d.strftime(cv.get("date_format") or "%Y.%m.%d")
    _p(doc, f"项目名称：{project}", size=cv["line_size"], bold=cv["line_bold"],
       indent=False, name=cv["line_font"], space_after=0)
    for _ in range(3):
        _blank()
    _p(doc, f"投标人名称：{co_name}", size=cv["line_size"], bold=cv["line_bold"],
       indent=False, name=cv["line_font"], space_after=0)
    if cv.get("show_seal_note"):
        _p(doc, "（盖公章）", size=cv["line_size"], indent=False,
           name=cv["line_font"], space_after=0)
    for _ in range(5):
        _blank()
    _dp = _p(doc, f"日  期：{_date_txt}", size=cv["line_size"], bold=cv["line_bold"],
             align="left", indent=False, name=cv["line_font"], space_after=0)
    _dp.paragraph_format.space_before = Pt(24)
    doc.add_page_break()

    # ── 目录 ──
    _add_toc(doc, style)
    doc.add_page_break()

    # ── 一、资格性和符合性检查响应对照表（纵向，人工稿同此）──
    _new_section(doc, False)
    _h(doc, t_check, 1)
    _p(doc, f"投标人全称：{co_name}                    （加盖公章）",
       size=12, indent=False, name="仿宋")
    # 资格证明文件清单：从**招标文件正文**解析（文件1..N）；解析不到才用通用八件套。
    _file_items = [(str(f.get("no")), str(f.get("text") or "").strip())
                   for f in (tender.get("qual_files") or [])
                   if str(f.get("text") or "").strip()]
    if len(_file_items) < 3:
        _file_items = [
            ("1", "法人或者其他组织的营业执照等证明文件，自然人的身份证明"),
            ("2", "上一年度的财务报表"),
            ("3", "依法缴纳税收和社会保障资金的相关材料"),
            ("4", "具备履行合同所必需的设备和专业技术能力的书面声明"),
            ("5", "参加政府采购活动前3年内在经营活动中没有重大违法记录的书面声明"),
            ("6", "法人授权书"),
            ("7", "投标人资质证书"),
            ("8", "招标文件中规定要求提供的证明材料和投标人认为需要提供的其他材料"),
        ]
    # 资格性和符合性检查表：**逐件对应文件1~N**（人工稿即 N 行 + 其他实质性要求），
    # 页码用 {{文件N …}} 占位，装配后按真实分页填（原来所有行都指章节首页，全部印成 3/37）。
    _rows = []
    for _i, (_no, _txt) in enumerate(_file_items, 1):
        _rows.append([str(_i), _txt, "是", "{{" + _no + "." + _txt + "}}"])
    _rows.append([str(len(_rows) + 1), "招标文件中的其他实质性要求", "是", "全文"])
    _ck = style.get("checklist") or {}
    _table(doc, ["序号", "资格性和符合性检查响应内容",
                 "是否响应\n（填是或者否）", "投标文件中的\n页码位置"],
           _rows, widths=_ck.get("widths") or [1.14, 9.3, 3.27, 3.15],
           font_size=style["table"]["size"],
           row_heights=[_ck.get("header_row_cm") or 1.55]
           + [_ck.get("row_cm") or 1.75] * len(_rows),
           aligns=["center", "left", "center", "center"])

    # ── 二、资格信誉等证明文件 ──
    _new_section(doc, False)
    _h(doc, t_qual, 1)
    _p(doc, "实质性资格证明文件目录", size=12, bold=True, align="center",
       indent=False, name="仿宋")
    for _no, _txt in _file_items:
        _p(doc, f"文件{_no}  {_txt}；", size=12, indent=False, name="仿宋")

    # 文件1~N 按**招标目录的编号与名称**逐件附材料；材料类型按文件名的关键词判断，
    # 不按固定编号写死（不同招标的件数/顺序会变）。
    _imgw = style["images"]

    def _decl_equipment():
        # 文件4 的标题由「文件N」二级标题负责，这里不再重复印一遍标题
        _p(doc, "我公司郑重声明：我公司具备履行本项采购合同所必需的设备和专业技术能力，"
                "为履行本项采购合同我公司具备如下主要设备和主要专业技术能力：",
           size=12, align="justify", name="仿宋")
        _p(doc, "主要设备有：全站仪、GNSS 接收机（RTK）、无人机、绘图仪、测高仪、"
                "水位仪、CASS 成图软件及图形工作站等。", size=12, align="justify",
           name="仿宋")
        _p(doc, "主要专业技术能力有：工程测量、不动产测绘（地籍测绘、房产测绘）、"
                "权籍调查与数据库建设、测绘成果质量检查与验收。", size=12,
           align="justify", name="仿宋")
        # 落款下移（人工稿落款在下半页），但留白均匀，不是把正文挤在一起
        for _ in range(8):
            _p(doc, "", size=12, indent=False, name="仿宋")
        _p(doc, f"投标人名称（公章）：{co_name}", size=12, indent=False, name="仿宋")
        _p(doc, f"日期：{today}", size=12, indent=False, name="仿宋")

    def _decl_no_violation():
        _p(doc, "声明", size=12, bold=True, align="center", indent=False, name="仿宋")
        _p(doc, "我公司郑重声明：参加本次政府采购活动前3年内，我公司在经营活动中"
                "没有因违法经营受到刑事处罚或者责令停产停业、吊销许可证或者执照、"
                "较大数额罚款等行政处罚。", size=12, align="justify", name="仿宋")
        for _ in range(8):
            _p(doc, "", size=12, indent=False, name="仿宋")
        _p(doc, f"投标人名称（公章）：{co_name}", size=12, indent=False, name="仿宋")
        _p(doc, f"日期：{today}", size=12, indent=False, name="仿宋")

    def _auth_letter():
        # 按招标文件附表逐行填写：**填写值带下划线**，签名处留下划线空位
        # （业主 2026-11-11：「要保留出下划线的内容」）。
        _p_mix(doc, [
            ("本授权书声明：", False), (co_name, False), ("（投标人名称）授权", False),
            (af["agent"], False), ("（被授权人姓名）为我方就", False),
            (f" {project} ", True), ("项目采购活动的合法代理人，以本公司名义全权"
                                     "处理一切与该项目采购有关的事务。", False),
        ], size=12)
        _p(doc, f"本授权书于{today}起生效，特此声明。", size=12, align="justify",
           name="仿宋")
        _p(doc, "", size=12, indent=False, name="仿宋")
        _p(doc, "代理人（被授权人）：___________________________", size=12,
           indent=False, name="仿宋")
        _p(doc, f"单位名称：{co_name}", size=12, indent=False, name="仿宋")
        _p(doc, "", size=12, indent=False, name="仿宋")
        _p_mix(doc, [("授权单位盖章：", False), (co_name, True)], size=12,
               indent=False, align="left")
        _p(doc, f"单位名称：{co_name}", size=12, indent=False, name="仿宋")
        _p(doc, f"地    址：{af['address']}", size=12, indent=False, name="仿宋")
        _p(doc, f"日    期：{af['date_dot']}", size=12, indent=False, name="仿宋")

        def _id_pair(name: str):
            _imgs = _asset_images(f"{name} 身份证", limit=4)
            if not _imgs:
                return False
            # 正反面**并排**：用无边框两列表格，比「两张图塞一个段落」稳
            # （段落里插图到 14.6cm 时 LibreOffice/WPS 会折行成上下两张）
            from docx.enum.table import WD_TABLE_ALIGNMENT
            from docx.shared import Cm as _Cm
            _t = doc.add_table(rows=1, cols=2)
            _t.alignment = WD_TABLE_ALIGNMENT.CENTER
            _t.autofit = False
            for _i, (_nm, _data) in enumerate(_imgs[:2]):
                _cell = _t.cell(0, _i)
                _cell.width = _Cm(_imgw["id_card_cm"])
                _cp = _cell.paragraphs[0]
                _cp.alignment = 1
                _cp.paragraph_format.space_after = 0
                try:
                    _cp.add_run().add_picture(io.BytesIO(_data),
                                              width=_Cm(_imgw["id_card_cm"]))
                except Exception as _e:
                    logger.warning(f"[投标文件] 身份证图插入失败: {_e}")
            return True

        # 身份证紧跟授权书（人工稿：同一页印完；不另起一整页留白）
        _p(doc, "", size=12, indent=False, name="仿宋")
        if _asset_images(f"{af['legal']} 身份证", limit=1):
            _p(doc, "法定代表人身份证：", size=12, indent=False, name="仿宋")
            _id_pair(af["legal"])
        if (af.get("agent") and af["agent"] != af["legal"]
                and _asset_images(f"{af['agent']} 身份证", limit=1)):
            _p(doc, "授权委托人身份证：", size=12, indent=False, name="仿宋")
            _id_pair(af["agent"])

    def _credit_block():
        _credit = (texts.get("信用记录承诺书") or "").strip()
        _indep = (texts.get("供应商独立性承诺书") or "").strip()
        if not (_credit or _indep):
            return
        _p(doc, "（1）承诺书", size=12, bold=True, indent=False, name="仿宋")
        _p(doc, f"{purchaser}：", size=12, indent=False, name="仿宋")
        _p_mix(doc, [("我方在 ", False), (project, True),
                     (" 的响应过程中，做如下承诺：", False)], size=12)
        _body = "\n".join(x for x in (_credit, _indep) if x)
        _skip_pref = ("承诺书", "特此承诺", "磋商供应商", "供应商：", "供应商:",
                      "法定代表人", "单位名称", "日期")
        for _ln in [x.strip() for x in _body.splitlines()
                    if x.strip() and not x.strip().startswith(_skip_pref)]:
            _p(doc, _ln, size=12, align="justify", name="仿宋")
        _p(doc, f"供应商：{co_name}（盖单位章）", size=12, indent=False,
           name="仿宋")
        _p(doc, f"日期：{today}", size=12, indent=False, name="仿宋")

    def _file_assets(text: str):
        t = str(text)
        if any(k in t for k in ("营业执照", "身份证明")):
            # 人工稿：文件1 只放营业执照（横放扫描件，旋转 90° 占满整页）
            _asset_block(doc, "营业执照", [("1.营业执照",)], caption="营业执照",
                         width_cm=_imgw["license_cm"],
                         rotate90=bool(_imgw.get("rotate_license", True)),
                         break_after=False)
            return
        if "财务" in t:
            # 「上一年度财务报表」= 上一年度审计报告 + 其后的资产负债表/利润表/
            # 现金流量表（公司档案里整套扫描件按「YYYY年度审计报告 第N页」命名），
            # 再附更早一年的审计意见。**年份从档案里自动取**，不写死 2025/2024。
            _years = sorted({int(_m.group(1)) for _nm in _asset_names()
                             if (_m := re.search(r'(20\d{2})\s*年度', _nm))})
            _cur_y = _date.today().year - 1
            _last = max([y for y in _years if y <= _cur_y], default=None) \
                or (max(_years) if _years else None)
            if _last:
                _pages = [(f"{_last}年度审计报告 第{i}页",) for i in range(1, 41)]
                _asset_block(doc, f"{_last}年度审计报告", _pages,
                             caption=f"{_last}年度审计报告及财务报表",
                             width_cm=_imgw["financial_cm"], break_after=False)
                _prev = max([y for y in _years if y < _last], default=None)
                if _prev:
                    _asset_block(doc, f"{_prev}年度",
                                 [(f"{_prev}年度审计报告",),
                                  (f"{_prev}年度财务报告",)],
                                 caption=f"{_prev}年度审计报告",
                                 width_cm=_imgw["financial_cm"],
                                 break_after=False, break_before_first=True)
            return
        if "税收" in t or "社会保障" in t or "社保" in t:
            # 人工稿顺序：先社保证明，再税收证明。社保优先用**每月导入的最新材料**
            # （单位权益记录单=人员参保信息），没有再退回公司档案里的证明。
            _si_imgs = _social_insurance_images(company_id)
            if _si_imgs:
                _cp = _p(doc, "依法缴纳社会保障资金证明", bold=False,
                         indent=False)
                for _i2, (_nm2, _data2) in enumerate(_si_imgs):
                    _img(doc, _data2, _imgw["screenshot_cm"],
                         page_break_before=(_i2 > 0))
            else:
                _asset_block(doc, "社保", [("依法社会保障资金证明",)],
                             caption="依法缴纳社会保障资金证明",
                             width_cm=_imgw["screenshot_cm"], break_after=False)
            _asset_block(doc, "税收", [("依法缴纳税收证明",)],
                         caption="依法缴纳税收证明",
                         width_cm=_imgw["screenshot_cm"], break_after=False,
                         break_before_first=True)
            return
        if "设备" in t and "专业技术能力" in t:
            _decl_equipment()
            return
        if "重大违法" in t:
            _decl_no_violation()
            return
        if "授权书" in t:
            _auth_letter()
            return
        if "资质证书" in t:
            _asset_block(doc, "测绘资质",
                         [("9.供应商具有有效期内的测绘资质乙级及以上资质证书",)],
                         caption="测绘资质证书", width_cm=_imgw["cert_cm"],
                         break_after=False)
            return
        if any(k in t for k in ("其他材料", "其他证明材料", "认为需要提供")):
            _credit_block()
            _asset_block(doc, "信用", [("未被列入失信被执行人网站截图",),
                                      ("未被列入重大税收违法失信主体",),
                                      ("未被列入政府采购严重违法失信行为记录名单",)],
                         caption="信用记录查询截图",
                         width_cm=_imgw["screenshot_cm"], break_after=False,
                         break_before_first=True)
            _lead_major = str((people[0] if people else {}).get("major") or "")
            _cert_kw = "高级工程师——测绘" if "测绘" in _lead_major else "高级工程师——土地"
            _asset_block(doc, _cert_kw, [(_cert_kw,)], caption="项目负责人职称证书",
                         width_cm=_imgw["cert_cm"], break_after=False,
                         break_before_first=True)
            _asset_block(doc, "注册测绘师", [("注册测绘师",)],
                         caption="注册测绘师证书", width_cm=_imgw["cert_cm"],
                         break_after=False, break_before_first=True)
            return

    for _no, _txt in _file_items:
        # 每个「文件N」另起一页（业主：图片必须紧跟在名称之后，不能标题在一页、
        # 图片在另一页），标题用二级标题体例（宋体 15 加粗居中）。
        _hp = _h(doc, f"{_no}.{_txt}", 2)
        _hp.paragraph_format.page_break_before = True
        _file_assets(_txt)

    # ── 三、投标函 ──
    _hp = _h(doc, t_letter, 1)
    _hp.paragraph_format.page_break_before = True
    _letter_from = len(doc.paragraphs)
    _p(doc, f"致：{purchaser}", indent=False)
    _p_mix(doc, [
        ("根据贵方的 ", False), (project, True), (" (项目名称)招标文件，"
                                                  "正式授权下述签字人", False),
        (af["agent"], True), ("(姓名)代表我方", False), (co_name, True),
        ("（投标人名称），全权处理本次项目投标的有关事宜。", False),
    ])
    _p(doc, "据此函，签字人兹宣布同意如下：", align="justify")
    for _ln in (
        "1. 按招标文件规定的各项要求，向买方提供所需货物与服务。",
        "2. 我们完全理解贵方不一定将合同授予最低报价的投标人。",
        "3. 我们已详细审核全部招标文件及其有效补充文件，我们知道必须放弃提出"
        "含糊不清或误解问题的权利。",
        "4. 我们同意从规定的开标日期起遵循本投标文件，并在规定的投标有效期期满"
        "之前均具有约束力。",
        "5. 如果在开标后规定的投标有效期内撤回投标或中标后拒绝签订合同，则按照"
        "国家相关法律法规受到相应处罚。",
        "6. 同意向贵方提供贵方可能另外要求的与投标有关的任何证据或资料，并保证"
        "我方已提供和将要提供的文件是真实的、准确的。",
        "7. 一旦我方中标，我方将根据招标文件的规定，严格履行合同的责任和义务，"
        "并保证在招标文件规定的时间完成项目，交付买方验收、使用。",
    ):
        _p(doc, _ln, align="justify")
    _p(doc, "8. 与本投标有关的正式通讯地址为：", align="justify")
    _p(doc, f"地址：{af['address']}", indent=False)
    _p(doc, f"邮编：{company.get('postcode') or '223005'}", indent=False)
    _p(doc, f"电话：{company.get('office_phone') or af['mobile']}", indent=False)
    _p(doc, "传真：无", indent=False)
    _p(doc, f"投标人开户行：{company.get('bank_name') or '/'}", indent=False)
    _p(doc, f"账    户：{company.get('bank_account') or '/'}", indent=False)
    _p(doc, f"投标人授权代表姓名（签字）：{af['agent']}", indent=False)
    _p(doc, f"投标人名称（公章）：{co_name}", indent=False)
    _p(doc, f"日期：{today}", indent=False)
    # 投标函压到 1.3 倍行距：整封信一页放得下（签名/日期不单独掉到第二页）
    for _lp in doc.paragraphs[_letter_from:]:
        _lp.paragraph_format.line_spacing = 1.3

    # ── 四、开标一览表 ──
    _hp = _h(doc, t_open, 1)
    _hp.paragraph_format.page_break_before = True
    _table(doc, ["项目", "内容"], [
        ["项目名称", project],
        ["项目投标报价", f"人民币（大写）{big}（￥{total_yuan}.00 元）"],
        ["服务期限", f"{duration} 日历天"],
        ["项目负责人", lead],
        ["是否为小微企业", "是"],
    ], widths=[4.2, 10.8], font_size=12,
        aligns=["center", "left"])
    _p(doc, "注：开标一览表不得填报选择性报价，否则将作为无效投标；"
            "开标一览表中报价与投标报价清单中不符时，以开标一览表为准。",
       size=10.5, indent=False)
    _p_two(doc, f"投标人（盖章）：{co_name}", f"日期：{today}", size=12,
           indent=0.0)

    # ── 五、报价清单（横排）──
    _new_section(doc, True)
    _h(doc, t_quote, 1)
    _p(doc, f"{project}项目报价表", size=14, align="center", indent=False,
       name="仿宋")
    _qty = households or "1"
    try:
        _unit = round(total_yuan / float(_qty), 2)
    except Exception:
        _unit = float(total_yuan)
    _unit_txt = f"{_unit:.2f}".rstrip("0").rstrip(".")
    _note = ("该费用包含：人工费、仪器设备使用费、运输进退场费、材料费、内外业"
             "作业费、资料整理编制费、成果打印装订及交付费、现场协调配合费、"
             "安全文明施工费、企业管理费、规费、保险费、合理利润、招标代理费、"
             "增值税及附加税费、市场价格波动风险费、常规返工修改费、报审配合"
             "服务费、零星杂费等一切费用。合价以投标总价为准，综合单价按总价"
             "折算。")
    _scope = (tender.get("scope") or "").strip()
    if not _scope:
        _scope = (f"{project}约{households}户的乡村建设规划许可证、土地测绘落宗、"
                  f"资料建档及不动产权证办理工作" if households
                  else f"{project}服务范围内的全部工作内容")
    _tquote = _table(doc, ["序号", "项目名称", "工作范围", "单位", "数量",
                 "全费用综合单价（元）", "合价（元）", "备注"],
           [["1", project, _scope[:160],
             "户" if households else "项", _qty, _unit_txt, str(total_yuan),
             "全费用综合单价"],
            # 人工稿的报价表里「合计」是表内一行，不是表外一句话
            ["", f"合    计（{big}）", "", "", "", "", str(total_yuan), ""]],
           widths=[1.2, 3.4, 7.6, 1.4, 1.2, 3.2, 2.8, 3.9], font_size=10.5,
           aligns=["center", "left", "left", "center", "center", "center",
                   "center", "left"])
    # 表内备注行（人工稿的说明在报价表最后一行，不是表外一句话）
    _nr = _tquote.add_row()
    _mcell = _nr.cells[0].merge(_nr.cells[len(_nr.cells) - 1])
    _mcell.text = ""
    _pp = _mcell.paragraphs[0]
    _pp.alignment = 0
    _pp.paragraph_format.line_spacing = 1.0
    _set_font(_pp.add_run("备注：1、该报价为全费用固定单价，包括完成上述内容所必须的"
                          "其他费用及管理费、利润、规费、税金、招标代理费等全部费用。"),
              name="宋体", size=9)

    # ── 六、商务条款响应及偏离表 ──
    _new_section(doc, False)
    _h(doc, t_dev, 1)
    _p(doc, f"投标人全称：{co_name}          （加盖公章）", size=12,
       indent=False, name="仿宋")
    # 商务条款逐条响应：条款文字一律取招标文件原文（`parse_tender.commerce`），
    # 不再写死「盐都区大冈镇 / 124 户」这类只对单个项目成立的句子
    # （2026-11-11 业主：「跟原来模板缺少那么多内容」——缺的正是这些商务条款）。
    _com = tender.get("commerce") or {}
    _biz = []

    def _add(label, req, ans="完全响应。"):
        if req:
            _biz.append((label, req, ans))

    _add("投标有效期", _com.get("投标有效期"),
         "完全响应，投标有效期内不撤回投标、不修改报价。")
    _add("服务期限", f"{duration} 日历天，具体时间服从采购人要求。",
         "完全响应，按采购人要求组织作业，确保按期完成。")
    _add("报价要求",
         (f"报价不高于最高限价 {max_yuan} 元，且不得采用选择性报价。"
          if max_yuan else "全费用综合单价包干，不得采用选择性报价。"),
         "完全响应，报价不超最高限价，无选择性报价。")
    for _label in ("履约保证金", "招标代理费", "投标保证金", "投标文件份数",
                   "付款方式", "质量要求", "其他实质性要求"):
        _add(_label, _com.get(_label))
    if not _biz:      # 招标文件里一条商务条款都没解析出来时的最小回退
        _add("报价要求", "全费用综合单价包干，不得采用选择性报价。",
             "完全响应。")
    _cm_style = style.get("commerce") or {}
    _com_rows = [[a, b, "是", c] for a, b, c in _biz]
    _table(doc, ["项目", "招标文件要求", "是否响应", "投标人的承诺或说明"],
           _com_rows,
           widths=_cm_style.get("widths") or [2.71, 5.87, 2.43, 5.16],
           font_size=style["table"]["size"],
           # 行高把版面撑满（业主：不能「没占满一页」，也不能挤成一团）
           row_heights=[_cm_style.get("header_row_cm") or 1.1]
           + [_cm_style.get("row_cm") or 1.75] * len(_com_rows),
           aligns=["center", "left", "center", "left"])
    _p(doc, f"法定代表人或授权代表签字：{af['agent']}", size=12, indent=False,
       name="仿宋")

    # ── 中小企业声明函：按人工稿作为**第六章的二级小节**（不单列「附件」一级章）
    doc.add_page_break()
    _h(doc, "中小企业声明函（工程、服务）", 2)
    _p(doc, "本公司郑重声明，根据《政府采购促进中小企业发展管理办法》"
            "（财库﹝2020﹞46号）的规定，本公司参加" + purchaser +
            "的" + project + "采购活动，服务全部由符合政策要求的中小企业承接。",
       size=12, align="justify", name="仿宋")
    _p(doc, "相关企业的具体情况如下：", size=12, align="justify", name="仿宋")
    _p(doc, f"1. {project}，属于{tender.get('sme_industry') or '其他未列明行业'}行业；"
            f"承接企业为{co_name}，"
            f"从业人员{_se.get('employees') or '______'}人，"
            f"营业收入为{_se.get('revenue') or '______'}万元，"
            f"资产总额为{_se.get('assets') or '______'}万元，"
            f"属于（{_se.get('type') or '小型企业'}）。", size=12,
       align="justify", name="仿宋")
    _p(doc, "以上企业，不属于大企业的分支机构，不存在控股股东为大企业的情形，"
            "也不存在与大企业的负责人为同一人的情形。", size=12, align="justify",
       name="仿宋")
    _p(doc, "本企业对上述声明内容的真实性负责。如有虚假，将依法承担相应责任。",
       size=12, align="justify", name="仿宋")
    _p(doc, f"企业名称（盖章）：{co_name}", size=12, indent=False, name="仿宋")
    _p(doc, f"日期：{today}", size=12, indent=False, name="仿宋")

    # ── 七、项目组成员一览表（横排）──
    _new_section(doc, False)
    _h(doc, t_team, 1)
    _p(doc, f"投标人名称（盖章）：{co_name}", size=12, indent=False, name="仿宋")
    # 人员表列按人工稿：姓名 / 拟任职务 / 学历 / 身份证号 / 办公电话 / 备注
    # （身份证号、办公电话取自人员库；缺的留空，不编造）
    _rows = []
    for p in people:
        _rows.append([p.get("name") or "", p.get("role") or "",
                      p.get("education") or "", str(p.get("id_no") or ""),
                      str(p.get("phone") or ""), ""])
    _table(doc, ["姓名", "本项目拟任职务", "学历", "身份证号", "办公电话", "备注"],
           _rows, font_size=style["table"]["size"],
           widths=(style.get("team") or {}).get("widths"))
    for p in people:
        pid = p.get("id")
        if pid:
            imgs = _person_images(int(pid))
            if imgs:
                _p(doc, f"{p.get('name')}（{p.get('role')}）", size=12,
                   indent=False, name="仿宋")
                for nm, data in imgs[:6]:
                    _img(doc, data, style["images"]["cert_cm"], nm)
    # 人员社保：**只有招标文件要求拟派人员社保时**才附（业主 2026-11-11：
    # 「根据招标文件中的人员要求……交社保」）。优先用每月导入的社保材料，
    # 没有月度材料时退回公司档案里的社保权益记录单。
    if person_si:
        _si_imgs = _social_insurance_images(company_id)
        if _si_imgs:
            _p(doc, "拟派人员社会保险缴纳证明", size=12, bold=True,
               align="center", indent=False, name="仿宋")
            for _nm, _data in _si_imgs:
                _img(doc, _data, style["images"]["screenshot_cm"])
        else:
            _asset_block(doc, "社会保险权益记录单", [("江苏省社会保险权益记录单",)],
                         caption="拟派人员社会保险缴纳证明",
                         width_cm=style["images"]["financial_cm"])

    # ── 八、盐都区政府采购事前信用承诺书 ──
    _new_section(doc, False)
    _h(doc, t_credit, 1)
    _pledge = (
        "为营造我区公开、公平、公正、诚实守信的政府采购交易环境，树立诚信守法的"
        "投标人形象，本人代表本单位作出以下承诺：",
        "（一）本单位对所提交的企（事）业单位基本信息、企（事）业负责人、项目"
        "负责人、技术负责人、从业资质和资格、业绩、财务状况、信誉等所有资料，均"
        "合法、真实、准确、有效，无任何伪造、修改、虚假成份，并对所提供资料的真实"
        "性负责；",
        "（二）本单位无涉及政府采购活动的违法、违规不良记录，我公司及相关负责人"
        "无因存在重大隐患整改不力、发生有重大社会影响生产安全事故或其他严重违法"
        "违规行为而被列入失信联合惩戒的不良记录；",
        "（三）严格依照国家和省、市、区关于政府采购的法律、法规、规章、规范性"
        "文件，参加政府采购投标活动；积极履行社会责任，促进廉政建设；",
        "（四）自我约束、自我管理，守合同、重信用，自觉维护政府采购交易的良好"
        "秩序，与参与本次政府采购活动的其他供应商不存在直接控股或管理关系，不参与"
        "围标串标、弄虚作假、骗取中标、干扰评标、违约毁约等行为；",
        "（五）自觉接受政府、行业组织、社会公众、新闻舆论的监督；",
        "（六）本单位自愿接受政府采购监督管理机构和有关行政监督部门的依法检查。"
        "如发生违法违规或不良失信行为，自愿接受依法给予的行政处罚（处理），并"
        "依法承担相应责任；",
        "（七）本人已认真阅读了上述承诺，并向本单位员工作了宣传教育。",
    )
    # 招标文件里有自己的承诺书正文时，逐字使用招标文件的（不写死某一地区版本）
    _tp = tender.get("credit_pledge") or []
    if len(_tp) >= 3:
        _pledge = tuple(_tp)
    for _ln in _pledge:
        _p(doc, _ln, size=12, align="justify", name="仿宋")
    _p(doc, f"法定代表人签名：{af['legal']}", size=12, indent=False, name="仿宋")
    _p(doc, f"单位名称（盖章）：{co_name}", size=12, indent=False, name="仿宋")
    _p(doc, f"日期：{today}", size=12, indent=False, name="仿宋")

    _apply_body_format(doc, style)
    _center_all_cells(doc)
    _page_number_footer(doc)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    try:
        if _stamp_toc(out, titles=titles,
                  anchors=tuple(f"{_no}.{_txt}" for _no, _txt in _file_items),
                  style=style):
            logger.info("[投标文件] 目录已写入真实页码")
    except Exception as e:
        logger.warning(f"[投标文件] 目录页码写入失败，保留目录域: {e}")
    logger.info(f"[投标文件] 已生成简式投标文件 {out}（{len(people)} 人，"
                f"报价 {small} 万元）")
    return out


def _company_addr(name: str) -> str:
    return {"江苏众拓测绘有限公司": "淮安经济技术开发区海口路9号1幢1211室"}.get(name, "")


def _company_contact(name: str) -> str:
    return {"江苏众拓测绘有限公司": "张红艳 15949194424"}.get(name, "")


def _performance_rows() -> List[Tuple[str, str, str]]:
    """从 company_assets / asset_images 里取业绩（案例中 6 项）。"""
    names = [
        ("2024年食品科技产业园部分地块征地报批", "征地报批", "2024.6"),
        ("井神绿化通道项目征地服务", "征地报批", "2025.5"),
        ("张兴矿区采输卤扩建项目征地服务（含稳评）", "征地报批/稳评", "2025.6"),
        ("世坤迪地块征地报批（含稳评及林地报批）服务", "征地报批", "2025.8"),
        ("大王庄路网接线提升改造工程用地报批咨询服务", "用地报批", "2025.9"),
        ("淮阴区烈士集中安葬区项目社会稳定风险评估服务", "稳评", "2025.10"),
    ]
    return names


def _asset_block(doc, category_kw: str, names: Sequence[Tuple[str, ...]], caption="",
                 *, break_after=True, width_cm=15.0, rotate90=False,
                 break_before_first=False):
    """按图片名插入公司档案图片（不一定全有，有几张插几张），宽度由版式配置给定。

    🔴 图片之间用**段前分页**，不用「图片 + 分页符段落」：整页高的扫描件正好顶到
       下边距时，分页符段落放不下会单独占一页，导致每张图后面夹一张空白页
       （实测 2026-11-11：文件2 的审计报告全套变成「一页图、一页白」）。
    🔴 `break_before_first`：这一组从新的一页起（同一「文件N」里有第二组材料时用）。
    """
    count = 0
    for name_tuple in names:
        kw = name_tuple[0]
        for nm, data in _asset_images(kw, limit=20):
            if count == 0 and caption:
                _cp = _p(doc, caption, bold=False, indent=False)
                if break_before_first:
                    _cp.paragraph_format.page_break_before = True
            _rot = bool(rotate90 and _asset_rotate_deg(nm) != 0)
            _w = width_cm
            if not _rot:
                # 竖版扫描件按 20.98cm 放会超出纸张高度（图直接消失）：
                # 按版心宽 15.8cm 与最大高度 20.5cm 双约束缩放
                try:
                    from PIL import Image as _PILImage
                    _im = _PILImage.open(io.BytesIO(data))
                    _iw, _ih = _im.size
                    if _iw and _ih:
                        _w = min(_w, 15.8, 20.5 * _iw / _ih)
                except Exception:
                    _w = min(_w, 15.8)
            _img(doc, data, _w,
                 page_break_before=(count > 0 or
                                    (count == 0 and break_before_first and not caption)),
                 rotate90=_rot)
            count += 1
    if count and break_after:
        doc.add_page_break()
    if count == 0 and caption:
        _p(doc, f"（{caption}：见公司档案原件）", indent=False)
