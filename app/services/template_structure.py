# -*- coding: utf-8 -*-
"""按地区模板的章节结构出报告 —— 解析模板章标题，映射到系统章节类型。

业主口径（2026-11-20）：「每份报告肯定跟着他所在地区的模板走」。
各区模板章数/顺序/标题都不一样（洪泽 12 章、淮阴 10 章、淮安 14 章…），
生成内容仍按系统 14 类章节写，但**装配时的章节标题、顺序、取舍**以模板为准。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 模板章标题 → 系统章节类型（对应 chapter_definitions 的 1..14 章）
_KIND_RULES = (
    ("评审", "review"),
    ("编制说明", "intro"),
    ("编制依据", "basis"),
    ("工作报告", "work_report"),
    ("应急预案", "emergency"),
    ("工作预案", "emergency"),
    ("突发", "emergency"),
    ("不稳定因素", "emergency"),
    ("基本情况", "overview"),
    ("项目概况", "overview"),
    ("基本概况", "overview"),
    ("概况", "overview"),
    ("工作方案", "plan"),
    ("方法与过程", "method"),
    ("评估过程", "method"),
    ("过程、方法", "method"),
    ("过程和方法", "method"),
    ("风险评估主体", "method"),
    ("各利益相关者", "stakeholders"),
    ("利益相关者", "stakeholders"),
    ("合法性", "four_nature"),
    ("风险源", "risk_source"),
    ("风险因素调查", "risk_source"),
    ("风险因素识别", "risk_source"),
    ("综合分析", "four_nature"),
    ("风险等级", "risk_level"),
    ("研判", "risk_level"),
    ("结论", "risk_level"),
    ("建议", "measures"),
    ("防范", "measures"),
    ("附件", "attachment"),
)
_KIND_TO_SYS = {
    "intro": 1, "basis": 2, "review": 3, "work_report": 4, "emergency": 5,
    "overview": 6, "plan": 7, "method": 8, "stakeholders": 9,
    "four_nature": 10, "risk_source": 11, "risk_level": 12,
    "measures": 13, "attachment": 14,
}

_CN = "零一二三四五六七八九十"


@dataclass
class Chapter:
    idx: int                 # 模板里的顺序（1 起）
    title: str               # 模板原文标题（不含“第X章”）
    kind: str                # 规范类型
    sys_no: Optional[int]    # 对应系统章节号
    numbered: bool = True    # 模板是否用「第X章」
    pre: bool = False        # 位于第一个编号章之前（如评审表）
    # 版式（按模板实测，2026-11-23）：
    #   own_page=True  章标题独占一页，正文从下一页开始；
    #   own_page=False 章标题与正文同页（标题只是该页第一行）。
    #   new_page=False 该章不另起新页（少见）。
    own_page: bool = True
    new_page: bool = True


@dataclass
class Plan:
    chapters: List[Chapter] = field(default_factory=list)
    review_in_front: bool = False
    source: str = ""
    # 前置件写法（各模板不同）：工作组名单标题、岗位分组名、证书节标题
    front: Dict[str, Any] = field(default_factory=dict)

    def by_sys(self) -> Dict[int, Chapter]:
        return {c.sys_no: c for c in self.chapters if c.sys_no}


def _norm(t: str) -> str:
    return re.sub(r'\s+', '', t or "")


def _kind(title: str) -> str:
    t = _norm(title)
    for key, kind in _KIND_RULES:
        if key in t:
            return kind
    return ""


def _cn_to_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _CN.index(s[1]) if len(s) > 1 and s[1] in _CN else 10
    if "十" in s:
        a, _, b = s.partition("十")
        return _CN.index(a) * 10 + (_CN.index(b) if b and b in _CN else 0)
    return _CN.index(s) if s in _CN else 0


_RE_CN_HEAD = re.compile(r'^第([零一二三四五六七八九十]+)章[、．.:：\s]*(.+)$')
_RE_NUM_HEAD = re.compile(r'^(\d{1,2})\s*[、．.]?\s*([一-龥].{1,40})$')
_RE_TOC = re.compile(
    r'^(第[零一二三四五六七八九十]+章|\d{1,2})\s*[、．.]?\s*(.+?)'
    r'[\s\.·…]*?(\d{1,3})\s*$')


def _chapter_from_text(t: str, *, numbered_ok=True):
    """一行文字 → (no, title, numbered)；不是章标题返回 None。"""
    t = re.sub(r'\s+', '', t or "")
    m = _RE_CN_HEAD.match(t)
    if m:
        title = m.group(2).strip(" 、．.:：")
        if 2 <= len(title) <= 40 and not re.search(r'[。；，]$', title):
            return _cn_to_int(m.group(1)), title, True
    if numbered_ok:
        m = _RE_NUM_HEAD.match(t)
        if m:
            title = m.group(2).strip(" 、．.:：")
            if 2 <= len(title) <= 40 and not re.search(r'[。；，]$', title):
                return int(m.group(1)), title, False
    return None


def _from_toc_style(doc) -> List[Any]:
    """Word 目录一级条目（style=toc 1）：标题 + tab + 页码。"""
    out = []
    for p in doc.paragraphs:
        sn = (p.style.name or "").lower()
        if sn not in ("toc 1", "toc1"):
            continue
        t = (p.text or "").strip()
        if not t or t in ("目录", "目 录"):
            continue
        t = re.sub(r'\s*\t\s*\d{1,3}\s*$', '', t)   # 去掉 tab+页码
        t = re.sub(r'[\s.·…]+\d{1,3}\s*$', '', t)
        got = _chapter_from_text(t, numbered_ok=True)
        if not got:
            # 「淮安市社会稳定风险评估评审表」这类没有编号的
            title = re.sub(r'[\s.·…]+\d{1,3}\s*$', '', t)
            if 2 <= len(title) <= 40 and _kind(title):
                out.append(("", title))
            continue
        no, title, _numbered = got
        out.append((str(no), title))
    return out


def _from_toc(doc) -> List[Any]:
    """先读模板自己的目录（「标题……页码」），最可靠。"""
    out = []
    for p in doc.paragraphs[:400]:
        t = (p.text or "").strip()
        if not t or len(t) > 60:
            continue
        m = _RE_TOC.match(t)
        if not m:
            continue
        title = m.group(2).strip(" .·…、")
        if not (2 <= len(title) <= 40) or re.search(r'[。；]$', title):
            continue
        if title not in [x[1] for x in out]:
            out.append((m.group(1), title))
    return out


def _from_headings(doc) -> List[Any]:
    out = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        sn = p.style.name if p.style else ""
        is_h1 = sn == "Heading 1"
        # 数字标题只认 Heading 1（正文里的「1.项目负责人」不算）
        got = _chapter_from_text(t, numbered_ok=is_h1)
        if not got:
            continue
        no, title, numbered = got
        if not numbered and not is_h1:
            continue
        if title not in [x[1] for x in out]:
            out.append((f"第{no}章" if numbered else str(no), title))
    return out


def _from_cn_text(doc) -> List[Any]:
    """没有标题样式时，只认正文里独立成段的「第X章 标题」。"""
    ps = list(doc.paragraphs)
    out = []
    for i, p in enumerate(ps):
        t = (p.text or "").strip()
        if not t or len(t) > 45:
            continue
        got = _chapter_from_text(t, numbered_ok=False)
        if not got:
            continue
        no, title, _ = got
        # 标题被排版拆成两段：「第十一章 突发性事件…处置」+「应对工作预案」
        j = i + 1
        while j < len(ps) and not (ps[j].text or "").strip():
            j += 1
        if j < len(ps):
            nxt = ps[j].text.strip()
            if (0 < len(nxt) <= 14 and not re.match(r'^第.+章', nxt)
                    and re.match(r'^(应对|工作预案|预案)', nxt)):
                title = title + nxt
        if title not in [x[1] for x in out]:
            out.append((f"第{no}章", title))
    return out


def _p_page_breaks(p) -> int:
    """段落里分页符（`w:br type=page`）个数。"""
    from docx.oxml.ns import qn
    return sum(1 for b in p._p.findall('.//' + qn('w:br'))
               if (b.get(qn('w:type')) or '') == 'page')


def _p_page_break_before(p) -> bool:
    from docx.oxml.ns import qn
    return p._p.find(qn('w:pPr') + '/' + qn('w:pageBreakBefore')) is not None


def _p_has_sectpr(p) -> bool:
    from docx.oxml.ns import qn
    return p._p.find(qn('w:pPr') + '/' + qn('w:sectPr')) is not None


def _leading_page_break(p) -> bool:
    """段落内**文字之前**有分页符（模板常用它把章标题顶到新页）。"""
    from docx.oxml.ns import qn
    seen_text = False
    for el in p._p.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 't' and (el.text or '').strip():
            seen_text = True
        elif (tag == 'br' and not seen_text
              and (el.get(qn('w:type')) or '') == 'page'):
            return True
    return False


def _trailing_page_break(p) -> bool:
    """段落内**文字之后**有分页符（标题后直接跳页 → 正文在下一页）。"""
    from docx.oxml.ns import qn
    seen_text = False
    for el in p._p.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 't' and (el.text or '').strip():
            seen_text = True
        elif (tag == 'br' and seen_text
              and (el.get(qn('w:type')) or '') == 'page'):
            return True
    return False


def _annotate_layout(doc, plan: Plan) -> None:
    """按模板实测填 `Chapter.own_page/new_page`。

    各地模板的章标题版式并不统一（2026-11-23 实测）：
      · 洪泽/淮阴区/清江浦区/淮安区：章标题 `pageBreakBefore`，多数章正文
        另起一页（标题独占一页），但个别章（洪泽第四章、淮阴第三/十章）
        正文紧跟在标题下面 —— 不能一刀切；
      · 金湖/淮安市：标题段首放分页符，标题只是新页第一行，正文同页。
    """
    ps = list(doc.paragraphs)
    texts = [_norm(p.text or '') for p in ps]
    styles = [((p.style.name or '') if p.style else '').lower() for p in ps]

    for ch in plan.chapters:
        if ch.kind in ('review', 'attachment'):
            ch.own_page = True
            ch.new_page = True
            continue
        tgt = _norm(ch.title)
        if not tgt:
            continue
        idx = None
        for i, nt in enumerate(texts):
            if not nt or styles[i].startswith('toc'):
                continue
            if tgt not in nt or len(nt) > len(tgt) + 12:
                continue
            if not (nt.startswith('第') or re.match(r'^\d{1,2}', nt)
                    or styles[i].startswith('heading')):
                continue
            idx = i
            break
        if idx is None:
            continue
        head = ps[idx]
        # 是否另起新页：标题段自身（段前分页 / 段首分页符）或前一段带分页/分节
        ch.new_page = (_p_page_break_before(head) or _leading_page_break(head)
                       or _trailing_page_break(head))
        if not ch.new_page:
            j = idx - 1
            while j >= 0:
                if _p_page_breaks(ps[j]) or _p_has_sectpr(ps[j]):
                    ch.new_page = True
                    break
                if texts[j]:
                    break
                j -= 1
        # 是否独占一页：标题之后、第一段正文之前出现分页/分节
        own = _trailing_page_break(head)
        k = idx + 1
        while k < len(ps):
            q = ps[k]
            if _p_page_break_before(q) or _p_has_sectpr(q) or _p_page_breaks(q):
                own = True
            if texts[k]:
                break
            k += 1
        ch.own_page = own


_ROLE_STARTS = ("稳评负责人", "项目负责人", "负责人", "调研工作", "风险研判",
                "数据汇总", "资料与档案", "档案工作")


def _match_role_label(s: str) -> str:
    """一行文字 → 岗位名（去掉「1.」「1、」编号与「：职责说明」），不是岗位返回空。"""
    t = (s or "").strip()
    if "：" in t or ":" in t:
        t = re.split(r'[：:]', t, 1)[0]
    t = re.sub(r'^\d+\s*[、.．]?\s*', '', t).strip()
    if len(t) > 24 or not t:
        return ""
    if not t.startswith(_ROLE_STARTS):
        return ""
    if re.search(r'[。，,；;]', t):
        return ""
    return t


def _front_spec(doc) -> Dict[str, Any]:
    """前置件的模板写法：工作组名单标题 / 岗位分组名 / 证书节标题。

    六套模板并不统一（2026-11-23 实测）：
      · 洪泽/金湖/淮阴区：稳评工作组人员及分工情况，5 组；
      · 淮安区：4 组（无「数据汇总整理」），组名带「1 、」编号；
      · 淮安市：标题是「社会稳定风险评估报告项目组主要成员」，
        首组叫「1.项目负责人」，组名带「1.」编号；
      · 清江浦区：没有独立名单页（写在 6.8 人员组成里），回退默认五组。
    """
    ps = list(doc.paragraphs)
    texts = [(p.text or "").strip() for p in ps]
    out: Dict[str, Any] = {
        "roster_heading": "", "roster_groups": [], "cert_heading": "",
    }

    # ① 工作组名单：标题 + 岗位分组行
    for i, t in enumerate(texts):
        if not t or len(t) > 50:
            continue
        if ("工作组人员及分工" in t or "项目组主要成员" in t
                or ("人员组成" in t and "分工" in t)):
            out["roster_heading"] = t
            for j in range(i + 1, min(len(texts), i + 40)):
                tj = texts[j]
                if not tj:
                    continue
                if re.match(r'^第[零一二三四五六七八九十]+章', tj):
                    break
                if tj in ("公司简介",):
                    break
                # 收齐岗位后遇到下一个「6.8.2 工作要求」这类小节标题就停；
                # 前面的「6.8.1 人员组成」还不能停（分组行在它后面）
                if (out["roster_groups"]
                        and re.match(r'^\d+(\.\d+)+\s*\S', tj) and len(tj) < 60):
                    break
                if len(tj) > 120:
                    continue
                _label = _match_role_label(tj)
                if _label:
                    if "：" in tj or ":" in tj:
                        _label_text = re.split(r'[：:]', tj, 1)[0].strip()
                    else:
                        _label_text = tj.strip()
                    out["roster_groups"].append(_label_text)
            break

    # ② 证书节标题（人员证书/培训证书那一行）
    for t in texts[:800]:
        if (t and len(t) <= 30
                and ("人员证书" in t or "培训证书" in t)
                and not t.startswith(("1.", "2.", "3.", "4.", "5.", "6.",
                                      "7.", "8.", "9."))):
            out["cert_heading"] = t
            break
    if not out["cert_heading"]:
        for t in texts[:800]:
            if t and len(t) <= 30 and ("人员证书" in t or "培训证书" in t):
                out["cert_heading"] = t
                break
    return out


def extract_plan(path: str | Path) -> Plan:
    """解析模板 docx 的章标题 → Plan；解析不到返回空 Plan。"""
    from docx import Document
    try:
        doc = Document(str(path))
    except Exception as e:
        logger.warning(f"[模板结构] 打开失败 {path}: {e}")
        return Plan(source=str(path))

    # ① Heading 1（金湖/淮安市等规范模板）；② 正文里的「第X章」（洪泽/淮阴区
    # 转换稿没有标题样式）；③ 目录行（前两者都失败时）
    raw = _from_toc_style(doc)
    if len(raw) < 3:
        raw = _from_cn_text(doc)
    if len(raw) < 3:
        raw = _from_headings(doc)
    if len(raw) < 3:
        raw = _from_toc(doc)
    plan = Plan(source=str(path))
    seen = []
    for head, title in raw:
        kind = _kind(title)
        if not kind or title in seen:
            continue
        seen.append(title)
        numbered = str(head).startswith("第")
        plan.chapters.append(Chapter(
            idx=len(plan.chapters) + 1, title=title, kind=kind,
            sys_no=_KIND_TO_SYS.get(kind), numbered=numbered))

    if not plan.chapters:
        return plan

    # 评审表通常不是正文章（洪泽/清江浦区放在目录后、第一章前）
    if plan.chapters and plan.chapters[0].kind == "review":
        plan.chapters[0].pre = True
        plan.review_in_front = True

    # 淮阴区这类模板：评审表不在章目录里，而是第一章前的一张独立表页
    # （评审表常整张是表格，doc.paragraphs 读不到，要按 body 顺序扫表格）
    if not any(c.kind == "review" for c in plan.chapters):
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        title = ""
        for el in doc.element.body.iterchildren():
            if el.tag == qn('w:p'):
                p = Paragraph(el, doc)
                t = (p.text or "").strip()
                if p.style and p.style.name == "Heading 1" and t:
                    break            # 到第一章了
                if "评审" in t and "表" in t and 4 <= len(t) <= 40:
                    title = t
                    break
            elif el.tag == qn('w:tbl'):
                tb = Table(el, doc)
                first = " ".join(c.text for c in tb.rows[0].cells) if len(tb.rows) else ""
                head = (first + " " + (tb.rows[1].cells[0].text if len(tb.rows) > 1 else ""))[:60]
                if "评审" in head:
                    title = "社会稳定风险评估评审表"
                    break
        if title:
            plan.chapters.insert(0, Chapter(
                idx=0, title=title, kind="review", sys_no=3,
                numbered=False, pre=True))
            plan.review_in_front = True
    # 章标题版式（独占页 / 与正文同页）按模板实测标注
    try:
        _annotate_layout(doc, plan)
    except Exception as e:
        logger.warning(f"[模板结构] 版式识别失败: {e}")
    # 前置件写法（名单标题/岗位分组/证书节标题）
    try:
        plan.front = _front_spec(doc)
    except Exception as e:
        logger.warning(f"[模板结构] 前置件识别失败: {e}")
    return plan


def format_title(ch: Chapter, no: Optional[int] = None) -> str:
    """按模板体例写章标题：中文「第X章、」或阿拉伯「N 」。

    `no` 是**正文顺序号**（前置的评审表若在模板里不带编号，不占号）。
    """
    if (ch.pre or ch.kind == "attachment") and not ch.numbered:
        return ch.title
    n = no if no is not None else ch.idx
    if ch.numbered:
        return f"第{_cn_num(n)}章、{ch.title}"
    return f"{n} {ch.title}"


def _cn_num(n: int) -> str:
    if n <= 10:
        return _CN[n]
    if n < 20:
        return "十" + (_CN[n - 10] if n > 10 else "")
    return str(n)


# ═══════════════════════════════════════════════════════════════
# 装配后按模板重排章节
# ═══════════════════════════════════════════════════════════════
def _rename_heading(p, text: str) -> None:
    if p.runs:
        p.runs[0].text = text
        for r in p.runs[1:]:
            r._element.getparent().remove(r._element)
    else:
        p.add_run(text)


def _rebuild_toc(doc, start_text: str = "",
                 pages: Optional[Dict[str, int]] = None) -> int:
    """目录条目按当前 Heading 1/2 重建（稳定性报告的目录不印页码）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    body = doc.element.body
    sdt = None
    for el in body.iterchildren():
        if el.tag.split('}')[-1] != 'sdt':
            continue
        txt = "".join(t.text or '' for t in el.iter(qn('w:t')))
        if "目" in txt and "录" in txt:
            sdt = el
            break
    if sdt is None:
        return 0
    contents = [c for c in sdt.iter(qn('w:sdtContent'))
                if c.findall(qn('w:p'))]
    if not contents:
        return 0
    content = contents[-1]
    ps = list(content.findall(qn('w:p')))
    title_p = None
    for p in ps:
        t = "".join(x.text or '' for x in p.iter(qn('w:t')))
        if "目" in t and "录" in t:
            title_p = p
            break
    for p in ps:
        if p is not title_p:
            content.remove(p)

    heads = []
    started = not start_text
    for p in doc.paragraphs:
        sn = p.style.name if p.style else ""
        t = p.text.strip()
        if not started:
            if sn == "Heading 1" and t == start_text:
                started = True
            else:
                continue
        if sn in ("Heading 1", "Heading 2") and t:
            heads.append((1 if sn == "Heading 1" else 2, t))

    def _entry(level: int, text: str, page: Optional[int] = None):
        np = OxmlElement('w:p')
        pPr = OxmlElement('w:pPr')
        tabs = OxmlElement('w:tabs')
        tab = OxmlElement('w:tab')
        tab.set(qn('w:val'), 'right')
        tab.set(qn('w:leader'), 'dot')
        tab.set(qn('w:pos'), '8300')
        tabs.append(tab)
        pPr.append(tabs)
        if level == 2:
            ind = OxmlElement('w:ind')
            ind.set(qn('w:leftChars'), '200')
            pPr.append(ind)
        np.append(pPr)
        r = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        f = OxmlElement('w:rFonts')
        f.set(qn('w:eastAsia'), '黑体' if level == 1 else '宋体')
        rPr.append(f)
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), '24')
        rPr.append(sz)
        if level == 1:
            rPr.append(OxmlElement('w:b'))
        r.append(rPr)
        t = OxmlElement('w:t')
        t.text = text
        r.append(t)
        np.append(r)
        rt = OxmlElement('w:r')
        rt.append(OxmlElement('w:tab'))
        np.append(rt)
        if page is not None:
            rp = OxmlElement('w:r')
            tp = OxmlElement('w:t')
            tp.text = str(page)
            rp.append(tp)
            np.append(rp)
        return np

    anchor = title_p
    n = 0
    for level, text in heads:
        node = _entry(level, text, (pages or {}).get(text))
        if anchor is not None:
            anchor.addnext(node)
            anchor = node
        else:
            content.append(node)
        n += 1
    return n


