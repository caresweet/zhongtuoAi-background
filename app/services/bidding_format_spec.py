# -*- coding: utf-8 -*-
"""从招标文件里**读它自己写的「投标/响应文件格式」**，不套固定模板。

不同项目的格式章差别很大：章节名、章节数、顺序、人员表列名、要不要单独放
「示范格式」都不一样。这里把格式章抽成结构化 spec：

    {
      "chapter": "第二章 响应文件格式",
      "sections": [
        {"no": "一", "title": "磋商函", "kind": "letter", "text": "...",
         "items": [], "tables": []},
        {"no": "三", "title": "人员配备表", "kind": "personnel",
         "tables": [{"headers": ["序号", "姓名", ...], "rows": []}]},
        ...
      ],
      "forms": ["法定代表人资格证明", "授权委托书", ...],
      "notes": ["请供应商另准备二份空白磋商函，以备最终报价用。"]
    }

doc/docx 先转 HTML 再解析：`.doc` 里的表头常放在文本框，python-docx 直接读
docx 会丢（洪泽磋商文件的人员配备表就是这样），HTML 里能读到。
"""

from __future__ import annotations

import html as _html
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_KIND_BY_TITLE = (
    ("磋商函", "letter"), ("投标函", "letter"), ("投选函", "letter"),
    ("响应函", "letter"),
    ("报价函", "letter"),
    ("资格", "qualification"),
    ("人员配备", "personnel"), ("人员配置", "personnel"),
    ("其它资料", "other"), ("其他资料", "other"),
    ("示范格式", "forms"),
    ("报价", "quote"), ("开标一览", "quote"),
    ("偏离", "deviation"),
    ("评分导读", "scoring"), ("评分", "scoring"),
    ("服务方案", "plan"), ("服务内容的详细说明", "plan"),
    ("技术方案", "plan"), ("实施方案", "plan"),
    ("技术建议", "plan"), ("技术文件", "plan"),
    ("服务建议", "plan"),
    ("身份证明", "forms"), ("授权委托", "forms"), ("承诺书", "forms"),
    ("评审条件", "qualification"),
    ("封面", "cover"),
    ("相关情况", "other"),
    ("其他", "other"),
)

_FORM_NAMES = ("法定代表人资格证明", "授权委托书", "声明函", "承诺书",
               "中小企业声明函", "残疾人福利性单位声明函")


def _clean(text: str) -> str:
    text = _html.unescape(re.sub(r'<[^>]+>', ' ', str(text or "")))
    text = text.replace("\xa0", " ")
    return re.sub(r'[ \t　]+', " ", text).strip()


def _kind_of(title: str) -> str:
    for key, kind in _KIND_BY_TITLE:
        if key in title:
            return kind
    return "unknown"


