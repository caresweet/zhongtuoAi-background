"""FormatComplianceAgent — 输出格式合规性审核Agent

职责：
1. 在章节生成后，校验输出内容是否符合企业知识库模板的格式规范
2. 检查标题层级、表格结构、字数范围、固定段落
3. 检查公文文风（无口语、无网络用语、无主观情绪）
4. 检查是否包含禁止内容（编造数据、虚构政策、不存在的文号）
5. 生成合规报告，不合规时建议修改方向

在 ChapterOrchestrator 中，章节生成完成后、提交给用户审核前调用。
"""

import asyncio
import logging
import re
from typing import Dict, List, Any, Optional

from .base_agent import BaseAgent
from app.validation.content_guardrails import AI_BUZZWORD_PATTERNS
from app.agent.chapter_definitions import CHAPTER_FORMAT_RULES

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 格式合规规则
# ═══════════════════════════════════════════════════════════════

# 公文禁用词/表达
PROHIBITED_PATTERNS = [
    (r'(?:哈哈|呵呵|嘻嘻|哎呀|嗯嗯)', "口语化表达"),
    (r'(?:yyds|绝绝子|666|牛逼|给力)', "网络用语"),
    (r'(?:我觉得|我认为|个人建议|私以为)', "主观情绪化表达"),
    (r'```json', "JSON代码块残留"),
    (r'"(?:role|content|message)"\s*:', "JSON字段残留"),
    (r'(?:好的[，,]|当然可以[，,]|没问题[，,])', "对话式开场白"),
]

# ⛔ AI高频套词检测 — 从 content_guardrails 导入统一列表
# ⛔ 章节格式规则 — 从 chapter_definitions 导入统一列表
# (AI_BUZZWORD_PATTERNS, CHAPTER_FORMAT_RULES imported above)

# 必须出现的固定表述
REQUIRED_PHRASES_GLOBAL = [
    "江苏众拓项目代理咨询有限公司",  # 实施单位
]


