"""Strict content guardrails for generated report chapters."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


ALLOWED_MISSING_MARKER = r"【待用户补充：[^】]+（对应章节：[^】]+）】"

BLOCKING_PATTERNS: List[Tuple[str, str]] = [
    (r"待补充(?!：)", "未完成占位表达"),
    (r"后续提供|稍后补充|后期提供", "未完成资料表达"),
    (r"需要补充|需补充", "未完成资料表达"),
    (r"请提供|请补充|请填写", "对话式指令残留"),
    (r"根据实际情况|视情况而定", "泛化不确定表达"),
    (r"具体.*?待定|暂未确定|尚未明确", "待定表达"),
    (r"有关单位|相关部门", "责任主体泛化表达"),
    (r"\{\{[^}]+\}\}|____+|<[^>]{1,50}>", "占位符残留"),
    (r"\[.*?\]\(.*?\)", "Markdown链接残留"),
    (r"好的[，,]|当然可以[，,]|下面我来|我将为您", "口语/对话式表达"),
    (r"哈哈|呵呵|嘻嘻|yyds|666|给力", "网络或口语表达"),
    (r"我们认为|我们建议|笔者认为", "第一人称主观表达"),
    (r"以上内容仅供参考|以上是.*?的内容", "呈现式表达残留"),
]


def find_blocking_issues(text: str) -> List[Dict[str, str]]:
    """Return hard-blocking wording/placeholder issues in generated content."""
    masked = re.sub(ALLOWED_MISSING_MARKER, "", text or "")
    issues: List[Dict[str, str]] = []
    for pattern, desc in BLOCKING_PATTERNS:
        if re.search(pattern, masked):
            issues.append({"type": "blocking_wording", "description": desc, "pattern": pattern})
    return issues


def has_blocking_issues(text: str) -> bool:
    return bool(find_blocking_issues(text))
