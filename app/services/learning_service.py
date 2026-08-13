"""Continuous learning service — feedback recording, analysis, and prompt improvement.

Three-layer learning loop:
1. Feedback Collection — record every generation/review result
2. Experience Extraction — analyze patterns from accumulated feedback
3. Feedback Application — inject learned hints into generation prompts
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Layer 1: Feedback Recording
# ═══════════════════════════════════════════════════════════════

async def record_generation_feedback(
    session_id: str = "",
    report_title: str = "",
    domain: str = "stability",
    quality_audit: Optional[Dict[str, Any]] = None,
    rewrite_count: int = 0,
    rewrite_chapters: Optional[List[int]] = None,
    passed: bool = False,
    output_path: str = "",
    full_text: str = "",
) -> Optional[int]:
    """Record a generation session's quality feedback for learning.

    Called after report generation completes (success or failure).
    """
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text

        audit = quality_audit or {}
        issues_list = audit.get("all_issues", [])

        overall = _calc_overall_score(audit, passed)
        data_score = 100 - (
            audit.get("critical_issues", len(issues_list)) * 8
            + len([i for i in issues_list if i.get("type") in ("fabricated_data", "data_validity")]) * 10
        )
        data_score = max(0, min(100, data_score))

        fabricated = sum(1 for i in issues_list if i.get("type") == "fabricated_data")
        validity = sum(1 for i in issues_list if i.get("type") == "data_validity")
        regulation = sum(1 for i in issues_list if i.get("type") == "hallucinated_regulation")
        blocking = sum(1 for i in issues_list if i.get("type") in ("blocking_wording", "forbidden_pattern"))

        async with async_session() as db:
            result = await db.execute(text("""
                INSERT INTO generation_feedback
                (session_id, report_title, domain, overall_score, data_score,
                 total_issues, fabricated_count, validity_count, regulation_count, blocking_count,
                 rewrite_count, rewrite_chapters, passed, output_path, full_text, feedback_json)
                VALUES (:sid, :title, :dom, :oscore, :dscore, :total, :fab, :val, :reg, :blk,
                 :rw, :rwc, :passed, :opath, :text, :json)
            """), {
                "sid": session_id, "title": report_title[:200], "dom": domain,
                "oscore": overall, "dscore": data_score,
                "total": audit.get("total_issues", len(issues_list)),
                "fab": fabricated, "val": validity, "reg": regulation, "blk": blocking,
                "rw": rewrite_count, "rwc": json.dumps(rewrite_chapters or []),
                "passed": 1 if passed else 0, "opath": output_path,
                "text": full_text[:100000] if full_text else "",
                "json": json.dumps(audit, ensure_ascii=False, default=str)[:50000],
            })
            await db.commit()
            row = await db.execute(text("SELECT last_insert_rowid()"))
            doc_id = row.scalar()
            logger.info(f"Feedback recorded: id={doc_id}, score={overall}, passed={passed}")
            return doc_id
    except Exception as e:
        logger.warning(f"Feedback recording failed (non-critical): {e}")
        return None


def _calc_overall_score(audit: dict, passed: bool) -> float:
    """Calculate overall score from audit data."""
    if passed:
        base = 80
    else:
        base = 50
    total_issues = audit.get("total_issues", 0)
    critical = audit.get("critical_issues", 0)
    score = base - (total_issues * 2) - (critical * 5)
    return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════
# Layer 2: Experience Extraction
# ═══════════════════════════════════════════════════════════════

async def get_recent_stats(domain: str = "stability", limit: int = 20) -> Dict[str, Any]:
    """Get recent generation statistics."""
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text
        async with async_session() as db:
            # Recent stats
            row = await db.execute(text("""
                SELECT COUNT(*) as total, AVG(overall_score) as avg_score,
                       SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END) as passed_count,
                       AVG(total_issues) as avg_issues, AVG(rewrite_count) as avg_rewrites
                FROM generation_feedback
                WHERE domain = :dom AND created_at > datetime('now', '-30 days')
            """), {"dom": domain})
            stats = row.fetchone()
        return {
            "total_generations": stats[0] or 0,
            "avg_score": round(stats[1] or 0, 1),
            "pass_rate": round((stats[2] or 0) / max(1, stats[0] or 1) * 100, 1),
            "avg_issues": round(stats[3] or 0, 1),
            "avg_rewrites": round(stats[4] or 0, 1),
        }
    except Exception as e:
        logger.warning(f"Stats query failed: {e}")
        return {"total_generations": 0, "avg_score": 0, "pass_rate": 0, "avg_issues": 0, "avg_rewrites": 0}


async def get_common_issues(domain: str = "stability", limit: int = 10) -> List[Dict[str, Any]]:
    """Get the most common failure patterns from recent feedback."""
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text
        async with async_session() as db:
            rows = await db.execute(text("""
                SELECT feedback_json FROM generation_feedback
                WHERE domain = :dom AND passed = 0 AND feedback_json IS NOT NULL
                ORDER BY created_at DESC LIMIT :lim
            """), {"dom": domain, "lim": limit})
            all_issues = []
            for (json_str,) in rows.fetchall():
                try:
                    data = json.loads(json_str) if isinstance(json_str, str) else {}
                    for issue in data.get("all_issues", [])[:20]:
                        msg = issue.get("message", issue.get("description", ""))
                        if msg:
                            all_issues.append(issue.get("type", "unknown"))
                except:
                    pass

        # Count and rank
        from collections import Counter
        ranked = Counter(all_issues).most_common(5)
        return [{"type": t, "count": c, "label": _label_for_type(t)} for t, c in ranked]
    except Exception as e:
        logger.warning(f"Common issues query failed: {e}")
        return []


def _label_for_type(issue_type: str) -> str:
    return {
        "fabricated_data": "编造数据",
        "data_validity": "数据异常（负数/年份错误/反对率）",
        "hallucinated_regulation": "编造法规文号",
        "invalid_range": "数值超出合理范围",
        "blocking_wording": "占位符/口语表达残留",
        "forbidden_pattern": "禁用表达",
        "too_short": "内容过短",
        "missing_table": "缺少表格",
        "source_inconsistency": "数据源不一致",
        "missing_materials": "缺少必要资料",
    }.get(issue_type, issue_type)


# ═══════════════════════════════════════════════════════════════
# Layer 3: Feedback Application
# ═══════════════════════════════════════════════════════════════

async def build_learning_hints(domain: str = "stability") -> str:
    """Generate prompt hints from accumulated learning experience.

    Returns a string to inject into chapter generation prompts.
    """
    stats = await get_recent_stats(domain)
    common = await get_common_issues(domain)

    if stats["total_generations"] < 3:
        return ""  # Not enough data yet

    parts = ["\n## 📊 历史经验提醒（基于最近{}次生成）\n".format(stats["total_generations"])]
    parts.append(f"平均评分: {stats['avg_score']}/100 | 通过率: {stats['pass_rate']}% | 平均重写: {stats['avg_rewrites']}次\n")

    if common:
        parts.append("\n### ⚠️ 最常见问题（请特别注意避免）：\n")
        for item in common[:5]:
            parts.append(f"- {item['label']}（最近{stats['total_generations']}次中出现{item['count']}次）\n")

    return "".join(parts)


async def get_excellent_examples(domain: str = "stability", chapter_num: int = 0, limit: int = 3) -> List[Dict[str, Any]]:
    """Retrieve excellent reports for use as few-shot examples.

    Queries knowledge_documents for high-quality learned reports.
    """
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text
        async with async_session() as db:
            rows = await db.execute(text("""
                SELECT title, raw_text FROM knowledge_documents
                WHERE domain = :dom AND document_type = 'report' AND is_active = 1
                ORDER BY created_at DESC LIMIT :lim
            """), {"dom": domain, "lim": limit * 3})
            results = []
            for title, raw_text in rows.fetchall():
                if raw_text and len(raw_text) > 500:
                    results.append({"title": title, "text": raw_text[:3000]})
            return results[:limit]
    except Exception as e:
        logger.warning(f"Excellent examples query failed: {e}")
        return []


# Singleton
learning_service = type("LearningService", (), {
    "record_feedback": staticmethod(record_generation_feedback),
    "get_recent_stats": staticmethod(get_recent_stats),
    "get_common_issues": staticmethod(get_common_issues),
    "build_learning_hints": staticmethod(build_learning_hints),
    "get_excellent_examples": staticmethod(get_excellent_examples),
})()
