"""交付体检 —— 把「80% 可用、用户只看一遍」变成可量化的指标。

业主口径（2026-10-30）：
    「每个报告生成之后要达到 80% 可用率，并且用户只需要看一遍就行。」

**怎么算「可用」**：逐段判。一段正文只要命中下面任一类，就算「要人动一下」，
其余算「可直接用」：

    可用率 = 可直接用的段落数 / 总段落数

需要人动的四类（都是一眼看得出、举手能改的，不是要重写）：

  1. `placeholder` 占位/待核：待补充、待核对、待填写、【xx】、XXX、××
  2. `ai_tone`     AI 腔：套话词（有力保障、高度重视…）、三字排比、公文宣传腔
  3. `vague`       空话：没有数字、没有主体、没有动作的结论句（"各有关单位应高度重视"）
  4. `shaky`       数据可疑：同一指标在本段与其他章取值矛盾（只在传入 facts 时判）

判定刻意**保守**：宁可多标几处让人扫一眼，也不要漏掉真硬伤 ——
「看一遍」的清单必须是完整的，不能让人看完清单还要自己再发现一遍。

纯函数、零依赖：不调模型、不读数据库，装配完直接算。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from app.validation.content_guardrails import AI_BUZZWORD_PATTERNS

# 目标可用率（低于它就在日志与前端标红）
TARGET_RATE = 0.80

_PLACEHOLDER_RE = re.compile(r"(待补充|待核对|待填写|待完善|【[^】]{0,20}】|XXX|××|xx待)")
# 空话：整句只有态度、没有数字/主体/动作
_VAGUE_RE = re.compile(
    r"(各?(有关|相关|上级)部门|有关部门|有关单位|相关单位)"
    r"|(高度重视|狠抓落实|扎实推进|进一步增强|不断提升)"
)
_HAS_NUM_RE = re.compile(r"\d")
# 不算正文的块：标题、表格、图片、标记行
_SKIP_RE = re.compile(r"^\s*(#{1,6}\s|\||!\[|\[TABLE:|\[SYS_|\[图|图\s*\d+-\d+\s*$)")

# 🔴 AI 味的两个硬指标（人一眼就能看出、也最容易改）：
#   ① 长句：单句超过 60 字（AI 爱堆修饰语，人写的地名/文号句一般到不了 60）
#   ② 同章 ≥3 段用同一个四字短语开头（"本项目…"刷屏是模板腔的典型症状）
_SENT_SPLIT_RE = re.compile(r"[。！？；;]")
_MAX_SENT_LEN = 60
_OPEN_PREFIX_LEN = 4
_REPEAT_OPEN_MIN = 3


def _paragraphs(markdown: str) -> List[str]:
    """把章 markdown 切成「段」：跳过标题/表格/图片/标记行，按空行断开。"""
    out: List[str] = []
    buf: List[str] = []
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        if _SKIP_RE.match(line):
            continue
        buf.append(line)
    if buf:
        out.append("".join(buf))
    return [p for p in out if len(p) >= 8]


def _classify(para: str) -> Optional[str]:
    """返回该段的问题类型；没问题返回 None。"""
    if _PLACEHOLDER_RE.search(para):
        return "placeholder"
    for pat, _name in AI_BUZZWORD_PATTERNS:
        if re.search(pat, para):
            return "ai_tone"
    # 空话：≥2 个模糊主体/口号，且通篇没有一个数字
    if len(_VAGUE_RE.findall(para)) >= 2 and not _HAS_NUM_RE.search(para):
        return "vague"
    # 长句：有一句超过 60 字
    for s in _SENT_SPLIT_RE.split(para):
        if len(s.strip()) > _MAX_SENT_LEN:
            return "long_sentence"
    return None


def _repeat_open_indexes(paras: List[str]) -> set:
    """同章 ≥3 段以同一个四字短语开头 → 返回这些段的下标。"""
    from collections import Counter

    prefixes = []
    for p in paras:
        head = p[:_OPEN_PREFIX_LEN]
        # 只统计中文开头（数字/符号开头的「3.1」「（一）」不算模板腔）
        prefixes.append(head if re.match(r"^[一-龥]{4}$", head) else "")
    counter = Counter(x for x in prefixes if x)
    return {i for i, x in enumerate(prefixes) if x and counter[x] >= _REPEAT_OPEN_MIN}


_KIND_LABEL = {
    "placeholder": "待补充/待核对",
    "ai_tone": "套话腔",
    "vague": "空泛表述",
    "long_sentence": "长句难读",
    "repeat_open": "段首雷同",
    "shaky": "数据可疑",
}


def audit_chapters(chapters: Dict[Any, Any]) -> Dict[str, Any]:
    """体检整份报告，返回可用率与需人看的清单。

    Args:
        chapters: {章号: {"markdown": str, ...}}（装配前/后的章节字典都行）

    Returns:
        {
          "rate": 0.0~1.0,          # 可用率
          "total": 段落总数,         # 分母
          "flagged": 需人看的段数,   # 分子 = total - flagged
          "below_target": bool,     # 是否低于 80%
          "by_kind": {"ai_tone": n, ...},
          "items": [{"chapter": 3, "kind": "ai_tone", "label": "套话腔",
                     "detail": "命中「切实保障」", "text": "前 60 字"}, ...]  # 最多 60 条
        }
    """
    total = 0
    items: List[Dict[str, Any]] = []
    by_kind: Dict[str, int] = {}
    flagged = 0

    for ch_num in sorted(chapters or {}, key=lambda x: str(x)):
        data = chapters.get(ch_num) or {}
        md = data.get("markdown") if isinstance(data, dict) else str(data or "")
        paras = _paragraphs(md or "")
        repeat_idx = _repeat_open_indexes(paras)
        for i, para in enumerate(paras):
            total += 1
            kind = _classify(para)
            if not kind and i in repeat_idx:
                kind = "repeat_open"
            if not kind:
                continue
            flagged += 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if len(items) < 60:
                items.append({
                    "chapter": _as_int(ch_num),
                    "kind": kind,
                    "label": _KIND_LABEL.get(kind, kind),
                    "detail": _detail(para, kind),
                    "text": para[:60],
                })

    rate = (total - flagged) / total if total else 1.0
    return {
        "rate": round(rate, 4),
        "total": total,
        "flagged": flagged,
        "below_target": bool(total) and rate < TARGET_RATE,
        "target": TARGET_RATE,
        "by_kind": by_kind,
        "items": items,
    }


def _detail(para: str, kind: str) -> str:
    if kind == "placeholder":
        m = _PLACEHOLDER_RE.search(para)
        return f"含「{m.group(0) if m else '待补充'}」"
    if kind == "ai_tone":
        for pat, name in AI_BUZZWORD_PATTERNS:
            m = re.search(pat, para)
            if m:
                return f"命中「{m.group(0)}」（{name}）"
        return "套话腔"
    if kind == "vague":
        hits = _VAGUE_RE.findall(para)
        words = "、".join({h[0] if isinstance(h, tuple) else h for h in hits[:3]})
        return f"空泛表述「{words}」，且无具体数字"
    if kind == "long_sentence":
        longest = max((len(s.strip()) for s in _SENT_SPLIT_RE.split(para)), default=0)
        return f"有 {longest} 字长句，建议断开"
    if kind == "repeat_open":
        return f"同章多段以「{para[:_OPEN_PREFIX_LEN]}」开头"
    return "数据可疑"


def _as_int(v: Any) -> Any:
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def summarize(audit: Dict[str, Any]) -> str:
    """一行摘要，给日志/前端用。"""
    if not audit or not audit.get("total"):
        return "交付体检：无正文可体检"
    pct = round((audit.get("rate") or 0) * 100, 1)
    kinds = "、".join(f"{_KIND_LABEL.get(k, k)} {n}" for k, n in (audit.get("by_kind") or {}).items())
    return f"交付体检：可用率 {pct}%（需扫一眼 {audit.get('flagged', 0)} 段{'：' + kinds if kinds else ''}）"


def paragraphs_of(markdown: str) -> Iterable[str]:
    """暴露给测试/调试：按同一口径切段。"""
    return _paragraphs(markdown)
