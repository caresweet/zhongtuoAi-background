# -*- coding: utf-8 -*-
"""从招标文件「响应文件格式」章里量出**排版规格**（字体/字号/对齐/缩进/行距）。

`bidding_format_spec` 只管结构（几章、几项、哪些示范格式）；这里补版式：
正文 12 磅宋体、首行缩进 2 字、固定行距 26 磅这类数值，不同代理机构差别很大
（洪泽磋商件是固定值 26 磅 + 首行 1 字，公司老体例是 1.5 倍行距 + 首行 2 字），
写死任何一套都会和招标文件对不上。

做法：把 .doc 另存成 .docx（LibreOffice），按章节/示范格式分段，逐段量
  jc / w:ind / w:spacing / rFonts / sz / b
把同一角色的**众数**记下来：

    spec["style"] = {
      "chapter_title": {...},
      "h1_by_no": {"一": {...}, "二": {...}, ...},
      "section": {"一": {"plain": {...}, "item": {...}, "sign": {...},
                         "note": {...}, "field": {...}},
                  ...},
      "form": {"示范格式一": {"title": {...}, "body": {...}, "field": {...},
                            "sign": {...}, "note": {...}}, ...},
      "page": {...},
    }

生成时逐段套用；读不到时返回空 dict，调用方走公司兜底（见 DEFAULT_PROFILE）。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROLE_H1 = re.compile(r'^([一二三四五六七八九十]+)、(.{1,60})$')
_ROLE_H1_PAREN = re.compile(r'^[（(]([一二三四五六七八九十]+)[）)](.{1,60})$')
_ROLE_FORM = re.compile(r'^示范格式[一二三四五六七八九十]+$')
_SIGN_START = ("供应商", "日期", "日 期", "日    期", "日  期", "委托单位",
               "企业名称", "法定代表人（或受托人）")
_ITEM_START = re.compile(r'^(（?\d+[）)、.]|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]\d*)')


def _to_docx(path: Path) -> Optional[Path]:
    """doc/docx → 临时 docx；没有 LibreOffice 时返回原 docx 或 None。"""
    if path.suffix.lower() == ".docx":
        return path
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="bidfmt_"))
    try:
        subprocess.run([soffice, "--headless", "--norestore", "--convert-to",
                        "docx", "--outdir", str(tmp), str(path)],
                       check=True, capture_output=True, timeout=300)
        out = next(tmp.glob("*.docx"), None)
        if out is None:
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        return out
    except Exception as e:
        logger.warning(f"[格式规格] {path.name} 转 docx 失败: {e}")
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def _para_fmt(p) -> Dict[str, Any]:
    from docx.oxml.ns import qn
    el = p._element
    pPr = el.find(qn('w:pPr'))
    d: Dict[str, Any] = {}
    if pPr is not None:
        jc = pPr.find(qn('w:jc'))
        if jc is not None:
            d["align"] = jc.get(qn('w:val'))
        ind = pPr.find(qn('w:ind'))
        if ind is not None:
            attrs = {k.split('}')[-1]: v for k, v in ind.attrib.items()}
            if attrs.get("firstLineChars"):
                d["first_line_chars"] = int(attrs["firstLineChars"]) / 100
            elif attrs.get("firstLine"):
                d["first_line_pt"] = round(int(attrs["firstLine"]) / 20, 1)
        sp = pPr.find(qn('w:spacing'))
        if sp is not None:
            attrs = {k.split('}')[-1]: v for k, v in sp.attrib.items()}
            try:
                line = int(attrs.get("line") or 0)
                rule = attrs.get("lineRule") or "auto"
                if line and rule == "exact":
                    d["line_exact_pt"] = round(line / 20, 1)
                elif line and rule == "atLeast":
                    d["line_min_pt"] = round(line / 20, 1)
                elif line:
                    d["line_multiple"] = round(line / 240, 2)
            except ValueError:
                pass
    for r in p.runs:
        if not (r.text or "").strip():
            continue
        rPr = r._element.find(qn('w:rPr'))
        if rPr is None:
            break
        f = rPr.find(qn('w:rFonts'))
        if f is not None:
            d["font"] = f.get(qn('w:eastAsia')) or f.get(qn('w:ascii'))
        sz = rPr.find(qn('w:sz'))
        if sz is not None:
            try:
                d["size"] = int(sz.get(qn('w:val'))) / 2
            except ValueError:
                pass
        b = rPr.find(qn('w:b'))
        if b is not None:
            d["bold"] = b.get(qn('w:val')) not in ("0", "false")
        break
    return d


def _mode(fmts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同一角色多段取众数；缩进类把「无缩进」也算一票。"""
    fmts = [f for f in fmts if f]
    if not fmts:
        return {}
    out: Dict[str, Any] = {}
    keys = set().union(*(f.keys() for f in fmts))
    for k in keys:
        if k in ("first_line_chars", "first_line_pt"):
            vals = [f.get(k) for f in fmts]      # None = 该段无缩进
        else:
            vals = [f[k] for f in fmts if f.get(k) is not None]
        if not vals:
            continue
        try:
            counts = {v: vals.count(v) for v in set(vals)}
            top = max(counts.values())
            # 无缩进和有缩进票数相同时，按「有缩进」处理（招标文件里
            # 少写一个 firstLine 的段落，不等于要顶格排版）
            has = [v for v in counts if v is not None and counts[v] == top]
            best = has[0] if has else None
        except TypeError:
            best = vals[0]
        if best is not None:
            out[k] = best
    return out


