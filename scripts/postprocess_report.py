# -*- coding: utf-8 -*-
"""给已生成的稳评报告补两样东西（不改正文、不重跑 LLM）：

1. 模板里没有附件章的（淮阴区/洪泽/清江浦区），在文末补“附件”章 + 留白两页；
2. 用 LibreOffice 量分页，回填目录页码（`stamp_toc_pages`）。

用法：
    python scripts/postprocess_report.py <docx> [<docx> ...]
容器内需有 soffice（Dockerfile.prod 已装 libreoffice-writer）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _cn_num(n: int) -> str:
    from app.services.template_structure import _cn_num as f
    return f(n)


def _tail_blank_pages(d, n: int = 2) -> bool:
    """文末是否已有 n 个分页符（附件留白）。"""
    from docx.oxml.ns import qn
    tail = d.paragraphs[-8:]
    brk = 0
    for p in tail:
        brk += sum(1 for r in p._p.findall('.//' + qn('w:br'))
                   if r.get(qn('w:type')) == 'page')
        brk += len(p._p.findall('.//' + qn('w:lastRenderedPageBreak')))
    return brk >= n


def ensure_attachment(path: str | Path) -> bool:
    """没有附件章就补一章（标题体例沿用前一章）+ 附件清单 + 两页留白；
    有附件章但文末没有留白页的，补两页。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    d = Document(str(path))
    paras = d.paragraphs
    has_att = any((p.text or "").strip().startswith("附件") for p in paras[-20:])
    if has_att:
        if _tail_blank_pages(d):
            return False
        for _ in range(2):
            d.add_page_break()
            d.add_paragraph("")
        d.save(str(path))
        return True
    heads = [(i, p) for i, p in enumerate(paras)
             if p.style and p.style.name == "Heading 1" and p.text.strip()]
    # 只数「正文章」（第X章 / N 标题）；前置评审表不算号
    import re as _re
    numbered = [p for _, p in heads
                if _re.match(r'^第[零一二三四五六七八九十]+章', p.text.strip())
                or _re.match(r'^\d{1,2}\s+\S', p.text.strip())]
    n = len(numbered) + 1
    sample = heads[-1][1] if heads else None
    title = f"第{_cn_num(n)}章、附件"

    p = d.add_paragraph()
    p.style = d.styles["Heading 1"] if "Heading 1" in [s.name for s in d.styles] else p.style
    if sample is not None:
        p.alignment = sample.alignment
        p.paragraph_format.space_before = sample.paragraph_format.space_before
        p.paragraph_format.space_after = sample.paragraph_format.space_after
    r = p.add_run(title)
    if sample is not None and sample.runs:
        sr = sample.runs[0]
        r.font.size = sr.font.size or Pt(22)
        r.font.bold = True
        if sr.font.name:
            r.font.name = sr.font.name
            r._element.get_or_add_rPr().get_or_add_rFonts().set(
                qn('w:eastAsia'), sr.font.name)

    cp = d.add_paragraph()
    cp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    cr = cp.add_run("附件清单")
    cr.font.name = "宋体"
    cr._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), "宋体")
    cr.font.size = Pt(14)
    cr.bold = True

    for _ in range(2):
        d.add_page_break()
        d.add_paragraph("")
    d.save(str(path))
    return True


def main() -> None:
    from app.services.template_structure import stamp_toc_pages
    for p in sys.argv[1:]:
        added = ensure_attachment(p)
        n = stamp_toc_pages(p)
        print(f"{Path(p).name}: 附件{'已补' if added else '已有/无需'}，目录页码 {n} 条")


if __name__ == "__main__":
    main()
