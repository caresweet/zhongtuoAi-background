"""Skill Distillation Service — 专家反馈蒸馏成审核 skill。

闭环：
1. 专家评估报告 → 提交反馈（优化点/不足）
2. LLM 蒸馏 → 提炼成结构化 skill（规则 + 文本）
3. skill 存储 → review_skills 表
4. 审核 agent 加载规则 → 审核时额外检查
5. 生成注入 → build_chapter_prompt 注入 skill 文本

skill 两种形式：
- 规则型（rule）：正则/关键词 + 描述 + 严重级别，可程序化自动检查
- 文本型（text）：优化建议/纠正示例，注入生成 prompt
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


async def record_expert_feedback(
    report_title: str = "",
    session_id: str = "",
    report_file_path: str = "",
    domain: str = "stability",
    chapter_num: int = 0,
    issue_type: str = "",
    issue_desc: str = "",
    suggestion: str = "",
    severity: str = "warning",
) -> Optional[int]:
    """记录专家对报告的评估反馈（优化点/不足），关联具体报告。"""
    if not issue_desc:
        return None
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text
        async with async_session() as db:
            result = await db.execute(text("""
                INSERT INTO expert_reviews
                (report_title, session_id, report_file_path, domain, chapter_num, issue_type, issue_desc, suggestion, severity)
                VALUES (:report_title, :session_id, :report_file_path, :domain, :chapter_num, :issue_type, :issue_desc, :suggestion, :severity)
            """), {
                "report_title": report_title, "session_id": session_id,
                "report_file_path": report_file_path, "domain": domain,
                "chapter_num": chapter_num, "issue_type": issue_type,
                "issue_desc": issue_desc, "suggestion": suggestion, "severity": severity,
            })
            await db.commit()
            return result.lastrowid
    except Exception as e:
        logger.warning(f"record_expert_feedback failed: {e}")
        return None


async def distill_skills(domain: str = "stability", limit: int = 50) -> Dict[str, Any]:
    """LLM 蒸馏：把累积的专家反馈提炼成结构化 skill（规则 + 文本）。"""
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text
        async with async_session() as db:
            rows = (await db.execute(text("""
                SELECT id, chapter_num, issue_type, issue_desc, suggestion, severity
                FROM expert_reviews
                WHERE domain = :domain
                ORDER BY id DESC LIMIT :limit
            """), {"domain": domain, "limit": limit})).fetchall()

        if not rows:
            return {"distilled": 0, "message": "无待蒸馏的专家反馈"}

        # 🔴 分组统计：每个 (章节, 问题类型) 被多少条意见提到（近似专家共识度）
        from collections import Counter
        group_counter = Counter((r.chapter_num, r.issue_type) for r in rows)

        # 构造专家反馈文本（标注共识度）
        review_lines = []
        for r in rows:
            cnt = group_counter[(r.chapter_num, r.issue_type)]
            tag = f"【{cnt}位专家一致提到】" if cnt > 1 else "【单一专家意见】"
            review_lines.append(
                f"- {tag}[第{r.chapter_num}章][{r.issue_type}][{r.severity}] {r.issue_desc} → 建议：{r.suggestion}"
            )
        review_text = "\n".join(review_lines)

        # LLM 蒸馏
        from app.services.llm_service import LLMService
        llm = LLMService()
        prompt = f"""你是社会稳定风险评估报告的资深审核专家。以下是多位人工专家对报告评估后提出的优化点和不足，请蒸馏成结构化的「审核 skill」。

每条 skill 两种形式之一：
1. 规则型（rule）：可程序化检查的正则/关键词模式 + 描述 + 严重级别 + 纠正写法
2. 文本型（text）：优化建议/纠正示例（注入生成 prompt，让章节 agent 生成时避免犯错）

专家反馈（已标注共识度）：
{review_text}

处理规则（多位专家意见冲突时）：
- 【多位专家一致提到】的问题 → 高置信度，优先采纳
- 【单一专家意见】→ 降级为参考，谨慎采纳
- 矛盾意见（同一问题有相反建议）→ 以多数派为准，少数派忽略
- 相似意见 → 合并成一条

🔴 系统已有规则（蒸馏时绝不能和这些冲突，否则会失效）：
- 数据铁律：用户未提供的数据，用【待补充】标注是「合法且正确」的做法，不是错误
- 所以专家反馈「数据是待补充」的真实含义是「该数据本来能提取到（在资料里），但没提取出来」，应蒸馏成「从资料中提取真实数据」，而不是「禁止待补充」
- 如果专家的反馈和系统规则有冲突，理解专家的真实意图后再蒸馏，不要字面蒸馏

