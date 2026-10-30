# -*- coding: utf-8 -*-
"""按报告域加载「行文规则」md —— 不同报告类型有各自的内容规则，只共用去 AI 腔/格式。

业主口径（2026-10-30）：
    「不同类型的报告不应该单独做一个md或者文章限制么？……有些是可以复用的，
      比如文章的去AI话、格式这些，但是内容上大多数是不能复用的。」

结构（`app/content_rules/`）：
    common_style.md       —— 共用：去 AI 腔、短句、禁套话、禁编造（所有域都拼在最前）
    reclamation_style.md  —— 复垦内容规则（不搬稳评口径、勘测定界/复垦义务人、规程引用…）
    bidding_style.md      —— 投标内容规则（只对招标文件负责、不编业绩…）

🔴 稳评**不读这里**：它的禁止/鼓励块一直内联在
   `chapter_base._default_system_prompt` 里，逐字不动是历史护栏（改一个字都可能
   让已通过的稳评稿变样）。什么时候迁移到 md，要单独做回归对比。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_RULE_DIR = Path(__file__).resolve().parents[1] / "content_rules"


def _read(name: str) -> str:
    p = _RULE_DIR / name
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip() + "\n"


def load_domain_rules(
    domain: Optional[str],
    *,
    buzzwords: str = "",
    transition_examples: str = "",
    impl_unit: str = "",
) -> str:
    """拼出该域的规则块：common + `<domain>_style.md`，并替换占位符。

    没有对应 md 的域（如 stability）只返回 common —— 调用方通常不会走到这里。
    """
    parts = [_read("common_style.md")]
    if domain:
        parts.append(_read(f"{domain}_style.md"))
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return ""
    return (text
            .replace("{buzzwords}", buzzwords or "")
            .replace("{transition_examples}", transition_examples or "")
            .replace("{impl_unit}", impl_unit or "")) + "\n\n"
