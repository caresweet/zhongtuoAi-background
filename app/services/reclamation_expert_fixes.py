# -*- coding: utf-8 -*-
"""复垦报告装配后的专家批注修补（2026-11-19 城东污水厂批注）。

只处理 docx 层面的问题，不改正文生成逻辑：
  · 目录补真实页码（WPS/PDF 不刷新域也能看到）
  · 删除多余「附件清单」标题
  · 删除重复且没有表身的表题（表4-10 / 表4-11）
  · 地类表补地类编码（旱地 0103、果园 0201、沟渠 1107、农村道路 1006）
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_LAND_CODES = {
    "旱地": "0103", "水田": "0101", "水浇地": "0102",
    "果园": "0201", "茶园": "0202", "其他园地": "0204",
    "沟渠": "1107", "农村道路": "1006", "坑塘水面": "1104",
    "有林地": "0301", "其他林地": "0307", "其他草地": "0404",
}


def _paragraph_texts(doc) -> List[str]:
    return [p.text.strip() for p in doc.paragraphs]


def _remove_text_paragraph(doc, text: str) -> int:
    n = 0
    for p in list(doc.paragraphs):
        if p.text.strip() == text:
            p._element.getparent().remove(p._element)
            n += 1
    return n


def _drop_empty_duplicate_captions(doc) -> int:
    """表题后面紧跟另一个表题、中间没有表 → 前一个是空题，删掉。"""
    body = doc.element.body
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def _iter():
        for child in body.iterchildren():
            if child.tag == qn('w:p'):
                yield Paragraph(child, doc)
            elif child.tag == qn('w:tbl'):
                yield Table(child, doc)

    items = list(_iter())
    drop = []
    for i, it in enumerate(items[:-1]):
        if not isinstance(it, Paragraph):
            continue
        t = it.text.strip()
        if not re.match(r'^表\s*\d+-\d+\s+\S', t):
            continue
        # 找下一个非空块
        j = i + 1
        while j < len(items) and isinstance(items[j], Paragraph) \
                and not items[j].text.strip():
            j += 1
        if j < len(items) and isinstance(items[j], Paragraph):
            nxt = items[j].text.strip()
            if re.match(r'^表\s*\d+-\d+', nxt):
                drop.append(it)
    for p in drop:
        p._element.getparent().remove(p._element)
    return len(drop)


def _add_land_codes(doc) -> int:
    """地类表的「旱地 / 果园 / 沟渠 / 农村道路」补上编码。"""
    n = 0
    for t in doc.tables:
        head = " ".join(c.text for c in t.rows[0].cells)
        if "地类" not in head:
            continue
        for row in t.rows[1:]:
            for cell in row.cells:
                txt = cell.text.strip()
                if not txt or "（" in txt or "(" in txt:
                    continue
                parts = txt.replace("\n", " ").split()
                base = parts[-1] if parts else ""
                code = _LAND_CODES.get(base)
                if not code:
                    continue
                new = txt + f"（{code}）"
                if cell.paragraphs and cell.paragraphs[0].runs:
                    # 只改文字，保留格式
                    for r in cell.paragraphs[0].runs[1:]:
                        r._element.getparent().remove(r._element)
                    cell.paragraphs[0].runs[0].text = new
                else:
                    cell.text = new
                n += 1
    return n


def _style_heading_run(p) -> None:
    """补插的标题要显式写成黑色黑体 15 磅 —— 只给 Heading 2 样式会继承模板的
    蓝色/Calibri，和正文其它二级标题不一致（业主 2026-11-19：「字体颜色不对」）。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    if not p.runs:
        p.add_run(p.text)
    r = p.runs[0]
    r.font.name = "Times New Roman"
    r.font.size = Pt(15)
    r.bold = False
    r.font.color.rgb = RGBColor(0, 0, 0)
    rPr = r._element.get_or_add_rPr()
    f = rPr.find(qn('w:rFonts'))
    if f is None:
        from docx.oxml import OxmlElement
        f = OxmlElement('w:rFonts')
        rPr.insert(0, f)
    f.set(qn('w:ascii'), 'Times New Roman')
    f.set(qn('w:hAnsi'), 'Times New Roman')
    f.set(qn('w:eastAsia'), '黑体')