_CAP = re.compile(r'^(表|图)\s*(\d{1,2})-(\d{1,3})(?=\s|　|$)')
_REF = re.compile(r'(表|图)\s*(\d{1,2})-(\d{1,3})')


def _renumber_captions(doc) -> int:
    """按重排后的新章号重编表号/图号，并把正文里的引用一起改。"""
    # ① 先按出现顺序建旧号 → 新号映射（只认段首的表题/图题）
    mapping: Dict[tuple, tuple] = {}
    seq: Dict[tuple, int] = {}
    ch = 0
    for p in doc.paragraphs:
        sn = p.style.name if p.style else ""
        if sn == "Heading 1" and p.text.strip():
            ch += 1
            continue
        t = p.text.strip()
        m = _CAP.match(t)
        if not m or not ch:
            continue
        typ, old_ch, old_no = m.group(1), int(m.group(2)), int(m.group(3))
        key = (typ, old_ch, old_no)
        seq[(typ, ch)] = seq.get((typ, ch), 0) + 1
        mapping.setdefault(key, (typ, ch, seq[(typ, ch)]))
    if not mapping:
        return 0

    def _rep(m):
        key = (m.group(1), int(m.group(2)), int(m.group(3)))
        new = mapping.get(key)
        return f"{new[0]}{new[1]}-{new[2]}" if new else m.group(0)

    n = 0
    def _fix_para(p):
        nonlocal n
        for r in p.runs:
            if not r.text:
                continue
            new = _REF.sub(_rep, r.text)
            if new != r.text:
                r.text = new
                n += 1
    for p in doc.paragraphs:
        _fix_para(p)
    for tb in doc.tables:
        for row in tb.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    _fix_para(p)
    return n