请输出 JSON 数组（去重，合并相似项，控制在 15 条以内）：
[
  {{"chapter": 0, "skill_type": "rule", "rule_pattern": "综上所述", "rule_desc": "禁止口语化'综上所述'", "severity": "error", "correction": "综合以上分析"}},
  {{"chapter": 3, "skill_type": "text", "rule_desc": "调查数据要具体", "correction": "第3章要写具体支持人数和百分比，不能泛泛说'大部分支持'"}}
]

只输出 JSON，不要解释文字。"""

        result = await llm.chat(messages=[{"role": "user", "content": prompt}], max_tokens=3000, temperature=0.2)
        content = result if isinstance(result, str) else (result.get("content", "") if isinstance(result, dict) else "")

        # 解析 JSON
        skills = _parse_skills_json(content)
        if not skills:
            return {"distilled": 0, "message": "LLM 蒸馏结果无法解析"}

        # 存入 review_skills
        source_ids = ",".join(str(r.id) for r in rows)
        async with async_session() as db:
            for s in skills:
                await db.execute(text("""
                    INSERT INTO review_skills
                    (domain, chapter_num, skill_type, rule_pattern, rule_desc, severity, correction, source_review_ids)
                    VALUES (:domain, :chapter_num, :skill_type, :rule_pattern, :rule_desc, :severity, :correction, :source_ids)
                """), {
                    "domain": domain,
                    "chapter_num": int(s.get("chapter", 0) or 0),
                    "skill_type": s.get("skill_type", "text"),
                    "rule_pattern": s.get("rule_pattern", "") or "",
                    "rule_desc": s.get("rule_desc", "") or "",
                    "severity": s.get("severity", "warning") or "warning",
                    "correction": s.get("correction", "") or "",
                    "source_ids": source_ids,
                })
            await db.commit()

        logger.info(f"蒸馏完成：{len(skills)} 条 skill 来自 {len(rows)} 条专家反馈")
        return {"distilled": len(skills), "source_reviews": len(rows), "skills": skills}
    except Exception as e:
        logger.warning(f"distill_skills failed: {e}")
        return {"distilled": 0, "message": f"蒸馏失败: {e}"}


def _parse_skills_json(content: str) -> List[Dict]:
    """解析 LLM 蒸馏出的 skill JSON。"""
    if not content:
        return []
    content_clean = re.sub(r'```(?:json)?', '', content).strip()
    start = content_clean.find('[')
    end = content_clean.rfind(']')
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(content_clean[start:end + 1])
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
    except json.JSONDecodeError:
        pass
    return []


async def get_active_skills(domain: str = "stability") -> Dict[str, Any]:
    """获取活跃的审核 skill（规则 + 文本）。"""
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text
        async with async_session() as db:
            rows = (await db.execute(text("""
                SELECT id, chapter_num, skill_type, rule_pattern, rule_desc, severity, correction
                FROM review_skills
                WHERE domain = :domain AND is_active = 1
                ORDER BY id
            """), {"domain": domain})).fetchall()

        rules = []
        texts = []
        for r in rows:
            if r.skill_type == "rule" and r.rule_pattern:
                rules.append({
                    "pattern": r.rule_pattern,
                    "desc": r.rule_desc or "",
                    "severity": r.severity or "warning",
                    "correction": r.correction or "",
                    "chapter": r.chapter_num or 0,
                })
            elif r.skill_type == "text" and (r.rule_desc or r.correction):
                texts.append({
                    "desc": r.rule_desc or "",
                    "correction": r.correction or "",
                    "chapter": r.chapter_num or 0,
                })
        return {"rules": rules, "texts": texts, "total": len(rows)}
    except Exception as e:
        logger.warning(f"get_active_skills failed: {e}")
        return {"rules": [], "texts": [], "total": 0}


async def get_skill_hints(domain: str = "stability") -> str:
    """获取文本型 skill，格式化为生成 prompt 注入用的提示文本。"""
    skills = await get_active_skills(domain)
    texts = skills.get("texts", [])
    if not texts:
        return ""
    lines = ["\n## ⚠️ 历史专家反馈（请务必避免）"]
    for t in texts[:10]:
        ch = f"第{t['chapter']}章 " if t.get("chapter") else ""
        lines.append(f"- {ch}{t.get('desc', '')}：{t.get('correction', '')}")
    return "\n".join(lines) + "\n"