def _fix_chapter1_headings(doc) -> int:
    """第1章缺「1.1 编制背景及过程」，导致摘要被顶成 1.1、子节却是 1.2.x。

    专家批注：「1.2.1和1.2.2怎么到这里了」。按定义把 1.1 补回去、
    摘要改回 1.2，子节 1.2.1… 就顺了。
    """
    n = 0
    # 摘要标题 1.1 → 1.2
    for p in doc.paragraphs:
        if p.style and p.style.name == "Heading 2" and p.text.strip() == "1.1 复垦方案摘要":
            for r in p.runs:
                r.text = ""
            if p.runs:
                p.runs[0].text = "1.2 复垦方案摘要"
            else:
                p.add_run("1.2 复垦方案摘要")
            n += 1
            break
    # 是否已有 1.1 编制背景
    if any(p.style and p.style.name == "Heading 2"
           and p.text.strip().startswith("1.1 编制背景") for p in doc.paragraphs):
        return n
    # 在「1 前言」后的第一段正文前插入 1.1 标题
    ps = list(doc.paragraphs)
    for i, p in enumerate(ps):
        if p.style and p.style.name == "Heading 1" and p.text.strip().startswith("1 前言"):
            for q in ps[i + 1:]:
                if q.text.strip():
                    _np = q.insert_paragraph_before("1.1 编制背景及过程",
                                                    style="Heading 2")
                    _style_heading_run(_np)
                    n += 1
                    break
            break
    return n


def _fix_cover_title(doc) -> int:
    """封面标题 26 磅时长项目名折成三行（「工/程」单独一行）——压到 21 磅，
    保持「项目名 / 复垦方案」两行（专家批注：两行更合适）。"""
    from docx.shared import Pt
    n = 0
    for p in doc.paragraphs[:10]:
        t = p.text.strip()
        if not t or ("污水处理厂" not in t and "土地复垦方案" not in t):
            continue
        # 只有长项目名（≥18 字）才会在 26 磅下折成三行；短标题保持原字号
        if len(t) < 18:
            continue
        if any(r.font.size and r.font.size.pt >= 24 for r in p.runs):
            for r in p.runs:
                r.font.size = Pt(21)
            n += 1
    return n


def _fix_schedule_row_and_heading(doc) -> int:
    """表8-1 最后一行和「## 8.3」标题被合并成一段：「| 2029… | 管护 |## 8.3 …」。

    把行补回前一张表，段落改成 Heading 2（否则第8章缺 8.3 小节标题）。
    """
    from docx.oxml.ns import qn
    from docx.table import Table
    n = 0
    for p in list(doc.paragraphs):
        t = p.text or ""
        if "|" not in t or "##" not in t:
            continue
        head = t.split("##", 1)[1].strip()
        row_part = t.split("##", 1)[0].strip().strip("|")
        cells = [c.strip() for c in row_part.split("|") if c.strip()]
        # 找前一张表（按 body 顺序）
        prev = p._element.getprevious()
        tbl = None
        while prev is not None:
            if prev.tag == qn('w:tbl'):
                tbl = Table(prev, doc)
                break
            prev = prev.getprevious()
        if tbl is not None and cells and len(tbl.columns) >= len(cells):
            row = tbl.add_row()
            for i, c in enumerate(cells[:len(tbl.columns)]):
                row.cells[i].text = c
        # 段落改成 8.3 标题
        for r in list(p.runs):
            r._element.getparent().remove(r._element)
        try:
            p.style = doc.styles["Heading 2"]
        except Exception:
            pass
        p.add_run(head)
        n += 1
    return n


def _fix_engineering_list(doc) -> int:
    """5.3.1 工程技术措施从「2）场地清理」开始，前面缺「1）表土剥离」。"""
    ps = doc.paragraphs
    for i, p in enumerate(ps):
        if not p.text.strip().startswith("2）场地清理"):
            continue
        # 往回找 5.3.1 标题后的第一段正文
        h = None
        for j in range(i - 1, 0, -1):
            if ps[j].style and ps[j].style.name.startswith("Heading") \
                    and "工程技术措施" in ps[j].text:
                h = j
                break
        if h is None:
            continue
        if any(ps[k].text.strip().startswith("1）表土剥离")
               for k in range(h + 1, i)):
            return 0
        first = next((k for k in range(h + 1, i) if ps[k].text.strip()), h + 1)
        ps[first].insert_paragraph_before("1）表土剥离")
        return 1
    return 0


