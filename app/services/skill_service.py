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

🔴 蒸馏铁律（必须严格遵守）：
1. **不丢失内容**：如果一条专家反馈里提到了多个小节（如"1.1 决策名称 和 1.2 决策主体"），必须为每个小节**分别**生成一条 skill，不能只提炼其中一个而丢弃其他。
2. **精准定位小节**：专家反馈里明确提到的小节号（如 1.1、1.2、1.3）要保留在 rule_desc 里（如"1.2 决策主体应..."），不能模糊成"第1章"。
3. **提取小节标题**：从反馈中识别出专家指的是哪一节（决策名称/决策主体/稳评责任单位/征收位置等），明确写入 skill 描述。

处理规则（多位专家意见冲突时）：
- 【多位专家一致提到】的问题 → 高置信度，优先采纳
- 【单一专家意见】→ 降级为参考，谨慎采纳
- 矛盾意见（同一问题有相反建议）→ 以多数派为准，少数派忽略

🔴 系统已有规则（蒸馏时绝不能和这些冲突，否则会失效）：
- 数据铁律：用户未提供的数据，用【待补充】标注是「合法且正确」的做法，不是错误
- 所以专家反馈「数据是待补充」的真实含义是「该数据本来能提取到（在资料里），但没提取出来」，应蒸馏成「从资料中提取真实数据」，而不是「禁止待补充」
- 如果专家的反馈和系统规则有冲突，理解专家的真实意图后再蒸馏，不要字面蒸馏

请输出 JSON 数组（去重，合并相似项，控制在 20 条以内）：
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

        # 🔴 蒸馏去重：检查已有 skill，跳过相似重复项
        source_ids = ",".join(str(r.id) for r in rows)
        async with async_session() as db:
            # 加载已有 skill 用于去重
            existing = (await db.execute(text("""
                SELECT rule_desc, rule_pattern FROM review_skills
                WHERE domain = :domain AND is_active = 1
            """), {"domain": domain})).fetchall()
            existing_descs = [str(r.rule_desc or "") for r in existing]
            existing_patterns = [str(r.rule_pattern or "") for r in existing]

            inserted = 0
            skipped = 0
            for s in skills:
                desc = s.get("rule_desc", "") or ""
                pattern = s.get("rule_pattern", "") or ""
                # 去重判断：rule_desc 或 rule_pattern 与已有 skill 相似
                if _is_duplicate_skill(desc, pattern, existing_descs, existing_patterns):
                    skipped += 1
                    continue
                await db.execute(text("""
                    INSERT INTO review_skills
                    (domain, chapter_num, skill_type, rule_pattern, rule_desc, severity, correction, source_review_ids)
                    VALUES (:domain, :chapter_num, :skill_type, :rule_pattern, :rule_desc, :severity, :correction, :source_ids)
                """), {
                    "domain": domain,
                    "chapter_num": int(s.get("chapter", 0) or 0),
                    "skill_type": s.get("skill_type", "text"),
                    "rule_pattern": pattern,
                    "rule_desc": desc,
                    "severity": s.get("severity", "warning") or "warning",
                    "correction": s.get("correction", "") or "",
                    "source_ids": source_ids,
                })
                inserted += 1
            await db.commit()

        logger.info(f"蒸馏完成：{inserted} 条新 skill，跳过 {skipped} 条重复（来自 {len(rows)} 条反馈）")
        return {"distilled": inserted, "skipped": skipped, "source_reviews": len(rows), "skills": skills}
    except Exception as e:
        logger.warning(f"distill_skills failed: {e}")
        return {"distilled": 0, "message": f"蒸馏失败: {e}"}


def _is_duplicate_skill(desc: str, pattern: str, existing_descs: list, existing_patterns: list) -> bool:
    """判断新蒸馏的 skill 是否与已有 skill 重复。

    规则型：rule_pattern 相同或高度相似
    文本型：rule_desc 包含/被包含于已有描述，或提取关键词重叠
    """
    if not desc and not pattern:
        return True  # 空 skill 直接跳过

    # 规则型：pattern 相同
    if pattern:
        for ep in existing_patterns:
            if pattern == ep or (pattern in ep or ep in pattern):
                return True

    # 文本型：desc 关键词重叠（提取核心关键词判断）
    import re as _re_dd
    def _keywords(text: str) -> set:
        # 提取"1.1 决策名称"中的小节号和小节名，以及核心词
        kws = set()
        for m in _re_dd.findall(r'(\d\.\d|\d+[-—]\d+|[一-龥]{2,6}名称|[一-龥]{2,6}单位|[一-龥]{2,6}数据|应|禁止)', text):
            kws.add(m)
        return kws

    new_kws = _keywords(desc)
    if new_kws:
        for ed in existing_descs:
            ed_kws = _keywords(ed)
            if new_kws & ed_kws:  # 关键词有重叠
                return True
    return False


