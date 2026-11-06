# -*- coding: utf-8 -*-
"""模板分析的项目既定规则 —— 从 `content_rules/template_analysis.md` 读取。

为什么单独一层
--------------
模板分析原来只靠关键词判域 + 正则判地区 + 逐节三色分类，缺的是**项目里已经
踩过的坑**：复垦第 5 章点状工程没有「路线方案设计」是正常的、表4-2 空表是系统
渲染的、附件缺材料要连续编号、图3-1 必须三要素……这些规则散在业主批注和历史
记忆里，人知道、代码不知道。

本模块把规则落成仓库里的 Markdown（生产服务器也读得到），提供三件事：

1. `rules_block(domain, chapter_no)` —— 生成章节时注入的「本项目既定规则」；
2. `score_domains(text)` —— 判域时的**结构词打分**（比单个关键词稳）；
3. `check_structure(template_path, domain)` —— 模板章结构与项目应有结构对账，
   告诉用户「这份模板缺第几章 / 多了哪一章」，而不是默默按缺章模板生成。

文件格式（`app/content_rules/template_analysis.md`）：

    ## reclamation
    - [结构] 1 前言 | 2 编制总则 | …
    - [关键词] 土地复垦 复垦方案 …
    - [全部] 全章通用规则
    - [第4章] 只给第 4 章的规则
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).resolve().parents[1] / "content_rules" / "template_analysis.md"

_SCOPE_ALL = "全部"
_SCOPE_STRUCT = "结构"
_SCOPE_TERMS = "关键词"

_cache: Dict[str, object] = {"mtime": None, "data": {}}


# ═══════════════════════════════════════════════════════════════
# 读取与解析
# ═══════════════════════════════════════════════════════════════

def _empty_domain() -> Dict[str, object]:
    return {"global": [], "chapters": {}, "structure": {}, "terms": []}


def _parse_line(rule_dom: Dict[str, object], scope: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    if scope == _SCOPE_STRUCT:
        for part in text.split("|"):
            m = re.match(r"\s*(\d{1,2})\s+(.+?)\s*$", part)
            if m:
                rule_dom["structure"][int(m.group(1))] = m.group(2)
        return
    if scope == _SCOPE_TERMS:
        words = [w for w in re.split(r"[\s,，、;；]+", text) if w]
        rule_dom["terms"].extend(words)
        return
    if scope == _SCOPE_ALL:
        rule_dom["global"].append(text)
        return
    m = re.match(r"第(\d{1,2})章", scope)
    if m:
        rule_dom["chapters"].setdefault(int(m.group(1)), []).append(text)
        return
    # 没写范围的一律当全章通用，宁可多给不可漏
    rule_dom["global"].append(text)


def load_rules() -> Dict[str, Dict[str, object]]:
    """解析规则文件，按 mtime 缓存（改 md 不用重启进程）。"""
    try:
        mtime = _RULES_FILE.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] == mtime:
        return _cache["data"]  # type: ignore[return-value]

    data: Dict[str, Dict[str, object]] = {}
    domain = ""
    try:
        for raw in _RULES_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("## "):
                domain = line[3:].strip().lower()
                data.setdefault(domain, _empty_domain())
                continue
            m = re.match(r"^-\s*\[([^\]]+)\]\s*(.+)$", line)
            if not m or not domain:
                continue
            _parse_line(data[domain], m.group(1).strip(), m.group(2))
    except OSError as e:
        logger.warning(f"[模板规则] 读取失败 {_RULES_FILE}: {e}")
        return {}

    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def _domain_rules(domain: str) -> Dict[str, object]:
    return load_rules().get(str(domain or "").strip().lower()) or _empty_domain()


# ═══════════════════════════════════════════════════════════════
# 对外：规则取用
# ═══════════════════════════════════════════════════════════════

def rules_for(domain: str, chapter_no: Optional[int] = None) -> List[str]:
    """该域的全章通用规则 + 指定章的规则（顺序：通用在前，章节在后）。"""
    d = _domain_rules(domain)
    out = list(d.get("global") or [])  # type: ignore[arg-type]
    if chapter_no:
        out += list((d.get("chapters") or {}).get(int(chapter_no)) or [])  # type: ignore[union-attr]
    return out


def rules_block(domain: str, chapter_no: Optional[int] = None) -> str:
    """章节 prompt 里的「本项目既定规则」块；没有规则返回空串。"""
    rules = rules_for(domain, chapter_no)
    if not rules:
        return ""
    lines = ["## 📌 本项目既定规则（模板分析先验，必须遵守）"]
    lines += [f"- {r}" for r in rules]
    return "\n".join(lines)


def domain_terms(domain: str) -> List[str]:
    return list(_domain_rules(domain).get("terms") or [])  # type: ignore[arg-type]


def score_domains(text: str) -> Tuple[str, int]:
    """按各域的结构词命中数判域，返回 (域, 分值)；全不中返回 ("", 0)。"""
    blob = str(text or "")
    if not blob:
        return "", 0
    scores = {d: sum(1 for t in (v.get("terms") or []) if t in blob)  # type: ignore[union-attr]
              for d, v in load_rules().items()}
    best = max(scores, key=lambda k: scores[k]) if scores else ""
    if not best or scores[best] <= 0:
        return "", 0
    # 并列不算数：两域词都命中同样多时交给调用方的强关键词规则
    if sum(1 for v in scores.values() if v == scores[best]) > 1:
        return "", 0
    return best, scores[best]


# ═══════════════════════════════════════════════════════════════
# 对外：模板章结构对账
# ═══════════════════════════════════════════════════════════════

_HEAD_CN = re.compile(r"^第([一二三四五六七八九十百\d]+)章(?=[、．.：:\s]|$)")
_HEAD_NUM = re.compile(r"^(\d{1,2})(?:[、．.]\s+|\s+)(\S.*)$")
_TOC_DOTS = re.compile(r"[.．·…]{3,}\s*\d*\s*$")


def _heading_text(p) -> str:
    return re.sub(r"\s+", " ", str(p.text or "")).strip()


def _chapter_no_of(text: str) -> Tuple[int, str]:
    """标题文本 → (章号, 标题)。不是章标题返回 (0, "")。"""
    t = _TOC_DOTS.sub("", text).strip()
    m = _HEAD_CN.match(t)
    if m:
        try:
            from app.agent.chapter_definitions import cn_num_to_int
            no = cn_num_to_int(m.group(1))
        except Exception:
            no = 0
        title = t[m.end():].strip(" 、．.：:")
        return (no, title) if no else (0, "")
    m = _HEAD_NUM.match(t)
    if m and len(m.group(2)) <= 40:
        return int(m.group(1)), m.group(2).strip()
    return 0, ""


def expected_structure(domain: str) -> Dict[int, str]:
    """项目应有的章节结构：优先取代码定义，取不到退回规则文件的 [结构]。"""
    try:
        if domain == "reclamation":
            # 🔴 复垦的章序/章名以规程第六章为准（业主 2026-11-04）
            from app.services.reclamation_outline import full_structure
            _out = full_structure()
            if _out:
                return _out
            from app.agent.reclamation_definitions import (
                RECLAMATION_CHAPTER_DEFINITIONS, RECLAMATION_POST_BODY_TITLES)
            out = {n: d.get("title", "") for n, d in
                   RECLAMATION_CHAPTER_DEFINITIONS.items()}
            out.update(RECLAMATION_POST_BODY_TITLES)
            return out
        from app.agent.chapter_definitions import chapter_titles
        return chapter_titles(domain)
    except Exception as e:
        logger.info(f"[模板结构] 取代码定义失败，回落规则文件: {e}")
        return dict(_domain_rules(domain).get("structure") or {})  # type: ignore[arg-type]


# 系统渲染的「正文之后」部分：模板里没有属正常（生成时由装配器补），
# 不算模板缺陷，但要在分析结果里告诉用户会补哪几章。
_SYSTEM_PARTS = {"reclamation": {11, 12, 13, 14}, "stability": {14}}


def check_structure(template_path: str | Path, domain: str) -> Dict[str, object]:
    """模板 docx 的章结构与项目应有结构对账。

    只比**章号**，标题只作展示（地区模板的章标题措辞会变，比标题会假报警）。
    正文缺章 = `missing`（要提醒）；11–14 这类系统渲染部分缺 = `missing_parts`
    （模板本来就不该有，生成时补）。不是 docx 或解析失败返回空 dict。
    """
    p = Path(str(template_path or ""))
    if not p.exists() or p.suffix.lower() != ".docx":
        return {}
    try:
        from docx import Document
        doc = Document(str(p))
    except Exception as e:
        logger.info(f"[模板结构] 打开失败 {p}: {e}")
        return {}

    found: Dict[int, str] = {}
    for para in doc.paragraphs:
        txt = _heading_text(para)
        if not txt:
            continue
        style = str(getattr(getattr(para, "style", None), "name", "") or "").lower()
        if style.startswith("toc"):
            continue
        no, title = _chapter_no_of(txt)
        if not no:
            continue
        # Normal 样式里以数字开头的正文句子：标题部分过长即视为正文
        if not style.startswith(("heading", "标题")) and len(title) > 24:
            continue
        found.setdefault(no, title)

    expected = expected_structure(domain)
    if not found or not expected:
        return {}
    system = _SYSTEM_PARTS.get(str(domain or ""), set())
    missing_all = sorted(set(expected) - set(found))
    missing = [n for n in missing_all if n not in system]
    missing_parts = [n for n in missing_all if n in system]
    extra = sorted(set(found) - set(expected))
    return {
        "expected": [{"no": n, "title": expected[n]} for n in sorted(expected)],
        "found": [{"no": n, "title": found[n]} for n in sorted(found)],
        "missing": missing,
        "missing_parts": missing_parts,
        "extra": extra,
        "order_ok": list(found) == sorted(found),
        "ok": not missing and not extra,
        "standard": _standard_label(domain),
    }


def _standard_label(domain: str) -> str:
    if domain == "reclamation":
        try:
            from app.services.reclamation_outline import standard_source
            return standard_source()
        except Exception:
            return ""
    return ""