def apply_plan(path: str | Path, plan: Plan) -> Dict[str, Any]:
    """把已装配好的 14 章报告重排成模板的章节结构（标题/顺序/取舍）。"""
    from copy import deepcopy
    from docx import Document
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    if not plan or not plan.chapters:
        return {"applied": False, "reason": "no plan"}
    try:
        doc = Document(str(path))
    except Exception as e:
        return {"applied": False, "reason": str(e)}

    body = doc.element.body
    children = list(body.iterchildren())
    sectPr = body.find(qn('w:sectPr'))
    front, blocks = [], []
    cur = None
    for el in children:
        if el is sectPr:
            continue
        is_head = False
        if el.tag == qn('w:p'):
            p = Paragraph(el, doc)
            if (p.style and p.style.name == "Heading 1" and p.text.strip()):
                is_head = True
                m = re.match(r'^第([零一二三四五六七八九十]+)章', p.text.strip())
                no = _cn_to_int(m.group(1)) if m else len(blocks) + 1
                cur = {"no": no, "els": [el]}
                blocks.append(cur)
        if is_head:
            continue
        if cur is not None:
            cur["els"].append(el)
        else:
            front.append(el)
    if not blocks:
        return {"applied": False, "reason": "no chapter blocks"}

    by_no = {b["no"]: b for b in blocks}
    used = set()
    pre_blocks, seq_blocks = [], []
    _counter = 0
    _titles: Dict[int, str] = {}
    for ch in plan.chapters:
        if (ch.pre or ch.kind == "attachment") and not ch.numbered:
            _titles[id(ch)] = ch.title
        else:
            _counter += 1
            _titles[id(ch)] = format_title(ch, _counter)
    for ch in plan.chapters:
        b = by_no.get(ch.sys_no)
        if b is None:
            continue
        els = b["els"] if b["no"] not in used else [deepcopy(e) for e in b["els"]]
        used.add(b["no"])
        hp = Paragraph(els[0], doc)
        _rename_heading(hp, _titles[id(ch)])
        (pre_blocks if ch.pre else seq_blocks).append(els)

    if not seq_blocks:
        return {"applied": False, "reason": "no mapped chapters"}

    # 🔴 模板章节表里没有「附件」章（淮阴区/洪泽/清江浦区）时，附件区不能丢：
    #    业主口径——报告末尾始终保留附件页，材料没上传就留空。
    if not any(ch.kind == "attachment" for ch in plan.chapters):
        _b_att = by_no.get(14)
        if _b_att is not None and 14 not in used:
            _sample = next((c for c in plan.chapters
                            if not c.pre and c.kind != "attachment"), None)
            _att = Chapter(idx=len(plan.chapters) + 1, title="附件",
                           kind="attachment", sys_no=14,
                           numbered=bool(_sample.numbered) if _sample else True)
            _counter += 1
            _els = _b_att["els"]
            used.add(14)
            _rename_heading(Paragraph(_els[0], doc), format_title(_att, _counter))
            seq_blocks.append(_els)

    for b in blocks:
        for el in b["els"]:
            if el.getparent() is not None:
                el.getparent().remove(el)

    anchor = front[-1] if front else None
    for group in (pre_blocks + seq_blocks):
        for el in group:
            if anchor is not None:
                anchor.addnext(el)
            else:
                body.insert(0, el)
            anchor = el

    n_cap = 0
    try:
        n_cap = _renumber_captions(doc)
    except Exception as e:
        logger.warning(f"[模板结构] 表号重编失败: {e}")
    n_toc = 0
    try:
        _start = ""
        for ch in plan.chapters:
            if ch.sys_no in by_no:
                _start = _titles[id(ch)]
                break
        n_toc = _rebuild_toc(doc, _start)
    except Exception as e:
        logger.warning(f"[模板结构] 目录重建失败: {e}")
    doc.save(str(path))
    # 正文页码 + 目录页码（按页脚实际数字回填）
    try:
        stamp_toc_pages(path)
    except Exception as e:
        logger.warning(f"[模板结构] 页码回填失败: {e}")
    return {"applied": True, "chapters": len(seq_blocks),
            "pre": len(pre_blocks), "toc_entries": n_toc,
            "renumbered": n_cap}


