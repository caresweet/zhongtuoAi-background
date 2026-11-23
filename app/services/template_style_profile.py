# -*- coding: utf-8 -*-
"""从模板 DOCX 反读版式：标题/正文字体字号、表格字体字号、表题格式。

为什么
------
此前这些值是**按样本手抄进代码**的（稳评一套、复垦一套），换个地区的模板
就未必对得上。模板文件本身带着样式定义（`styles.xml` 的 Heading 1/2/3、
Normal 的 `w:eastAsia` 字体与字号），也带着每张表的实际字体字号 ——
能读就不该猜（业主 2026-11-04：「表格的格式和一级二级标题正文的字体字号，
你能自己识别出来么」）。

取值优先级
----------
1. 段落实测（同一 style 下出现次数最多的「中文字体 + 字号」）—— 模板作者
   常常直接改 run 而不改样式，实测比样式表准；
2. 样式定义（Heading N / Normal 的 `w:rFonts/@w:eastAsia` + `w:sz`）；
3. 调用方兜底（装配器里原有的按域常量）。

结果缓存到 `data/template_profiles/style_<stem>.json`，按 mtime + 版本失效。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
_CACHE_DIR = _BACKEND / "data" / "template_profiles"
_STYLE_VERSION = 6

_HEADING_STYLES = {
    1: ("heading 1", "heading1", "标题 1", "标题1"),
    2: ("heading 2", "heading2", "标题 2", "标题2"),
    3: ("heading 3", "heading3", "标题 3", "标题3"),
}


def _east_asia(el) -> str:
    """元素的 `w:rFonts/@w:eastAsia`（中文实际字体）。"""
    from docx.oxml.ns import qn
    if el is None:
        return ""
    rpr = el.find(qn('w:rPr'))
    if rpr is None:
        rpr = el
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        return ""
    return str(rf.get(qn('w:eastAsia')) or "").strip()


def _style_hint(doc, level: int) -> Dict[str, Any]:
    """从样式定义取 (中文字体, 字号, 加粗)。"""
    for name in _HEADING_STYLES.get(level, ()) + ("Normal",):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        font = _east_asia(st.element)
        try:
            size = st.font.size.pt if st.font.size else None
        except Exception:
            size = None
        bold = st.font.bold
        if font or size:
            return {"font": font, "size": size, "bold": bold}
    return {}


def _run_font_of(paragraph) -> tuple:
    """段落第一个有文字的 run 的 (中文字体, 字号 pt)。"""
    for r in paragraph.runs:
        if not str(r.text or "").strip():
            continue
        font = _east_asia(r._element)
        try:
            size = r.font.size.pt if r.font.size else None
        except Exception:
            size = None
        if font or size:
            return font, size
    return "", None


def _para_format_of(para) -> Dict[str, Any]:
    """段落版式：对齐 / 首行缩进 / 行距 / 段前段后（均为可比较的标量）。"""
    pf = para.paragraph_format
    out: Dict[str, Any] = {}

    def _pt(v):
        try:
            return round(float(v.pt), 1)
        except Exception:
            try:
                return round(float(v) / 12700.0, 1)     # EMU → pt
            except Exception:
                return None

    try:
        if para.alignment is not None:
            out["align"] = str(getattr(para.alignment, "name",
                                        str(para.alignment))).lower()
    except Exception:
        pass
    fi = pf.first_line_indent
    if fi is not None:
        out["first_indent_pt"] = _pt(fi)
    ls = pf.line_spacing
    if ls is not None:
        from docx.shared import Length
        if isinstance(ls, Length):          # Length 是 int 子类，必须先判
            out["line_spacing"] = _pt(ls)
            out["line_rule"] = "exact"
        elif isinstance(ls, (int, float)):
            out["line_spacing"] = float(ls)
            out["line_rule"] = "multiple"
        else:
            out["line_spacing"] = _pt(ls)
            out["line_rule"] = "exact"
    if pf.space_before is not None:
        out["space_before_pt"] = _pt(pf.space_before)
    if pf.space_after is not None:
        out["space_after_pt"] = _pt(pf.space_after)
    return out


def _common_formats(paras) -> Dict[str, Any]:
    """一组段落里出现最多的版式值（对齐/缩进/行距/段前后）。"""
    keys = ("align", "first_indent_pt", "line_spacing", "line_rule",
            "space_before_pt", "space_after_pt")
    counters = {k: Counter() for k in keys}
    for p in paras:
        f = _para_format_of(p)
        for k in keys:
            if k in f:
                counters[k][f[k]] += 1
    return {k: c.most_common(1)[0][0] for k, c in counters.items() if c}


def _heading_slot(doc, level: int) -> Dict[str, Any]:
    """标题版式：**样式表优先**，段落实测只补样式里缺的字体/字号。

    模板里多数标题 run 不直接设字体（继承样式），而封面标题也套 Heading 1
    并单独设成 26pt —— 「实测多数」会把封面字号当成章标题字号（实测踩过）。
    """
    names = {x.lower() for x in _HEADING_STYLES.get(level, ())}
    fonts, sizes = Counter(), Counter()
    bold_vals = Counter()
    paras = []
    for para in doc.paragraphs:
        st = str(getattr(getattr(para, "style", None), "name", "") or "").lower().replace(" ", "")
        _txt = str(para.text or "").strip()
        # 一级标题：除 Heading 1 外，也认「第四章 …」这类正文段落
        # （洪泽/淮安区/淮阴区/清江浦区的章标题没有套标题样式）
        _is_cn_ch = (level == 1 and _txt and len(_txt) <= 50
                     and not st.startswith(("toc", "目录"))
                     and re.match(r'^第[零一二三四五六七八九十]+章\s*\S', _txt) is not None)
        if st not in {n.replace(" ", "") for n in names} and not _is_cn_ch:
            continue
        if not _txt:
            continue
        paras.append(para)
        font, size = _run_font_of(para)
        if font:
            fonts[font] += 1
        if size:
            sizes[size] += 1
        for r in para.runs:
            if str(r.text or "").strip() and r.font.bold is not None:
                bold_vals[bool(r.font.bold)] += 1
    hint = _style_hint(doc, level)
    # 🔴 以模板**实际 run** 为准：井神 11.2 的标题样式写着加粗，但每个标题 run
    #    都显式设了 bold=False（成稿不加粗）。样本数够时用实测，否则回落到样式。
    bold = hint.get("bold")
    if sum(bold_vals.values()) >= 3:
        bold = bold_vals.most_common(1)[0][0]
    out = {
        "font": hint.get("font") or (fonts.most_common(1)[0][0] if fonts else ""),
        "size": hint.get("size") or (sizes.most_common(1)[0][0] if sizes else None),
        "bold": bold,
    }
    out.update(_common_formats(paras))
    return out


def _body_slot(doc) -> Dict[str, Any]:
    fonts, sizes = Counter(), Counter()
    paras = []
    started = False
    for para in doc.paragraphs:
        st = str(getattr(getattr(para, "style", None), "name", "") or "").lower()
        txt = str(para.text or "").strip()
        if not started:
            # 正文样本从**第一章之后**开始取：封面/扉页/目录的行距与对齐不能代表正文
            if (st.startswith(("heading 1", "heading1"))
                    or (len(txt) <= 50
                        and re.match(r'^第[零一二三四五六七八九十]+章\s*\S', txt))):
                started = True
            continue
        if st.startswith(("heading", "toc", "标题")):
            continue
        if re.match(r'^[图表]\s*\d', txt):      # 图表题不是正文
            continue
        if len(txt) < 20:            # 跳过图表题、短行
            continue
        paras.append(para)
        font, size = _run_font_of(para)
        if font:
            fonts[font] += 1
        if size:
            sizes[size] += 1
        if sum(fonts.values()) >= 80:
            break
    hint = _style_hint(doc, 0)
    out = {
        "font": (fonts.most_common(1)[0][0] if fonts else "") or hint.get("font") or "",
        "size": (sizes.most_common(1)[0][0] if sizes else None) or hint.get("size"),
    }
    out.update(_common_formats(paras))
    return out


def _table_slot(doc) -> Dict[str, Any]:
    fonts, sizes = Counter(), Counter()
    hdr_bold = 0
    hdr_n = 0
    for tb in doc.tables[:40]:
        for ri, row in enumerate(tb.rows[:6]):
            for cell in row.cells:
                for para in cell.paragraphs:
                    txt = str(para.text or "").strip()
                    if not txt:
                        continue
                    font, size = _run_font_of(para)
                    if font:
                        fonts[font] += 1
                    if size:
                        sizes[size] += 1
                    if ri == 0:
                        hdr_n += 1
                        if any(r.bold for r in para.runs):
                            hdr_bold += 1
    return {
        "font": fonts.most_common(1)[0][0] if fonts else "",
        "size": sizes.most_common(1)[0][0] if sizes else None,
        "header_bold": (hdr_bold * 2 >= hdr_n) if hdr_n else True,
    }


_CAPTION_RE = re.compile(r'^\s*表\s*\d+\s*[-—]\s*\d+')


def _tbl_borders_of(tbl):
    """一张表的边框定义：先看直接格式，没写就看它用的表格样式。"""
    from docx.oxml.ns import qn
    tblPr = tbl._tbl.tblPr
    b = tblPr.find(qn('w:tblBorders')) if tblPr is not None else None
    if b is None and tbl is not None and tbl.style is not None:
        try:
            stPr = tbl.style.element.find(qn('w:tblPr'))
            if stPr is not None:
                b = stPr.find(qn('w:tblBorders'))
        except Exception:
            b = None
    if b is None:
        return None
    out = {}
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = b.find(qn('w:' + side))
        if el is None:
            out[side] = None
            continue
        try:
            sz = int(el.get(qn('w:sz')) or 0)
        except (TypeError, ValueError):
            sz = 0
        out[side] = (str(el.get(qn('w:val')) or "single"), sz,
                     str(el.get(qn('w:color')) or "auto"))
    return out


def _table_border_slot(doc) -> Dict[str, Any]:
    """表格边框：取多数表的公共边框（0.5pt 单线 / 1pt / 无框线都要能认出来）。"""
    from collections import Counter
    specs = Counter()
    for tb in doc.tables[:40]:
        b = _tbl_borders_of(tb)
        if not b:
            continue
        edges = [(b.get(k) or (None, 0, ""))[0] for k in
                 ("top", "left", "bottom", "right")]
        vals = {e for e in edges if e}
        # 四边不一致的（封面装饰表、合并表）不参与统计
        if len(vals) != 1:
            continue
        edge = next(iter(vals))
        top = b.get("top") or (edge, 0, "auto")
        inside = [(b.get(k) or (None, 0, ""))[0] for k in ("insideH", "insideV")]
        has_inside = any(x and x != "none" and x != "nil" for x in inside)
        specs[(edge, int(top[1]), str(top[2]), has_inside)] += 1
    if not specs:
        return {}
    (edge, sz, color, has_inside), _ = specs.most_common(1)[0]
    none = str(edge).lower() in ("none", "nil")
    return {
        "val": "nil" if none else str(edge),
        "size_pt": round(max(sz, 4) / 8.0, 2) if not none else 0.0,
        "color": color if color and color != "auto" else "000000",
        "inside": bool(has_inside) and not none,
        "none": none,
    }


def _table_layout_slot(doc) -> Dict[str, Any]:
    """表格对齐、单元格边距、行高、表头底纹 —— 模板怎么排就怎么排。"""
    from collections import Counter
    from docx.oxml.ns import qn
    jc, mar, trh, shd = Counter(), Counter(), Counter(), Counter()
    for tb in doc.tables[:40]:
        p = tb._tbl.tblPr
        if p is not None:
            j = p.find(qn('w:jc'))
            jc[j.get(qn('w:val')) if j is not None else None] += 1
            m = p.find(qn('w:tblCellMar'))
            if m is not None:
                vals = {}
                for side in ("top", "left", "bottom", "right"):
                    el = m.find(qn('w:' + side))
                    try:
                        vals[side] = int(el.get(qn('w:w')) or 0) if el is not None else None
                    except (TypeError, ValueError):
                        vals[side] = None
                mar[tuple(vals.get(k) for k in ("top", "left", "bottom", "right"))] += 1
        for row in tb.rows[:1]:
            for c in row.cells:
                tcPr = c._tc.tcPr
                el = tcPr.find(qn('w:shd')) if tcPr is not None else None
                fill = str(el.get(qn('w:fill')) or "") if el is not None else ""
                if fill and fill.lower() != "auto":
                    shd[fill] += 1
        for row in tb.rows[:4]:
            trPr = row._tr.find(qn('w:trPr'))
            el = trPr.find(qn('w:trHeight')) if trPr is not None else None
            if el is not None:
                try:
                    trh[int(el.get(qn('w:val')) or 0)] += 1
                except (TypeError, ValueError):
                    pass

    out: Dict[str, Any] = {}
    a = jc.most_common(1)[0][0] if jc else None
    if a:
        out["align"] = str(a)
    if mar:
        vals = mar.most_common(1)[0][0]
        tw = lambda v: round((v or 0) / 1440.0 * 2.54, 2)  # noqa: E731
        out["cell_margin_cm"] = {"top": tw(vals[0]), "left": tw(vals[1]),
                                 "bottom": tw(vals[2]), "right": tw(vals[3])}
    if shd:
        out["header_shading"] = shd.most_common(1)[0][0]
    if trh:
        v, c = trh.most_common(1)[0]
        # 只有多数行都用同一高度时才算模板设定（否则是内容撑开的偶然值）
        if v > 0 and c >= max(2, len(doc.tables[:40]) // 3):
            out["row_height_cm"] = round(v / 1440.0 * 2.54, 2)
    return out


def _caption_slot(doc) -> Dict[str, Any]:
    fonts, sizes = Counter(), Counter()
    bold_n = 0
    n = 0
    for para in doc.paragraphs:
        txt = str(para.text or "").strip()
        if not _CAPTION_RE.match(txt):
            continue
        font, size = _run_font_of(para)
        if font:
            fonts[font] += 1
        if size:
            sizes[size] += 1
        n += 1
        if any(r.bold for r in para.runs):
            bold_n += 1
        if n >= 60:
            break
    return {
        "font": fonts.most_common(1)[0][0] if fonts else "",
        "size": sizes.most_common(1)[0][0] if sizes else None,
        "bold": (bold_n * 2 >= n) if n else False,
    }


def _page_slot(doc) -> Dict[str, Any]:
    try:
        s = doc.sections[0]
        return {
            "width_cm": round(s.page_width.cm, 2),
            "height_cm": round(s.page_height.cm, 2),
            "margins_cm": [round(s.left_margin.cm, 2), round(s.right_margin.cm, 2),
                           round(s.top_margin.cm, 2), round(s.bottom_margin.cm, 2)],
        }
    except Exception:
        return {}


def build_style_profile(template_path: str | Path) -> Optional[Dict[str, Any]]:
    """读模板 DOCX 的版式；不是 docx 或解析失败返回 None。"""
    p = Path(str(template_path or ""))
    if not p.exists() or p.suffix.lower() != ".docx":
        return None
    try:
        from docx import Document
        doc = Document(str(p))
    except Exception as e:
        logger.info(f"[版式] 打开失败 {p}: {e}")
        return None
    return {
        "version": _STYLE_VERSION,
        "source": str(p),
        "h1": _heading_slot(doc, 1),
        "h2": _heading_slot(doc, 2),
        "h3": _heading_slot(doc, 3),
        "body": _body_slot(doc),
        "table": _table_slot(doc),
        "table_border": _table_border_slot(doc),
        "table_layout": _table_layout_slot(doc),
        "caption": _caption_slot(doc),
        "page": _page_slot(doc),
    }


def _cache_path(template_path: str | Path) -> Path:
    return _CACHE_DIR / f"style_{Path(str(template_path)).stem}.json"


def load_style_profile(template_path: str | Path) -> Optional[Dict[str, Any]]:
    """有缓存读缓存（版本一致才用），没有就现读并落盘。"""
    p = Path(str(template_path or ""))
    if not p.exists() or p.suffix.lower() != ".docx":
        return None
    cache = _cache_path(p)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == _STYLE_VERSION:
                return data
        except Exception:
            pass
    prof = build_style_profile(p)
    if prof:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(prof, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        except OSError as e:
            logger.info(f"[版式] 缓存写入失败 {cache}: {e}")
    return prof


def style_summary(prof: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """给上传分析结果展示的简表：字体 + 字号（pt）。"""
    if not prof:
        return {}

    def _one(k):
        d = prof.get(k) or {}
        return {"font": d.get("font"), "size": d.get("size")}

    return {"h1": _one("h1"), "h2": _one("h2"), "h3": _one("h3"),
            "body": _one("body"), "table": _one("table"),
            "table_border": prof.get("table_border") or {},
            "table_layout": prof.get("table_layout") or {},
            "caption": _one("caption"), "page": prof.get("page") or {}}