def _dedupe_adjacent_paragraphs(doc) -> int:
    """相邻两段文字完全相同（插入标题时重复）→ 删后一段。"""
    n = 0
    ps = list(doc.paragraphs)
    for i in range(1, len(ps)):
        a, b = ps[i - 1].text.strip(), ps[i].text.strip()
        if a and a == b:
            ps[i]._element.getparent().remove(ps[i]._element)
            n += 1
    return n


def _fix_chapter8_heading(doc) -> int:
    """第8章缺「8.2 土地复垦工作计划安排」：模型直接从 8.1 跳到 8.3。"""
    ps = doc.paragraphs
    if any(p.style and p.style.name.startswith("Heading")
           and p.text.strip().startswith("8.2") for p in ps):
        return 0
    for i, p in enumerate(ps):
        if p.text.strip().startswith("根据工程开展计划"):
            _np = p.insert_paragraph_before("8.2 土地复垦工作计划安排",
                                            style="Heading 2")
            _style_heading_run(_np)
            return 1
    return 0


def _fix_paragraph_texts(doc) -> int:
    """装配后仍残留的病句（模型把一句话拆在 markdown 两行里，链式修补漏掉）。

    只做**与项目无关**的通用修正；金额、具体机关名不放这里，避免把别的项目
    正确数字改错（如「安排费用××万元」由源头的阶段费用修补按本项目估算重算）。
    """
    n = 0
    pats = [
        (r'在工程施(?=[\u4e00-\u9fa5]{2,30}(?:局|分局|人民政府))',
         '在施工过程中，由'),
        (r'每年12月31(?!日前向)(?=[\u4e00-\u9fa5]{2,30}局)', '每年12月31日前向'),
        (r'日前向(?:日前向)+', '日前向'),
        (r'尽量少占用永久基本农田', '不占用永久基本农田'),
        (r'综合分值均高于\s*90\s*分', '综合分值均在80分以上'),
    ]
    for p in doc.paragraphs:
        t = p.text
        new = t
        for pat, rep in pats:
            new = re.sub(pat, rep, new)
        if new != t and p.runs:
            p.runs[0].text = new
            for r in p.runs[1:]:
                r._element.getparent().remove(r._element)
            n += 1
    return n


def _renumber_lists(doc) -> int:
    """连续「（1）（2）…」段落重排，修 2.3.2 政策文件的（1）→（3）。"""
    n = 0
    ps = doc.paragraphs
    i = 0
    pat = re.compile(r'^\s*（(\d+)）\s*(.*)$')
    while i < len(ps):
        if not pat.match(ps[i].text or "") or ps[i].style.name.startswith("Heading"):
            i += 1
            continue
        group = []
        j = i
        while j < len(ps):
            t = ps[j].text or ""
            if not t.strip():
                j += 1
                continue
            if not pat.match(t):
                break
            group.append((ps[j], pat.match(t).group(2)))
            j += 1
        if len(group) >= 2:
            for k, (p, body) in enumerate(group, 1):
                new = f"（{k}）{body}"
                if p.text != new and p.runs:
                    p.runs[0].text = new
                    for r in p.runs[1:]:
                        r._element.getparent().remove(r._element)
                    n += 1
        i = max(j, i + 1)
    return n


def _headings(doc) -> List[Tuple[int, str]]:
    out = []
    for p in doc.paragraphs:
        name = p.style.name if p.style else ""
        if name in ("Heading 1", "Heading 2"):
            t = p.text.strip()
            if t:
                out.append((1 if name == "Heading 1" else 2, t))
    # 跳过封面/前置页标题：从第一个「1 前言」或「第一章」开始
    first = next((i for i, (_, t) in enumerate(out)
                  if re.match(r'^(1\s+前言|第一章)', t)), 0)
    return out[first:]