# ═══════════════════════════════════════════════════════════════
# 按模板章取「本章底稿」（供章节 prompt 注入）
# ═══════════════════════════════════════════════════════════════
_TEXT_CACHE: Dict[str, tuple] = {}


def _match_title(t: str, titles) -> str:
    """段落是不是某个章标题（容忍「第X章 」「、」等前后缀）。

    只在「第X章 …」或整段就是章标题时成立；正文里的「（二）编制依据」
    不能算（否则后面整章都会被算到这一章）。
    """
    if re.match(r'^\d{1,2}\s*[.．、]', t):
        return ""          # 「2.编制依据」是小节，不是章
    bare = _strip_no(t)
    if bare in titles:
        return bare
    if re.match(r'^第[零一二三四五六七八九十百]+章', t):
        for title in titles:
            if title and title in re.sub(r'\s+', '', t) and \
                    len(re.sub(r'\s+', '', t)) <= len(title) + 8:
                return title
    return ""


def _strip_no(t: str) -> str:
    return re.sub(r'^第[零一二三四五六七八九十百]+章[、．.:：\s]*', '',
                  re.sub(r'^\d{1,2}\s*[、．.]?\s*', '', re.sub(r'\s+', '', t or "")))


def chapter_text(path: str | Path, title: str, plan: Plan,
                 limit: int = 16000) -> str:
    """取模板里「某一章」的原文（标题行到下一个章标题之前）。"""
    from docx import Document
    key = str(Path(path).resolve())
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = 0
    cache = _TEXT_CACHE.get(key)
    if cache and cache[0] == mtime:
        chapters = cache[1]
    else:
        try:
            doc = Document(str(path))
        except Exception:
            return ""
        titles = {_strip_no(c.title) for c in plan.chapters if c.title}
        chapters: Dict[str, List[str]] = {}
        cur = ""
        for p in doc.paragraphs:
            t = (p.text or "").strip()
            if not t:
                continue
            bare = _strip_no(t)
            if bare in titles and len(bare) + 6 >= len(t):
                cur = bare
                chapters.setdefault(cur, [t])
                continue
            if cur:
                chapters[cur].append(t)
        _TEXT_CACHE[key] = (mtime, chapters)
    lines = (chapters or {}).get(_strip_no(title)) or []
    text = "\n".join(lines).strip()
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit] + "\n…（模板后续内容略）"
    return text


