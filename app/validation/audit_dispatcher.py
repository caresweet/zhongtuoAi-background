"""Skill 审核输出 → 章节级审核任务解析。

把 quality_review_agent / skill_service 的原始审核输出（all_issues）解析成
章节级任务，并按 disposition 分类：ai_rewrite（AI 可自动重写）/ human（需人工介入）。

铁律：每个 issue 必须落入某个章节的 ai_rewrite 或 human 队列，禁止只打日志不生成任务。
"""
from __future__ import annotations

from typing import Dict, List, Any, Set, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# 分类规则（可后续迁到 config/DB，供专家反馈蒸馏动态调整）
# ═══════════════════════════════════════════════════════════════════════════════

# 🔴 需人工介入的类型：AI 无法凭空造数据/补材料
NEEDS_HUMAN_TYPES: Set[str] = {
    "fabricated_data",        # 编造数据 → 需人工提供真实数据
    "missing_materials",      # 缺营业执照/资质 → 需人工上传
    "unfilled_placeholder",   # 占位符残留 → 缺真实数据
    "image_no_match",         # 图注无对应图片 → 需人工确认
    "image_bad_caption",      # 图注命名不规范 → 需人工改名
    "image_wrong_chapter",    # 图片位置错乱 → 需人工调整
}

# 🔴 AI 可自动重写的类型（重写时携带缺陷 + 人工意见）
AI_REWRITE_TYPES: Set[str] = {
    "word_count", "missing_content", "missing_pattern",
    "style_or_data_issue", "score_out_of_range", "score_inconsistency",
    "hallucinated_regulation", "expert_skill_violation", "empty_section",
    "over_description", "duplicate_heading", "date_error",
    "support_rate_inconsistency", "oppose_rate_nonzero",
    "data_validity", "invalid_range", "missing_reference",
    "missing_table", "table_incomplete", "table_format",
    "forbidden_pattern", "blocking_wording", "image_placeholder",
    "image_marker_residual",
}


def classify_issue(issue: Dict[str, Any]) -> str:
    """把单个 issue 分类为 disposition。

    优先级：auto_fix > human > ai_rewrite > warning。
    """
    suggestion = issue.get("suggestion", "")
    itype = issue.get("type", "")
    severity = issue.get("severity", "warning")

    if suggestion == "auto_fix":
        return "auto_fix"
    if itype in NEEDS_HUMAN_TYPES or suggestion == "manual_fix":
        return "human"
    if suggestion == "regenerate" and severity == "critical":
        return "ai_rewrite"
    if itype in AI_REWRITE_TYPES and severity == "critical":
        return "ai_rewrite"
    # 非阻塞警告：不进入重写/人工队列，只入 audit_meta
    return "warning"


def parse_skill_audit_to_chapter_tasks(audit_result: Dict[str, Any]) -> Dict[int, List[Dict]]:
    """把审核原始输出解析为 {ch_num: [audit_item, ...]}。

    这是「Skill 输出 → 章节任务」的唯一入口，取代原先只打日志的 verify_skill_effect。
    """
    tasks: Dict[int, List[Dict]] = {}
    all_issues = audit_result.get("all_issues", []) or []
    for issue in all_issues:
        if not isinstance(issue, dict):
            continue
        ch = issue.get("chapter", 0)
        if not ch:
            # chapter=0 的全局项（如 missing_materials）不归入具体章，由调用方单独处理
            continue
        disposition = classify_issue(issue)
        item = {
            "chapter": ch,
            "type": issue.get("type", ""),
            "severity": issue.get("severity", "warning"),
            "message": issue.get("message", ""),
            "correction": issue.get("correction", ""),
            "disposition": disposition,
            "skill_rule_id": issue.get("skill_rule_id"),
            "rule_pattern": issue.get("pattern"),
            "match": issue.get("match", ""),
        }
        tasks.setdefault(ch, []).append(item)
    return tasks


def split_tasks(tasks: Dict[int, List[Dict]]) -> Tuple[Set[int], Set[int]]:
    """把章节任务拆成 (ai_rewrite 章节集合, human 章节集合)。"""
    ai_chapters: Set[int] = set()
    human_chapters: Set[int] = set()
    for ch, items in tasks.items():
        for it in items:
            if it["disposition"] == "ai_rewrite":
                ai_chapters.add(ch)
            elif it["disposition"] == "human":
                human_chapters.add(ch)
    return ai_chapters, human_chapters


def collect_global_human_items(audit_result: Dict[str, Any]) -> List[Dict]:
    """收集 chapter=0 的全局人工待办（如缺营业执照），不入具体章重写。"""
    out: List[Dict] = []
    for issue in audit_result.get("all_issues", []) or []:
        if not isinstance(issue, dict):
            continue
        if issue.get("chapter", 0) == 0 and classify_issue(issue) == "human":
            out.append(issue)
    return out


def filter_ai_rewrite_chapters(
    regenerate_chapters: List[int],
    human_items: Dict[int, Dict[str, Any]],
    human_queue: List[int],
    ai_chapters: Set[int],
) -> List[int]:
    """全局全稿重写循环的章节过滤：只保留还允许 AI 修复的章节。

    - 过滤已人工介入的章节（human_queue 中 / human_approved / human_override）
    - 过滤 disposition != ai_rewrite 的章节（如 fabricated_data 需人工补数据）
    已人工介入的章节不再参与 AI 重写，也不再消耗全局重写轮次。
    """
    blocked = set(human_queue or [])
    blocked |= {c for c, hi in (human_items or {}).items()
                if hi.get("human_approved") or hi.get("human_override")}
    return [c for c in (regenerate_chapters or [])
            if c not in blocked and c in ai_chapters]
