# -*- coding: utf-8 -*-
"""模板库按「章 → 小节」切片（docx + PDF），供章节 agent 学习**对应小节**的写法。

## 为什么要有它

业主口径（2026-11-02）：「都要切，每章节每小节。」
此前只有**被选中的那一份**模板会按 Heading 1 切章、整章塞进提示词；
库里另外几份范文（4 份复垦 PDF、金湖/洪泽稳评等）完全没参与构写。
模型在模板较薄的章节（复垦 ch6/8/9）只能自由发挥。

## 能读什么

| 格式 | 结构识别 |
|---|---|
| docx | Heading 1 = 章；Heading 2/3 = 小节；无样式时退回 `1 前言` / `1.1 …` / `第一章 …` 文本规则 |
| pdf  | PDF 文字层的 `1 前言` / `1.1 编制背景及过程` / `1.1.1 …`（跳过带 `......` 的目录行） |

## 产物

    {"<绝对路径>": {"mtime": …, "chapters": {
        "4": {"title": "土地复垦方向可行性分析",
              "sections": {"4.1": {"title": "土地损毁分析与评定", "text": "…"},
                           "4.1.1": {…}}}}}}

缓存到 `backend/data/template_sections_cache.json`，按 mtime 失效。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_CACHE_FILE = _BACKEND_DIR / "data" / "template_sections_cache.json"

# 章 / 小节 / 三级小节（阿拉伯编号）
_CH_RE = re.compile(r'^\s*(\d{1,2})\s+([^\d\s].{0,40})\s*$')
_SEC_RE = re.compile(r'^\s*(\d{1,2}\.\d{1,2})\s+([^\d\s].{0,60})\s*$')
_SUB_RE = re.compile(r'^\s*(\d{1,2}\.\d{1,2}\.\d{1,2})\s+([^\d\s].{0,60})\s*$')
# 中文章号（稳评模板用「第一章 编制说明」）
_CN_CH_RE = re.compile(r'^\s*第([一二三四五六七八九十百]{1,4})章\s*(.{0,40})\s*$')
# 中文小节：稳评模板用「（一）稳评理由」「二、…」
_CN_SEC_RE = re.compile(r'^\s*[（(]?([一二三四五六七八九十]{1,4})[）)、．.]\s*(.{1,60})\s*$')
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
           "十四": 14, "十五": 15}
# 目录行 / 页眉页脚噪声
_DOTS_RE = re.compile(r'(\.{5,}|…{3,}|·{4,}|\.{2,}\s*\d+$)')


def _cn_to_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if s in _CN_NUM:
        return _CN_NUM[s]
    if s.startswith("十") and len(s) == 2 and s[1] in _CN_NUM:
        return 10 + _CN_NUM[s[1]]
    return None


def _is_heading_title(t: str, strict: bool = False) -> bool:
    """标题行必须含中文、不含制表符/圆点，长度受限。

    `strict=True`（用于**章**标题）：标题里不许有数字 —— 否则 PDF 表格行
    「1 施工便道」会被误判成新的一章。
    """
    t = (t or "").strip()
    if not t or len(t) > 60:
        return False
    if _DOTS_RE.search(t):
        return False
    if strict and re.search(r'\d', t):
        return False
    return bool(re.search(r'[一-鿿]', t))


def _new_chapter(no: int, title: str) -> dict:
    return {"title": title.strip(), "sections": {}}


def _ensure_section(ch: dict, key: str) -> dict:
    secs = ch["sections"]
    if key not in secs:
        secs[key] = {"title": "", "text": ""}
    return secs[key]


# ═══════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════

_NUM_ONLY = re.compile(r'^\d{1,2}(\.\d{1,2}){0,2}$')


def _merge_split_headings(lines: List[str]) -> List[str]:
    """把「编号一行、标题拆成几行」的 PDF 行拼回一行。

    实测王元佳苑那份 PDF 的文字层是：`1` / `前` / `言` / `1.1` / `编制背景及过程`，
    不拼的话编号规则一条都匹配不上（只能切出 3 个章）。
    """
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if _NUM_ONLY.match(s):
            parts = [s]
            j = i + 1
            while j < n and len(parts) <= 4:
                t = lines[j].strip()
                if not t:
                    j += 1
                    continue
                # 🔴 遇到**另一条标题**立即停：否则页码「12」会把后面的
                #    「4 土地复垦方向可行性分析 4.1 土地损毁…」整串拼成一行。
                if (_NUM_ONLY.match(t) or _CH_RE.match(t)
                        or _SEC_RE.match(t) or _SUB_RE.match(t)):
                    break
                if len(t) > 20 or _DOTS_RE.search(t):
                    break
                # 标题已经拼够 4 个字就停 —— 再拼下去会把正文首句并进标题
                #（实测「1.1 / 编制背景及过程 / 正文…」会把「正文」也并进来）
                if sum(len(x) for x in parts[1:]) >= 4:
                    break
                # 标题片段：不含句号/分号这类正文标点
                parts.append(t)
                j += 1
            if len(parts) >= 2:
                out.append(" ".join(parts))
                i = j
                continue
        out.append(s)
        i += 1
    return out


def split_pdf_sections(path: str) -> Dict[str, dict]:
    """PDF → `{章号: {title, sections: {小节号: {title, text}}}}`。"""
    try:
        import fitz
        doc = fitz.open(str(path))
    except Exception as e:
        logger.warning(f"[模板切片] 打开 PDF 失败 {path}: {e}")
        return {}
    try:
        text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    finally:
        try:
            doc.close()
        except Exception:
            pass

    chapters: Dict[str, dict] = {}
    cur_ch: Optional[dict] = None
    cur_no: Optional[str] = None
    cur_key: Optional[str] = None
    seen_heading: Dict[str, int] = {}

    def _append(line: str) -> None:
        if cur_ch is None or cur_key is None:
            return
        sec = _ensure_section(cur_ch, cur_key)
        sec["text"] = (sec["text"] + "\n" + line).strip()

    lines = _merge_split_headings([l.strip() for l in text.splitlines() if l.strip()])

    def _is_noise(line: str) -> bool:
        """目录行（带引导点/页码）与「目 录」标题。"""
        return bool(_DOTS_RE.search(line)) or line in ("目 录", "目录", "目　录")

    def _has_section_ahead(i: int, no: str) -> bool:
        """章标题之后（40 行内）必须先出现**本章的 1 级小节** `N.M`。

        🔴 这条是防「附件清单」误判：PDF 前面的附件目录里也有
           「1 土地复垦方案委托函」「2 委托合同」…，它们后面紧跟的是下一项，
           不是 `1.1`。没有这条，正文第 1 章会被附件第 1 项顶掉（实测）。
        """
        n = str(no)
        for j in range(i + 1, min(len(lines), i + 40)):
            s = lines[j]
            if _is_noise(s):
                continue
            m_sec = _SEC_RE.match(s)
            if m_sec and m_sec.group(1).split(".")[0] == n:
                return True
            m_ch = _CH_RE.match(s)
            if m_ch and _is_heading_title(m_ch.group(2), strict=True):
                return False
        return False

    for i, line in enumerate(lines):
        if _is_noise(line):
            continue
        m_sub = _SUB_RE.match(line)
        m_sec = _SEC_RE.match(line)
        m_ch = _CH_RE.match(line)
        m_csec = _CN_SEC_RE.match(line)
        if (m_csec and cur_ch is not None
                and _is_heading_title(m_csec.group(2))):
            cur_key = m_csec.group(1)
            _ensure_section(cur_ch, cur_key)["title"] = m_csec.group(2).strip()
            continue
        if m_sub and cur_ch is not None and _is_heading_title(m_sub.group(2)):
            cur_key = m_sub.group(1)
            _ensure_section(cur_ch, cur_key)["title"] = m_sub.group(2).strip()
            continue
        if m_sec and _is_heading_title(m_sec.group(2)):
            no = m_sec.group(1)
            ch_no = no.split(".")[0]
            if ch_no not in chapters:
                chapters[ch_no] = _new_chapter(int(ch_no), "")
            cur_ch, cur_no, cur_key = chapters[ch_no], ch_no, no
            _ensure_section(cur_ch, cur_key)["title"] = m_sec.group(2).strip()
            continue
        if m_ch and _is_heading_title(m_ch.group(2), strict=True):
            no = m_ch.group(1)
            if int(no) < 1 or int(no) > 30 or not _has_section_ahead(i, no):
                continue
            # 重复出现（页眉）超过 3 次就不当新章
            seen_heading[no] = seen_heading.get(no, 0) + 1
            if seen_heading[no] <= 3:
                cur_ch = chapters.setdefault(no, _new_chapter(int(no), ""))
                if not cur_ch["title"]:
                    cur_ch["title"] = m_ch.group(2).strip()
                cur_no, cur_key = no, None
            continue
        _append(line)

    # 去掉空章/空小节，并只保留 1–14 章（附件区那些「1 委托函」不是章）
    out: Dict[str, dict] = {}
    for no, ch in chapters.items():
        if not (str(no).isdigit() and 1 <= int(no) <= 14):
            continue
        secs = {k: v for k, v in ch["sections"].items() if v.get("text") or v.get("title")}
        if secs:
            ch = dict(ch, sections=secs)
            out[no] = ch
    return out


# ═══════════════════════════════════════════════════════════════
# DOCX
# ═══════════════════════════════════════════════════════════════

def split_docx_sections(path: str) -> Dict[str, dict]:
    """docx → `{章号: {title, sections: {小节号: {title, text}}}}`。

    优先按 Heading 1/2/3 样式；没有样式（或样式名不规范）时退回文本编号规则。
    """
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(str(path))
    except Exception as e:
        logger.warning(f"[模板切片] 打开 docx 失败 {path}: {e}")
        return {}

    _H_RE = re.compile(r'^(?:Heading|标题)\s*([123])$', re.IGNORECASE)

    chapters: Dict[str, dict] = {}
    cur_ch: Optional[dict] = None
    cur_key: Optional[str] = None
    has_styles = False

    def _append(line: str) -> None:
        if cur_ch is None or cur_key is None:
            return
        sec = _ensure_section(cur_ch, cur_key)
        sec["text"] = (sec["text"] + "\n" + line).strip()

    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            text = (block.text or "").strip()
            if not text:
                continue
            style = (block.style.name if block.style is not None else "") or ""
            m_h = _H_RE.match(style)
            if m_h:
                has_styles = True
                lvl = int(m_h.group(1))
                if lvl == 1:
                    m_cn = _CN_CH_RE.match(text)
                    m_num = _CH_RE.match(text)
                    no = None
                    title = text
                    if m_cn:
                        no = _cn_to_int(m_cn.group(1))
                        title = m_cn.group(2).strip() or text
                    elif m_num and _is_heading_title(m_num.group(2), strict=True):
                        no = int(m_num.group(1))
                        title = m_num.group(2).strip()
                    if no is None:
                        continue
                    cur_ch = chapters.setdefault(str(no), _new_chapter(no, title))
                    if not cur_ch["title"]:
                        cur_ch["title"] = title
                    cur_key = None
                else:
                    m_sub = _SUB_RE.match(text) or _SEC_RE.match(text)
                    key = m_sub.group(1) if m_sub else None
                    title = m_sub.group(2).strip() if m_sub else text
                    if cur_ch is None:
                        continue
                    if key is None:
                        # 无编号小节：用标题当键
                        key = title
                    cur_key = key
                    _ensure_section(cur_ch, key)["title"] = title
                continue
            # 无样式兜底：文本编号规则
            if not has_styles and cur_ch is not None:
                m_sec = _SEC_RE.match(text)
                if m_sec and _is_heading_title(m_sec.group(2)):
                    cur_key = m_sec.group(1)
                    _ensure_section(cur_ch, cur_key)["title"] = m_sec.group(2).strip()
                    continue
            _append(text)
        elif isinstance(block, Table) and cur_ch is not None and cur_key is not None:
            for row in block.rows:
                cells = [(c.text or "").strip().replace("\n", " ") for c in row.cells]
                line = " | ".join(cells).strip(" |")
                if line:
                    _append(line)

    # 样式探测失败 → 纯文本规则重来一遍
    if not chapters:
        return _split_docx_text_fallback(doc)

    out: Dict[str, dict] = {}
    for no, ch in chapters.items():
        secs = {k: v for k, v in ch["sections"].items() if v.get("text") or v.get("title")}
        if secs:
            out[no] = dict(ch, sections=secs)
    return out


def _split_docx_text_fallback(doc) -> Dict[str, dict]:
    """没有 Heading 样式时：按 `1 前言` / `第一章 …` / `1.1 …` 文本规则切。"""
    chapters: Dict[str, dict] = {}
    cur_ch: Optional[dict] = None
    cur_key: Optional[str] = None
    for p in doc.paragraphs:
        line = (p.text or "").strip()
        if not line:
            continue
        m_cn = _CN_CH_RE.match(line)
        m_ch = _CH_RE.match(line)
        no = title = None
        if m_cn:
            no, title = _cn_to_int(m_cn.group(1)), m_cn.group(2).strip() or line
        elif m_ch and _is_heading_title(m_ch.group(2), strict=True):
            no, title = int(m_ch.group(1)), m_ch.group(2).strip()
        if no is not None:
            cur_ch = chapters.setdefault(str(no), _new_chapter(no, title))
            if not cur_ch["title"]:
                cur_ch["title"] = title
            cur_key = None
            continue
        m_sec = _SEC_RE.match(line)
        if m_sec and cur_ch is not None and _is_heading_title(m_sec.group(2)):
            cur_key = m_sec.group(1)
            _ensure_section(cur_ch, cur_key)["title"] = m_sec.group(2).strip()
            continue
        m_csec = _CN_SEC_RE.match(line)
        if (m_csec and cur_ch is not None
                and _is_heading_title(m_csec.group(2))):
            cur_key = m_csec.group(1)
            _ensure_section(cur_ch, cur_key)["title"] = m_csec.group(2).strip()
            continue
        if cur_ch is not None and cur_key is not None:
            sec = _ensure_section(cur_ch, cur_key)
            sec["text"] = (sec["text"] + "\n" + line).strip()
    return {k: v for k, v in chapters.items()
            if v["sections"] and k.isdigit() and 1 <= int(k) <= 14}


# ═══════════════════════════════════════════════════════════════
# 缓存 + 对外查询
# ═══════════════════════════════════════════════════════════════

def _read_cache() -> Dict[str, dict]:
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(data: Dict[str, dict]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[模板切片] 写缓存失败: {e}")


def load_sections(path: str, cache: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
    """取某模板文件的章→小节切片（按 mtime 缓存；读失败返回 {}）。"""
    p = Path(str(path or ""))
    if not p.exists():
        return {}
    try:
        mt = p.stat().st_mtime
    except OSError:
        mt = 0
    _own_cache = cache is None
    cache = _read_cache() if cache is None else cache
    ent = cache.get(str(p))
    if ent and ent.get("mtime") == mt:
        return ent.get("chapters") or {}
    low = p.suffix.lower()
    if low == ".pdf":
        chapters = split_pdf_sections(str(p))
    elif low in (".docx", ".docm"):
        chapters = split_docx_sections(str(p))
    else:
        chapters = {}
    cache[str(p)] = {"mtime": mt, "chapters": chapters}
    if _own_cache:                      # 重新解析过的结果落盘，下次进程直接命中
        _write_cache(cache)
    return chapters


def chapter_sections(path: str, chapter_no: int) -> Dict[str, dict]:
    """某模板某章的全部小节 `{小节号: {title, text}}`（没有返回 {}）。"""
    chapters = load_sections(path)
    ch = chapters.get(str(int(chapter_no))) or {}
    return ch.get("sections") or {}


_EX_CACHE: Dict[tuple, Dict[str, str]] = {}


def section_excerpts(path: str, chapter_no: int, per_section: int = 900) -> Dict[str, str]:
    """某模板某章的**一级小节**摘录 `{4.1: 标题+正文+其下三级小节}`。

    🔴 一级小节（4.1）本身常常只有标题，正文都在 4.1.1/4.1.2 里 ——
       只取 `4.1` 会拿到空串。这里把 `4.1.*` 子树按序拼起来。
    没有数字编号的模板（如稳评「一、」）按整键分组，行为不变。
    """
    p = Path(str(path or ""))
    try:
        _mt = p.stat().st_mtime
    except OSError:
        _mt = 0
    _ck = (str(p), int(chapter_no), int(per_section), _mt)
    if _ck in _EX_CACHE:
        return _EX_CACHE[_ck]
    secs = chapter_sections(path, chapter_no)
    groups: Dict[str, List[str]] = {}
    for key in secs:
        parts = str(key).split(".")
        top = ".".join(parts[:2]) if len(parts) >= 2 and parts[0].isdigit() else str(key)
        groups.setdefault(top, []).append(key)
    out: Dict[str, str] = {}
    for top in sorted(groups.keys(), key=_sec_sort_key):
        block: List[str] = []
        for key in sorted(groups[top], key=_sec_sort_key):
            s = secs[key]
            text = (s.get("text") or "").strip()
            if not text:
                continue
            title = (s.get("title") or "").strip()
            block.append((f"{key} {title}".strip() + "\n" + text).strip())
        if not block:
            continue
        body = "\n".join(block)
        if len(body) > per_section:
            body = _cut_at_sentence(body, per_section)
        out[top] = body
    _EX_CACHE[_ck] = out
    return out


_SAMPLE_CORPUS_CACHE: Dict[str, str] = {}


def sample_corpus(domain: str, limit_per_file: int = 60000) -> str:
    """该域**所有范文**的正文拼成一段（供「范文残留文字」清理比对）。

    只取正文（章/小节），不含封面与附件；按域缓存。取不到返回空串。
    """
    key = str(domain)
    if key in _SAMPLE_CORPUS_CACHE:
        return _SAMPLE_CORPUS_CACHE[key]
    try:
        import sqlite3
        db = _BACKEND_DIR / "data" / "knowledge_base.db"
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT name, category, template_file_path FROM templates "
            "WHERE domain=? AND is_active=1 "
            "AND (template_file_path LIKE '%.docx' OR template_file_path LIKE '%.pdf')",
            (key,)).fetchall()
        conn.close()
    except Exception:
        return ""
    chunks: List[str] = []
    for name, category, rel in rows:
        if not rel or str(category or "") == "格式规范":
            continue
        if any(k in str(name or "") for k in ("评审表", "字体", "文字格式")):
            continue
        p = _BACKEND_DIR / "storage" / str(rel)
        if not p.exists():
            continue
        chs = load_sections(str(p))
        text = "\n".join(
            (s.get("text") or "")
            for ch in chs.values()
            for s in (ch.get("sections") or {}).values()
        )
        if text:
            chunks.append(text[:limit_per_file])
    out = "\n".join(chunks)
    _SAMPLE_CORPUS_CACHE[key] = out
    return out


def supplemental_block(domain: str, chapter_no: int, base_text: str = "",
                       exclude_path: str = "", total_limit: int = 9000) -> str:
    """**多模板同章小节参考**：把库里所有范文的同一章按小节对齐成提示词块。

    业主 2026-11-02：「都要切，每章节每小节。」
    与「本章模板底稿」（单份整章）互补：这里给的是**其他范文**的同小节原文，
    让模板较薄的章节（复垦 ch6/8/9）也有对应写法可学。
    """
    try:
        import sqlite3
    except Exception:
        return ""
    db = _BACKEND_DIR / "data" / "knowledge_base.db"
    if not db.exists():
        return ""
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT name, category, template_file_path FROM templates "
            "WHERE domain=? AND is_active=1 "
            "AND (template_file_path LIKE '%.docx' OR template_file_path LIKE '%.pdf') "
            "ORDER BY id", (str(domain),)).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"[模板切片] 查询模板库失败: {e}")
        return ""
    if not rows:
        return ""

    _ex_path = str(Path(str(exclude_path)).resolve()) if exclude_path else ""
    # 🔴 稳评模板的章号是「案卷章号」（第1章=评审表），与本系统 DB32 章号不同 ——
    #    用 `template_chapter_chunker.DB32_TO_TEMPLATE_KEY` 换算成模板原章号（键+1）。
    _ch_no = int(chapter_no)
    if str(domain) == "stability":
        try:
            from app.services.template_chapter_chunker import DB32_TO_TEMPLATE_KEY
            _k = DB32_TO_TEMPLATE_KEY.get(_ch_no)
            if not _k:
                return ""
            _ch_no = int(_k) + 1
        except Exception:
            return ""
    _groups: Dict[str, List[tuple]] = {}
    _seen_texts = set()
    for name, category, rel in rows:
        if not rel:
            continue
        # 稳评模板库里混着格式规范/字体/评审表 —— 不是正文范文，不进参考块
        if str(category or "") == "格式规范":
            continue
        if any(k in str(name or "") for k in ("评审表", "字体", "文字格式")):
            continue
        p = _BACKEND_DIR / "storage" / str(rel)
        if _ex_path and str(p.resolve()) == _ex_path:
            continue
        if not p.exists():
            continue
        ex = section_excerpts(str(p), _ch_no, per_section=800)
        if not ex:
            continue
        _src = re.sub(r'[（(].*?[)）]', '', str(name or p.stem)).strip()[:18]
        for sec, text in ex.items():
            _head = text[:48]
            if _head in _seen_texts:
                continue
            if base_text and _head[:30] in base_text:
                continue                       # 与所选模板的整章底稿重复
            _seen_texts.add(_head)
            _groups.setdefault(sec, []).append((_src, text))

    if not _groups:
        return ""
    lines = [
        "\n## 📚 同章多模板参考（按小节对齐 · 来自库内其他范文）",
        "🔴 只学**结构与行文写法**：下面是各范文同一小节的原文；"
        "别的地名/面积/单位/日期/文号一律不得照抄，本项目只用自己的事实卡。",
    ]
    _budget = int(total_limit)
    for sec in sorted(_groups.keys(), key=_sec_sort_key):
        if _budget <= 0:
            break
        lines.append(f"### {sec}")
        for src, text in _groups[sec][:2]:          # 每小节最多两份，防爆 prompt
            piece = text if len(text) <= _budget else _cut_at_sentence(text, _budget)
            lines.append(f"〔{src}〕\n{piece}")
            _budget -= len(piece) + len(src) + 4
            if _budget <= 0:
                break
    return "\n".join(lines).strip()


def _sec_sort_key(k: str):
    parts = re.findall(r'\d+', str(k))
    return [int(x) for x in parts] or [999]


def _cut_at_sentence(text: str, limit: int) -> str:
    cut = text[:limit]
    for sep in ("。", "；", "\n"):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[: i + 1]
    return cut