def build_block_for_chapter(path: str | Path, sys_no: int,
                            limit: int = 16000) -> str:
    """按**模板章节**（不是系统章号）生成「本章模板底稿」prompt 块。"""
    plan = extract_plan(path)
    ch = next((c for c in plan.chapters if c.sys_no == int(sys_no)), None)
    if ch is None:
        return ""
    text = chapter_text(path, ch.title, plan, limit=limit)
    if not text:
        return ""
    return (
        "\n## 📐 本章模板底稿（同地区官方模板原文 —— **结构与固定表述以此为准**）\n"
        f"底稿对应模板的「{ch.title}」。请按下列规则使用：\n"
        "- **不需要动的不动**：固定条款、政策依据、程序性表述、清单与表格结构，照原样保留；\n"
        "- **要写的地方重新拟**：涉及本项目情况、数据、分析的段落，依据本项目事实卡与知识库内容重新撰写，"
        "读起来像有经验的编制人员写的，不要套模板腔；\n"
        "- 模板里属于**其他项目**的事实（项目名/地名/单位/面积/日期/文号/人名）一律不得照抄。\n"
        f"```\n{text}\n```\n"
    )


# ═══════════════════════════════════════════════════════════════
# 目录页码（按页脚实际印出的页码）
# ═══════════════════════════════════════════════════════════════
def _pdf_printed_numbers(pdf_path: str) -> List[tuple]:
    """[(页序, 页脚印出的页码)] —— 页脚没有数字的页（封面/资质页）页码为 None。"""
    import fitz
    out = []
    doc = fitz.open(str(pdf_path))
    try:
        for i in range(doc.page_count):
            # 页码在页面最下方：取 y 最大的独立数字块
            best = None
            for b in doc[i].get_text("blocks"):
                txt = (b[4] or "").strip()
                m = re.match(r'^(?:第\s*)?(\d{1,3})(?:\s*页)?$', txt)
                if not m:
                    continue
                y = b[1]
                if best is None or y > best[0]:
                    best = (y, int(m.group(1)))
            out.append((i + 1, best[1] if best else None))
    finally:
        doc.close()
    return out


