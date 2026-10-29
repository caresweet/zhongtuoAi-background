"""缺口检测 —— 找正文里「没写清楚、缺硬数据」的地方。

🔴 「以…为准」**不再作为缺口处理**（业主 2026-10-29 口径）。
   原因：这类表述在稳评/复垦报告里是**标准法律写法** —— 稳评本来就在补偿安置方案
   批准之前做，「以批准的方案为准」陈述的是事实，不是推脱。把它当缺口去"处理"，
   等于拿大白话去改公文，越改越不像话。

现在只抓三类**真的缺东西**的地方：

    1. 悬置：把数据推给后续程序（「待…后补充/确认/报批」）
    2. 截断：材料原文明显没抄完（「原文表述至此」）
    3. 空泛判断：只有结论、没有依据（句内无数字、文号、法规名）

🔴 产出去向
    `state["_chapter_gaps"]` → 能由 AI 就地改写的先改写正文；改不了的弹窗问用户。
    **不往正文塞占位符** —— 成稿中不得出现【待补充】/【待填写】。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── 「待…后补充」式悬置 ──
_HANG_RE = re.compile(r'待[^，。；\n]{0,26}(?:后|完成|确认|明确|出具|批复)[^，。；\n]{0,20}')

# ── 模型自曝截断（「公告原文表述至此」这种）──
_TRUNCATED_RE = re.compile(r'(?:原文|材料)[^，。；\n]{0,10}(?:表述至此|至此为|到此为止|不完整|有截断)')

# ── 「无依据的判断句」 ──────────────────────────────────────────────
# 🔴 业主：「生成出来的内容还是很虚，不像专家写的。」
#    实测根因**不是用词问题，是「下了判断但没给依据」**：
#    抽测 12 章共 33 句评价性断言，其中 22 句句内**没有任何数字/文号/法规名/专名**，
#    纯靠形容词撑着（「有利于完善…配套」「总体实施难度相对可控」）。
#    禁词表治不了这个 —— 它管「语气」，不管「有没有事实」。
_JUDGE_RE = re.compile(
    r'(有利于|有助于|符合[^，。；\n]{0,14}(?:需要|要求|实际|规划)|'
    r'具备[^，。；\n]{0,12}(?:能力|条件)|具有[^，。；\n]{0,10}(?:意义|作用)|'
    r'为[^，。；\n]{0,14}(?:提供|奠定|营造|创造)|'
    r'与[^，。；\n]{0,12}(?:相匹配|相适应)|'
    r'总体[^，。；\n]{0,8}(?:可控|良好|可行|稳定)|'
    r'切实|有力(?:保障|支撑|推动)|有效保障)')

# 「有依据」的判据：数字 / 文号 / **法规或文件的完整名称**
_EVIDENCE_RE = re.compile(r'(\d|〔\d{4}〕|《[^》]+》)')

# 免判：① 法定口径（「以…为准」是必须写的规范表述，不是虚）
#       ② 方法性说明（「评估…是否…」在交代要评估什么，不是在下结论）
_HOLLOW_EXEMPT_RE = re.compile(r'(是否|以[^，。；\n]{0,20}为准)')


def detect_hollow_claims(markdown: str, chapter_no: int = 0) -> List[Dict[str, str]]:
    """找「下了判断却没有依据」的句子。

    判据：句内有评价性谓语（有利于/符合…需要/具备…条件…），
          且句内**没有任何**数字、文号、法规名。
    """
    out: List[Dict[str, str]] = []
    for sent in re.split(r'(?<=[。；])', markdown or ""):
        t = sent.strip()
        if len(t) < 10:
            continue
        if not _JUDGE_RE.search(t):
            continue
        if _EVIDENCE_RE.search(t):        # 句内有数字/文号/法规名 → 有依据
            continue
        if _HOLLOW_EXEMPT_RE.search(t):   # 法定口径 / 方法性说明
            continue
        out.append({
            "field": f"第{chapter_no}章",
            "reason": "hollow_claim",
            "severity": "warn",
            "detail": "这句只有结论，没写依据 —— 补个数字、文号或文件名就行"
                      "—— 请补依据，或改写成事实陈述",
            "sentence": t[:120],
        })
    return out

# 正文里提到的数据 → (事实卡字段, 展示名)。用来把「含糊表述」升级成「其实我们有」。
_HAVE_FIELDS: Dict[str, tuple] = {
    "户数": ("household_count", "涉及户数"),
    "人数": ("household_count", "涉及户数"),
    "面积": ("area_m2", "拟征收面积"),
    "坐落": ("location", "坐落位置"),
    "位置": ("location", "坐落位置"),
    "用途": ("land_use", "土地用途"),
    "参会人员": ("symposium_attendees", "座谈会参会人员"),
}


def _have_assets(state: Dict[str, Any]) -> Dict[str, str]:
    """本项目**事实卡里已经有**的值：关键词 → 「展示名＝值」。

    用来把「以现状调查结果为准」这类含糊句，升级成
    「涉及户数事实卡里已有 120」—— 客户一眼知道**这个能补**，不是要等调查。
    """
    filled = (state or {}).get("filled_data") or {}
    have: Dict[str, str] = {}
    for kw, (field, label) in _HAVE_FIELDS.items():
        v = str(filled.get(field) or "").strip()
        if v:
            have[kw] = f"{label}（事实卡中已有：{v[:40]}）"
    return have


def detect_evasions(markdown: str, chapter_no: int = 0,
                    state: Dict[str, Any] = None) -> List[Dict[str, str]]:
    """扫描一章正文里的遁词，返回缺口清单（不修改正文）。"""
    if not markdown:
        return []
    have = _have_assets(state or {})
    out: List[Dict[str, str]] = []

    def _add(kind: str, sentence: str, detail: str, severity: str = "warn"):
        out.append({
            "field": f"第{chapter_no}章",
            "reason": kind,
            "severity": severity,
            "detail": detail,
            "sentence": sentence.strip()[:120],
        })

    for m in _HANG_RE.finditer(markdown):
        sent = _sentence_of(markdown, m.start())
        if _HAS_DIGIT.search(m.group(0)):   # 同上：只看悬置片段本身
            continue
        _add("deferred_data", sent, f"「{m.group(0)}」把数据推到了后面 —— 现在能补上吗？")

    for m in _TRUNCATED_RE.finditer(markdown):
        _add("material_truncated", _sentence_of(markdown, m.start()),
             f"材料原文像是截断了：「{m.group(0)}」，麻烦对一下原件")

    # 🔴 「下了判断但没给依据」的空泛句（业主：「内容还是很虚，不像专家写的」）。
    #    与遁词并列跑 —— 两者都是「正文里没有实质内容」的表现，只是形态不同：
    #      · 遁词   = 把缺口推给别人/别的时点（「以…为准」）
    #      · 空泛句 = 用形容词把缺口盖住（「有利于完善…配套」）
    out.extend(detect_hollow_claims(markdown, chapter_no))

    return out


def _sentence_of(text: str, pos: int) -> str:
    """取 pos 所在的整句（以中文句读切分）。"""
    start = max((text.rfind(c, 0, pos) for c in "。！？\n"), default=-1) + 1
    end = min((i for i in (text.find(c, pos) for c in "。！？\n") if i != -1),
              default=len(text)) + 1
    return text[start:end].strip()


_HAS_DIGIT = re.compile(r'\d')


def collect_gaps(state: Dict[str, Any], chapters: Dict[int, Any] = None) -> Dict[int, List[dict]]:
    """汇总「遁词检测」+「已有缺口」，按章号归并。

    `chapters` 传 `{章号: markdown}`；已有缺口从 `state["_chapter_gaps"]` 取。

    🔴 **必须按 (章, reason, 原句) 去重**：`_chapter_gaps` 里可能已经有上一次
       遁词检测的结果（组装报告时会写进去并随会话持久化），这里再检测一遍就会
       **同一处缺口出现两条** —— 实测第6章 5 项翻成 10 项、弹窗里一问两遍。
    """
    state = state or {}
    acc: Dict[int, List[dict]] = {}
    seen: set = set()

    def _push(ch: int, items) -> None:
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            sig = (ch, str(it.get("reason") or ""), str(it.get("sentence") or ""),
                   str(it.get("detail") or ""))
            if sig in seen:
                continue
            seen.add(sig)
            acc.setdefault(ch, []).append(it)

    for ch, items in (state.get("_chapter_gaps") or {}).items():
        try:
            _push(int(ch), items)
        except (ValueError, TypeError):
            continue

    for ch, md in (chapters or {}).items():
        try:
            n = int(ch)
        except (ValueError, TypeError):
            continue
        if not isinstance(md, str) or not md.strip():
            continue
        _push(n, detect_evasions(md, n, state))

    return acc
