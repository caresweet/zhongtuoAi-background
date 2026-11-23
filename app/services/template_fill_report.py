# -*- coding: utf-8 -*-
"""以地区模板为底版出报告：只换数据/必要文案，其余 1:1 保留。

与「生成 14 章再重排」不同，这条路**直接复制模板 docx**：
  · 章标题、固定条款、程序表述、表格结构、版式全部不动；
  · 只替换模板里属于**旧项目**的事实（项目名/文号/责任单位/位置/日期等）
    与少量必须重写的文案；
  · 目录沿用模板的 TOC 域，打开时更新页码。
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CN_DATE = re.compile(r'[二〇零一二三四五六七八九]{4}\s*年\s*[一二三四五六七八九十]{1,2}\s*月')
_OLD_DOCREF = re.compile(r'[一-龥]{1,8}〔\d{4}〕第?\s*\d+\s*号')
_LOC_SENT = re.compile(r'位于([^，。；\n]{4,40}?)(?:范围内|范围|境内|，|。)')


def _norm(s: str) -> str:
    return re.sub(r'\s+', '', s or "")


def extract_old_facts(doc) -> Dict[str, str]:
    """从模板里认出「旧项目」的事实值（用于替换）。"""
    facts: Dict[str, str] = {}
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    _BAD_REF = ("中办发", "国办发", "苏办发", "苏政办发", "财库", "苏自然资",
                "淮办", "苏政发", "国发", "发改", "环发")

    refs: List[tuple] = []
    for t in texts:
        if "项目名称" in t and "：" in t:
            v = re.split(r'项目名称[：:]', t, 1)[-1].strip()
            if 4 <= len(v) <= 60:
                facts.setdefault("project_name", v)
        if "决策事项名称" in t and "：" in t:
            v = re.split(r'决策事项名称[：:]', t, 1)[-1].strip()
            if 4 <= len(v) <= 60:
                facts.setdefault("project_name", v)
        m = re.search(r'(?:稳评责任单位|责任单位|决策单位|评估责任主体)\s*[：:]\s*'
                      r'([^\s，。；]{4,40})', t)
        if m and "根据用户提供" not in m.group(1):
            facts.setdefault("unit", m.group(1))
        for m in re.finditer(r'[一-龥]{1,12}〔20\d{2}〕第?\s*\d+\s*号', t):
            raw = re.sub(r'\s+', '', m.group(0))
            _i = raw.find("〔")
            prefix = raw[:_i][-5:]
            while len(prefix) > 2 and prefix[0] not in "洪金淮清泗涟盱":
                prefix = prefix[1:]
            ref = prefix + raw[_i:]
            score = 0
            if "征" not in prefix:
                continue          # 不是征地公告文号（法规/批复一律跳过）
            if "告" in prefix:
                score += 2
            if any(ref.startswith(b) or b in ref[:8] for b in _BAD_REF):
                score -= 20
            ym = re.search(r'〔(20\d{2})〕', ref)
            if ym and ym.group(1) >= "2024":
                score += 2
            refs.append((score, ref))
        m = re.search(r'(?:地块|项目|决策|工程)位于([^。；\n]{4,50}?)'
                      r'(?:范围内|范围|境内|。|；)', t)
        if m:
            cand = m.group(1).strip("，, ")
            if any(k in cand for k in ("镇", "村", "街道", "乡", "市", "区")):
                facts.setdefault("location", cand)
        m = _CN_DATE.search(t)
        if m:
            facts.setdefault("date_cn", _norm(m.group(0)))

    if refs:
        refs.sort(key=lambda x: -x[0])
        if refs[0][0] > 0:
            facts["doc_ref"] = refs[0][1]

    # 拆出旧镇/村（去掉市/区前缀再取）
    loc = facts.get("location") or ""
    tail = loc
    while True:
        m = re.match(r'^[^区县市]{1,10}?[区县市]', tail)
        if not m:
            break
        tail = tail[m.end():]
    m = re.match(r'([\u4e00-\u9fa5]{2,6}(?:镇|街道|乡))', tail)
    if m:
        facts["town"] = m.group(1)
    _rest = tail
    if facts.get("town") and _rest.startswith(facts["town"]):
        _rest = _rest[len(facts["town"]):]
    _vs = re.findall(r'([\u4e00-\u9fa5]{2,6}村)', _rest or tail or loc)
    if _vs:
        facts["village"] = _vs[-1]

    # 旧年份：优先项目文号年份，其次中文日期年份
    _y = re.search(r'〔(20\d{2})〕', facts.get("doc_ref") or "")
    if _y:
        facts["year"] = _y.group(1)
    else:
        m = re.search(r'([二〇零一二三四五六七八九]{4})\s*年',
                      facts.get("date_cn") or "")
        if m:
            _cn = {"〇": "0", "零": "0", "一": "1", "二": "2", "三": "3",
                   "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
            facts["year"] = "".join(_cn.get(c, c) for c in m.group(1))

    if "project_name" not in facts:
        for t in texts[:60]:
            if 6 <= len(t) <= 50 and (t.endswith("决策") or t.endswith("项目")):
                facts["project_name"] = t
                break
    return facts


def _replace_in_para(p, mapping: List[Tuple[str, str]]) -> int:
    """段落内替换；跨 run 的按段落重写（保留首个 run 的格式）。"""
    old_text = p.text or ""
    new_text = old_text
    for a, b in mapping:
        if a and a in new_text:
            new_text = new_text.replace(a, b)
    if new_text == old_text:
        return 0
    if p.runs:
        # 简单情形：整段只在一个 run 里
        for r in p.runs:
            if r.text:
                for a, b in mapping:
                    if a and a in r.text:
                        r.text = r.text.replace(a, b)
        if p.text != new_text:
            p.runs[0].text = new_text
            for r in p.runs[1:]:
                r.text = ""
    else:
        p.add_run(new_text)
    return 1


def apply_replacements(doc, mapping: List[Tuple[str, str]]) -> int:
    n = 0
    for p in doc.paragraphs:
        n += _replace_in_para(p, mapping)
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    n += _replace_in_para(p, mapping)
    return n


def build_from_template(*, template_path: str | Path, filled: Dict[str, Any],
                        out_path: str | Path,
                        extra_replacements: Optional[List[Tuple[str, str]]] = None) -> Path:
    """复制模板 → 替换旧事实 → 另存。返回输出路径。"""
    from docx import Document

    src, dst = Path(template_path), Path(out_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    doc = Document(str(dst))

    old = extract_old_facts(doc)
    logger.info(f"[模板底版] 识别旧事实: {old}")

    new_name = str(filled.get("project_name") or "").strip()
    new_ref = str(filled.get("doc_reference") or "").strip()
    new_unit = str(filled.get("org_name") or filled.get("decision_unit") or "").strip()
    new_loc = str(filled.get("location") or "").strip()
    report_month = str(filled.get("report_month") or "").strip()

    mapping: List[Tuple[str, str]] = []
    if old.get("project_name") and new_name:
        mapping.append((old["project_name"], new_name))
        # 模板里常省掉“决策”二字
        base = re.sub(r'(土地征收)?决策$', '', old["project_name"]).strip()
        if base and base != old["project_name"] and new_name:
            mapping.append((base, re.sub(r'(土地征收)?决策$', '', new_name).strip()))
    if old.get("unit") and new_unit:
        mapping.append((old["unit"], new_unit))
        # 封面常省略「淮安市」前缀
        mapping.append((re.sub(r'^淮安市', '', old["unit"]),
                        re.sub(r'^淮安市', '', new_unit)))
        m = re.match(r'^(.*?[区县])', old["unit"])
        if m:
            mapping.append((m.group(1) + old.get("town", ""),
                            re.sub(r'^淮安市', '', new_unit)))
    if old.get("location") and new_loc:
        mapping.append((old["location"], new_loc.split("（")[0].strip()))
    if old.get("doc_ref") and new_ref:
        mapping.append((old["doc_ref"], new_ref))
    # 旧镇/村 → 新镇/村
    new_town = ""
    m = re.search(r'([\u4e00-\u9fa5]{2,8}(?:镇|街道|乡))', new_loc)
    if m:
        new_town = m.group(1)
    new_village = ""
    m = re.search(r'([\u4e00-\u9fa5]{2,10}村)', new_loc)
    if m:
        new_village = m.group(1)
    if old.get("town") and new_town:
        mapping.append((old["town"], new_town))
    if old.get("village") and new_village:
        mapping.append((old["village"], new_village))
    # 旧年份 → 新年份
    _ym = str(filled.get("report_month") or filled.get("notice_date") or "")
    _ref_y = re.search(r'〔(20\d{2})〕', new_ref)
    if _ref_y:
        _ym = _ref_y.group(1)
    if _ym:
        _new_y = _ym[:4]
        _years = {old.get("year")} if old.get("year") else set()
        # 模板时间线常跨 2024/2025 两个年头
        _years.update({"2023", "2024", "2025", "2026"})
        for _y in sorted(x for x in _years if x):
            if _y != _new_y:
                mapping.append((f"{_y}年", f"{_new_y}年"))
                mapping.append((f"{_y} 年", f"{_new_y} 年"))
    if old.get("date_cn") and report_month:
        m = re.match(r'(\d{4})-(\d{2})', report_month)
        if m:
            cn = {"0": "〇", "1": "一", "2": "二", "3": "三", "4": "四",
                  "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}
            y = "".join(cn.get(c, c) for c in m.group(1))
            mo = int(m.group(2))
            mn = "十" if mo == 10 else "十一" if mo == 11 else "十二" if mo == 12 else cn.get(str(mo), str(mo))
            mapping.append((old["date_cn"], f"{y}年{mn}月"))
    for a, b in (extra_replacements or []):
        if a and b:
            mapping.append((a, b))

    # 长串先替换，避免“旧项目名”被短串先改坏
    mapping.sort(key=lambda x: -len(x[0]))
    n = apply_replacements(doc, mapping)

    # 目录/页码域允许 Word 打开时刷新
    try:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        st = doc.settings.element
        if st.find(qn('w:updateFields')) is None:
            uf = OxmlElement('w:updateFields')
            uf.set(qn('w:val'), 'true')
            st.append(uf)
    except Exception:
        pass

    doc.save(str(dst))
    logger.info(f"[模板底版] 替换 {n} 段；输出 {dst}")
    return dst


# ═══════════════════════════════════════════════════════════════
# 按模板标注「重写」的小节，用 AI 生成的章节正文替换（其余照搬）
# ═══════════════════════════════════════════════════════════════
def _gen_chapters(gen_doc) -> Dict[int, List[str]]:
    """已生成报告 → {系统章号: [正文段落]}。

    按**章节类型**归类，不按显示章号：重排后的报告章号是模板顺序
    （淮阴区第6章是四性分析），不能拿它当系统章号用。
    """
    from app.services.template_structure import _kind, _KIND_TO_SYS
    out: Dict[int, List[str]] = {}
    cur = 0
    for p in gen_doc.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        sn = p.style.name if p.style else ""
        if sn == "Heading 1":
            _k = _kind(t)
            cur = int(_KIND_TO_SYS.get(_k) or 0)
            if cur:
                out[cur] = []
            continue
        if cur and sn not in ("Heading 1",):
            out[cur].append(t)
    return out


_H1_RE = re.compile(r'^(?:[（(][一二三四五六七八九十]+[）)]|'
                    r'第[一二三四五六七八九十]+[节章]|'
                    r'[一二三四五六七八九十]+、)')
_H2_RE = re.compile(r'^(?:\d{1,2}[.．、]\s*\D|\d{1,2}\.\d{1,2}\s*\D|'
                    r'[（(]\d{1,2}[）)])')


def _role(text: str) -> str:
    """段落角色：h1（（一）/一、/第X节）、h2（1./1.1/（1））、body。"""
    t = (text or "").strip()
    if not t or len(t) > 60:
        return "body"
    if _H1_RE.match(t):
        return "h1"
    if _H2_RE.match(t):
        return "h2"
    return "body"


def merge_ai_sections(*, template_path: str | Path, gen_path: str | Path,
                      analysis_sections: List[Dict[str, Any]],
                      out_path: str | Path) -> Path:
    """模板为底，按分析标注把「重写」小节换成 AI 正文；其余原样。

    格式按**角色**取模板样本，不按位置：AI 的正文段永远抄模板正文段的
    字体/字号/加粗/首行缩进，AI 的小标题段才抄模板小标题段。此前按位置
    套格式，模板章首两行是 14 磅加粗的项目名，AI 正文被灌进这两行，
    整章正文就都成了 14 磅加粗。
    """
    from docx import Document
    from app.services.template_structure import (
        extract_plan, _match_title, _strip_no)

    src = Path(template_path)
    dst = Path(out_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    doc = Document(str(dst))
    gen = Document(str(gen_path))
    gen_ch = _gen_chapters(gen)
    plan = extract_plan(src)
    titles = {_strip_no(c.title): c for c in plan.chapters}

    # 需要重写的系统章号集合
    rewrite_sys = set()
    for s in analysis_sections or []:
        if not s.get("replace"):
            continue
        title = _strip_no(s.get("title") or "")
        c = titles.get(title)
        if c and c.sys_no:
            rewrite_sys.add(int(c.sys_no))
    if not rewrite_sys:
        return dst

    ps = list(doc.paragraphs)
    ch_idx = []
    seen = set()
    for i, p in enumerate(ps):
        t = (p.text or "").strip()
        if not t:
            continue
        c = titles.get(_match_title(t, set(titles)))
        if c is None or id(c) in seen:
            continue          # 同一章的重复标题（章下项目名/副标题）不算新章
        seen.add(id(c))
        ch_idx.append((i, c))
    for k, (start, c) in enumerate(ch_idx):
        if int(c.sys_no or 0) not in rewrite_sys:
            continue
        end = ch_idx[k + 1][0] if k + 1 < len(ch_idx) else len(ps)
        body = [i for i in range(start + 1, end)
                if (ps[i].text or "").strip()]
        new_paras = [t for t in gen_ch.get(int(c.sys_no), []) if t]
        if not new_paras or not body:
            continue
        h1_src = h2_src = text_src = None
        for i in body:
            t = (ps[i].text or "").strip()
            r = _role(t)
            if r == "h1" and h1_src is None:
                h1_src = ps[i]
            elif r == "h2" and h2_src is None:
                h2_src = ps[i]
            elif (r == "body" and text_src is None and len(t) >= 25
                  and ("。" in t or "；" in t or len(t) >= 40)):
                text_src = ps[i]
        if text_src is None:
            text_src = _fallback_body_para(ps, set(body))
        if h1_src is None:
            h1_src = text_src
        if h2_src is None:
            h2_src = h1_src
        # 先把格式取成快照：后面段落正文会被 AI 文本覆盖，引用会失真
        f_h1 = _capture_fmt(h1_src)
        f_h2 = _capture_fmt(h2_src)
        f_body = _capture_fmt(text_src)

        def _fmt_for(txt: str):
            r = _role(txt)
            if r == "h1":
                return f_h1, True
            if r == "h2":
                return f_h2, True
            return f_body, False

        for j, idx in enumerate(body):
            if j < len(new_paras):
                txt = new_paras[j]
                fmt, head = _fmt_for(txt)
                _set_para_text(ps[idx], txt)
                _apply_fmt(ps[idx], fmt, heading=head)
            else:
                _set_para_text(ps[idx], "")
        if len(new_paras) > len(body):
            anchor = ps[body[-1]]
            for extra in new_paras[len(body):]:
                fmt, head = _fmt_for(extra)
                _insert_para_after(anchor, extra, fmt, heading=head)
                from docx.text.paragraph import Paragraph
                anchor = Paragraph(anchor._p.getnext(), doc)
    doc.save(str(dst))
    return dst


def _fallback_body_para(ps, skip: set):
    """章内没有正文段时，从全篇找一个最长、带首行缩进的正文段当样式源。"""
    best, best_len = None, 0
    for i, p in enumerate(ps):
        if i in skip:
            continue
        t = (p.text or "").strip()
        if len(t) < 30 or _role(t) != "body":
            continue
        if t.count("。") < 1:
            continue
        if p.paragraph_format.first_line_indent is not None:
            return p
        if len(t) > best_len:
            best, best_len = p, len(t)
    return best


def _run_size(p):
    for r in p.runs:
        if r.font.size:
            return r.font.size
    try:
        if p.style is not None and p.style.font.size:
            return p.style.font.size
    except Exception:
        pass
    return None


def _run_name(p):
    for r in p.runs:
        if r.font.name:
            return r.font.name
    try:
        if p.style is not None and p.style.font.name:
            return p.style.font.name
    except Exception:
        pass
    return None


def _capture_fmt(p) -> Dict[str, Any]:
    """把一段的排版取成快照（字号/加粗/字体/缩进/行距/对齐）。"""
    if p is None:
        return {}
    pf = p.paragraph_format
    return {
        "style": p.style,
        "alignment": p.alignment,
        "first_line_indent": pf.first_line_indent,
        "left_indent": pf.left_indent,
        "line_spacing": pf.line_spacing,
        "space_before": pf.space_before,
        "space_after": pf.space_after,
        "size": _run_size(p),
        "name": _run_name(p),
    }


def _apply_fmt(dst, fmt: Dict[str, Any], heading: bool = False) -> None:
    if not fmt:
        return
    try:
        if fmt.get("style") is not None:
            dst.style = fmt["style"]
        dpf = dst.paragraph_format
        dpf.first_line_indent = fmt.get("first_line_indent")
        dpf.left_indent = fmt.get("left_indent")
        dpf.line_spacing = fmt.get("line_spacing")
        dpf.space_before = fmt.get("space_before")
        dpf.space_after = fmt.get("space_after")
        if fmt.get("alignment") is not None:
            dst.alignment = fmt["alignment"]
    except Exception:
        pass
    _apply_run_fmt(dst, fmt, heading)


def _apply_run_fmt(dst_p, fmt: Dict[str, Any], heading: bool) -> None:
    if not dst_p.runs:
        dst_p.add_run("")
    r = dst_p.runs[0]
    size, name = fmt.get("size"), fmt.get("name")
    if size:
        r.font.size = size
    r.font.bold = bool(heading)
    if name:
        r.font.name = name
        _set_east_asia(r, name)


def _set_east_asia(r, name: str) -> None:
    try:
        from docx.oxml.ns import qn
        r._element.get_or_add_rPr().get_or_add_rFonts().set(
            qn('w:eastAsia'), name)
    except Exception:
        pass


def _set_para_text(p, text: str) -> None:
    if not p.runs:
        p.add_run(text)
        return
    p.runs[0].text = text
    for r in p.runs[1:]:
        r.text = ""


def _insert_para_after(anchor, text: str, fmt: Dict[str, Any],
                       heading: bool = False) -> None:
    from docx.text.paragraph import Paragraph
    new_p = anchor.insert_paragraph_before("")   # 先插在前面，再挪到后面
    new_p._p.getparent().remove(new_p._p)
    anchor._p.addnext(new_p._p)
    r = new_p.add_run(text)
    _apply_fmt(new_p, fmt, heading=heading)