def measure_heading_pages(path: str | Path) -> Dict[str, int]:
    """docx → PDF，返回 {标题: 页脚页码}（标题首次出现在正文页的那一页）。"""
    import shutil
    import subprocess
    import tempfile
    from docx import Document

    try:
        heads = []
        d = Document(str(path))
        for p in d.paragraphs:
            sn = p.style.name if p.style else ""
            t = p.text.strip()
            if sn in ("Heading 1", "Heading 2") and t:
                heads.append(t)
        if not heads:
            return {}
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            return {}
        tmp = Path(tempfile.mkdtemp(prefix="toctoc_"))
        try:
            subprocess.run([soffice, "--headless", "--norestore", "--convert-to",
                            "pdf", "--outdir", str(tmp), str(path)],
                           check=True, capture_output=True, timeout=300)
            pdf = next(tmp.glob("*.pdf"), None)
            if not pdf:
                return {}
            import fitz
            numbers = dict(_pdf_printed_numbers(str(pdf)))
            doc = fitz.open(str(pdf))
            found: Dict[str, int] = {}
            try:
                for i in range(doc.page_count):
                    txt = "".join(doc[i].get_text().split())
                    if txt.count("....") >= 30:      # 目录页
                        continue
                    num = numbers.get(i + 1)
                    if num is None:
                        continue
                    for h in heads:
                        if h in found:
                            continue
                        if "".join(h.split()) in txt:
                            found[h] = num
            finally:
                doc.close()
            return found
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        logger.warning(f"[目录页码] 测量失败 {path}: {e}")
        return {}


