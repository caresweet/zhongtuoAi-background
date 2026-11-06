# -*- coding: utf-8 -*-
"""范文 / 模板的「结构画像」—— docx 与 PDF 都能分析。

为什么
------
模板库里不只有 docx 模板，还有 PDF 范文（明远佳苑、王元佳苑、宁淮铁路拌合站、
污水处理厂……）。它们虽然不能直接当版式模板（没有目录域和样式），但**结构**
（有哪些章、哪些节、每节是固定话术还是换数据还是重写）和**版式倾向**
（标题多大、正文多大）完全可以读出来，用于：

- 提示词：同地区没有 docx 模板时，按范文的章节结构写；
- 上传分析：告诉用户「这份范文有 10 章 97 节、其中 30 节是项目专属内容」；
- 质量核对：范文之间章节差在哪，哪些章是我们漏掉的。

与既有模块的分工
----------------
- `reclamation_template_profile.py`：docx 的逐节三色画像（生成时注入 prompt）；
- `template_style_profile.py`：docx 的字体字号（生成时排版）；
- 本模块：**任意格式**的结构 + 版式倾向汇总，落 `corpus_<domain>_<stem>.json`。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
_OUT_DIR = _BACKEND / "data" / "template_profiles"
_PROFILE_VERSION = 1

_HEAD_CN = re.compile(r'^第([一二三四五六七八九十百\d]+)[章部分]\s*')
# 编号 + 分隔 + 标题；分隔后必须还有文字，但**不能吃掉标题首字**。
# 「1编制说明」（无空格）也要认 —— 稳评模板大量这么写。
_HEAD_NUM = re.compile(
    r'^(\d{1,2}(?:\.\d{1,2}){0,2})(?:[、.．]\s*|\s+|(?=[^\d\s.．、]))(?=\S)')
_TOC_STYLE = ("toc",)


def _no_of(title: str) -> Tuple[str, str]:
    """标题 → (编号串, 纯标题)。不是编号标题返回 ("", 原文)。"""
    t = re.sub(r'\s+', ' ', str(title or "")).strip()
    m = _HEAD_CN.match(t)
    if m:
        try:
            from app.agent.chapter_definitions import cn_num_to_int
            n = cn_num_to_int(m.group(1))
            if n:
                return str(n), t[m.end():].strip(" 、．.：:")
        except Exception:
            pass
    m = _HEAD_NUM.match(t)
    if m and len(t) <= 60:
        return m.group(1), t[m.end():].strip()
    return "", t


def _level_of(no: str, style_level: int = 0) -> int:
    if style_level:
        return style_level
    if not no:
        return 0
    return no.count(".") + 1


# ═══════════════════════════════════════════════════════════════
# DOCX
# ═══════════════════════════════════════════════════════════════

def _para_size_bold(para) -> Tuple[float, bool]:
    size, bold = 0.0, False
    for r in para.runs:
        if not str(r.text or "").strip():
            continue
        try:
            size = max(size, float(r.font.size.pt) if r.font.size else 0.0)
        except Exception:
            pass
        bold = bold or bool(r.font.bold)
    return size, bold


def _norm_title(t: str) -> str:
    return re.sub(r'[\s　、，。；：:．.（）()]', '', str(t or ""))


def _std_chapter_no(title: str, domain: str) -> int:
    """标题是否命中本项目标准章名（「编制总则」等），返回章号；不中返回 0。

    字号识别会把正文里的大字短行误当章标题（实测洪泽模板被拆成 109 章），
    所以**没有真正 Heading 1 的文档**只认标准章名。
    """
    try:
        from app.services.template_rules import expected_structure
        expected = expected_structure(domain)
    except Exception:
        return 0
    t = _norm_title(title)
    if len(t) < 3:
        return 0
    for n, std in expected.items():
        s = _norm_title(std)
        # 🔴 只认完全一致：「风险等级」是「确定风险等级」的子串、
        #    「工作建议」是「防范和化解风险的工作建议」的子串，宽松包含会
        #    把正文定义行当成章标题（洪泽模板实测多出 5 个假章）。
        if s and t == s:
            return int(n)
    return 0


def _docx_sections(path: Path, domain: str = "reclamation") -> List[Dict[str, Any]]:
    from docx import Document
    doc = Document(str(path))

    items = []
    for para in doc.paragraphs:
        txt = re.sub(r'\s+', ' ', str(para.text or "")).strip()
        if not txt:
            continue
        style = str(getattr(getattr(para, "style", None), "name", "") or "").lower()
        if any(style.startswith(t) for t in _TOC_STYLE):
            continue
        level = 0
        if style.startswith(("heading 1", "heading1", "标题 1", "标题1")):
            level = 1
        elif style.startswith(("heading 2", "heading2", "标题 2", "标题2")):
            level = 2
        elif style.startswith(("heading 3", "heading3", "标题 3", "标题3")):
            level = 3
        size, bold = _para_size_bold(para)
        items.append({"text": txt, "style": style, "level": level,
                      "size": size, "bold": bold})

    # 文档是否真的把章标题套了 Heading 1（有编号）—— 只套了 Heading 2 的
    # 稳评模板（清江浦）章标题是 Normal + 大字号，必须走字号识别。
    numbered_h1 = 0
    for it in items:
        if it["level"] == 1:
            no, _ = _no_of(it["text"])
            if no:
                numbered_h1 += 1
    proper_styles = numbered_h1 >= 3

    # 正文字号：长段落里出现最多的字号
    body_sizes = Counter(it["size"] for it in items
                         if it["size"] and len(it["text"]) >= 20)
    body_size = body_sizes.most_common(1)[0][0] if body_sizes else 0.0

    sections: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    seen_numbered = False
    for it in items:
        txt = it["text"]
        no, title = _no_of(txt)
        level = it["level"]
        if not proper_styles:
            # 不看样式给的层级（清江浦把封面信息行套成 Heading 3，会出现
            # 「报告编制单位：…」这种假小节）；只认**编号**或**标准章名**。
            level = 0
            looks_head = (len(txt) <= 50
                          and not txt.endswith(("。", "；", "，", "：", ":"))
                          and (it["size"] >= (body_size + 1.2 if body_size else 0)
                               or (it["bold"] and bool(no))))
            if not looks_head:
                if cur is not None:
                    cur["body"].append(txt)
                continue
            if no:
                level = _level_of(no)
                if level == 1:
                    # 「1 表土剥离」这种列表行也带编号，必须命中标准章名；
                    # 但「第四章 决策事项基本情况」的**第X章**是可信的章结构，
                    # 章节改名（各区模板口径不同）照样认。
                    if _HEAD_CN.match(txt):
                        pass
                    else:
                        std = _std_chapter_no(title or txt, domain)
                        if not std:
                            if cur is not None:
                                cur["body"].append(txt)
                            continue
                        no = str(std)
            else:
                std = _std_chapter_no(title or txt, domain)
                if not std:
                    if cur is not None:
                        cur["body"].append(txt)
                    continue
                no, level = str(std), 1
        else:
            if not level:
                if cur is not None:
                    cur["body"].append(txt)
                continue
        level = min(int(level or 1), 3)
        if level == 1 and not no and not seen_numbered:
            continue      # 封面/扉页套了标题样式的公司名
        if no:
            seen_numbered = True
        if cur is not None:
            sections.append(cur)
        cur = {"no": no, "title": title or txt, "level": level, "body": []}
    if cur is not None:
        sections.append(cur)
    return sections


# ═══════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════

def _pdf_text_and_fonts(doc) -> Tuple[Counter, Dict[str, Any]]:
    """全文字号分布 + 大字号候选。

    🔴 封面/目录页的大字（26pt 项目名）不是章标题，统计大字号时跳过前两页。
    """
    sizes: Counter = Counter()
    big_sizes: Counter = Counter()
    lines: List[Tuple[float, str]] = []
    for pno, page in enumerate(doc):
        d = page.get_text("dict") or {}
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans") or []
                text = "".join(str(s.get("text") or "") for s in spans).strip()
                if not text:
                    continue
                size = max((float(s.get("size") or 0) for s in spans), default=0.0)
                sizes[round(size, 1)] += len(text)
                if pno >= 2 and len(text) <= 60 and size:
                    lines.append((size, text))
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    for size, _ in lines:
        if body and size >= body + 1.5:
            big_sizes[round(size, 1)] += 1
    return sizes, {"body": body, "big": big_sizes, "lines": lines}


def _pdf_toc_sizes(doc, toc) -> Dict[int, float]:
    """按目录条目回原页找标题字号：{级别: 字号}（比「最大字号」稳）。"""
    per_level: Dict[int, Counter] = {1: Counter(), 2: Counter(), 3: Counter()}
    for lvl, title, page in toc:
        lvl = int(lvl)
        if lvl not in per_level:
            continue
        idx = int(page) - 1
        if not (0 <= idx < doc.page_count):
            continue
        key = str(title).strip()[:8]
        if not key:
            continue
        best = 0.0
        for block in (doc[idx].get_text("dict") or {}).get("blocks", []):
            for line in block.get("lines", []):
                text = "".join(str(s.get("text") or "")
                               for s in line.get("spans", [])).strip()
                if key in text:
                    best = max(best, max((float(s.get("size") or 0)
                                          for s in line.get("spans", [])), default=0.0))
        if best:
            per_level[lvl][round(best, 1)] += 1
    return {lvl: c.most_common(1)[0][0] for lvl, c in per_level.items() if c}


def _pdf_sections(path: Path, domain: str = "reclamation") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    import fitz
    with fitz.open(str(path)) as doc:
        toc = []
        try:
            toc = doc.get_toc(simple=True) or []
        except Exception:
            toc = []
        _, info = _pdf_text_and_fonts(doc)
        body = info["body"]
        toc_sizes = _pdf_toc_sizes(doc, toc) if toc else {}

        sections: List[Dict[str, Any]] = []
        if len(toc) >= 3:
            for lvl, title, page in toc:
                if int(lvl) > 3:
                    continue
                no, clean = _no_of(str(title))
                # 目录里把公司名/封面标题列为一级条目的，不算章
                if int(lvl) == 1 and not no and (
                        "公司" in clean or len(clean) < 6):
                    continue
                sections.append({"no": no, "title": clean, "level": int(lvl),
                                 "body": [], "page": int(page)})
            # 正文按页归属（够做数据位识别，不做逐段归类）
            for i, sec in enumerate(sections):
                start = int(sec.get("page") or 1) - 1
                end = int(sections[i + 1].get("page") or doc.page_count) if i + 1 < len(sections) else doc.page_count
                chunks = []
                for pno in range(max(0, start), min(doc.page_count, max(end, start + 1))):
                    chunks.append(doc[pno].get_text("text"))
                sec["body"] = _split_paragraphs("\n".join(chunks))
            info["toc_sizes"] = toc_sizes
            return sections, info

        # 没有目录：按字号判定标题
        big = sorted(info["big"].keys())
        rank = {s: len(big) - i for i, s in enumerate(big)}   # 越大字号层级越高
        cur = None
        for size, text in info["lines"]:
            if size >= body + 1.5:
                no, clean = _no_of(text)
                lvl = min(3, rank.get(round(size, 1), 1))
                if lvl == 1:
                    std = _std_chapter_no(clean or text, domain)
                    if not std:
                        if cur is not None:
                            cur["body"].append(text)
                        continue
                    no = str(std)
                if cur is not None:
                    sections.append(cur)
                cur = {"no": no, "title": clean or text, "level": lvl, "body": []}
            elif cur is not None and len(text) >= 20:
                cur["body"].append(text)
        if cur is not None:
            sections.append(cur)
        return sections, info


def _split_paragraphs(text: str) -> List[str]:
    out: List[str] = []
    buf: List[str] = []
    for raw in str(text or "").splitlines():
        s = raw.strip()
        if not s:
            if buf:
                out.append(" ".join(buf)); buf = []
            continue
        buf.append(s)
        if s.endswith(("。", "；", "！", "？")):
            out.append(" ".join(buf)); buf = []
    if buf:
        out.append(" ".join(buf))
    return [x for x in out if len(x) >= 12]


# ═══════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════

def _typography_docx(path: Path) -> Dict[str, Any]:
    try:
        from app.services.template_style_profile import build_style_profile
        prof = build_style_profile(path) or {}
        return {k: {"font": prof.get(k, {}).get("font"),
                    "size": prof.get(k, {}).get("size")}
                for k in ("h1", "h2", "h3", "body", "table", "caption")}
    except Exception:
        return {}


def _typography_pdf(info: Dict[str, Any]) -> Dict[str, Any]:
    body = float(info.get("body") or 0)
    out = {"body": {"font": "", "size": body}}
    toc_sizes = info.get("toc_sizes") or {}
    if toc_sizes:
        for i, key in enumerate(("h1", "h2", "h3"), start=1):
            if toc_sizes.get(i):
                out[key] = {"font": "", "size": float(toc_sizes[i])}
        return out
    # 没有目录：按「正文之外的大字号」从大到小排
    big = {float(k): int(v) for k, v in (info.get("big") or {}).items()}
    ordered = sorted(big, reverse=True)
    for i, key in enumerate(("h1", "h2", "h3")):
        if i < len(ordered):
            out[key] = {"font": "", "size": ordered[i]}
    return out


_MD_HEAD = re.compile(r'^(#{1,6})\s+(.+?)\s*#*$')


def _text_sections(path: Path) -> List[Dict[str, Any]]:
    """Markdown / HTML / 纯文本范文 → 章节列表（无版式信息，只有结构）。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in (".html", ".htm"):
        import html as _html
        for n in range(6, 0, -1):
            text = re.sub(rf'<h{n}[^>]*>(.*?)</h{n}>',
                          lambda m, _n=n: "\n" + "#" * _n + " " + re.sub(r'<[^>]+>', '', m.group(1)) + "\n",
                          text, flags=re.S | re.I)
        text = re.sub(r'<(br|/p|/div|/li|/tr)\s*/?>', "\n", text, flags=re.I)
        text = re.sub(r'<[^>]+>', '', text)
        text = _html.unescape(text)

    raw: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _MD_HEAD.match(s)
        if m:
            level = len(m.group(1))
            no, title = _no_of(m.group(2))
            if cur:
                raw.append(cur)
            cur = {"no": no, "title": title, "level": level, "body": []}
        elif cur is not None:
            cur["body"].append(s)
    if cur:
        raw.append(cur)
    return raw