def _to_html(path: Path) -> Optional[str]:
    """doc/docx → LibreOffice HTML；失败返回 None。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="bidspec_"))
    try:
        subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "html",
             "--outdir", str(tmp), str(path)],
            check=True, capture_output=True, timeout=180)
        html_files = list(tmp.glob("*.html"))
        if not html_files:
            return None
        return html_files[0].read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"[格式解析] {path} 转 HTML 失败: {e}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _blocks(html: str) -> List[Dict[str, Any]]:
    """按文档顺序把 <p> / <table> 切成块（表格保留行列）。"""
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r'<(p|table)\b[^>]*>(.*?)</\1>', html, re.S | re.I):
        tag, inner = m.group(1).lower(), m.group(2)
        if tag == "table":
            rows = []
            for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', inner, re.S | re.I):
                cells = [_clean(c) for c in re.findall(
                    r'<t[dh]\b[^>]*>(.*?)</t[dh]>', tr, re.S | re.I)]
                if any(cells):
                    rows.append(cells)
            if rows:
                out.append({"tag": "table", "rows": rows, "text": ""})
            continue
        text = _clean(inner)
        if text:
            out.append({"tag": "p", "text": text, "rows": []})
    return out


def _is_toc(text: str) -> bool:
    return "…" in text or "......" in text or bool(re.search(r'\.{4,}', text))


def _find_chapter(blocks: List[Dict[str, Any]]) -> int:
    """找格式章正文起点（跳过目录里的同名条目）。"""
    hit = -1
    for i, b in enumerate(blocks):
        t = b.get("text") or ""
        if not t or _is_toc(t):
            continue
        # 原 .doc 的章标题常写成「响 应 文 件 格 式」（字间空格），先去掉空白再比
        norm = re.sub(r'\s+', '', t)
        if re.search(r'(投标|响应|投选)文件格式', norm):
            hit = i
    return hit


def _heading(text: str):
    """识别格式章的章节标题：一、 / （一） / 1. / 附件1：。"""
    norm = re.sub(r'\s+', '', str(text or ""))
    m = re.match(r'^([一二三四五六七八九十]+)、(.{2,60})$', norm)
    if m:
        return m.group(1), m.group(2), "cn"
    m = re.match(r'^[（(]([一二三四五六七八九十]+)[）)]、?(.{2,60})$', norm)
    if m:
        return m.group(1), m.group(2), "cn"
    m = re.match(r'^(\d{1,2})[.．](?!\d)(.{2,60})$', norm)
    if m:
        return m.group(1), m.group(2), "num"
    m = re.match(r'^(\d{1,2})\s+(.{2,60})$', norm)
    if m:
        return m.group(1), m.group(2), "num"
    m = re.match(r'^(\d{1,2})(?!\d)([^\d.．、，,；;:：].{1,60})$', norm)
    if m:
        return m.group(1), m.group(2), "num"
    m = re.match(r'^附件(\d+)[:：]?(.{2,60})$', norm)
    if m:
        return f"附件{m.group(1)}", m.group(2), "att"
    return None


def _clean_title(title: str) -> str:
    title = re.sub(r'[（(]格式[^）)]*[）)]', '', title)
    title = re.sub(r'[：:]\s*$', '', title)
    return title.strip(" ：:、")


def parse_format_spec(html: str) -> Dict[str, Any]:
    blocks = _blocks(html)
    start = _find_chapter(blocks)
    if start < 0:
        return {"chapter": "", "sections": [], "forms": [],
                "attachments": [], "notes": []}

    chapter = blocks[start].get("text") or ""
    # 章标题可能是「第二章」和「响应文件格式」两块，或字间带空格
    if re.fullmatch(r'第[一二三四五六七八九十]+章', chapter) and start + 1 < len(blocks):
        chapter = f"{chapter} {blocks[start + 1].get('text') or ''}".strip()
        start += 1
    _cm = re.match(r'^(第[一二三四五六七八九十]+章)\s*(.*)$', chapter, re.S)
    if _cm:
        _cno, _ctitle = _cm.group(1), re.sub(r'\s+', '', _cm.group(2))
        chapter = f"{_cno} {_ctitle}"

    sections: List[Dict[str, Any]] = []
    attachments: List[Dict[str, str]] = []
    forms: List[str] = []
    notes: List[str] = []
    cur: Optional[Dict[str, Any]] = None
    in_forms = False
    num_mode = False

    for b in blocks[start + 1:]:
        text = b.get("text") or ""
        if b.get("tag") == "p" and re.match(r'^第[一二三四五六七八九十]+章', text):
            break
        if b.get("tag") == "table":
            if cur is not None:
                cur["tables"].append({"rows": b["rows"]})
            continue
        if not text:
            continue
        if text.startswith("注"):
            notes.append(text)
        # 示范格式开始：后面的一级序号不是响应文件章节
        if re.fullmatch(r'示范格式[一二三四五六七八九十]+', text):
            in_forms = True
            continue
        if in_forms:
            norm = re.sub(r'\s+', '', text)
            for name in _FORM_NAMES:
                hit = norm.startswith(name) or (
                    name == "声明函" and norm.endswith("声明函"))
                if hit:
                    label = norm if name == "声明函" else name
                    if label not in forms:
                        forms.append(label)
                    break
            continue
        h = None if _is_toc(text) else _heading(text)
        if h:
            no, raw_title, style = h
            title = _clean_title(raw_title)
            if style == "att":
                attachments.append({"no": no, "title": title})
                continue
            if style == "num" and not num_mode:
                pass          # 中文序号章下的 1、2、3 是条目，不是章节
            elif title:
                if "补充说明" in title:
                    break                 # 格式章的补充说明不是响应文件章节
                if any(k in title for k in ("按照下列内容", "编制投选文件",
                                            "编制投标文件")):
                    num_mode = True       # 这一节下面是真正的章节清单
                    continue
                cur = {"no": no, "title": title, "kind": _kind_of(title),
                       "text": "", "items": [], "tables": []}
                sections.append(cur)
                continue
        if cur is None:
            continue
        if re.match(r'^\d+\s*[、.．]', text):
            cur["items"].append(re.sub(r'^(\d+)\s*[、.．]\s*', r'\1、', text))
        else:
            cur["text"] = (cur["text"] + "\n" + text).strip()

    # 附件清单可能在「补充说明」之后，单独再扫一遍（如询比文件第六章）
    for _b in blocks[start + 1:]:
        _h = _heading(_b.get("text") or "")
        if _h and _h[2] == "att":
            _no, _title = _h[0], _clean_title(_h[1])
            if _title and not any(a["no"] == _no and a["title"] == _title
                                  for a in attachments):
                attachments.append({"no": _no, "title": _title})

    # 人员表表头：取该节第一张非空表的第一行
    for s in sections:
        if s["kind"] == "personnel":
            for t in s.get("tables") or []:
                rows = t.get("rows") or []
                if rows and any(rows[0]):
                    s["headers"] = rows[0]
                    break

    return {"chapter": chapter, "sections": sections, "forms": forms,
            "attachments": attachments, "notes": notes}


def extract_format_spec(path: str | Path) -> Dict[str, Any]:
    """doc/docx 读格式章；pdf 或无 LibreOffice 时返回空 spec（调用方走兜底）。"""
    p = Path(str(path))
    if p.suffix.lower() not in (".doc", ".docx"):
        return {"chapter": "", "sections": [], "forms": [],
                "attachments": [], "notes": []}
    html = _to_html(p)
    if not html:
        return {"chapter": "", "sections": [], "forms": [],
                "attachments": [], "notes": []}
    try:
        spec = parse_format_spec(html)
    except Exception as e:
        logger.warning(f"[格式解析] {p} 解析失败: {e}")
        return {"chapter": "", "sections": [], "forms": [],
                "attachments": [], "notes": []}
    # 版式（字体/字号/缩进/行距）另走 docx 量取：HTML 丢了加粗和缩进，
    # 用它生成的响应文件会和招标文件对不上字号（业主 2026-11-20）。
    try:
        from app.services.bidding_format_profile import profile_from_tender
        spec["style"] = profile_from_tender(p) or {}
    except Exception as e:
        logger.warning(f"[格式解析] {p} 版式读取失败: {e}")
    return spec