def _chapter_range(doc) -> List[Any]:
    """返回格式章范围内的段落（从章标题到下一章）。"""
    ps = list(doc.paragraphs)
    cands = []
    for i, p in enumerate(ps):
        norm = re.sub(r'\s+', '', p.text or "")
        if not norm.startswith("第") or "文件格式" not in norm:
            continue
        # 目录里的同名条目后面不会紧跟封面；有的封面写「正本/副本」，
        # 有的直接写项目名称/投标人，两种都算
        tail = [re.sub(r'\s+', '', q.text or "") for q in ps[i + 1:i + 8]]
        if any(t.startswith("【正") or "正本" in t or "副本" in t
               or t.startswith(("项目名称", "项目编号", "投标人", "供应商"))
               for t in tail if t):
            cands.append(i)
    # 没有封面特征的，取最后一个（前面出现的是目录/正文引用）
    start = cands[-1] if cands else None
    if start is None:
        idx = [i for i, p in enumerate(ps)
               if "文件格式" in re.sub(r'\s+', '', p.text or "")]
        start = idx[-1] if idx else None
    if start is None:
        return []
    out = []
    for p in ps[start:]:
        norm = re.sub(r'\s+', '', p.text or "")
        if out and norm.startswith("第") and re.match(r'^第[一二三四五六七八九十]+章', norm):
            break
        out.append(p)
    return out


def _kind_of(text: str) -> str:
    if text.startswith("注"):
        return "note"
    if text.startswith(_SIGN_START):
        return "sign"
    # 只有「短标签：」算字段行；长句里带冒号的是正文
    if len(text) <= 20 and ("：" in text or ":" in text):
        return "field"
    if _ITEM_START.match(text):
        return "item"
    return "plain"