def analyze_template_file(path: str | Path,
                          domain: str = "reclamation") -> Optional[Dict[str, Any]]:
    """分析一份模板/范文（docx 或 pdf），返回结构 + 版式倾向 + 逐节分类。"""
    from app.services.reclamation_template_profile import classify_block

    p = Path(str(path or ""))
    if not p.exists():
        return None
    suffix = p.suffix.lower()
    if suffix not in (".docx", ".pdf", ".md", ".txt", ".html", ".htm"):
        return None

    try:
        if suffix == ".docx":
            raw = _docx_sections(p, domain)
            typo = _typography_docx(p)
            fmt = "docx"
        elif suffix == ".pdf":
            raw, info = _pdf_sections(p, domain)
            typo = _typography_pdf(info)
            fmt = "pdf"
        else:
            raw, typo, fmt = _text_sections(p), {}, suffix.lstrip(".")
    except Exception as e:
        logger.info(f"[范文画像] 解析失败 {p}: {e}")
        return None

    sections = []
    counts = {"fixed": 0, "slot": 0, "create": 0}
    slots: Dict[str, Dict[str, Any]] = {}
    for sec in raw:
        cls = classify_block(sec.get("body") or [], sec.get("title") or "")
        if cls["fixed"]:
            counts["fixed"] += 1
        if cls["slot"] or cls["slots"]:
            counts["slot"] += 1
        if cls["create"]:
            counts["create"] += 1
        for s in cls["slots"]:
            k = s.get("key") or ""
            if not k:
                continue
            d = slots.setdefault(k, {"key": k, "examples": [], "sections": []})
            ex = s.get("example") or ""
            if ex and ex not in d["examples"] and len(d["examples"]) < 3:
                d["examples"].append(ex)
            loc = f"{sec.get('no') or ''} {sec.get('title') or ''}".strip()
            if loc not in d["sections"] and len(d["sections"]) < 5:
                d["sections"].append(loc)
        sections.append({
            "no": sec.get("no") or "",
            "title": sec.get("title") or "",
            "level": int(sec.get("level") or 1),
            "chars": sum(len(x) for x in (sec.get("body") or [])),
            "prior": cls["prior"],
            "fixed": len(cls["fixed"]),
            "slot": len(cls["slot"]),
            "create": len(cls["create"]),
            "keys": cls["keys"],
        })

    chapters = [s for s in sections if s["level"] == 1]
    # 复垦范文：与规程第六章的标准大纲对账（缺章/多章）
    outline_check: Dict[str, Any] = {}
    if domain == "reclamation" and len(chapters) >= 5:
        try:
            from app.services.reclamation_outline import check_chapters
            _found = {}
            for c in chapters:
                try:
                    _found[int(c.get("no") or 0)] = c.get("title") or ""
                except (TypeError, ValueError):
                    continue
            if _found:
                outline_check = check_chapters(_found, include_post=False)
        except Exception as e:
            logger.info(f"[标准大纲] 对账跳过: {e}")
    return {
        "version": _PROFILE_VERSION,
        "source": str(p),
        "format": fmt,
        "domain": domain,
        "title": _title_of(p, raw),
        "chapters": len(chapters),
        "sections": len(sections),
        "counts": counts,
        "data_slots": list(slots.values()),
        "typography": typo,
        "outline_check": outline_check,
        "structure": [{"no": c["no"], "title": c["title"], "level": 1}
                      for c in chapters],
        "items": sections,
    }


def _title_of(path: Path, raw: List[Dict[str, Any]]) -> str:
    for sec in raw[:3]:
        t = str(sec.get("title") or "").strip()
        if 4 <= len(t) <= 80 and any(k in t for k in ("报告", "方案", "评估")):
            return t
    return path.stem


def save_corpus_profile(prof: Dict[str, Any]) -> Path:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(str(prof.get("source") or "")).stem
    p = _OUT_DIR / f"corpus_{prof.get('domain','x')}_{stem}.json"
    p.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_corpus_profile(path: str | Path,
                        domain: str = "reclamation") -> Optional[Dict[str, Any]]:
    stem = Path(str(path or "")).stem
    p = _OUT_DIR / f"corpus_{domain}_{stem}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if data.get("version") == _PROFILE_VERSION else None
    except Exception:
        return None
