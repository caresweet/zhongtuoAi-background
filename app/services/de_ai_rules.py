# -*- coding: utf-8 -*-
"""去 AI 味规则（中文公文口径）。

来源与取舍（2026-11-17，按业主「网络上的去 AI 味 skill」调研结果整理）：
    · op7418/Humanizer-zh、KKKKhazix/human-writing、qu-ai-wei、
      lieflat-less-ai-tone、hardikpandya/stop-slop 的公开规则；
    · **只抽可跨领域复用的「表达层」规则**（过渡词、热词、翻译腔、排比、
      破折号堆叠、空洞开头），不搬它们的创作口吻/个人视角/网络梗；
    · 报告里的数字、单位、文号、日期、专名、标题、表格**一律不在这层改**，
      需要模型重写时由 `prose_polish` 的数字/标题/表格硬校验兜底。

两类规则：
    HARD —— 高置信、可直接删/可检测的 AI 表达；
    SOFT —— 视上下文而定，只进生成提示词，不参与自动修改（避免误伤公文）。
"""

from __future__ import annotations

from typing import Tuple

# (正则, 中文标签, 自动替换文本；None = 只提示、不自动改)
HARD_AI_PATTERNS: Tuple[Tuple[str, str, str | None], ...] = (
    # ── 机械过渡 / 口水总结 ──
    (r"(?:值得(?:注意|一提)的是|不难看出|由此可见|显而易见|毋庸置疑|不言而喻)"
     r"[，,、]?", "AI过渡词", ""),
    (r"(?:总的来说|总体而言|概括地说|简而言之|换言之|换句话说)[，,、]?",
     "AI总结词", ""),
    # ── 空洞开头 ──
    (r"随着[^。；\n]{0,30}?(?:不断发展|快速推进|持续深入|日益完善)",
     "空洞开头", None),
    (r"在(?:当今|新时代|新形势)[^。；\n]{0,20}?背景下", "空洞开头", None),
    # ── AI 热词 / 互联网黑话 ──
    (r"(?:赋能|底层逻辑|抓手|组合拳|护城河|生态位|强引擎|新引擎|"
     r"关键变量|最大增量)", "AI热词", None),
    # ── 翻译腔 / 名词化 ──
    (r"通过[^。；\n]{1,20}?的方式", "翻译腔", None),
    (r"对于[^。；\n]{1,20}?而言", "翻译腔", None),
    (r"在[^。；\n]{1,20}?的背景下", "翻译腔", None),
    # ── 排比 / 三段式 ──
    (r"(?:全方位[、，]多层次[、，]宽领域|多维度[、，]全方位[、，]深层次|"
     r"系统[、，]全面[、，]深入)", "工整排比", None),
    (r"首先[，,][^。\n]{0,50}?其次[，,][^。\n]{0,50}?(?:最后|再次)",
     "机械序数排比", None),
    # ── 破折号堆叠（一句里两个以上「——」）──
    (r"(?:——[^。\n]{1,18}){2,}", "破折号堆叠", None),
)

# 只进提示词、不做匹配的软规则
SOFT_AI_GUIDANCE: Tuple[str, ...] = (
    "不用「随着…的不断发展」「在当今…背景下」这类放之四海而皆准的开头，直接写事实",
    "不用「需要注意的是」「由此可见」「总的来说」收尾，结论直接说结论",
    "不用「赋能、抓手、组合拳、底层逻辑、生态位、护城河」这类互联网黑话",
    "不用「不仅…而且…」「既…又…」连续堆叠；一句话只说一件事",
    "破折号一句最多一处，不用它制造金句或反转",
    "不写对仗工整的三段式排比；宁可长短句交错，读起来像人写的",
    "不写「为…提供了有力支撑/发挥了重要作用」这类空泛评价，写具体做了什么、结果是多少",
    "不用「通过…的方式」「对于…而言」翻译腔，直接写主语和动作",
)

# 生成提示词用的整块文本（注入到各域 prompt / 内置技能）
DE_AI_STYLE_TEXT = (
    "【去 AI 味行文要求】\n"
    + "\n".join(f"- {x}" for x in SOFT_AI_GUIDANCE)
    + "\n- 以上只改表达，不改事实：数字、单位、文号、日期、专名、标题、表格一律原样保留"
)


def find_hits(text: str) -> list:
    """返回 [(片段, 标签)]，供审核/日志用。"""
    import re

    out = []
    for pat, label, _repl in HARD_AI_PATTERNS:
        try:
            for m in re.finditer(pat, str(text or "")):
                out.append((m.group(0), label))
        except re.error:
            continue
    return out


def strip_filler(text: str) -> tuple:
    """删除可安全删除的 AI 口水过渡词（只动 replacement 非 None 的规则）。

    Returns: `(新文本, 删除处数)`。数字、文号、表格不在这些规则里，不会被碰到。
    """
    import re

    out = str(text or "")
    n = 0
    for pat, _label, repl in HARD_AI_PATTERNS:
        if repl is None:
            continue
        try:
            out, k = re.subn(pat, repl, out)
            n += k
        except re.error:
            continue
    return out, n