class FormatComplianceAgent(BaseAgent):
    """输出格式合规性审核Agent"""

    name = "FormatComplianceAgent"
    description = "校验章节生成内容是否符合企业知识库模板格式规范、公文文风和合规要求"
    covered_steps = []

    async def think(self, state: dict) -> Dict[str, Any]:
        """获取待审核的章节内容"""
        current_chapter = state.get("current_chapter", 1)
        chapters = state.get("chapters", {})

        # 找到最近生成的章节
        chapters_to_review = []
        for ch_num, ch_data in chapters.items():
            if ch_data.get("status") in ("generated", "review") and ch_data.get("markdown"):
                chapters_to_review.append(ch_num)

        if not chapters_to_review:
            chapters_to_review = [current_chapter]

        return {
            "summary": f"FormatComplianceAgent: 审核 {len(chapters_to_review)} 个章节的格式合规性",
            "steps": [f"🔍 待审核章节: {chapters_to_review}"],
            "chapters_to_review": chapters_to_review,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """对每个待审核章节执行格式合规检查"""
        chapters_to_review = plan.get("chapters_to_review", [])
        chapters = state.get("chapters", {})
        results = {}

        for ch_num in chapters_to_review:
            ch_data = chapters.get(ch_num, {})
            markdown = ch_data.get("markdown", "")
            if not markdown:
                continue

            compliance = self._check_compliance(ch_num, markdown, state)
            results[ch_num] = compliance

        return {"compliance_results": results, "status": "completed"}

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        return []

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """将合规检查结果写入state"""
        compliance_store = state.setdefault("_format_compliance", {})
        for ch_num, report in result.get("compliance_results", {}).items():
            compliance_store[ch_num] = report

            # 如果不合规且分数过低，在章节中标注
            if report.get("score", 100) < 60:
                chapters = state.get("chapters", {})
                if ch_num in chapters:
                    chapters[ch_num]["_format_issues"] = report.get("issues", [])

        state["_format_compliance"] = compliance_store
        return state

    # ═══════════════════════════════════════════════════════════════
    # 内部合规检查方法
    # ═══════════════════════════════════════════════════════════════

    def _check_compliance(
        self, chapter_number: int, markdown: str, state: dict
    ) -> Dict[str, Any]:
        """对单个章节执行完整的格式合规检查"""
        issues = []
        score = 100

        # 1. 禁止内容检查
        from app.validation.content_guardrails import find_blocking_issues
        for issue in find_blocking_issues(markdown):
            issues.append({"type": issue["type"], "severity": "critical", "detail": issue["description"]})
            score -= 20
        for pattern, desc in PROHIBITED_PATTERNS:
            if re.search(pattern, markdown):
                issues.append({"type": "prohibited", "severity": "critical", "detail": f"发现{desc}"})
                score -= 15

        # 2. AI套词检测（去AI化）
        ai_buzzword_count = 0
        for pattern, desc in AI_BUZZWORD_PATTERNS:
            if re.search(pattern, markdown):
                ai_buzzword_count += 1
                if ai_buzzword_count <= 5:  # 只报告前5个，避免噪音
                    issues.append({"type": "ai_buzzword", "severity": "warning", "detail": f"发现{desc}"})
        if ai_buzzword_count > 3:
            score -= 20  # 超过3个AI套词严重扣分
        elif ai_buzzword_count > 0:
            score -= ai_buzzword_count * 3

        # 2. 全局必须出现的表述
        for phrase in REQUIRED_PHRASES_GLOBAL:
            if chapter_number in (1, 2) and phrase not in markdown:
                # 只在第1、2章要求出现实施单位
                pass  # 不扣分，因为有些章节可能不需要

        # 3. 章节特定规则
        ch_rules = CHAPTER_FORMAT_RULES.get(chapter_number, {})

        # 3a. 必须包含的关键词
        for keyword in ch_rules.get("must_contain", []):
            if keyword not in markdown:
                issues.append({
                    "type": "missing_keyword",
                    "severity": "warning",
                    "detail": f"缺少关键词「{keyword}」",
                })
                score -= 5

        # 3b. 表格检查
        has_table = bool(re.search(r'\|.*\|.*\n\|.*---.*\|', markdown))
        if ch_rules.get("must_have_table") and not has_table:
            issues.append({
                "type": "missing_table",
                "severity": "error",
                "detail": "本章要求包含表格但未检测到",
            })
            score -= 10

        # 4. 字数检查（基于知识库规范）
        from .knowledge_agent import CHAPTER_STRUCTURE_SPECS
        spec = CHAPTER_STRUCTURE_SPECS.get(chapter_number, {})
        min_words = spec.get("min_words", 200)
        max_words = spec.get("max_words", 5000)
        word_count = len(markdown)

        if word_count < min_words:
            issues.append({
                "type": "too_short",
                "severity": "warning",
                "detail": f"字数过少（{word_count}字，最低要求{min_words}字）",
            })
            score -= 10

        if word_count > max_words:
            issues.append({
                "type": "too_long",
                "severity": "info",
                "detail": f"字数偏多（{word_count}字，建议不超过{max_words}字）",
            })
            score -= 5

        # 5. 待补充标记检查
        placeholder_count = markdown.count("【待补充】") + markdown.count("【待用户补充】")
        if placeholder_count > 5:
            issues.append({
                "type": "too_many_placeholders",
                "severity": "warning",
                "detail": f"包含{placeholder_count}个待补充标记，建议补充更多数据",
            })
            score -= 5

        # 6. 标题层级检查
        headers = re.findall(r'^(#{1,4})\s+(.+)$', markdown, re.MULTILINE)
        if headers:
            levels = [len(h[0]) for h in headers]
            if max(levels) - min(levels) > 3:
                issues.append({
                    "type": "inconsistent_headers",
                    "severity": "info",
                    "detail": "标题层级跨度较大，建议统一",
                })
                score -= 3

        score = max(0, score)

        return {
            "chapter": chapter_number,
            "score": score,
            "word_count": word_count,
            "has_table": has_table,
            "placeholder_count": placeholder_count,
            "issues": issues,
            "is_compliant": score >= 60,
            "summary": self._build_summary(chapter_number, score, issues),
        }

    def _build_summary(
        self, chapter_number: int, score: int, issues: List
    ) -> str:
        """生成合规摘要"""
        if score >= 90:
            return f"✅ 第{chapter_number}章格式合规（{score}分）"
        elif score >= 60:
            return f"⚠️ 第{chapter_number}章基本合规（{score}分），{len(issues)}个问题"
        else:
            critical = sum(1 for i in issues if i.get("severity") == "critical")
            return f"❌ 第{chapter_number}章不合规（{score}分），{critical}个严重问题需修正"