async def auto_extract_high_freq_issues(domain: str = "stability", min_count: int = 3, limit: int = 50) -> Dict[str, Any]:
    """自动提炼高频问题：从 generation_feedback 的质量审计中统计高频问题类型，
    自动生成 skill（无需人工反馈）。

    当某类问题（如"编造百分比""口语化""内容为空"）在多份报告的质量审计中反复出现，
    说明章节 agent 系统性犯这个错，自动生成一条 skill 反哺生成。
    """
    import json as _json
    from collections import Counter
    try:
        from app.database.knowledge_db import async_session
        from sqlalchemy import text as _text
        async with async_session() as db:
            rows = (await db.execute(_text("""
                SELECT feedback_json FROM generation_feedback
                WHERE domain = :domain AND feedback_json IS NOT NULL
                ORDER BY id DESC LIMIT :limit
            """), {"domain": domain, "limit": limit})).fetchall()

        if not rows:
            return {"extracted": 0, "message": "无生成反馈可分析"}

        # 统计高频问题类型
        issue_counter = Counter()
        issue_examples = {}
        for r in rows:
            try:
                fj = _json.loads(r.feedback_json) if isinstance(r.feedback_json, str) else r.feedback_json
            except Exception:
                continue
            if not isinstance(fj, dict):
                continue
            for issue in (fj.get("all_issues") or []):
                if not isinstance(issue, dict):
                    continue
                itype = issue.get("type", "unknown")
                issue_counter[itype] += 1
                if itype not in issue_examples:
                    issue_examples[itype] = issue.get("message", "")

        # 高频问题 → skill
        SKILL_MAP = {
            "style_or_data_issue": ("text", "避免口语化表述（总的来说/综上所述/老百姓等），用规范公文表达", "写短句，用规范公文用语，不用口语化总结词"),
            "fabricated_data": ("text", "禁止编造数据（百分比/人数/问卷数），用户未提供的写【待补充】", "所有数字必须来自项目资料，缺失标【待补充】"),
            "data_validity": ("text", "数据有效性检查：支持率应为100%、反对率应为0%、年份应为2026", "核对数据合规性，不合规的数据不得写入"),
            "missing_content": ("text", "章节内容不能为空，数据缺失时写【待补充】而不是跳过", "每章都要有实质内容，缺失数据用【待补充】标注"),
            "word_count": ("text", "章节字数要达标，不能过短", "每章按规范字数要求撰写，内容充实"),
            "blocking_wording": ("text", "避免禁止性表达（未完成资料表达等AI套话）", "用具体事实和规范用语，不用模糊的AI套话"),
            "missing_pattern": ("text", "章节应包含标准小标题结构（如评估方法/风险识别等）", "按标准小标题结构组织章节内容"),
            "score_out_of_range": ("text", "评分必须在0-100范围内，不得出现负分或超100分", "核对评分范围，修正异常分值"),
            "hallucinated_regulation": ("text", "法规引用必须真实存在，禁止编造法规文号", "只引用知识库中确认的法规，不编造文号"),
        }

        inserted = 0
        async with async_session() as db:
            existing = (await db.execute(_text("""
                SELECT rule_desc, rule_pattern FROM review_skills WHERE domain = :domain AND is_active = 1
            """), {"domain": domain})).fetchall()
            existing_descs = [str(r.rule_desc or "") for r in existing]
            existing_patterns = [str(r.rule_pattern or "") for r in existing]

            extracted = []
            for itype, count in issue_counter.most_common():
                if count < min_count:
                    continue
                if itype not in SKILL_MAP:
                    continue
                skill_type, desc, correction = SKILL_MAP[itype]
                desc = f"[高频] {desc}（{count}份报告出现）"
                if _is_duplicate_skill(desc, "", existing_descs, existing_patterns):
                    continue
                await db.execute(_text("""
                    INSERT INTO review_skills
                    (domain, chapter_num, skill_type, rule_pattern, rule_desc, severity, correction)
                    VALUES (:domain, 0, :skill_type, '', :rule_desc, 'warning', :correction)
                """), {
                    "domain": domain, "skill_type": skill_type,
                    "rule_desc": desc, "correction": correction,
                })
                inserted += 1
                extracted.append({"type": itype, "count": count, "desc": desc})
            await db.commit()

        logger.info(f"自动提炼高频问题：{inserted} 条 skill（跳过重复）")
        return {"extracted": inserted, "high_freq": [{"type": t, "count": c} for t, c in issue_counter.most_common() if c >= min_count]}
    except Exception as e:
        logger.warning(f"auto_extract_high_freq_issues failed: {e}")
        return {"extracted": 0, "message": f"自动提炼失败: {e}"}


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