def profile_from_docx(path: str | Path) -> Dict[str, Any]:
    from docx import Document
    doc = Document(str(path))
    ps = _chapter_range(doc)
    if not ps:
        return {}

    profile: Dict[str, Any] = {"chapter_title": _para_fmt(ps[0])}
    h1_by_no: Dict[str, Any] = {}
    section: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    # 用「表名」而不是「示范格式N」做键：.doc 里的示范格式四标签常是文本框，
    # 转 docx 会丢；表名（16 磅加粗那行）不会丢，换项目也照样能分。
    form: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    label_pool: List[Dict[str, Any]] = []
    cover: Dict[str, Any] = {}

    cur_sec = ""
    cur_form = ""
    in_forms = False
    for p in ps[1:]:
        text = re.sub(r'\s+', '', p.text or "")
        if not text:
            continue
        # 「一、……」在示范格式正文里是条目（承诺书一、二、），不是章节标题
        _hm = _ROLE_H1.match(text) or _ROLE_H1_PAREN.match(text)
        if _hm and not cur_form and len(text) <= 30:
            cur_sec = _hm.group(1)[0]
            cur_form = ""
            h1_by_no[cur_sec] = _para_fmt(p)
            continue
        # 封面（章标题之后、一、之前）：正/副本、项目名称、项目编号、供应商、日期
        if not cur_sec:
            if text.startswith("【正"):
                cover["copy"] = _para_fmt(p)
            elif text.startswith("项目名称"):
                cover["name"] = _para_fmt(p)
            elif text.startswith("项目编号"):
                cover["no"] = _para_fmt(p)
            elif text.startswith("供应商"):
                cover["supplier"] = _para_fmt(p)
            elif "年" in text and "月" in text and "日" in text:
                cover["date"] = _para_fmt(p)
            continue
        fmt = _para_fmt(p)
        if _ROLE_FORM.match(text):
            label_pool.append(fmt)
            in_forms = True
            continue
        # 表名：≥15 磅、加粗、短行 —— 一个示范格式从这里开始（只在示范格式区）
        if (in_forms and (fmt.get("size") or 0) >= 15 and fmt.get("bold")
                and len(text) <= 40):
            cur_form = text
            form.setdefault(cur_form, {}).setdefault("title", []).append(fmt)
            continue
        if cur_form:
            form.setdefault(cur_form, {}).setdefault(_kind_of(text), []).append(fmt)
        elif cur_sec:
            section.setdefault(cur_sec, {}).setdefault(_kind_of(text), []).append(fmt)

    profile["h1_by_no"] = {k: v for k, v in h1_by_no.items()}
    profile["h1"] = h1_by_no.get("一") or next(iter(h1_by_no.values()), {})
    profile["section"] = {no: {k: _mode(v) for k, v in ks.items()}
                          for no, ks in section.items()}
    profile["form"] = {no: {k: _mode(v) for k, v in ks.items()}
                       for no, ks in form.items()}
    if label_pool:
        profile["form_label"] = _mode(label_pool)

    # 正文：先取出现最多的字号（排除章节标题和示范格式表名），再在该字号内
    # 取行距/首行缩进的众数——正文用 12 磅还是 15 磅，各家不一样
    all_pool: List[Dict[str, Any]] = []
    item_pool: List[Dict[str, Any]] = []
    for p in ps[1:]:
        text = re.sub(r'\s+', '', p.text or "")
        if not text or len(text) < 10:
            continue
        if (_ROLE_H1.match(text) or _ROLE_H1_PAREN.match(text)
                or _ROLE_FORM.match(text)):
            continue
        fmt = _para_fmt(p)
        if not fmt.get("size"):
            continue
        if (fmt.get("size") or 0) >= 15 and fmt.get("bold") and len(text) <= 40:
            continue
        all_pool.append(fmt)
        if _ITEM_START.match(text):
            item_pool.append(fmt)
    if all_pool:
        _sizes = [f["size"] for f in all_pool]
        _body_size = max(set(_sizes), key=_sizes.count)
        _pool = [f for f in all_pool if f["size"] == _body_size]
        _bm = _mode(_pool)
        body: Dict[str, Any] = {"size": _body_size,
                                "font": _bm.get("font") or "宋体",
                                "align": _bm.get("align") or "both"}
        if _bm.get("first_line_chars"):
            body["first_line_chars"] = _bm["first_line_chars"]
        if _bm.get("line_exact_pt"):
            body["line_exact_pt"] = _bm["line_exact_pt"]
        profile["body"] = body
        _ip = [f for f in item_pool if f.get("size") == _body_size]
        if _ip:
            _im = _mode(_ip)
            item = {"size": _body_size, "font": body["font"],
                    "align": _im.get("align") or body["align"]}
            if _im.get("first_line_chars"):
                item["first_line_chars"] = _im["first_line_chars"]
            if _im.get("line_exact_pt"):
                item["line_exact_pt"] = _im["line_exact_pt"]
            profile["item"] = item

    # 人员表表头字号（docx 里表头文字可能被文本框吞掉，读不到就让调用方兜底）
    for t in doc.tables:
        if len(t.columns) < 5:
            continue
        sizes = []
        try:
            for c in t.rows[0].cells:
                for p in c.paragraphs:
                    f = _para_fmt(p)
                    if f.get("size"):
                        sizes.append(f["size"])
        except IndexError:
            continue
        if sizes:
            profile["table_header"] = {"font": "宋体", "size": max(sizes),
                                       "bold": False, "align": "center"}
            break

    if cover:
        profile["cover"] = cover
    sec = doc.sections[0]
    profile["page"] = {
        "w_cm": round(sec.page_width.cm, 1),
        "h_cm": round(sec.page_height.cm, 1),
        "top_cm": round(sec.top_margin.cm, 1),
        "bottom_cm": round(sec.bottom_margin.cm, 1),
        "left_cm": round(sec.left_margin.cm, 1),
        "right_cm": round(sec.right_margin.cm, 1),
    }
    return profile


