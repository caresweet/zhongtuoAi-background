"""QualityReviewAgent — post-generation data merge, audit and auto-rewrite.

Runs after all 10 chapters are confirmed. Performs:
1. Data merge: consolidate all chapter content + tables into a unified view
2. Cross-chapter consistency checks (project name, area, dates, scores)
3. Format compliance review (headings, tables, placeholders, meta-text)
4. Content quality review (word count, completeness, professionalism)
5. LLM-powered deep review for logical consistency
6. Auto-fix minor formatting issues
7. Trigger chapter re-generation for critical issues (loop until clean)
"""

import re
import asyncio
import logging
from typing import Dict, List, Any, Tuple, Optional, Set

from .base_agent import BaseAgent
from app.validation.content_guardrails import (
    find_data_validity_issues,
    find_fabricated_data,
    validate_numeric_ranges,
    find_hallucinated_regulations,
    check_required_materials,
    AI_BUZZWORDS,
)

logger = logging.getLogger(__name__)


class QualityReviewAgent(BaseAgent):
    """Post-generation data merge + quality audit + auto-rewrite agent.

    Reviews all 10 chapters and their tables for consistency, format, and quality.
    Automatically fixes minor issues. Triggers re-generation of chapters with
    critical problems. Re-runs audit after regenerations until clean.
    """

    name = "QualityReviewAgent"
    description = "合并审核所有章节和表格数据，检查格式一致性与数据准确性，发现问题自动触发重写"
    covered_steps = [16]

    # Minimum word counts per chapter
    MIN_WORDS = {
        1: 300, 2: 300, 3: 400, 4: 600, 5: 300,
        6: 300, 7: 500, 8: 300, 9: 400, 10: 600,
    }

    # Expected tables per chapter (from CHAPTER_DEFINITIONS)
    EXPECTED_TABLES = {
        1: [],
        2: [],
        3: [],
        4: [],
        5: [],  # 金湖模板：第5章有表格但由LLM自然生成
        6: [],
        7: [],
        8: [],
        9: [],
        10: [],  # 金湖模板第10章无表格
    }

    # Critical patterns that MUST be present per chapter
    REQUIRED_PATTERNS = {
        1: [r"决策名称", r"责任单位", r"实施单位", r"(?:征收|项目).*(?:位置|坐落|位于)"],
        2: [r"评估过程", r"评估方法", r"对照表法|实地考察法|问卷调查法"],
        3: [r"问卷调查", r"(?:公众|部门)意见", r"(?:公示|公告)"],
        4: [r"合法性分析", r"合理性分析", r"可行性分析", r"可控性分析"],
        5: [r"风险因素", r"(?:发生概率|影响程度)", r"初始.*等级"],
        6: [r"措施前", r"量化.*(?:评分|打分|指标)", r"DB32"],
        7: [r"防范.*化解|化解.*措施", r"责任主体", r"完成时限"],
        8: [r"措施后", r"(?:前后|措施.*对比).*(?:对比|得分)"],  # 风险下降 is nice-to-have, not required
        9: [r"评估结论", r"低风险", r"工作建议"],
        10: [r"应急预案", r"组织领导", r"(?:预警预防|现场处置)"],
    }

    # Patterns that should NOT appear
    FORBIDDEN_PATTERNS = [
        (r'好的[，,]\s*作为稳评报告编制专家', 'Agent meta-text'),
        (r'[（(]注[：:][^）)]*[）)]', 'Agent note'),
        (r'[（(]全文共\d+字', 'Word count note'),
        (r'```json', 'JSON code block'),
        (r'【待补充】', 'Unfilled placeholder'),
    ]

    # 🔴 Colloquial/口语化 patterns that suggest AI-generated fluff
    COLLOQUIAL_PATTERNS = [
        (r'总的来说[，,]', '口语化总结「总的来说」'),
        (r'综上所述[，,]', '口语化「综上所述」（公文应用"综合以上分析"）'),
        (r'我们可以看出', '口语化「我们可以看出」'),
        (r'显而易见', '口语化「显而易见」'),
        (r'毋庸置疑', '口语化「毋庸置疑」'),
        (r'值得一提的是', '口语化「值得一提的是」'),
        (r'不得不提', '口语化「不得不提」'),
        (r'非常(?:重要|关键|必要)', '口语化程度副词「非常XX」'),
        (r'(?:很大的|极大地|显著地)', '口语化程度副词'),
        (r'大家(?:都|一致)', '口语化「大家」'),
        (r'老百姓', '口语化「老百姓」（应用"群众"或"被征收人"）'),
        (r'[，,]\s*然后\s*[，,]', '口语化连接词「然后」'),
        (r'[，,]\s*而且\s*[，,]', '口语化连接词「而且」'),
        (r'等等[。，]', '口语化「等等」（应用"等"）'),
    ]

    # 🔴 Obvious data errors to catch
    DATA_ERROR_PATTERNS = [
        (r'(?<!\d)0\s*亩(?!\d)', '面积异常：0亩'),
        (r'(?<!\d)0\s*平方米(?!\d)', '面积异常：0平方米'),
        (r'(?<!\d)0\s*人(?!\d)', '人数异常：0人'),
        (r'(?<!\d)0\s*户(?!\d)', '户数异常：0户'),
        (r'(?<!\d)0\s*%\s*(?:支持|反对)', '支持率/反对率异常：0%'),
        (r'补偿标准[：:]\s*0', '补偿标准异常：0'),
        (r'2024年(?!\d)', '年份错误：2024（应为2026）'),
        (r'2025年(?!\d)', '年份错误：2025（应为2026）'),
        (r'2027年(?!\d)', '年份错误：2027（应为2026）'),
    ]

    # Max regeneration loops per chapter
    MAX_REGENERATION_LOOPS = 3

    # 🔴 口语化/AI味 自动替换规则（检测到 → 替换成规范公文表达）
    STYLE_REPLACEMENTS = [
        (r'总的来说[，,]?\s*', ''),                 # 删除口语总结
        (r'综上所述[，,]?', '综合以上分析'),
        (r'我们可以看出[，,]?', '从调查情况看'),
        (r'显而易见[，,]?', ''),
        (r'毋庸置疑[，,]?', ''),
        (r'值得一提的是[，,]?', ''),
        (r'不得不提[，,]?', ''),
        (r'非常(重要|关键|必要)', r'\1'),
        (r'(?:很大的|极大地|显著地)', ''),
        (r'大家(?:都|一致)?', '群众'),
        (r'老百姓', '被征收人'),
        (r'[，,]\s*然后', '，随后'),
        (r'[，,]\s*而且', '，并且'),
        (r'等等[。，]', '等。'),
    ]

    # 🔴 年份错误替换（报告年度统一为 2026）
    YEAR_REPLACEMENTS = [
        (r'2024年', '2026年'),
        (r'2025年', '2026年'),
        (r'2027年', '2026年'),
    ]

    async def think(self, state: dict) -> Dict[str, Any]:
        """Analyze all chapters and produce a comprehensive audit plan."""
        chapters = state.get("chapters", {})
        generated = state.get("generated_sections", {})

        confirmed = sum(
            1 for ch_num, ch in chapters.items()
            if isinstance(ch, dict) and ch.get("status") == "approved"
        )
        total_chapters = sum(
            1 for ch_num, ch in chapters.items()
            if isinstance(ch, dict) and ch.get("markdown")
        )

        steps = [
            f"📋 开始数据合并审核：{total_chapters}/10 章已生成（{confirmed} 已确认）",
            "🔗 第一步：合并所有章节数据，构建统一数据视图...",
            "🔍 第二步：跨章节数据一致性检查（面积/日期/评分/单位名称）...",
            "📝 第三步：逐章格式合规检查（禁用模式/表格/标题）...",
            "📊 第四步：表格完整性检查（预期表格 vs 实际表格）...",
            "🧠 第五步：LLM深度逻辑审核（如有LLM）...",
            "🔧 第六步：自动修复可修复问题...",
            "🔄 第七步：标记需重写的章节并触发重新生成...",
        ]

        return {
            "summary": f"数据合并审核：{total_chapters}/10章 — 一致性+格式+表格+逻辑",
            "steps": steps,
            "actions": [
                {"type": "merge_data"},
                {"type": "cross_chapter_check"},
                {"type": "format_check"},
                {"type": "table_completeness_check"},
                {"type": "llm_deep_review"},
                {"type": "auto_fix"},
                {"type": "trigger_regeneration"},
            ],
            "total_chapters": total_chapters,
            "confirmed_count": confirmed,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute all quality checks: merge → audit → fix → regenerate."""
        chapters = state.get("chapters", {})

        all_issues: List[Dict] = []
        chapter_issues: Dict[int, List[Dict]] = {}
        format_issues: List[Dict] = []
        consistency_issues: List[Dict] = []
        table_issues: List[Dict] = []

        # ═══════════════════════════════════════════════════════════
        # Step 1: Merge all chapter data into a unified view
        # ═══════════════════════════════════════════════════════════
        merged_data = await self._merge_all_chapter_data(state)
        await self._emit_thinking(
            f"📊 数据合并完成：{len(merged_data.get('all_tables', []))} 个表格、"
            f"{merged_data.get('total_words', 0)} 字"
        )

        # ═══════════════════════════════════════════════════════════
        # Step 2: Per-chapter format + content checks
        # ═══════════════════════════════════════════════════════════
        for ch_num in range(1, 11):
            ch_data = chapters.get(ch_num, {})
            if not isinstance(ch_data, dict):
                continue
            markdown = ch_data.get("markdown", "")

            if not markdown:
                chapter_issues.setdefault(ch_num, []).append({
                    "chapter": ch_num,
                    "type": "missing_content",
                    "severity": "critical",
                    "message": f"第{ch_num}章内容为空",
                    "suggestion": "regenerate",
                })
                continue

            # Word count
            word_count = len(markdown.replace('\n', '').replace(' ', ''))
            min_words = self.MIN_WORDS.get(ch_num, 300)
            if word_count < min_words:
                chapter_issues.setdefault(ch_num, []).append({
                    "chapter": ch_num,
                    "type": "word_count",
                    "severity": "warning",
                    "message": f"第{ch_num}章字数不足（{word_count}字 < {min_words}字）",
                    "suggestion": "expand",
                })

            # Required patterns
            for pattern in self.REQUIRED_PATTERNS.get(ch_num, []):
                if not re.search(pattern, markdown):
                    chapter_issues.setdefault(ch_num, []).append({
                        "chapter": ch_num,
                        "type": "missing_pattern",
                        "severity": "critical",
                        "message": f"第{ch_num}章缺少关键内容：{pattern}",
                        "suggestion": "regenerate",
                    })

            # Forbidden patterns — 🔴 降级为 auto_fix（AI套词/占位符可自动修复，不触发整章重写）
            from app.validation.content_guardrails import find_blocking_issues
            for issue in find_blocking_issues(markdown):
                format_issues.append({
                    "chapter": ch_num,
                    "type": issue["type"],
                    "severity": "error",
                    "message": f"第{ch_num}章存在禁止表达：{issue['description']}",
                    "suggestion": "auto_fix",
                })
                chapter_issues.setdefault(ch_num, []).append(format_issues[-1])
            for pattern, desc in self.FORBIDDEN_PATTERNS:
                matches = re.findall(pattern, markdown)
                if matches:
                    format_issues.append({
                        "chapter": ch_num,
                        "type": "forbidden_pattern",
                        "severity": "error",
                        "message": f"第{ch_num}章发现：{desc}",
                        "matches": matches[:3],
                        "suggestion": "auto_fix",
                    })
                    chapter_issues.setdefault(ch_num, []).append(format_issues[-1])

            for pattern, desc in self.COLLOQUIAL_PATTERNS + self.DATA_ERROR_PATTERNS:
                matches = re.findall(pattern, markdown)
                if matches:
                    # 🔴 可自动修复：口语化词 + 年份错误；数据错误(0亩/0人)不可瞎改
                    is_data_error = any(d in desc for d in ['面积异常', '人数异常', '户数异常', '支持率/反对率异常', '补偿标准异常'])
                    if is_data_error:
                        severity, suggestion = "critical", "regenerate"
                    else:
                        severity, suggestion = "error", "auto_fix"
                    issue = {
                        "chapter": ch_num,
                        "type": "style_or_data_issue",
                        "severity": severity,
                        "message": f"第{ch_num}章发现：{desc}",
                        "matches": matches[:3],
                        "suggestion": suggestion,
                    }
                    format_issues.append(issue)
                    chapter_issues.setdefault(ch_num, []).append(issue)

            # Table formatting
            tbl_issues = self._check_table_format(markdown, ch_num)
            chapter_issues.setdefault(ch_num, []).extend(tbl_issues)

            # 🔴 结构问题检查：空小节 / 标题重复 / 责任单位过度描述
            struct_issues = self._check_structure_issues(markdown, ch_num)
            chapter_issues.setdefault(ch_num, []).extend(struct_issues)

        # ═══════════════════════════════════════════════════════════
        # Step 2.5: 🔴 Enhanced data validity checks (per-chapter)
        # ═══════════════════════════════════════════════════════════
        data_validity_issues: List[Dict] = []
        fabricated_issues: List[Dict] = []
        range_issues: List[Dict] = []
        regulation_issues: List[Dict] = []

        filled_data = state.get("filled_data", {}) or {}
        pdf_raw_text = state.get("_pdf_raw_text", "") or ""
        # Build full pdf_texts from dict if available
        if not pdf_raw_text:
            pdf_texts = state.get("_pdf_texts", {}) or {}
            if isinstance(pdf_texts, dict):
                pdf_raw_text = " ".join(str(v)[:5000] for v in pdf_texts.values() if v)

        for ch_num in range(1, 11):
            ch_data = chapters.get(ch_num, {})
            if not isinstance(ch_data, dict):
                continue
            markdown = ch_data.get("markdown", "")
            if not markdown:
                continue

            # 2.5a: Data validity (negatives, opposition, precision, years)
            for issue in find_data_validity_issues(markdown):
                data_validity_issues.append({
                    "chapter": ch_num,
                    "type": "data_validity",
                    "severity": "critical",
                    "message": f"第{ch_num}章：{issue['description']} → {issue.get('match', '')}",
                    "suggestion": "regenerate",
                })
                chapter_issues.setdefault(ch_num, []).append(data_validity_issues[-1])

            # 2.5b: Fabricated data detection
            if filled_data:
                for issue in find_fabricated_data(markdown, filled_data, pdf_raw_text):
                    fabricated_issues.append({
                        "chapter": ch_num,
                        "type": "fabricated_data",
                        "severity": "critical",
                        "message": issue["message"],
                        "suggestion": "regenerate",
                    })
                    chapter_issues.setdefault(ch_num, []).append(fabricated_issues[-1])

            # 2.5c: Numeric range validation
            for issue in validate_numeric_ranges(markdown):
                sev = issue.get("severity", "warning")
                range_issues.append({
                    "chapter": ch_num,
                    "type": "invalid_range",
                    "severity": sev,
                    "message": f"第{ch_num}章：{issue['message']}",
                    "suggestion": "regenerate" if sev in ("critical", "error") else "auto_fix",
                })
                chapter_issues.setdefault(ch_num, []).append(range_issues[-1])

            # 2.5d: Hallucinated regulation citations
            for issue in find_hallucinated_regulations(markdown):
                regulation_issues.append({
                    "chapter": ch_num,
                    "type": "hallucinated_regulation",
                    "severity": "critical",
                    "message": f"第{ch_num}章：{issue['message']}",
                    "suggestion": "regenerate",
                })
                chapter_issues.setdefault(ch_num, []).append(regulation_issues[-1])

        # ═══════════════════════════════════════════════════════════
        # Step 2.6: 🔴 Required materials verification
        # ═══════════════════════════════════════════════════════════
        material_issues: List[Dict] = []
        fixed_assets = state.get("_fixed_company_assets")
        for issue in check_required_materials(fixed_assets):
            material_issues.append({
                "chapter": 0,  # Global issue, not chapter-specific
                "type": "missing_materials",
                "severity": issue.get("severity", "warning"),
                "message": issue["message"],
                "suggestion": "manual_fix",
            })

        # ═══════════════════════════════════════════════════════════
        # Step 2.7: 🔴 Expert-distilled skill checks
        # ═══════════════════════════════════════════════════════════
        skill_issues = await self._check_expert_skills(state)
        for si in skill_issues:
            chapter_issues.setdefault(si.get("chapter", 0), []).append(si)

        # ═══════════════════════════════════════════════════════════
        # Step 3: Cross-chapter consistency checks
        # ═══════════════════════════════════════════════════════════
        consistency_issues = self._run_all_consistency_checks(state, merged_data)

        # ═══════════════════════════════════════════════════════════
        # Step 4: Table completeness check
        # ═══════════════════════════════════════════════════════════
        table_issues = self._check_table_completeness(merged_data)

        # ═══════════════════════════════════════════════════════════
        # Step 4.5: Integrate Collaboration Agents results
        # ═══════════════════════════════════════════════════════════
        collab_issues = self._integrate_collaboration_agents(state)

        # ═══════════════════════════════════════════════════════════
        # Step 5: LLM-powered deep review (if LLM available)
        # ═══════════════════════════════════════════════════════════
        llm_issues = []
        if self._llm:
            llm_issues = await self._llm_deep_review(state, merged_data)

        # ═══════════════════════════════════════════════════════════
        # Step 6: Classify and prioritize all issues
        # ═══════════════════════════════════════════════════════════
        critical_issues: List[Dict] = []
        auto_fixable: List[Dict] = []
        warnings: List[Dict] = []

        all_sources = [
            chapter_issues, format_issues, consistency_issues,
            table_issues, collab_issues, llm_issues,
            data_validity_issues, fabricated_issues, range_issues,
            regulation_issues, material_issues,
        ]

        for source in all_sources:
            if isinstance(source, dict):
                for ch_num, issues in source.items():
                    for issue in issues:
                        all_issues.append(issue)
                        self._classify_issue(issue, critical_issues, auto_fixable, warnings)
            elif isinstance(source, list):
                for issue in source:
                    all_issues.append(issue)
                    self._classify_issue(issue, critical_issues, auto_fixable, warnings)

        # ═══════════════════════════════════════════════════════════
        # Step 7: Auto-fix what we can
        # ═══════════════════════════════════════════════════════════
        fixed_count = 0
        for issue in auto_fixable:
            fixed = await self._auto_fix_issue(state, issue)
            if fixed:
                fixed_count += 1

        # Re-check for remaining 【待补充】 after fixes
        for ch_num in range(1, 11):
            ch_data = chapters.get(ch_num, {})
            if isinstance(ch_data, dict):
                md = ch_data.get("markdown", "")
                remaining = len(re.findall(r'【待补充】', md))
                if remaining > 0:
                    all_issues.append({
                        "chapter": ch_num,
                        "type": "unfilled_placeholder",
                        "severity": "warning",
                        "message": f"第{ch_num}章仍有 {remaining} 个未填充占位符",
                        "suggestion": "manual_fix",
                    })

        # ═══════════════════════════════════════════════════════════
        # Step 8: Identify chapters that need regeneration
        # ═══════════════════════════════════════════════════════════
        regenerate_chapters: Set[int] = set()
        for issue in critical_issues:
            if issue.get("suggestion") == "regenerate":
                ch = issue.get("chapter", 0)
                if ch:
                    regenerate_chapters.add(ch)

        # ═══════════════════════════════════════════════════════════
        # Step 9: Emit comprehensive audit report
        # ═══════════════════════════════════════════════════════════
        await self._emit_audit_report(
            all_issues, critical_issues, warnings,
            fixed_count, regenerate_chapters, merged_data, state,
        )

        passed = len(critical_issues) == 0 and len(regenerate_chapters) == 0

        return {
            "total_issues": len(all_issues),
            "critical_issues": len(critical_issues),
            "warnings": len(warnings),
            "auto_fixed": fixed_count,
            "regenerate_chapters": sorted(regenerate_chapters),
            "all_issues": all_issues,
            "consistency_issues": consistency_issues,
            "format_issues": format_issues,
            "table_issues": table_issues,
            "collab_issues": collab_issues,
            "llm_issues": llm_issues,
            "merged_data": merged_data,
            "passed": passed,
        }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """Self-validate the audit quality."""
        issues = []
        total = result.get("total_issues", 0)
        regenerate = result.get("regenerate_chapters", [])

        if total > 30:
            issues.append(f"问题数量较多（{total}个），建议人工全面复核")
        if regenerate:
            issues.append(
                f"以下章节需要重新生成：{', '.join(f'第{ch}章' for ch in regenerate)}"
            )
        if total == 0:
            issues.append("✅ 全部审核通过，无任何问题")
        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """Store complete audit results and trigger regeneration list."""
        import datetime
        state["_quality_audit"] = {
            "total_issues": result.get("total_issues", 0),
            "critical_issues": result.get("critical_issues", 0),
            "warnings": result.get("warnings", 0),
            "auto_fixed": result.get("auto_fixed", 0),
            "regenerate_chapters": result.get("regenerate_chapters", []),
            "all_issues": result.get("all_issues", []),
            "passed": result.get("passed", False),
            "merged_data": result.get("merged_data", {}),
            "timestamp": datetime.datetime.now().isoformat(),
        }

        state["_pending_regenerations"] = result.get("regenerate_chapters", [])
        return state

    # ═══════════════════════════════════════════════════════════════
    # Data Merge
    # ═══════════════════════════════════════════════════════════════

    async def _merge_all_chapter_data(self, state: dict) -> Dict[str, Any]:
        """Merge all chapter content, tables, and metadata into a unified view."""
        chapters = state.get("chapters", {})
        generated = state.get("generated_sections", {})
        filled = state.get("filled_data", {})

        all_tables = []
        all_text = []
        chapter_summaries = {}
        total_words = 0
        extracted_values = {}

        for ch_num in range(1, 11):
            ch_data = chapters.get(ch_num, {})
            if not isinstance(ch_data, dict):
                continue

            markdown = ch_data.get("markdown", "")
            tables = ch_data.get("tables", [])
            word_count = len(markdown.replace('\n', '').replace(' ', ''))

            total_words += word_count

            # Collect tables
            for t in tables:
                all_tables.append({
                    "chapter": ch_num,
                    "chapter_title": ch_data.get("title", f"第{ch_num}章"),
                    **t,
                })

            # Extract tables from markdown if not already parsed
            if not tables and markdown:
                extracted = self._extract_tables_from_md(markdown)
                for t in extracted:
                    all_tables.append({
                        "chapter": ch_num,
                        "chapter_title": ch_data.get("title", f"第{ch_num}章"),
                        **t,
                    })

            all_text.append(markdown)

            # Extract key values from this chapter
            chapter_summaries[ch_num] = {
                "title": ch_data.get("title", ""),
                "status": ch_data.get("status", "pending"),
                "word_count": word_count,
                "table_count": len(tables),
                "has_sources": bool(ch_data.get("rag_sources")),
            }

        # Extract key data points across all chapters
        combined_text = "\n\n".join(all_text)
        extracted_values = self._extract_key_data_points(combined_text, filled)

        return {
            "all_tables": all_tables,
            "all_text": combined_text,
            "chapter_summaries": chapter_summaries,
            "total_words": total_words,
            "extracted_values": extracted_values,
            "filled_data_keys": list(filled.keys()),
        }

    def _extract_key_data_points(self, text: str, filled: dict) -> Dict[str, Any]:
        """Extract key data points from merged text across all chapters."""
        data = {}

        # Project name
        m = re.search(r'(?:报告标题|项目名称|决策名称)[：:]\s*(.+?)(?:\n|$)', text)
        if m:
            data["project_name"] = m.group(1).strip()

        # Area (m²)
        m = re.search(r'(\d{3,6})\s*[㎡平方米]', text)
        if m:
            data["area_m2"] = int(m.group(1))
            data["area_mu"] = round(int(m.group(1)) / 666.67, 2)

        # Area (亩)
        m = re.search(r'(\d+\.?\d*)\s*亩', text)
        if m:
            data["area_mu_text"] = float(m.group(1))

        # Org name
        m = re.search(r'(?:责任单位|决策单位)[：:]\s*(.+?)(?:\n|$)', text)
        if m:
            data["org_name"] = m.group(1).strip()

        # Support rate
        m = re.search(r'支持率[：:]?\s*(\d+\.?\d*)\s*%', text)
        if m:
            data["support_rate"] = float(m.group(1))

        # Pre-measure score
        m = re.search(r'措施前.*?(\d{1,2})\s*分', text)
        if m:
            data["pre_measure_score"] = int(m.group(1))

        # Post-measure score
        m = re.search(r'措施后.*?(\d{1,2})\s*分', text)
        if m:
            data["post_measure_score"] = int(m.group(1))

        # Risk level
        m = re.search(r'风险等级[：:]\s*(低风险|中风险|高风险)', text)
        if m:
            data["risk_level"] = m.group(1)

        # Dates
        dates = re.findall(r'2026年\d{1,2}月\d{1,2}日', text)
        if dates:
            data["dates_found"] = list(set(dates))

        return data

    def _extract_tables_from_md(self, markdown: str) -> List[Dict]:
        """Extract table data from markdown."""
        tables = []
        table_pattern = r'(\|.+\|(?:\n\|.+\|)+)'
        matches = re.findall(table_pattern, markdown)

        for i, match in enumerate(matches):
            lines = match.strip().split('\n')
            if len(lines) >= 2:
                headers = [h.strip() for h in lines[0].split('|') if h.strip()]
                rows = []
                for line in lines[2:]:
                    cells = [c.strip() for c in line.split('|') if c.strip()]
                    if cells:
                        rows.append(cells)
                tables.append({
                    "index": i,
                    "headers": headers,
                    "rows": rows,
                    "row_count": len(rows),
                })

        return tables

    # ═══════════════════════════════════════════════════════════════
    # Consistency Checks
    # ═══════════════════════════════════════════════════════════════

    def _run_all_consistency_checks(
        self, state: dict, merged_data: Dict
    ) -> List[Dict]:
        """Run all cross-chapter consistency checks."""
        issues = []
        issues.extend(self._check_area_consistency(state, merged_data))
        issues.extend(self._check_date_consistency(state))
        issues.extend(self._check_risk_score_consistency(state))
        issues.extend(self._check_org_consistency(state))
        issues.extend(self._check_project_name_consistency(state, merged_data))
        issues.extend(self._check_chapter_cross_references(state))
        # 🔴 多agent协同：调查数据一致性 + 支持率合规 + 评分合规
        issues.extend(self._check_survey_consistency(state))
        issues.extend(self._check_support_rate_compliance(state))
        issues.extend(self._check_score_compliance(state))
        return issues

    def _check_area_consistency(self, state: dict, merged: Dict) -> List[Dict]:
        """Check m² and 亩 area values are consistent."""
        issues = []
        extracted = merged.get("extracted_values", {})
        area_m2 = extracted.get("area_m2")
        area_mu = extracted.get("area_mu_text")

        if area_m2 and area_mu:
            expected_mu = round(area_m2 / 666.67, 2)
            if abs(area_mu - expected_mu) > 1:
                issues.append({
                    "chapter": 0,
                    "type": "area_inconsistency",
                    "severity": "critical",
                    "message": f"面积数据不一致：{area_m2}㎡ ≈ {expected_mu}亩，但报告出现{area_mu}亩",
                    "suggestion": "manual_fix",
                })

        return issues

    def _check_date_consistency(self, state: dict) -> List[Dict]:
        """Check dates are consistent (公告期限7天 from April 13, 2026)."""
        issues = []
        chapters = state.get("chapters", {})

        wrong_dates = [
            (r'12天', '公告期限应为7天'),
            (r'4月13日至4月24日', '公告期限应为4月13日-4月19日（7天）'),
            (r'2026年[67]月', '日期月份错误，应为2026年4月'),
        ]

        for ch_num, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                continue
            md = ch_data.get("markdown", "")
            for pattern, msg in wrong_dates:
                if re.search(pattern, md):
                    issues.append({
                        "chapter": ch_num,
                        "type": "date_error",
                        "severity": "critical",
                        "message": f"第{ch_num}章：{msg}",
                        "suggestion": "auto_fix",
                        "pattern": pattern,
                    })

        return issues

    def _check_risk_score_consistency(self, state: dict) -> List[Dict]:
        """Check risk scores flow logically: Ch5→Ch6→Ch7→Ch8."""
        issues = []
        chapters = state.get("chapters", {})

        ch6_md = chapters.get(6, {}).get("markdown", "")
        ch8_md = chapters.get(8, {}).get("markdown", "")

        pre_match = re.search(r'(?:措施前|合计).*?(\d{1,2})\s*分', ch6_md)
        post_match = re.search(r'(?:措施后|合计).*?(\d{1,2})\s*分', ch8_md)

        if pre_match and post_match:
            pre = int(pre_match.group(1))
            post = int(post_match.group(1))
            if post < pre:
                issues.append({
                    "chapter": 8,
                    "type": "score_inconsistency",
                    "severity": "critical",
                    "message": f"措施后得分({post})应 ≥ 措施前得分({pre})（措施改善后得分应提升）",
                    "suggestion": "regenerate",
                })

        return issues

    def _check_survey_consistency(self, state: dict) -> List[Dict]:
        """多agent协同：检查调查人数、支持率在全文一致（不能第3章说54份、第6章说100份）。"""
        issues = []
        chapters = state.get("chapters", {})

        total_counts = set()
        support_rates = set()

        for ch_num, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                continue
            md = ch_data.get("markdown", "")
            # 问卷总数：共/发放/回收/收回 N 份/户
            for m in re.findall(r'(?:共|发放|回收|收回)\s*(\d+)\s*(?:份|户|人)(?:\s*问卷|\s*调查)?', md):
                total_counts.add(int(m))
            # 支持率：支持率 X% 或 X% ... 支持
            for m in re.findall(r'支持率\s*[：:为]?\s*(\d+(?:\.\d+)?)\s*%', md):
                support_rates.add(float(m))
            for m in re.findall(r'(\d+(?:\.\d+)?)\s*%\s*(?:的)?\s*(?:群众|村民|被征收人)?\s*(?:表示|持)?支持', md):
                support_rates.add(float(m))

        if len(total_counts) > 1:
            issues.append({
                "chapter": 0, "type": "survey_count_inconsistency", "severity": "critical",
                "message": f"调查人数不一致：全文出现 {sorted(total_counts)} 等不同问卷总数",
                "suggestion": "regenerate",
            })
        if len(support_rates) > 1:
            issues.append({
                "chapter": 0, "type": "support_rate_inconsistency", "severity": "critical",
                "message": f"支持率不一致：全文出现 {sorted(support_rates)} 等不同支持率",
                "suggestion": "regenerate",
            })
        return issues

    def _check_support_rate_compliance(self, state: dict) -> List[Dict]:
        """征地项目合规：支持率应100%、反对率应0%。"""
        issues = []
        chapters = state.get("chapters", {})

        for ch_num, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                continue
            md = ch_data.get("markdown", "")
            for m in re.findall(r'反对率\s*[：:为]?\s*(\d+(?:\.\d+)?)\s*%', md):
                rate = float(m)
                if rate > 0:
                    issues.append({
                        "chapter": ch_num, "type": "oppose_rate_nonzero", "severity": "critical",
                        "message": f"第{ch_num}章反对率{rate}%不为0%（征地项目合规要求反对率0%）",
                        "suggestion": "regenerate",
                    })
        return issues

    def _check_score_compliance(self, state: dict) -> List[Dict]:
        """评分合规：0-100 范围内，不得出现负数或超100分。"""
        issues = []
        chapters = state.get("chapters", {})

        for ch_num, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                continue
            md = ch_data.get("markdown", "")
            for m in re.findall(r'(-?\d+(?:\.\d+)?)\s*分', md):
                score = float(m)
                if score < 0 or score > 100:
                    issues.append({
                        "chapter": ch_num, "type": "score_out_of_range", "severity": "critical",
                        "message": f"第{ch_num}章评分{score}分超出0-100范围",
                        "suggestion": "regenerate",
                    })
                    break
        return issues

    async def _check_expert_skills(self, state: dict) -> List[Dict]:
        """加载专家蒸馏的审核 skill，检查章节是否违反规则。"""
        issues = []
        chapters = state.get("chapters", {})
        domain = state.get("_domain", "stability")
        try:
            from app.services import skill_service
            skills = await skill_service.get_active_skills(domain)
        except Exception:
            return issues

        for rule in skills.get("rules", []):
            pattern = rule.get("pattern", "")
            desc = rule.get("desc", "")
            severity = rule.get("severity", "warning")
            chapter = rule.get("chapter", 0)
            correction = rule.get("correction", "")
            if not pattern:
                continue
            for ch_num, ch_data in chapters.items():
                if not isinstance(ch_data, dict):
                    continue
                if chapter and ch_num != chapter:
                    continue
                md = ch_data.get("markdown", "")
                try:
                    if re.search(pattern, md):
                        issues.append({
                            "chapter": ch_num,
                            "type": "expert_skill_violation",
                            "severity": severity,
                            "message": f"第{ch_num}章违反专家规则：{desc}",
                            "correction": correction,
                            "suggestion": "auto_fix" if correction else "regenerate",
                        })
                except re.error:
                    continue
        return issues

    def _check_org_consistency(self, state: dict) -> List[Dict]:
        """Check implementing unit is consistently 江苏众拓."""
        issues = []
        chapters = state.get("chapters", {})
        correct = "江苏众拓项目代理咨询有限公司"
        wrong_cities = [
            "南京", "北京", "上海", "苏州", "无锡", "常州", "南通",
            "徐州", "扬州", "镇江", "泰州", "盐城", "连云港", "宿迁",
        ]

        for ch_num, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                continue
            md = ch_data.get("markdown", "")
            for city in wrong_cities:
                if f"{city}项目代理" in md or f"{city}咨询有限公司" in md:
                    issues.append({
                        "chapter": ch_num,
                        "type": "org_name_error",
                        "severity": "critical",
                        "message": f"第{ch_num}章实施单位错误(含{city})，应为{correct}",
                        "suggestion": "auto_fix",
                    })

        return issues

    def _check_project_name_consistency(
        self, state: dict, merged: Dict
    ) -> List[Dict]:
        """Check that the project name is consistent across all chapters."""
        issues = []
        extracted = merged.get("extracted_values", {})
        project_name = extracted.get("project_name", "")
        if not project_name:
            return issues

        chapters = state.get("chapters", {})

        # Extract core project identifier (doc number pattern)
        doc_match = re.search(r'[^\s]{2,8}告\s*〔?\d{4}〕?\s*\d+\s*号', project_name)
        if not doc_match:
            return issues

        doc_id = doc_match.group(0)

        # 🔴 文号唯一性检查：全文所有章节只能出现这一个文号，禁止编造其他文号（如10号 vs 7号）
        doc_num_match = re.search(r'(\d+)\s*号', doc_id)
        doc_num = doc_num_match.group(1) if doc_num_match else None
        if doc_num:
            wrong_pattern = rf'[^\s]{{1,6}}征告\s*〔?\d{{4}}〕?\s*(?!{re.escape(doc_num)}\s*号)\d+\s*号'
            for ch_num, ch_data in chapters.items():
                if not isinstance(ch_data, dict):
                    continue
                md = ch_data.get("markdown", "")
                for m in re.finditer(wrong_pattern, md):
                    issues.append({
                        "chapter": ch_num,
                        "type": "wrong_doc_number",
                        "severity": "critical",
                        "message": f"第{ch_num}章出现错误文号：{m.group(0)}（应为 {doc_id}），请统一修正",
                        "suggestion": "auto_fix",
                    })

        for ch_num, ch_data in chapters.items():
            if not isinstance(ch_data, dict) or ch_num == 1:
                continue
            md = ch_data.get("markdown", "")
            if md and doc_id not in md and "评估" not in project_name[:5]:
                # Only flag if the chapter references a different project name
                other_docs = re.findall(r'[^\s]{2,8}告\s*〔?\d{4}〕?\s*\d+\s*号', md)
                for other in other_docs:
                    if other != doc_id:
                        issues.append({
                            "chapter": ch_num,
                            "type": "project_name_mismatch",
                            "severity": "critical",
                            "message": f"第{ch_num}章项目文号不一致：{other} ≠ {doc_id}",
                            "suggestion": "auto_fix",
                        })

        return issues

    def _check_chapter_cross_references(self, state: dict) -> List[Dict]:
        """Check that cross-chapter references are valid."""
        issues = []
        chapters = state.get("chapters", {})

        # Chapter 9 should reference conclusions from chapters 4-8
        ch9_md = chapters.get(9, {}).get("markdown", "")
        if ch9_md:
            if "合法性" not in ch9_md:
                issues.append({
                    "chapter": 9,
                    "type": "missing_reference",
                    "severity": "warning",
                    "message": "第9章未引用合法性分析结论",
                    "suggestion": "expand",
                })

        # Chapter 8 should reference chapter 6 scores
        ch8_md = chapters.get(8, {}).get("markdown", "")
        if ch8_md and "措施前" not in ch8_md:
            issues.append({
                "chapter": 8,
                "type": "missing_reference",
                "severity": "warning",
                "message": "第8章未引用措施前评分",
                "suggestion": "expand",
            })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # Table Completeness
    # ═══════════════════════════════════════════════════════════════

    def _check_table_completeness(self, merged: Dict) -> List[Dict]:
        """Check that each chapter has its expected tables."""
        issues = []
        all_tables = merged.get("all_tables", [])
        chapters_with_tables: Dict[int, List[str]] = {}

        for t in all_tables:
            ch = t.get("chapter", 0)
            chapters_with_tables.setdefault(ch, [])

        for ch_num, expected_names in self.EXPECTED_TABLES.items():
            if not expected_names:
                continue

            ch_tables = chapters_with_tables.get(ch_num, [])
            ch_md_tables = [
                t for t in all_tables if t.get("chapter") == ch_num
            ]

            for expected in expected_names:
                found = False
                # Check table data
                for t in ch_md_tables:
                    headers = t.get("headers", [])
                    if headers and any(expected[:4] in h for h in headers):
                        found = True
                        break

                if not found:
                    # Check markdown directly
                    ch_data = {}
                    for c in all_tables:
                        if c.get("chapter") == ch_num:
                            ch_data = c
                            break

                    issues.append({
                        "chapter": ch_num,
                        "type": "missing_table",
                        "severity": "warning",
                        "message": f"第{ch_num}章缺少预期表格：「{expected}」",
                        "suggestion": "regenerate",
                    })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # Collaboration Agents Integration
    # ═══════════════════════════════════════════════════════════════

    def _integrate_collaboration_agents(self, state: dict) -> List[Dict]:
        """Integrate results from DataValidatorAgent, FormatComplianceAgent,
        and CrossReferenceAgent that ran before QualityReviewAgent.

        This consolidates their findings into the unified audit pipeline so
        QualityReviewAgent produces a single comprehensive report.
        """
        issues: List[Dict] = []

        # --- DataValidatorAgent results ---
        data_validation = state.get("_data_validation", {})
        if data_validation:
            for ch_num, validation in data_validation.items():
                if not isinstance(validation, dict):
                    continue
                score = validation.get("quality_score", 100)
                if score < 50:
                    missing_critical = [
                        f for f in validation.get("missing_fields", [])
                        if isinstance(f, dict) and f.get("level") == "critical"
                    ]
                    if missing_critical:
                        field_names = ", ".join(
                            f.get("display_name", f.get("key", "")) for f in missing_critical[:5]
                        )
                        issues.append({
                            "chapter": ch_num,
                            "type": "data_validation",
                            "severity": "critical",
                            "message": f"第{ch_num}章数据严重缺失（{score}分）: {field_names}",
                            "suggestion": "regenerate" if score < 30 else "manual_fix",
                        })
                elif score < 80:
                    issues.append({
                        "chapter": ch_num,
                        "type": "data_validation",
                        "severity": "warning",
                        "message": f"第{ch_num}章数据完整度偏低（{score}分），建议补充推荐字段",
                        "suggestion": "expand",
                    })

        # --- FormatComplianceAgent results ---
        format_compliance = state.get("_format_compliance", {})
        if format_compliance:
            for ch_num, compliance in format_compliance.items():
                if not isinstance(compliance, dict):
                    continue
                if not compliance.get("is_compliant", True):
                    severity = "critical" if compliance.get("score", 100) < 60 else "warning"
                    issues.append({
                        "chapter": ch_num,
                        "type": "format_compliance",
                        "severity": severity,
                        "message": compliance.get("summary", f"第{ch_num}章格式不合规"),
                        "suggestion": "auto_fix" if severity == "warning" else "regenerate",
                    })
                    # Add individual prohibited pattern violations
                    for violation in compliance.get("violations", [])[:3]:
                        issues.append({
                            "chapter": ch_num,
                            "type": "format_violation",
                            "severity": "error",
                            "message": violation.get("detail", violation.get("type", "格式违规")),
                            "suggestion": "auto_fix",
                        })

        # --- CrossReferenceAgent results ---
        cross_reference = state.get("_cross_reference", {})
        if cross_reference:
            score = cross_reference.get("consistency_score", 100)
            # Data consistency issues
            for issue in cross_reference.get("data_issues", []):
                issues.append({
                    "chapter": issue.get("chapter", 0),
                    "type": "cross_ref_data",
                    "severity": "critical" if issue.get("severity") == "error" else "warning",
                    "message": issue.get("detail", "跨章节数据不一致"),
                    "suggestion": "auto_fix" if issue.get("severity") != "error" else "manual_fix",
                })
            # Logic issues
            for issue in cross_reference.get("logic_issues", []):
                issues.append({
                    "chapter": issue.get("chapter", 0),
                    "type": "cross_ref_logic",
                    "severity": "critical",
                    "message": issue.get("detail", "逻辑关系异常"),
                    "suggestion": "regenerate",
                })
            # Terminology issues
            for issue in cross_reference.get("terminology_issues", []):
                issues.append({
                    "chapter": issue.get("chapter", 0),
                    "type": "cross_ref_terminology",
                    "severity": "warning",
                    "message": issue.get("detail", "术语不一致"),
                    "suggestion": "auto_fix",
                })

        if issues:
            logger.info(
                f"Collaboration agents contributed {len(issues)} issues "
                f"(DataValidator: {bool(data_validation)}, "
                f"FormatCompliance: {bool(format_compliance)}, "
                f"CrossReference: {bool(cross_reference)})"
            )

        return issues

    # ═══════════════════════════════════════════════════════════════
    # LLM Deep Review
    # ═══════════════════════════════════════════════════════════════

    async def _llm_deep_review(
        self, state: dict, merged: Dict
    ) -> List[Dict]:
        """Use LLM to perform deep logical and consistency review."""
        if not self._llm:
            return []

        await self._emit_thinking("🧠 正在进行LLM深度逻辑审核...")

        # Build a condensed version of all chapters for LLM review
        chapter_summaries = merged.get("chapter_summaries", {})
        extracted = merged.get("extracted_values", {})

        review_prompt = (
            "你是一位社会稳定风险评估报告的质量审核专家。请审核以下10章报告的数据一致性和逻辑连贯性。\n\n"
            f"## 提取的关键数据\n"
            f"项目名称：{extracted.get('project_name', '未知')}\n"
            f"面积：{extracted.get('area_m2', '未知')}㎡ / {extracted.get('area_mu', '未知')}亩\n"
            f"责任单位：{extracted.get('org_name', '未知')}\n"
            f"群众支持率：{extracted.get('support_rate', '未知')}%\n"
            f"措施前得分：{extracted.get('pre_measure_score', '未知')}\n"
            f"措施后得分：{extracted.get('post_measure_score', '未知')}\n"
            f"风险等级：{extracted.get('risk_level', '未知')}\n\n"
            f"## 各章摘要\n"
        )

        for ch_num in range(1, 11):
            summary = chapter_summaries.get(ch_num, {})
            review_prompt += (
                f"第{ch_num}章「{summary.get('title', '')}」："
                f"{summary.get('word_count', 0)}字，"
                f"{summary.get('table_count', 0)}个表格，"
                f"状态：{summary.get('status', '')}\n"
            )

        review_prompt += (
            "\n## 审核要求\n"
            "请检查以下方面并以JSON格式返回问题列表：\n"
            "1. 各章之间的数据是否一致（面积、日期、项目名称）\n"
            "2. 风险评分逻辑是否连贯（第5章→第6章→第7章→第8章）\n"
            "3. 第9章结论是否与前8章分析一致\n"
            "4. 是否有明显的内容重复或矛盾\n"
            "5. 表格数据是否与正文内容匹配\n\n"
            '返回格式：{"issues": [{"chapter": 数字, "severity": "critical|warning", '
            '"message": "问题描述", "suggestion": "regenerate|auto_fix|manual_fix"}], '
            '"overall_assessment": "简短总体评价"}'
        )

        try:
            result = await asyncio.wait_for(
                self._llm.chat_with_reasoning(
                    messages=[{"role": "user", "content": review_prompt}],
                    system="你是一个专业的社会稳定风险评估报告审核专家。只返回JSON格式的审核结果。",
                    max_tokens=1024,
                    temperature=0.1,
                ),
                timeout=90.0,
            )
            content = result.get("content", "")
            return self._parse_llm_review_response(content)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"LLM deep review failed: {type(e).__name__}: {e}")
            return []

    def _parse_llm_review_response(self, content: str) -> List[Dict]:
        """Parse LLM review response into structured issues."""
        import json
        issues = []

        # Try to extract JSON
        json_match = re.search(r'\{[\s\S]*"issues"[\s\S]*\}', content)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                raw_issues = data.get("issues", [])
                for issue in raw_issues:
                    issues.append({
                        "chapter": issue.get("chapter", 0),
                        "type": "llm_review",
                        "severity": issue.get("severity", "warning"),
                        "message": issue.get("message", ""),
                        "suggestion": issue.get("suggestion", "manual_fix"),
                    })
            except json.JSONDecodeError:
                pass

        return issues

    # ═══════════════════════════════════════════════════════════════
    # Format Checks
    # ═══════════════════════════════════════════════════════════════

    def _check_structure_issues(self, markdown: str, ch_num: int) -> List[Dict]:
        """检查章节结构问题：空小节 / 标题重复 / 责任单位过度描述。

        对应专家反复指出的问题：
        - 1.2 决策主体空内容
        - 1.3 稳评责任单位过多描述（应只写单位名称）
        - 章节标题重复
        """
        issues = []
        if not markdown:
            return issues

        # 1. 空小节检测：小节标题后紧跟下一标题，中间无内容
        lines = markdown.split('\n')
        heading_positions = []
        for idx, line in enumerate(lines):
            s = line.strip()
            if re.match(r'^#{1,3}\s+\d+\.\d+', s) or re.match(r'^\d+\.\d+\s', s):
                heading_positions.append((idx, s))

        for i, (idx, heading) in enumerate(heading_positions):
            # 下一标题位置
            next_idx = heading_positions[i + 1][0] if i + 1 < len(heading_positions) else len(lines)
            # 检查标题到下一标题之间是否有实质内容
            content_between = [l.strip() for l in lines[idx + 1:next_idx] if l.strip() and not l.strip().startswith('|')]
            real_content = [c for c in content_between if not re.match(r'^#{1,3}\s', c)]
            if not real_content:
                heading_text = re.sub(r'^#+\s*', '', heading).strip()
                issues.append({
                    "chapter": ch_num,
                    "type": "empty_section",
                    "severity": "critical",
                    "message": f"第{ch_num}章小节「{heading_text}」内容为空",
                    "suggestion": "regenerate",
                })

        # 2. 标题重复检测
        headings = [re.sub(r'^#+\s*', '', l.strip()) for l in lines if re.match(r'^#{1,3}\s+\d+\.\d+', l.strip())]
        seen = set()
        for h in headings:
            clean = h[:20]
            if clean in seen:
                issues.append({
                    "chapter": ch_num,
                    "type": "duplicate_heading",
                    "severity": "warning",
                    "message": f"第{ch_num}章标题重复：「{h}」",
                    "suggestion": "regenerate",
                })
            seen.add(clean)

        # 3. 稳评责任单位/实施单位过度描述（应只写单位名称）
        for section_name in ['稳评责任单位', '稳评实施单位']:
            # 找到该小节，检查是否有超过2句的职责描述
            m = re.search(rf'#+\s*\d+\.\d+\s+{section_name}\s*\n(.*?)(?=\n#+\s*\d+\.\d+|\Z)', markdown, re.DOTALL)
            if m:
                section_body = m.group(1).strip()
                # 如果小节正文超过2句（含"负责"、"统筹"等职责词且超过50字），说明过度描述
                sentences = re.split(r'[。\n]', section_body)
                sentences = [s for s in sentences if len(s) > 5]
                if len(sentences) > 2 and any(kw in section_body for kw in ['负责', '统筹', '组织', '协调']):
                    issues.append({
                        "chapter": ch_num,
                        "type": "over_description",
                        "severity": "error",
                        "message": f"第{ch_num}章「{section_name}」描述过多，应只写单位名称（如'XX人民政府、XX公司'），删除职责/能力描述",
                        "suggestion": "regenerate",
                    })

        # 4. 表格数据不完整（含【待补充】的表）
        table_pattern = r'(\|.+\|(?:\n\|.+\|)+)'
        for table in re.findall(table_pattern, markdown):
            if '待补充' in table:
                issues.append({
                    "chapter": ch_num,
                    "type": "table_incomplete",
                    "severity": "error",
                    "message": f"第{ch_num}章存在表格数据未填充（含【待补充】），应从资料提取真实数据",
                    "suggestion": "regenerate",
                })
                break

        return issues

    def _check_table_format(self, markdown: str, ch_num: int) -> List[Dict]:
        """Check table formatting within a chapter."""
        issues = []
        table_pattern = r'(\|.+\|(?:\n\|.+\|)+)'
        tables = re.findall(table_pattern, markdown)

        for i, table in enumerate(tables):
            lines = table.strip().split('\n')
            if len(lines) < 2:
                issues.append({
                    "chapter": ch_num,
                    "type": "table_format",
                    "severity": "warning",
                    "message": f"第{ch_num}章表{i+1}格式不完整（少于2行）",
                    "suggestion": "manual_fix",
                })
                continue

            headers = [c.strip() for c in lines[0].split('|') if c.strip()]
            if len(headers) < 2:
                issues.append({
                    "chapter": ch_num,
                    "type": "table_format",
                    "severity": "warning",
                    "message": f"第{ch_num}章表{i+1}缺少表头",
                    "suggestion": "manual_fix",
                })

        return issues

    # ═══════════════════════════════════════════════════════════════
    # Issue Classification
    # ═══════════════════════════════════════════════════════════════

    def _classify_issue(
        self, issue: Dict,
        critical: List[Dict], auto: List[Dict], warn: List[Dict],
    ) -> None:
        """Classify an issue by severity and fixability."""
        severity = issue.get("severity", "warning")
        suggestion = issue.get("suggestion", "")

        if severity == "critical":
            critical.append(issue)
        elif severity == "error" and suggestion == "auto_fix":
            auto.append(issue)
        elif severity == "warning":
            warn.append(issue)
        else:
            warn.append(issue)

    # ═══════════════════════════════════════════════════════════════
    # Auto-Fix
    # ═══════════════════════════════════════════════════════════════

    async def _auto_fix_issue(self, state: dict, issue: Dict) -> bool:
        """Auto-fix a format issue in chapter content."""
        ch_num = issue.get("chapter", 0)
        if not ch_num:
            return False

        chapters = state.get("chapters", {})
        ch_data = chapters.get(ch_num, {})
        if not isinstance(ch_data, dict):
            return False

        markdown = ch_data.get("markdown", "")
        if not markdown:
            return False

        fixed = False
        new_md = markdown
        issue_type = issue.get("type", "")

        if issue_type == "blocking_wording":
            # 🔴 自动修复 AI 套词/占位符/口语化
            desc = issue.get("message", "") or ""
            fixes = [
                (r'后续提供|稍后补充|后期提供', ''),
                (r'请提供|请补充|请填写', ''),
                (r'根据实际情况|视情况而定', ''),
                (r'\{\{[^}]+\}\}|____+|<[^>]{1,50}>', ''),
                (r'\[.*?\]\(.*?\)', ''),
                (r'好的[，,]|当然可以[，,]|下面我来|我将为您', ''),
                (r'哈哈|呵呵|嘻嘻|yyds|666|给力', ''),
                (r'我们认为|我们建议|笔者认为', ''),
                (r'以上内容仅供参考|以上是.*?的内容', ''),
            ]
            for pattern, replacement in fixes:
                new_md = re.sub(pattern, replacement, new_md)
            fixed = new_md != markdown

        elif issue_type == "forbidden_pattern":
            new_md = re.sub(
                r'好的[，,]\s*作为稳评报告编制专家[，,]\s*以下是[^。]*[。:]?\s*',
                '', new_md
            )
            new_md = re.sub(
                r'好的[，,]\s*根据您提供的要求和建议[，,]\s*现完成[^。]*[。:]?\s*',
                '', new_md
            )
            new_md = re.sub(r'[（(]注[：:][^）)]*[）)]', '', new_md)
            new_md = re.sub(r'[（(]全文共\d+字[，,][^）)]*[）)]', '', new_md)
            new_md = re.sub(r'```json\s*[\s\S]*?```', '', new_md)
            fixed = new_md != markdown

        elif issue_type == "date_error":
            replacements = {
                '12天': '7天',
                '4月13日至4月24日': '4月13日至4月19日',
                '2026年6月': '2026年4月',
                '2026年7月': '2026年4月',
            }
            pattern = issue.get("pattern", "")
            if pattern in replacements:
                new_md = re.sub(pattern, replacements[pattern], new_md)
                fixed = new_md != markdown

        elif issue_type == "org_name_error":
            correct = "江苏众拓项目代理咨询有限公司"
            for city in ["南京", "北京", "上海", "苏州", "无锡"]:
                new_md = re.sub(
                    rf'{city}[项目代理咨询有限公司|咨询有限公司]*',
                    correct, new_md
                )
            fixed = new_md != markdown

        elif issue_type == "project_name_mismatch":
            # Find and replace wrong project doc IDs
            chapters_all = state.get("chapters", {})
            ch1_md = chapters_all.get(1, {}).get("markdown", "")
            correct_doc = re.search(r'[^\s]{2,8}告\s*〔?\d{4}〕?\s*\d+\s*号', ch1_md)
            if correct_doc:
                wrong_docs = re.findall(
                    r'[^\s]{2,8}告\s*〔?\d{4}〕?\s*\d+\s*号', new_md
                )
                for wd in wrong_docs:
                    if wd != correct_doc.group(0):
                        new_md = new_md.replace(wd, correct_doc.group(0))
                        fixed = True

        elif issue_type == "style_or_data_issue":
            # 🔴 口语化/AI味 + 年份错误自动修复
            # 口语化词替换成规范公文表达
            for pattern, replacement in self.STYLE_REPLACEMENTS:
                new_md = re.sub(pattern, replacement, new_md)
            # 年份错误统一为 2026
            for pattern, replacement in self.YEAR_REPLACEMENTS:
                new_md = re.sub(pattern, replacement, new_md)
            fixed = new_md != markdown

        # 🔴 Data validity / fabrication / range issues → do NOT auto-fix.
        # These require full chapter regeneration with specific feedback.
        # The issue's suggestion="regenerate" ensures the chapter gets re-generated.

        if fixed:
            ch_data["markdown"] = new_md
            ch_data["auto_fixed"] = True
            # Update generated_sections
            generated = state.get("generated_sections", {})
            section_key = f"chapter_{ch_num}"
            if section_key in generated:
                generated[section_key]["markdown"] = new_md
                generated[section_key]["auto_fixed"] = True
            logger.info(f"Auto-fixed issue in Ch{ch_num}: {issue.get('message', '')}")
            return True

        return False

    # ═══════════════════════════════════════════════════════════════
    # SSE Emission
    # ═══════════════════════════════════════════════════════════════

    async def _emit_audit_report(
        self,
        all_issues: List[Dict],
        critical_issues: List[Dict],
        warnings: List[Dict],
        fixed_count: int,
        regenerate_chapters: Set[int],
        merged: Dict,
        state: dict,
    ) -> None:
        """Emit the comprehensive audit report via SSE."""
        if not self._stream_queue:
            return

        total = len(all_issues)
        critical = len(critical_issues)
        warn_count = len(warnings)

        # Build merged data summary
        extracted = merged.get("extracted_values", {})
        summary_parts = []

        if total == 0:
            summary_parts.append("## ✅ 质量审核通过\n")
            summary_parts.append("所有10章内容审核通过，数据一致、格式规范。\n")
        else:
            summary_parts.append(f"## 📋 质量审核报告\n")
            summary_parts.append(f"| 类别 | 数量 |\n|------|------|\n")
            summary_parts.append(f"| 总问题数 | {total} |\n")
            summary_parts.append(f"| 🔴 严重问题 | {critical} |\n")
            summary_parts.append(f"| ⚠️ 警告 | {warn_count} |\n")
            summary_parts.append(f"| ✅ 自动修复 | {fixed_count} |\n")

            if regenerate_chapters:
                ch_list = "、".join(f"第{ch}章" for ch in sorted(regenerate_chapters))
                summary_parts.append(f"| 🔄 需重新生成 | {ch_list} |\n")

            summary_parts.append(f"\n### 📊 合并数据概览\n")
            summary_parts.append(f"| 数据项 | 值 |\n|------|------|\n")
            for key, val in extracted.items():
                if val:
                    summary_parts.append(f"| {key} | {val} |\n")

            if critical_issues:
                summary_parts.append(f"\n### 🔴 严重问题（需重写/人工修复）\n")
                for issue in critical_issues[:15]:
                    summary_parts.append(
                        f"- **[第{issue.get('chapter', '?')}章]** {issue.get('message', '')}\n"
                    )

            if warnings:
                summary_parts.append(f"\n### ⚠️ 警告\n")
                for w in warnings[:8]:
                    summary_parts.append(
                        f"- [第{w.get('chapter', '?')}章] {w.get('message', '')}\n"
                    )

            if fixed_count > 0:
                summary_parts.append(f"\n### ✅ 已自动修复 {fixed_count} 个问题\n")

        summary = "".join(summary_parts)

        # Emit as message
        await self._stream_queue.put({
            "event": "message",
            "data": {
                "role": "agent",
                "content": summary,
                "message_type": "quality_audit",
            },
        })

        # Emit validation result
        await self._stream_queue.put({
            "event": "validation_result",
            "data": {
                "summary": (
                    f"审核完成：{total}个问题（{critical}严重/{warn_count}警告），"
                    f"自动修复{fixed_count}个"
                ),
                "details": summary,
                "passed": critical == 0,
                "total_issues": total,
                "critical_issues": critical,
                "warnings": warn_count,
                "auto_fixed": fixed_count,
                "regenerate_chapters": sorted(regenerate_chapters),
                "merged_data_summary": {
                    k: v for k, v in extracted.items() if v
                },
            },
        })
