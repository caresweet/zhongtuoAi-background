# -*- coding: utf-8 -*-
"""整张照搬模板里的表格（保留边框、列宽、合并单元格）。

业主口径（2026-11-04）：「表4-5 土地适宜性评价指标体系是模板，不动，保留模板
表格格式，同时合并同类项」——这张表是**规范性的指标体系**（一级/二级/三级
影响因子 + 权重 + 分级标准），不是项目数据，模型重画只会把列挤成竖排、
把合并单元格拆散。直接深拷贝模板里的 `w:tbl` 元素最稳。

缓存：同一份模板只解析一次。
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict[str, List[Tuple[str, object]]] = {}


def _load_tables(template_path: str) -> List[Tuple[str, object]]:
    p = str(template_path or "")
    if p in _CACHE:
        return _CACHE[p]
    out: List[Tuple[str, object]] = []
    try:
        from docx import Document
        doc = Document(p)
        for tb in doc.tables:
            text = " ".join(c.text for row in tb.rows[:3] for c in row.cells)
            out.append((text, tb._tbl))
    except Exception as e:
        logger.info(f"[模板表] 读取失败 {p}: {e}")
        out = []
    _CACHE[p] = out
    return out


def find_template_table(template_path: str, keywords) -> Optional[object]:
    """按表头/前三行关键词找模板表，返回**深拷贝**的 `<w:tbl>` 元素。"""
    if not template_path or not Path(str(template_path)).exists():
        return None
    for text, tbl in _load_tables(str(template_path)):
        if all(k in text for k in keywords):
            return copy.deepcopy(tbl)
    return None


_PLAN_CACHE: Dict[str, List[dict]] = {}


def chapter_table_plan(template_path: str, chapter_no: int) -> List[dict]:
    """模板某章的表格清单（按正文顺序）：`[{no, title, caption, header}]`。

    复垦第4章的系统表以前按**明远样本**定死表号（表4-8 等级标准、表4-10 复垦方向…），
    换到井神 11.2 模板后表号和顺序都对不上（业主 2026-11-05 实测）。这里直接读
    当前模板的第四章表格，后续按它的顺序/表号出表。
    """
    key = f"{template_path}#{chapter_no}"
    if key in _PLAN_CACHE:
        return _PLAN_CACHE[key]

    import re
    from docx import Document
    from docx.oxml.ns import qn

    out: List[dict] = []
    try:
        doc = Document(str(template_path))
        body = doc.element.body
        cur = 0
        last_cap = ""
        for el in body.iterchildren():
            if el.tag == qn('w:p'):
                text = "".join(n.text or "" for n in el.iter(qn('w:t'))).strip()
                m = re.match(r'^(\d{1,2})\s+\S', text)
                if m and len(text) <= 50:
                    cur = int(m.group(1))
                if re.match(r'^表\s*\d+\s*[-—]\s*\d+', text):
                    last_cap = text
            elif el.tag == qn('w:tbl') and cur == int(chapter_no):
                from docx.table import Table
                tb = Table(el, doc)
                header = " ".join(c.text.strip() for c in tb.rows[0].cells) if tb.rows else ""
                m = re.match(r'^表\s*(\d+)\s*[-—]\s*(\d+)\s*(.*)$', last_cap)
                if m:
                    _t = re.sub(r'\s+', ' ', m.group(3)).strip()
                    # 模板表题里常有「指标 体系」这种断词空格，去掉中文字之间的空格
                    _t = re.sub(r'(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])', '', _t)
                    out.append({"no": int(m.group(2)), "title": _t,
                                "caption": last_cap, "header": header})
                last_cap = ""
    except Exception as e:
        logger.info(f"[模板表] 第四章表格清单读取失败 {template_path}: {e}")
    _PLAN_CACHE[key] = out
    return out


_STYLE_PLAN_CACHE: Dict[str, List[dict]] = {}


def _dominant_run_style(tbl) -> Dict[str, Any]:
    """表格内最多数的（中文字体, 字号, 加粗）。"""
    from docx.oxml.ns import qn
    from collections import Counter
    fonts, sizes, bolds = Counter(), Counter(), Counter()
    for r in tbl.iter(qn('w:r')):
        texts = "".join(t.text or "" for t in r.iter(qn('w:t')))
        if not texts.strip():
            continue
        rpr = r.find(qn('w:rPr'))
        rf = rpr.find(qn('w:rFonts')) if rpr is not None else None
        ea = rf.get(qn('w:eastAsia')) if rf is not None else None
        sz = rpr.find(qn('w:sz')) if rpr is not None else None
        b = rpr.find(qn('w:b')) if rpr is not None else None
        if ea:
            fonts[ea] += 1
        if sz is not None:
            try:
                sizes[int(sz.get(qn('w:val'))) / 2.0] += 1
            except (TypeError, ValueError):
                pass
        if b is not None:
            bolds[str(b.get(qn('w:val')) or "1") not in ("0", "false")] += 1
    return {
        "font": fonts.most_common(1)[0][0] if fonts else "",
        "size": sizes.most_common(1)[0][0] if sizes else None,
        "bold": bolds.most_common(1)[0][0] if bolds else None,
    }


def chapter_table_styles(template_path: str, chapter_no: int) -> List[dict]:
    """模板某章每张表的表号/表题/表头 + 实际字体字号。"""
    key = f"{template_path}#{chapter_no}"
    if key in _STYLE_PLAN_CACHE:
        return _STYLE_PLAN_CACHE[key]
    plan = chapter_table_plan(template_path, chapter_no)
    out: List[dict] = []
    if plan:
        try:
            from docx import Document
            from docx.table import Table
            from docx.oxml.ns import qn
            doc = Document(str(template_path))
            idx = 0
            cur = 0
            for el in doc.element.body.iterchildren():
                if el.tag == qn('w:p'):
                    t = "".join(n.text or "" for n in el.iter(qn('w:t'))).strip()
                    m = re.match(r'^(\d{1,2})\s+\S', t) if False else None
                    import re as _re
                    m = _re.match(r'^(\d{1,2})\s+\S', t)
                    if m and len(t) <= 50:
                        cur = int(m.group(1))
                elif el.tag == qn('w:tbl') and cur == int(chapter_no):
                    if idx < len(plan):
                        tb = Table(el, doc)
                        style = _dominant_run_style(el)
                        out.append({**plan[idx], **style})
                        idx += 1
        except Exception as e:
            logger.info(f"[模板表] 取表样式失败 {template_path}: {e}")
    _STYLE_PLAN_CACHE[key] = out
    return out