_PROFILE_CACHE: Dict[str, tuple] = {}


def profile_from_tender(path: str | Path) -> Dict[str, Any]:
    p = Path(str(path))
    if p.suffix.lower() not in (".doc", ".docx"):
        return {}
    try:
        _key = str(p.resolve())
        _mtime = p.stat().st_mtime
    except OSError:
        _key = str(p)
        _mtime = 0
    # 一次解析要跑 LibreOffice（十几秒），同一份招标文件反复生成时复用
    _hit = _PROFILE_CACHE.get(_key)
    if _hit and _hit[0] == _mtime:
        return dict(_hit[1])
    tmp = _to_docx(p)
    if tmp is None:
        return {}
    try:
        prof = profile_from_docx(tmp)
    except Exception as e:
        logger.warning(f"[格式规格] {p.name} 读取失败: {e}")
        return {}
    finally:
        if tmp != p:
            shutil.rmtree(tmp.parent, ignore_errors=True)
    _PROFILE_CACHE[_key] = (_mtime, prof)
    return dict(prof)


# 招标文件读不到版式时才用的兜底（正常走 profile_from_tender）
DEFAULT_PROFILE: Dict[str, Any] = {
    "chapter_title": {"font": "宋体", "size": 22, "bold": True, "align": "center"},
    "h1": {"font": "宋体", "size": 14},
    "h1_by_no": {},
    "section": {},
    "form": {},
    "table_header": {"font": "宋体", "size": 14, "bold": False,
                     "align": "center"},
    "page": {"w_cm": 21.0, "h_cm": 29.7, "top_cm": 2.2, "bottom_cm": 2.0,
             "left_cm": 2.5, "right_cm": 2.3},
}

# 角色拿不到任何测量值时的通用正文（宋体 12、首行 2 字、固定 26 磅）
_BODY_FALLBACK = {"font": "宋体", "size": 12, "align": "both",
                  "first_line_chars": 2, "line_exact_pt": 26}


def merged(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """测量值覆盖兜底值（只覆盖有值的键）。"""
    out: Dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else v)
                           for k, v in DEFAULT_PROFILE.items()}
    for role, val in (profile or {}).items():
        if isinstance(val, dict) and isinstance(out.get(role), dict) and role not in (
                "h1_by_no", "section", "form", "page"):
            out[role].update({k: v for k, v in val.items() if v is not None})
        else:
            out[role] = val
    return out


def role_fmt(profile: Dict[str, Any], section_no: str = "",
             kind: str = "plain", *, form_no: str = "") -> Dict[str, Any]:
    """取某章/某示范格式某类段落的版式；没有再退回正文兜底。"""
    def _pick(bucket: Dict[str, Any]) -> Dict[str, Any]:
        for k in (kind, "plain", "item"):
            if bucket.get(k):
                return dict(bucket[k])
        return {}

    if form_no:
        f = _pick(((profile.get("form") or {}).get(form_no) or {}))
        if f:
            return f
    if section_no:
        f = _pick(((profile.get("section") or {}).get(section_no) or {}))
        if f:
            return f
    base = {k: v for k, v in _BODY_FALLBACK.items()}
    if kind == "note":
        base["bold"] = True
    return base