def stamp_toc_pages(path: str | Path) -> int:
    """给已生成的报告补正文页码、重建目录条目并填页码，返回条目数。"""
    from docx import Document
    try:
        ensure_body_page_numbers(path)
    except Exception as e:
        logger.warning(f"[页码] 正文页码补齐失败: {e}")
    pages = measure_heading_pages(path)
    if not pages:
        return 0
    doc = Document(str(path))
    first = ""
    for p in doc.paragraphs:
        if p.style and p.style.name == "Heading 1" and p.text.strip():
            first = p.text.strip()
            break
    n = _rebuild_toc(doc, first, pages=pages)
    doc.save(str(path))
    return n


def _write_page_field(p) -> None:
    """把段落的正文清空，写入一个合法的 PAGE 域（begin/instr/separate/结果/end）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    for el in list(p._p):
        if el.tag != qn('w:pPr'):
            p._p.remove(el)

    def _run(child=None):
        r = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        f = OxmlElement('w:rFonts')
        for k in ('w:ascii', 'w:hAnsi', 'w:eastAsia'):
            f.set(qn(k), 'Times New Roman')
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), '24')
        rPr.append(f)
        rPr.append(sz)
        r.append(rPr)
        if child is not None:
            r.append(child)
        p._p.append(r)

    fc = OxmlElement('w:fldChar'); fc.set(qn('w:fldCharType'), 'begin')
    it = OxmlElement('w:instrText'); it.set(qn('xml:space'), 'preserve')
    it.text = ' PAGE '
    sp = OxmlElement('w:fldChar'); sp.set(qn('w:fldCharType'), 'separate')
    t = OxmlElement('w:t'); t.text = '1'
    en = OxmlElement('w:fldChar'); en.set(qn('w:fldCharType'), 'end')
    for child in (fc, it, sp, t, en):
        _run(child)


def ensure_body_page_numbers(path: str | Path) -> int:
    """给正文各节**显式挂上同一个页脚页码部件**。

    装配器只把 footerReference 挂在「正文第一节」，后面各节靠 linked 继承；
    LibreOffice 转 PDF 时继承链断掉，正文全篇没有页码（Word 里看着正常）。
    这里把正文节的 footerReference 复制到后面所有节，并补全 PAGE 域的
    separate/结果 run（只有 begin/instr/end 时 LO 不计算域）。
    """
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document(str(path))
    body_rid = None
    first_body_idx = None
    # 找带 PAGE 域的页脚部件（装配器只在正文第一节建了它）
    for idx, s in enumerate(doc.sections):
        fr = s._sectPr.find(qn('w:footerReference'))
        if fr is None:
            continue
        try:
            if 'PAGE' in s.footer._element.xml:
                body_rid = fr.get(qn('r:id'))
                first_body_idx = idx
                break
        except Exception:
            continue
    if body_rid is None:
        # 装配器没建页脚（该模板/报告节结构异常）——这里补一个正文页脚
        try:
            idx = 1 if len(doc.sections) > 1 else 0
            sec = doc.sections[idx]
            sec.footer.is_linked_to_previous = False
            fp = sec.footer.paragraphs[0] if sec.footer.paragraphs \
                else sec.footer.add_paragraph()
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _write_page_field(fp)
            fr = sec._sectPr.find(qn('w:footerReference'))
            if fr is not None:
                body_rid = fr.get(qn('r:id'))
            first_body_idx = idx
        except Exception as e:
            logger.warning(f"[页码] 创建页脚失败: {e}")
            return 0
    if body_rid is None:
        return 0

    # 页码改成**全文绝对页序**：去掉各节重新起算，避免前面缺号/后面重号
    for s in doc.sections:
        pn = s._sectPr.find(qn('w:pgNumType'))
        if pn is not None:
            s._sectPr.remove(pn)

    n = 0
    for idx, s in enumerate(doc.sections):
        if idx == 0:          # 封面不印页码
            continue
        sp = s._sectPr
        if sp.find(qn('w:footerReference')) is None:
            fr = OxmlElement('w:footerReference')
            fr.set(qn('w:type'), 'default')
            fr.set(qn('r:id'), body_rid)
            sp.append(fr)
            n += 1

    # 补全 PAGE 域（begin/instr/end 必须在不同 run 里，且要有 separate + 结果）
    try:
        ftr = doc.sections[first_body_idx].footer
        p = ftr.paragraphs[0] if ftr.paragraphs else ftr.add_paragraph()
        if 'PAGE' in p._p.xml:
            for el in list(p._p):
                if el.tag != qn('w:pPr'):
                    p._p.remove(el)

            def _run(child=None):
                r = OxmlElement('w:r')
                rPr = OxmlElement('w:rPr')
                f = OxmlElement('w:rFonts')
                for k in ('w:ascii', 'w:hAnsi', 'w:eastAsia'):
                    f.set(qn(k), 'Times New Roman')
                sz = OxmlElement('w:sz')
                sz.set(qn('w:val'), '24')
                rPr.append(f)
                rPr.append(sz)
                r.append(rPr)
                if child is not None:
                    r.append(child)
                p._p.append(r)

            for kind in ('begin', 'instr', 'separate', 'text', 'end'):
                if kind == 'instr':
                    it = OxmlElement('w:instrText')
                    it.set(qn('xml:space'), 'preserve')
                    it.text = ' PAGE '
                    _run(it)
                elif kind == 'text':
                    t = OxmlElement('w:t')
                    t.text = '1'
                    _run(t)
                else:
                    fc = OxmlElement('w:fldChar')
                    fc.set(qn('w:fldCharType'), kind)
                    _run(fc)
    except Exception as e:
        logger.warning(f"[页码] 页脚域修复失败: {e}")
    doc.save(str(path))
    return n


# ═══════════════════════════════════════════════════════════════
# 每章的小节结构与段落规格（各模板不同，要逐份学）
# ═══════════════════════════════════════════════════════════════
_SEC_HEAD = re.compile(r'^[（(][一二三四五六七八九十]+[）)]\s*[一-龥].{1,28}$')


def extract_layout(path: str | Path, plan: Optional[Plan] = None) -> Dict[str, Any]:
    """返回 {章标题: {"subs": [{title, paras, chars}], "paras": n, "chars": m}}。"""
    from docx import Document
    plan = plan or extract_plan(path)
    if not plan.chapters:
        return {}
    try:
        doc = Document(str(path))
    except Exception:
        return {}

    titles = {_strip_no(c.title): c.title for c in plan.chapters}
    out: Dict[str, Any] = {}
    cur_ch = ""
    cur_sub = ""
    stats: Dict[str, Dict[str, Any]] = {}

    def _st(ch: str) -> Dict[str, Any]:
        return stats.setdefault(ch, {"subs": {}, "paras": 0, "chars": []})

    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        _hit = _match_title(t, titles)
        if _hit:
            cur_ch = _hit
            cur_sub = ""
            _st(cur_ch)
            continue
        if not cur_ch:
            continue
        sn = p.style.name if p.style else ""
        m = _SEC_HEAD.match(t) or (sn == "Heading 2" and len(t) <= 40)
        if m and len(t) <= 40:
            cur_sub = t
            _st(cur_ch)["subs"].setdefault(cur_sub, {"paras": 0, "chars": []})
            continue
        if len(t) >= 20 and not t.startswith(("表", "图")):
            s = _st(cur_ch)
            s["paras"] += 1
            s["chars"].append(len(t))
            if cur_sub:
                s["subs"][cur_sub]["paras"] += 1
                s["subs"][cur_sub]["chars"].append(len(t))

    for ch, s in stats.items():
        chars = sorted(s["chars"])
        med = chars[len(chars) // 2] if chars else 0
        subs = []
        for title, d in s["subs"].items():
            c = sorted(d["chars"])
            subs.append({"title": title, "paras": d["paras"],
                         "chars": c[len(c) // 2] if c else 0})
        out[titles.get(ch, ch)] = {"paras": s["paras"], "chars": med,
                                   "subs": subs}
    return out


def layout_hint_for_chapter(path: str | Path, sys_no: int) -> str:
    """给章节 prompt 的「本章模板小节规格」：几小节、每节几段、每段多少字。"""
    plan = extract_plan(path)
    ch = next((c for c in plan.chapters if c.sys_no == int(sys_no)), None)
    if ch is None or ch.kind == "review":
        return ""
    layout = extract_layout(path, plan).get(ch.title) or {}
    if not layout:
        return ""
    lines = [f"\n## 📏 本章模板的小节与段落规格（来自「{ch.title}」，按它排）"]
    lines.append(f"- 本章正文约 {layout.get('paras', 0)} 段，"
                 f"每段中位 {layout.get('chars', 0)} 字；")
    for s in layout.get("subs") or []:
        lines.append(f"- {s['title']}：约 {s['paras']} 段，"
                     f"每段约 {s['chars']} 字")
    lines.append("- 小节顺序、每小节段数与段落长度按上述规格写，不要套别的地区模板。\n")
    return "\n".join(lines)
