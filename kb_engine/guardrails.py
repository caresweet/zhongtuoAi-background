"""guardrails.py — 章节内容审核护栏

规则：
1. 禁止"待补充/后续提供/待完善"等占位词
2. 禁止口语化表达（我觉得/呢/吧/啊/嘛...）
3. 结构完整性（非空、有标题、有实质内容）
4. 禁止暴露 AI 身份或元注释（"作为AI/根据要求/以上是"）
"""

import re
from dataclasses import dataclass, field
from typing import List

# ── 禁止占位词 ──────────────────────────────────────────────────
FORBIDDEN_PLACEHOLDERS = [
    "待补充", "后续提供", "待提供", "待完善", "待确认", "待填",
    "需后期提供", "需后续提供", "敬请期待", "稍后补充",
    "TODO", "TBD", "XXX", "xxx", "待定", "暂缺", "略",
    "见附件", "详见附件", "参照附件",  # 无附件上下文时
    "需补充", "待录入", "待上传", "待收集",
]

# ── 口语化表达 ──────────────────────────────────────────────────
COLLOQUIAL_PATTERNS = [
    r"我觉得", r"我认为", r"我们大家", r"大家", r"咱们",
    r"一下吧", r"帮你", r"帮您", r"亲[，,]", r"嗯[，,]", r"哦[，,]",
    r"哈[，,]", r"嘛[，。]", r"呢[？。]", r"吧[。？]", r"呀[，。]",
    r"这个嘛", r"那个嘛", r"然后呢", r"反正", r"差不多", r"大概是",
    r"可能吧", r"也许", r"应该吧", r"说实话", r"老实说",
    r"好啦", r"行啦", r"可以啦",
]

# ── AI 元注释 ───────────────────────────────────────────────────
META_COMMENT_PATTERNS = [
    r"作为AI", r"作为人工智能", r"我是一个AI", r"我无法",
    r"根据您的要求", r"按照您的要求", r"根据上述要求",
    r"以上是", r"如下是.*内容", r"以下是.*章节",
    r"请注意", r"需要说明的是", r"值得注意",
]


@dataclass
class ReviewResult:
    passed: bool
    issues: List[str] = field(default_factory=list)
    suggestions: str = ""


class ReviewGuard:
    """章节内容审核器。"""

    def review(self, content: str, chapter_title: str = "") -> ReviewResult:
        issues: List[str] = []

        # 1. 非空检查
        text = content.strip()
        if not text or len(text) < 50:
            issues.append(f"内容过短（{len(text)}字），章节「{chapter_title}」缺乏实质内容")

        # 2. 禁止占位词
        for w in FORBIDDEN_PLACEHOLDERS:
            if w in text:
                issues.append(f"包含占位词「{w}」，需替换为实际内容或删除")

        # 3. 口语化
        for pat in COLLOQUIAL_PATTERNS:
            m = re.search(pat, text)
            if m:
                issues.append(f"包含口语化表达「{m.group()}」，需改为正式书面语")

        # 4. AI 元注释
        for pat in META_COMMENT_PATTERNS:
            m = re.search(pat, text)
            if m:
                issues.append(f"包含元注释「{m.group()}」，需删除")

        # 5. 结构检查 — 应有标题行
        has_heading = bool(re.search(r"^#{1,3}\s+\S", text, re.MULTILINE)) or \
                      bool(re.search(r"^\d+(\.\d+)*\s+\S", text, re.MULTILINE))
        if not has_heading and chapter_title:
            issues.append("缺少章节标题，需以「数字. 标题」格式开头")

        passed = len(issues) == 0
        suggestions = ""
        if not passed:
            suggestions = "；".join(issues)
        return ReviewResult(passed=passed, issues=issues, suggestions=suggestions)

    def quick_scan(self, text: str) -> List[str]:
        """快速扫描，返回所有命中的问题词（用于终审统计）。"""
        hits = []
        for w in FORBIDDEN_PLACEHOLDERS:
            if w in text:
                hits.append(w)
        return hits