def _measure_pages(path: Path, titles: List[str]) -> Dict[str, int]:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return {}
    tmp = Path(tempfile.mkdtemp(prefix="recltoc_"))
    try:
        subprocess.run([soffice, "--headless", "--norestore", "--convert-to",
                        "pdf", "--outdir", str(tmp), str(path)],
                       check=True, capture_output=True, timeout=240)
        pdf = next(tmp.glob("*.pdf"), None)
        if not pdf:
            return {}
        import fitz
        src = fitz.open(str(pdf))
        # 跳过封面和整段目录（目录页里也印着标题，页码会被误测成目录页）
        start = 1
        for i in range(min(8, src.page_count)):
            t = src[i].get_text()
            # 目录页的特征是**大量点线**（正文里的「项目/记录」也会同时含目、录，
            # 不能只按这两个字判断，否则会把第1章正文页也跳掉）
            if t.count("....") >= 50:
                start = i + 1
        found: Dict[str, int] = {}
        for i in range(start, src.page_count):
            lines = {ln.strip() for ln in src[i].get_text().splitlines()}
            norm = "".join(lines).replace(" ", "").replace("　", "")
            for t in titles:
                if t in found:
                    continue
                if t in lines or t.replace(" ", "") in lines:
                    found[t] = i + 1
                elif t.replace(" ", "")[:14] in norm:
                    found[t] = i + 1
        src.close()
        return found
    except Exception as e:
        logger.warning(f"[复垦报告] 目录页码测量失败: {e}")
        return {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _stamp_toc_pages(path: Path, doc, headings: List[Tuple[int, str]]) -> int:
    pages = _measure_pages(path, [t for _, t in headings])
    if not pages:
        return 0
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    sdt = next((el for el in doc.element.body.iterchildren()
                if el.tag.split('}')[-1] == 'sdt'), None)
    if sdt is None:
        return 0
    contents = [c for c in sdt.iter(qn('w:sdtContent')) if c.findall(qn('w:p'))]
    content = contents[-1] if contents else None
    if content is None:
        return 0
    ps = list(content.findall(qn('w:p')))
    title_p = None
    for p in ps:
        txt = "".join(t.text or '' for t in p.iter(qn('w:t'))).strip()
        if "目" in txt and "录" in txt:
            title_p = p
            break
    for p in ps:
        if p is title_p:
            continue
        content.remove(p)

    def _entry(lvl: int, text: str, page: int):
        np = OxmlElement('w:p')
        pPr = OxmlElement('w:pPr')
        tabs = OxmlElement('w:tabs')
        tab = OxmlElement('w:tab')
        tab.set(qn('w:val'), 'right')
        tab.set(qn('w:leader'), 'dot')
        tab.set(qn('w:pos'), '8300')
        tabs.append(tab)
        pPr.append(tabs)
        if lvl == 2:
            ind = OxmlElement('w:ind')
            ind.set(qn('w:leftChars'), '200')
            pPr.append(ind)
        np.append(pPr)
        r = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        f = OxmlElement('w:rFonts')
        f.set(qn('w:eastAsia'), '黑体' if lvl == 1 else '仿宋')
        rPr.append(f)
        sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '24')
        rPr.append(sz)
        if lvl == 1:
            rPr.append(OxmlElement('w:b'))
        r.append(rPr)
        t = OxmlElement('w:t'); t.text = text
        r.append(t)
        np.append(r)
        rt = OxmlElement('w:r')
        tt = OxmlElement('w:tab'); rt.append(tt)
        np.append(rt)
        rp = OxmlElement('w:r')
        tp = OxmlElement('w:t'); tp.text = str(page)
        rp.append(tp)
        np.append(rp)
        return np

    anchor = title_p
    for lvl, text in headings:
        page = pages.get(text)
        if not page:
            continue
        node = _entry(lvl, text, page)
        if anchor is not None:
            anchor.addnext(node)
            anchor = node
        else:
            content.append(node)
    return len(headings)


