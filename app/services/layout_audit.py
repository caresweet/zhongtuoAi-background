# -*- coding: utf-8 -*-
"""文档版式审核 Agent —— 识别报告内容、布局结构、标题层级与文本样式。

输入三类，处理策略强制分先后（业主 2026-11-23 口径）：

1. **结构化解析数据**（DOCX / 原生 PDF）：
   - DOCX：以段落样式、run 的字体/字号/颜色/加粗/斜体/下划线为**真值**，
     bbox 记为 null（DOCX 原生不带版面坐标，不猜）；
   - PDF：以 PyMuPDF 的 span 字体/字号/颜色/flags + bbox 为真值，
     按字号排序 + 位置 + 文本特征推断标题层级（confidence 0.6~0.8）。
2. **扫描件 / 图片截图**：只用视觉模型做相对判断，明确标 `source=visual_estimate`，
   不输出精确字体名/字号，confidence 上限 0.7。
3. 业务规则：区分「大号正文伪装标题」、跨页标题、跨栏内容，
   不编造样式字段（判不出填 null），最后给章节树 + 异常样式清单。

输出为标准 JSON；`render_text_summary()` 给人工可读摘要。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 文本特征 ──────────────────────────────────────────────────────
_TABLE_CAP_RE = re.compile(r'^\s*表\s*\d+(?:\s*[-—－.]\s*\d+)?')
_FIG_CAP_RE = re.compile(r'^\s*图\s*\d+(?:\s*[-—－.]\s*\d+)?')
_LIST_RE = re.compile(
    r'^\s*(?:[（(]\s*\d{1,3}\s*[）)]|\d{1,3}\s*[）)]|[①-⑳]|[•·●▪◆■□-]\s*)\S')
_H2_CN_RE = re.compile(r'^\s*(?:（[一二三四五六七八九十]+）|[一二三四五六七八九十]+、)\s*\S')
_H3_CN_RE = re.compile(r'^\s*(?:\d{1,2}\.\d{1,2}(?:\.\d{1,2})?|\d{1,2}[.、])\s*\S')
_PAGE_NUM_RE = re.compile(r'^\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*$')
_SENT_END = ('。', '；', ';', '，', ',')
_HEADING_STYLES = {
    1: ('heading 1', 'heading1', '标题 1', '标题1'),
    2: ('heading 2', 'heading2', '标题 2', '标题2'),
    3: ('heading 3', 'heading3', '标题 3', '标题3'),
}
_TYPES = ('h1', 'h2', 'h3', 'body', 'table_caption', 'figure_caption',
          'header', 'footer', 'list_item', 'table_cell')

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}
_PDF_EXTS = {'.pdf'}
_DOCX_EXTS = {'.docx'}


def _qn(tag: str):
    from docx.oxml.ns import qn
    return qn(tag)


def _align_name(v) -> Optional[str]:
    if v is None:
        return None
    try:
        name = str(getattr(v, 'name', v)).lower()
        return {'both': 'justify', 'distribute': 'justify'}.get(name, name)
    except Exception:
        return None


def _pt(v) -> Optional[float]:
    try:
        return round(float(v.pt), 1)
    except Exception:
        try:
            return round(float(v) / 12700.0, 1)   # EMU → pt
        except Exception:
            return None


def _empty_style() -> Dict[str, Any]:
    return {"font_name": None, "font_size": None, "color_hex": None,
            "bold": None, "italic": None, "underline": None}


def _docx_paragraph_style(p) -> Dict[str, Any]:
    """段落样式（真值）+ 首个有文字 run 的覆盖值。"""
    st = _empty_style()
    try:
        f = p.style.font
        rf = p.style.element.find(_qn('w:rPr'))
        if rf is not None:
            fonts = rf.find(_qn('w:rFonts'))
            if fonts is not None:
                st["font_name"] = (fonts.get(_qn('w:eastAsia'))
                                   or fonts.get(_qn('w:ascii')))
        if f.size:
            st["font_size"] = _pt(f.size)
        st["bold"] = f.bold
        st["italic"] = f.italic
        if f.underline is not None:
            st["underline"] = bool(f.underline)
        try:
            if f.color and f.color.rgb:
                st["color_hex"] = "#" + str(f.color.rgb).lower()
        except Exception:
            pass
    except Exception:
        pass
    for r in p.runs:
        if not (r.text or '').strip():
            continue
        try:
            rpr = r._element.find(_qn('w:rPr'))
            if rpr is not None:
                fonts = rpr.find(_qn('w:rFonts'))
                if fonts is not None:
                    st["font_name"] = (fonts.get(_qn('w:eastAsia'))
                                       or fonts.get(_qn('w:ascii'))
                                       or st["font_name"])
                if r.font.size:
                    st["font_size"] = _pt(r.font.size)
            if r.font.bold is not None:
                st["bold"] = bool(r.font.bold)
            if r.font.italic is not None:
                st["italic"] = bool(r.font.italic)
            if r.font.underline is not None:
                st["underline"] = bool(r.font.underline)
            if r.font.color and r.font.color.rgb:
                st["color_hex"] = "#" + str(r.font.color.rgb).lower()
        except Exception:
            pass
        break
    return st


def _docx_paragraph_layout(p) -> Dict[str, Any]:
    pf = p.paragraph_format
    indent = None
    try:
        fi, li = _pt(pf.first_line_indent), _pt(pf.left_indent)
        if fi is not None or li is not None:
            indent = {"first_line_pt": fi, "left_pt": li}
    except Exception:
        pass
    return {"bbox": None, "align": _align_name(p.alignment),
            "indent": indent, "column": None}


def _heading_level_from_style(p) -> Optional[int]:
    try:
        st = (getattr(p.style, 'name', '') or '').lower()
        for lvl, names in _HEADING_STYLES.items():
            if st in names:
                return lvl
        # 段落 outlineLvl（0 起）也认
        ppr = p._p.find(_qn('w:pPr'))
        if ppr is not None:
            ol = ppr.find(_qn('w:outlineLvl'))
            if ol is not None:
                try:
                    return min(int(ol.get(_qn('w:val'))) + 1, 3)
                except Exception:
                    pass
    except Exception:
        pass
    return None


_LIST_VERB = ('为', '确保', '根据', '按照', '通过', '加强', '严格', '依法', '做好',
              '落实', '建立', '完善', '坚持', '实行', '规范', '控制', '对', '在',
              '从', '以', '经', '如', '若', '凡', '本决策', '该项目')


def _looks_like_heading_text(t: str, level: int) -> bool:
    if not t or len(t) > 45:
        return False
    if t.endswith(_SENT_END):
        return False
    if level == 2:
        return bool(_H2_CN_RE.match(t))
    if level == 3:
        if not (bool(_H3_CN_RE.match(t)) and len(t) <= 30):
            return False
        # 「1.为维护人民群众的合法权益」是正文编号列表，不是三级标题
        title = re.sub(r'^\d{1,2}(\.\d{1,2}){0,2}\s*[.、]?\s*', '', t)
        return not title.startswith(_LIST_VERB)
    return False


class _BlockIds:
    def __init__(self):
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"b{self.n:04d}"


# ═══════════════════════════════════════════════════════════════
# DOCX
# ═══════════════════════════════════════════════════════════════
def _docx_blocks(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    ids = _BlockIds()
    blocks: List[Dict[str, Any]] = []
    anomalies: List[Dict[str, Any]] = []

    # 页眉/页脚（各节；同文只记一次）
    seen_hf = set()
    for sec in doc.sections:
        for kind, part in (("header", sec.header), ("footer", sec.footer)):
            try:
                for p in part.paragraphs:
                    t = (p.text or "").strip()
                    if not t or (kind, t) in seen_hf:
                        continue
                    seen_hf.add((kind, t))
                    blocks.append({
                        "block_id": ids.next(), "block_type": kind,
                        "content": t, "style": _docx_paragraph_style(p),
                        "layout": {**_docx_paragraph_layout(p), "page": None},
                        "confidence": 0.95,
                    })
            except Exception:
                continue

    def _para_block(p: Paragraph) -> None:
        t = (p.text or "").strip()
        if not t:
            return
        style = _docx_paragraph_style(p)
        layout = {**_docx_paragraph_layout(p), "page": None}
        lvl = _heading_level_from_style(p)
        btype = f"h{lvl}" if lvl else None
        conf = 0.95 if btype else 0.9
        if btype is None:
            if _TABLE_CAP_RE.match(t):
                btype, conf = "table_caption", 0.85
            elif _FIG_CAP_RE.match(t):
                btype, conf = "figure_caption", 0.85
            elif _LIST_RE.match(t):
                btype, conf = "list_item", 0.85
            elif _looks_like_heading_text(t, 2):
                btype, conf = "h2", 0.72
            elif _looks_like_heading_text(t, 3):
                btype, conf = "h3", 0.7
            else:
                btype = "body"
        blocks.append({"block_id": ids.next(), "block_type": btype,
                       "content": t, "style": style, "layout": layout,
                       "confidence": conf})

    body = doc.element.body
    for el in body.iterchildren():
        if el.tag == qn('w:p'):
            _para_block(Paragraph(el, doc))
        elif el.tag == qn('w:tbl'):
            tbl = Table(el, doc)
            for ri, row in enumerate(tbl.rows):
                for ci, cell in enumerate(row.cells):
                    t = (cell.text or "").strip()
                    if not t:
                        continue
                    p0 = cell.paragraphs[0] if cell.paragraphs else None
                    blocks.append({
                        "block_id": ids.next(), "block_type": "table_cell",
                        "content": t,
                        "style": _docx_paragraph_style(p0) if p0 is not None
                        else _empty_style(),
                        "layout": {"bbox": None, "align": None, "indent": None,
                                   "column": None, "row": ri, "col": ci,
                                   "page": None},
                        "confidence": 0.9,
                    })

    # 「大号正文伪装标题」：正文样式但字号明显大于正文众数、且是长句
    _base = None
    try:
        _base = _pt(doc.styles['Normal'].font.size)
    except Exception:
        _base = None
    _flag_fake_headings(blocks, anomalies, _base)
    return blocks, anomalies


def _mode(values: List[Any]) -> Any:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    from collections import Counter
    return Counter(vals).most_common(1)[0][0]


def _flag_fake_headings(blocks: List[Dict[str, Any]],
                        anomalies: List[Dict[str, Any]],
                        base_size: Optional[float] = None) -> None:
    body_sizes = [b["style"].get("font_size") for b in blocks
                  if b["block_type"] == "body"
                  and b["style"].get("font_size")]
    base = base_size or _mode(body_sizes)
    # 正文都没写字号时（继承 Normal），退回 12pt；只有一个字号时也算不可信
    if not base or len(set(body_sizes)) <= 1:
        base = 12.0
    for b in blocks:
        if b["block_type"] != "body":
            continue
        sz = b["style"].get("font_size") or 0
        t = b["content"]
        if sz >= base + 3 and (len(t) > 40 or t.endswith(_SENT_END)):
            anomalies.append({
                "type": "fake_heading", "severity": "warning",
                "block_ids": [b["block_id"]],
                "message": f"大号正文伪装标题：字号 {sz}pt（正文约 {base}pt）但内容是长句/句末标点",
            })


# ═══════════════════════════════════════════════════════════════
# PDF（原生，带 bbox / 字体 / 字号 / 颜色）
# ═══════════════════════════════════════════════════════════════
def _hex_color(v) -> Optional[str]:
    try:
        return "#" + format(int(v) & 0xFFFFFF, '06x')
    except Exception:
        return None


def _pdf_blocks(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict]:
    import fitz

    doc = fitz.open(path)
    ids = _BlockIds()
    raw: List[Dict[str, Any]] = []
    table_rects: Dict[int, List[Tuple[Any, int, int]]] = {}

    for pno in range(doc.page_count):
        page = doc[pno]
        H, W = page.rect.height, page.rect.width
        # 表格矩形（用于把表格内文本标成 table_cell）
        try:
            tabs = page.find_tables()
            for ti, tb in enumerate(tabs.tables):
                for row_i, row in enumerate(tb.rows):
                    for col_i, cell in enumerate(getattr(row, "cells", []) or []):
                        if cell:
                            table_rects.setdefault(pno, []).append((cell, row_i, col_i))
        except Exception:
            pass
        data = page.get_text("dict")
        for b in data.get("blocks", []):
            if b.get("type") != 0:
                continue
            spans = [s for l in b.get("lines", []) for s in l.get("spans", [])
                     if str(s.get("text") or "").strip()]
            if not spans:
                continue
            content = " ".join(str(s.get("text") or "").strip() for s in spans).strip()
            if not content:
                continue
            main = max(spans, key=lambda s: len(str(s.get("text") or "")))
            flags = int(main.get("flags") or 0)
            font = str(main.get("font") or "")
            style = {
                "font_name": font or None,
                "font_size": round(float(main.get("size") or 0), 1) or None,
                "color_hex": _hex_color(main.get("color")),
                "bold": bool(flags & 16) or ('bold' in font.lower()),
                "italic": bool(flags & 2) or ('italic' in font.lower()),
                "underline": None,
            }
            bbox = [round(float(x), 1) for x in b.get("bbox", [0, 0, 0, 0])]
            raw.append({"block_id": ids.next(), "content": content,
                        "style": style, "bbox": bbox,
                        "page": pno + 1, "page_h": H, "page_w": W,
                        "align": None, "column": None})

    # 页眉页脚：页边区域内 + 多页重复 / 页码形态
    from collections import Counter
    margin_texts = Counter()
    for r in raw:
        y0, y1 = r["bbox"][1], r["bbox"][3]
        if y1 < r["page_h"] * 0.07 or y0 > r["page_h"] * 0.93:
            margin_texts[_norm(r["content"])] += 1

    # 正文基准字号（按字符数加权）
    from collections import Counter as _C
    size_weight = _C()
    for r in raw:
        if _TABLE_CAP_RE.match(r["content"]) or _FIG_CAP_RE.match(r["content"]):
            continue
        sz = r["style"].get("font_size")
        if sz:
            size_weight[sz] += len(r["content"])
    body_size = size_weight.most_common(1)[0][0] if size_weight else 12.0
    heading_sizes = sorted({s for s in size_weight if s >= body_size + 0.8},
                           reverse=True)[:3]
    level_map = {s: i + 1 for i, s in enumerate(heading_sizes)}

    blocks: List[Dict[str, Any]] = []
    anomalies: List[Dict[str, Any]] = []
    for r in raw:
        t = r["content"]
        n = _norm(t)
        y0, y1 = r["bbox"][1], r["bbox"][3]
        btype, conf = None, 0.75
        if ((y1 < r["page_h"] * 0.07) or (y0 > r["page_h"] * 0.93)) and (
                margin_texts.get(n, 0) >= 2 or _PAGE_NUM_RE.match(t)):
            btype = "header" if y1 < r["page_h"] * 0.07 else "footer"
            conf = 0.9
        if btype is None and _TABLE_CAP_RE.match(t):
            btype, conf = "table_caption", 0.85
        if btype is None and _FIG_CAP_RE.match(t):
            btype, conf = "figure_caption", 0.85
        if btype is None and _LIST_RE.match(t):
            btype, conf = "list_item", 0.8
        if btype is None:
            sz = r["style"].get("font_size") or 0
            lvl = level_map.get(sz)
            if lvl and len(t) <= 60 and not t.endswith(_SENT_END):
                btype, conf = f"h{lvl}", 0.7
            elif lvl and (len(t) > 60 or t.endswith(_SENT_END)):
                btype, conf = "body", 0.75
                anomalies.append({
                    "type": "fake_heading", "severity": "warning",
                    "block_ids": [r["block_id"]],
                    "message": f"大号正文伪装标题：字号 {sz}pt 但内容是长句/句末标点",
                })
            else:
                btype, conf = "body", 0.8
        # 表格内文本
        for cell, row_i, col_i in table_rects.get(r["page"], []):
            try:
                x0, yy0, x1, yy1 = float(cell[0]), float(cell[1]), float(cell[2]), float(cell[3])
                cx = (r["bbox"][0] + r["bbox"][2]) / 2
                cy = (r["bbox"][1] + r["bbox"][3]) / 2
                if x0 <= cx <= x1 and yy0 <= cy <= yy1:
                    btype = "table_cell"
                    conf = 0.85
                    r["row"], r["col"] = row_i, col_i
                    break
            except Exception:
                continue
        blocks.append({
            "block_id": r["block_id"], "block_type": btype,
            "content": t, "style": r["style"],
            "layout": {"bbox": r["bbox"], "align": "left",
                       "indent": None, "column": r.get("column"),
                       "page": r["page"], "row": r.get("row"),
                       "col": r.get("col")},
            "confidence": conf,
        })

    # 跨栏：块宽度超过页宽 60% 且横跨左右半页
    for b in blocks:
        bb = b["layout"].get("bbox")
        if not bb:
            continue
        r = next((x for x in raw if x["block_id"] == b["block_id"]), None)
        if not r:
            continue
        W = r["page_w"]
        if (bb[2] - bb[0]) > W * 0.6 and bb[0] < W * 0.35 and bb[2] > W * 0.65:
            b["layout"]["column"] = "span"
        else:
            b["layout"]["column"] = 1 if (bb[0] + bb[2]) / 2 < W / 2 else 2

    # 标题贴近页底 → 可能跨页
    for b in blocks:
        if b["block_type"] not in ("h1", "h2", "h3"):
            continue
        r = next((x for x in raw if x["block_id"] == b["block_id"]), None)
        if r and r["bbox"][3] > r["page_h"] * 0.88:
            anomalies.append({
                "type": "heading_at_page_edge", "severity": "warning",
                "block_ids": [b["block_id"]],
                "message": "标题位于页面底部，可能跨页或与正文分离",
            })

    meta = {"page_count": doc.page_count, "body_size": body_size}
    doc.close()
    return blocks, anomalies, meta


def _norm(s: str) -> str:
    return re.sub(r'\s+', '', s or '')


# ═══════════════════════════════════════════════════════════════
# 图片 / 扫描件（视觉估算）
# ═══════════════════════════════════════════════════════════════
_VISION_PROMPT = """你是文档版式审核助手。请只做**视觉估算**，不要给出精确字体名或精确字号。
严格输出 JSON（不要解释、不要代码块围栏）：
{
  "blocks": [
    {
      "block_id": "v1",
      "block_type": "h1|h2|h3|body|table_caption|figure_caption|header|footer|list_item|table_cell",
      "content": "原文文本",
      "style": {
        "size_hint": "large|medium|small",
        "bold_hint": "yes|no|unknown",
        "color_hint": "黑色/灰色/彩色描述",
        "source": "visual_estimate"
      },
      "layout": {
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "align": "left|center|right|unknown",
        "column": null
      },
      "confidence": 0.6
    }
  ]
}
规则：
1. bbox 用 0~1 相对坐标；无法判断的字段填 null；
2. 标题层级靠相对字号/加粗/位置判断；大号但长句的是正文，不是标题；
3. confidence 不得超过 0.7；
4. 不得输出具体字号数值或字体名称。"""


async def _visual_blocks(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from app.services.llm_service import llm_service
    ext = Path(path).suffix.lower()
    media = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
             '.bmp': 'image/bmp', '.webp': 'image/webp',
             '.tif': 'image/tiff', '.tiff': 'image/tiff'}.get(ext, 'image/png')
    data = Path(path).read_bytes()
    text = await llm_service.chat_with_image(
        _VISION_PROMPT, base64.b64encode(data).decode(),
        media_type=media, max_tokens=4096)
    payload = _parse_json(text)
    blocks = []
    ids = _BlockIds()
    for i, b in enumerate(payload.get("blocks") or []):
        if not isinstance(b, dict):
            continue
        btype = str(b.get("block_type") or "body").lower()
        if btype not in _TYPES:
            btype = "body"
        st = b.get("style") or {}
        style = {
            "font_name": None, "font_size": None,
            "color_hex": None,
            "bold": True if str(st.get("bold_hint")).lower() == "yes"
            else (False if str(st.get("bold_hint")).lower() == "no" else None),
            "italic": None, "underline": None,
            "size_hint": st.get("size_hint"), "source": "visual_estimate",
        }
        lay = b.get("layout") or {}
        conf = min(float(b.get("confidence") or 0.6), 0.7)
        blocks.append({
            "block_id": ids.next(), "block_type": btype,
            "content": str(b.get("content") or ""),
            "style": style,
            "layout": {"bbox": lay.get("bbox"), "align": lay.get("align"),
                       "indent": None, "column": lay.get("column")},
            "confidence": conf,
        })
    return blocks, []


def _parse_json(text: str) -> Dict[str, Any]:
    s = (text or "").strip()
    s = re.sub(r'^```(?:json)?\s*', '', s)
    s = re.sub(r'\s*```$', '', s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r'\{.*\}', s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    logger.warning("版式审核：视觉模型返回的不是 JSON")
    return {"blocks": []}


# ═══════════════════════════════════════════════════════════════
# 汇总：章节树 / 异常 / 样式画像
# ═══════════════════════════════════════════════════════════════
def _chapter_tree(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tree: List[Dict[str, Any]] = []
    stack: List[Tuple[Dict[str, Any], int]] = []
    for b in blocks:
        bt = b["block_type"]
        if bt not in ("h1", "h2", "h3"):
            continue
        lvl = int(bt[1])
        node = {"block_id": b["block_id"], "level": lvl,
                "title": b["content"][:80], "children": []}
        while stack and stack[-1][1] >= lvl:
            stack.pop()
        if stack:
            stack[-1][0]["children"].append(node)
        else:
            tree.append(node)
        stack.append((node, lvl))
    return tree


def _level_anomalies(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    last = 0
    for b in blocks:
        bt = b["block_type"]
        if bt not in ("h1", "h2", "h3"):
            continue
        lvl = int(bt[1])
        if last and lvl > last + 1:
            out.append({"type": "heading_level_skip", "severity": "error",
                        "block_ids": [b["block_id"]],
                        "message": f"标题层级跳级：h{last} → h{lvl}「{b['content'][:30]}」"})
        if lvl > 1 and not last:
            out.append({"type": "heading_without_parent", "severity": "warning",
                        "block_ids": [b["block_id"]],
                        "message": f"文档以 h{lvl} 开头，缺少上级标题"})
        last = lvl
    return out


def _style_conflicts(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from collections import Counter
    out = []
    for bt in ("h1", "h2", "h3"):
        items = [b for b in blocks if b["block_type"] == bt]
        if len(items) < 3:
            continue
        sigs = Counter(
            (b["style"].get("font_name"), b["style"].get("font_size"),
             b["style"].get("bold")) for b in items)
        if len(sigs) <= 1:
            continue
        main, _ = sigs.most_common(1)[0]
        outliers = [b["block_id"] for b in items
                    if (b["style"].get("font_name"), b["style"].get("font_size"),
                        b["style"].get("bold")) != main]
        if outliers:
            out.append({
                "type": "style_conflict", "severity": "warning",
                "block_ids": outliers,
                "message": (f"{bt} 存在 {len(sigs)} 种样式；主流为 "
                            f"{main[0] or '未记字体'} {main[1] or '?'}pt，"
                            f"另有 {len(outliers)} 段不一致"),
            })
    return out


def _style_profile(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    from collections import Counter
    prof: Dict[str, Any] = {}
    for bt in _TYPES:
        items = [b for b in blocks if b["block_type"] == bt]
        if not items:
            continue
        fonts = Counter(b["style"].get("font_name") for b in items
                        if b["style"].get("font_name"))
        sizes = Counter(b["style"].get("font_size") for b in items
                        if b["style"].get("font_size"))
        prof[bt] = {"count": len(items),
                    "font": fonts.most_common(1)[0][0] if fonts else None,
                    "size": sizes.most_common(1)[0][0] if sizes else None}
    return prof


def render_text_summary(result: Dict[str, Any]) -> str:
    """人工可读摘要。"""
    lines = [
        f"版式审核：{result.get('file') or ''}",
        f"模式：{result.get('mode')}；共 {result.get('block_count')} 个区块",
        f"样式来源：{'原生解析（真值）' if result.get('mode') != 'visual_estimate' else '视觉估算（近似）'}",
    ]
    counts = result.get("summary", {}).get("type_counts") or {}
    if counts:
        lines.append("区块统计：" + "、".join(f"{k} {v}" for k, v in counts.items()))
    tree = result.get("summary", {}).get("chapter_tree") or []

    def walk(nodes, depth=0):
        for n in nodes:
            lines.append("  " * depth + f"{'#' * (depth + 1)} {n.get('title')}")
            walk(n.get("children") or [], depth + 1)

    if tree:
        lines.append("章节树：")
        walk(tree)
    anomalies = result.get("summary", {}).get("anomalies") or []
    if anomalies:
        lines.append(f"异常 {len(anomalies)} 项：")
        for a in anomalies[:20]:
            lines.append(f"  - [{a.get('severity')}] {a.get('type')}：{a.get('message')}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════
async def audit_document(path: str | Path, *, original_name: str = "") -> Dict[str, Any]:
    """审核一份文档 → 结构化区块 + 摘要。自动按扩展名选模式。"""
    path = str(path)
    ext = Path(path).suffix.lower()
    anomalies: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    if ext in _DOCX_EXTS:
        mode = "structured_docx"
        import asyncio
        blocks, anomalies = await asyncio.to_thread(_docx_blocks, path)
    elif ext in _PDF_EXTS:
        mode = "structured_pdf"
        import asyncio
        blocks, anomalies, meta = await asyncio.to_thread(_pdf_blocks, path)
    elif ext in _IMAGE_EXTS:
        mode = "visual_estimate"
        blocks, anomalies = await _visual_blocks(path)
    else:
        raise ValueError(f"不支持的文件类型：{ext}（支持 DOCX/PDF/图片）")

    from collections import Counter
    anomalies = list(anomalies) + _level_anomalies(blocks) + _style_conflicts(blocks)
    type_counts = Counter(b["block_type"] for b in blocks)
    result = {
        "file": original_name or Path(path).name,
        "mode": mode,
        "block_count": len(blocks),
        "blocks": blocks,
        "summary": {
            "type_counts": dict(type_counts),
            "chapter_tree": _chapter_tree(blocks),
            "anomalies": anomalies,
            "style_profile": _style_profile(blocks),
            "meta": meta,
        },
    }
    result["text_summary"] = render_text_summary(result)
    return result