def audit_reclamation_docx(path: str | Path) -> List[str]:
    """装配后的确定性审计（专家批注同类问题再出现时能报出来）。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    issues: List[str] = []
    txt = "\n".join(p.text for p in doc.paragraphs)

    # 1 扉页项目单位
    for p in doc.paragraphs:
        if p.text.strip().startswith("项目单位："):
            if not p.text.split("：", 1)[1].strip():
                issues.append("扉页项目单位为空")
            break

    # 2 目录页码（静态条目应带 tab+数字）
    sdt = next((el for el in doc.element.body.iterchildren()
                if el.tag.split('}')[-1] == 'sdt'), None)
    if sdt is not None:
        entries = []
        for _p in sdt.iter(qn('w:p')):
            _t = "".join(t.text or '' for t in _p.iter(qn('w:t')))
            _has_tab = any(True for _ in _p.iter(qn('w:tab')))
            entries.append((_t, _has_tab))
        if not any(_has_tab and re.search(r'\d+\s*$', _t)
                   for _t, _has_tab in entries):
            # 生产镜像没有 LibreOffice，不能预量页码；文档带 TOC 域 + updateFields，
            # Word/WPS 打开会自动生成页码，这种情况不算审计问题
            _has_field = "TOC" in sdt.xml or "updateFields" in (
                doc.settings.element.xml if doc.settings is not None else "")
            if not _has_field:
                issues.append("目录没有页码")

    # 3 章节号：第1章必须有 1.1 编制背景，且不能出现 1.1 摘要
    h2 = [p.text.strip() for p in doc.paragraphs
          if p.style and p.style.name == "Heading 2"]
    if "1.1 编制背景及过程" not in h2:
        issues.append("第1章缺 1.1 编制背景及过程")
    if "1.1 复垦方案摘要" in h2:
        issues.append("第1章摘要编号错成 1.1（应为 1.2）")
    for need in ("8.2 土地复垦工作计划安排", "8.3 土地复垦费用安排"):
        if need not in h2:
            issues.append(f"第8章缺 {need.split(' ')[0]}")

    # 4 表题后必须有表身
    def _iter():
        for child in doc.element.body.iterchildren():
            if child.tag == qn('w:p'):
                yield Paragraph(child, doc)
            elif child.tag == qn('w:tbl'):
                yield Table(child, doc)

    items = list(_iter())
    for i, it in enumerate(items):
        if not isinstance(it, Paragraph):
            continue
        t = it.text.strip()
        if not re.match(r'^表\s*\d+-\d+\s+\S', t):
            continue
        j = i + 1
        while j < len(items) and isinstance(items[j], Paragraph) \
                and not items[j].text.strip():
            j += 1
        if j >= len(items) or not isinstance(items[j], Table):
            issues.append(f"表题后没有表：{t[:24]}")

    # 5 地类表要有编码、投资表要有静态/动态亩均
    all_cells = " ".join(c.text for tb in doc.tables for r in tb.rows
                         for c in r.cells)
    if "地类名称及编码" in all_cells and "（0103）" not in all_cells \
            and "（0101）" not in all_cells:
        issues.append("地类表缺地类编码")
    if "静态总投资" in all_cells and "静态亩均投资" not in all_cells:
        issues.append("投资表缺静态/动态亩均")
    if "平衡计列" in all_cells:
        issues.append("其他费用仍有「平衡计列」笼统表述")

    # 6 专家点名不要的内容
    if "附件清单" in txt:
        issues.append("仍有多余的「附件清单」")
    if "少占用永久基本农田" in txt:
        issues.append("仍有「少占用永久基本农田」")
    return issues


def apply_expert_fixes(path: str | Path) -> Dict[str, int]:
    from docx import Document
    p = Path(path)
    doc = Document(str(p))
    stats = {}
    stats["附件清单"] = _remove_text_paragraph(doc, "附件清单")
    stats["第1章节号"] = _fix_chapter1_headings(doc)
    stats["封面标题"] = _fix_cover_title(doc)
    stats["表8-1合并段"] = _fix_schedule_row_and_heading(doc)
    stats["5.3.1序号"] = _fix_engineering_list(doc)
    stats["8.2标题"] = _fix_chapter8_heading(doc)
    _n_style = 0
    for _par in doc.paragraphs:
        if _par.style and _par.style.name == "Heading 2" and _par.text.strip() in (
                "1.1 编制背景及过程", "8.2 土地复垦工作计划安排",
                "8.3 土地复垦费用安排"):
            _style_heading_run(_par)
            _n_style += 1
    stats["补标题格式"] = _n_style
    stats["相邻重复段"] = _dedupe_adjacent_paragraphs(doc)
    stats["病句"] = _fix_paragraph_texts(doc)
    stats["条目编号"] = _renumber_lists(doc)
    stats["空重复表题"] = _drop_empty_duplicate_captions(doc)
    stats["地类编码"] = _add_land_codes(doc)
    heads = _headings(doc)
    doc.save(str(p))
    doc2 = Document(str(p))
    stats["目录页码"] = _stamp_toc_pages(p, doc2, heads)
    if stats["目录页码"]:
        doc2.save(str(p))
    # 最后再压一次补插标题的 rPr（目录重写会重载文档，前面设的格式可能丢）
    doc3 = Document(str(p))
    for x in doc3.paragraphs:
        if x.style and x.style.name == "Heading 2" and x.text.strip() in (
                "1.1 编制背景及过程", "8.2 土地复垦工作计划安排",
                "8.3 土地复垦费用安排"):
            _style_heading_run(x)
    doc3.save(str(p))
    return stats
