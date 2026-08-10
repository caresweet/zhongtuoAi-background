"""Master Orchestrator — 主Agent动态编排报告生成。

职责：
1. 接收用户材料 → 检查完整性 → 不齐全则通知前端弹窗
2. 读取公告 → 识别项目信息（公司、项目名、位置、面积）
3. 生成大纲 → 动态决定章节数量和结构
4. 为每个章节Agent编写专属提示词 → 分配材料
5. 逐章Review → 内容错误则分析原因重新生成
6. 生成评审表 + 附件

不再使用固定10章模板，一切由主Agent根据实际材料决定。
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, List, Any, Optional

_log = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Outline Generation — master agent decides structure
# ═══════════════════════════════════════════════════════════

async def generate_outline(
    llm,
    filled_data: dict,
    has_survey: bool = False,
    has_review: bool = False,
    has_announcement: bool = False,
) -> dict:
    """主Agent分析材料后生成动态大纲。

    返回: {"chapters": [{"num":1,"title":"...","key_points":[...],"data_needed":[...]}, ...],
            "total": N, "has_review_table": True, "has_appendix": True}
    """
    materials_summary = []
    if has_announcement:
        materials_summary.append("- 征收土地预公告（含项目名称、位置、面积、用途）")
    if has_survey:
        materials_summary.append("- 群众调查问卷及座谈会记录（含支持率、诉求等数据）")
    if has_review:
        materials_summary.append("- 专家评审意见及签到表")

    project_name = filled_data.get("project_name", "某项目")
    location = filled_data.get("location", "")
    area = filled_data.get("area_mu", "")

    prompt = f"""你是社会稳定风险评估报告的主编专家。请根据以下材料决定报告结构。

## 项目信息
- 项目名称：{project_name}
- 位置：{location}
- 面积：{area}亩
- 用途：{filled_data.get('land_use', '')}

## 用户已提供材料
{chr(10).join(materials_summary) if materials_summary else '- 基本信息'}

## 任务
根据上述材料的实际内容，灵活决定报告章节结构。不同的项目、不同的材料组合应该产生不同的结构。

## 必须包含（根据材料按需组织）
- 项目基本概况（位置、面积、用途等）
- 风险调查分析（如有问卷/座谈数据）
- 评估依据（法律法规，可独立成章或融入各章）
- 风险识别与等级判定
- 防范化解措施
- 评估结论与建议
- 评审表（独立文件）
- 附件（图片、问卷等分类整理）

## 输出JSON格式
```json
{{
  "chapters": [
    {{"num": 1, "title": "根据内容自拟标题", "key_points": ["本重要点1", "本重要点2"], "data_needed": ["需要的字段key"]}},
    ...
  ],
  "has_review_table": true,
  "has_appendix": true,
  "has_legal_basis": true,
  "note": "说明为什么这样设计（基于什么材料特征）"
}}
```

## 说明
- 章节数根据材料丰富度决定，通常5-8章
- 材料多 → 可以细分章节；材料少 → 合并相关主题
- 标题要具体，不能所有项目都一样（如用"XX地块征收概况"而非"拟征收决策基本概况"）
- data_needed写真实的字段key（project_name, org_name, location, area_mu, land_use, compensation_standard, household_count, total_samples等）"""

    try:
        response = await asyncio.wait_for(
            llm.chat_with_reasoning(messages=[{"role": "user", "content": prompt}], max_tokens=2000, temperature=0.3),
            timeout=60.0
        )
        content = response.get("content", "")
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            outline = json.loads(json_match.group())
            logger.info(f"Outline generated: {len(outline.get('chapters', []))} chapters")
            return outline
    except Exception as e:
        logger.warning(f"Outline generation failed: {e}")

    # Fallback
    return {
        "chapters": [
            {"num": 1, "title": "拟征收决策基本概况", "key_points": ["决策名称", "决策主体", "项目位置面积", "补偿方案要点", "利益相关者"], "data_needed": ["project_name", "org_name", "location", "area_mu", "land_use"]},
            {"num": 2, "title": "评估过程、方法和依据", "key_points": ["评估过程", "评估方法", "法律法规与评估依据"], "data_needed": []},
            {"num": 3, "title": "社会稳定风险因素调查", "key_points": ["问卷调查结果", "利益相关者诉求"], "data_needed": ["total_samples", "support_rate"]},
            {"num": 4, "title": "决策综合分析", "key_points": ["合法性", "合理性", "可行性", "可控性"], "data_needed": []},
            {"num": 5, "title": "风险因素识别与等级研判", "key_points": ["风险因素识别", "初始等级表", "措施前评分", "防范措施", "措施后评分"], "data_needed": []},
            {"num": 6, "title": "评估结论与建议", "key_points": ["评估结论", "工作建议"], "data_needed": []},
            {"num": 7, "title": "应急预案", "key_points": ["组织体系", "分级响应", "处置措施"], "data_needed": []},
        ],
        "has_review_table": True,
        "has_appendix": True,
        "has_legal_basis": True,
        "note": "基于标准征地稳评报告结构，含法律法规依据"
    }


# ═══════════════════════════════════════════════════════════
# Chapter Prompt Builder — master writes prompt for each chapter
# ═══════════════════════════════════════════════════════════

def build_chapter_prompt(
    chapter_def: dict,
    filled_data: dict,
    image_guide: str,
    previous_chapters: dict = None,
    feedback: str = None,
    rag_context: dict = None,
) -> str:
    """主Agent为单个章节编写专属提示词。

    根据章节定义中的 key_points 和 data_needed 动态构建。
    如果有前序章节内容，注入作为上下文。
    如果有feedback，说明上一版的问题，要求修正。
    如果有rag_context，注入知识库检索的规范条款和范文参考。
    """
    num = chapter_def.get("num", 0)
    title = chapter_def.get("title", f"第{num}章")
    key_points = chapter_def.get("key_points", [])
    data_needed = chapter_def.get("data_needed", [])

    # Build data context
    data_lines = []
    for key in data_needed:
        val = filled_data.get(key, "")
        if val:
            data_lines.append(f"  {key}: {val}")

    prompt = f"""你是江苏众拓项目代理咨询有限公司的资深稳评工程师。
撰写报告第{num}章「{title}」。

## 本章要点
{chr(10).join(f'- {p}' for p in key_points)}

## 可用数据
{chr(10).join(data_lines) if data_lines else '（从项目资料和专业知识中获取）'}

## 图片
{image_guide if image_guide else '（本章无图片）'}

## 前序章节摘要
{_summarize_previous(previous_chapters) if previous_chapters else '（第一章，无前序内容）'}
"""

    # 🔴 RAG: inject retrieved regulations + example reports
    if rag_context:
        reg_text = (rag_context.get("chapter_context", "") or "") + "\n" + (rag_context.get("local_regulation_context", "") or "")
        reg_text = reg_text.strip()
        example_text = (rag_context.get("example_context", "") or "").strip()
        if reg_text:
            prompt += f"""
## 📖 评估依据（必须引用以下真实规范条款，不得编造法规名称和文号）
{reg_text[:3000]}
"""
        if example_text:
            prompt += f"""
## ✍️ 写作参考（模仿以下范文的专业措辞和分析角度，但必须用本项目的真实数据，不要照抄范文里的项目信息）
{example_text[:2500]}
"""

    if feedback:
        prompt += f"\n## ⚠️ 上一版问题\n{feedback}\n请修正上述问题后重新撰写。"

    # Scoring chapters: add scoring guidelines
    scoring_guidance = ""
    if any(kw in title for kw in ['风险', '评分', '等级', '研判', '措施']):
        scoring_guidance = """
## 评分规则（严格遵守）
- 评分范围：0-100分，不能出现负数，不能超过100
- 评分依据：根据实际调查数据（支持率、问卷结果等）合理打分，不能凭空编造
- 风险等级：≥80分=低风险，60-79分=中风险，<60分=高风险
- 措施前评分：反映当前风险水平（通常60-85分）
- 措施后评分：采取措施后的预期效果（通常比措施前高5-15分）
- 🔴 必须附评分理由：每个分数后面简要说明为什么给这个分（基于什么数据/事实）
"""
    # Word count varies by chapter type
    if num in (4,):  # 四性分析章
        min_words = 2500
    elif num in (6, 7):  # 评分/措施章
        min_words = 2000
    else:
        min_words = 1200

    prompt += scoring_guidance

    prompt += f"""
## 要求
- 🔴 本章必须达到{min_words}字以上
- 根据DB32/T4013-2021规范和你的专业经验组织内容
- 每个要点至少写2-3段（150-300字/段），详细深入分析
- 🔴 自主决定结构：根据本章实际内容和可用数据，灵活决定是否需要表格、图片
- 🔴 表格决策：如果「可用数据」中有充足的数字/对比数据 → 自创markdown表格呈现；数据不足 → 不要硬凑表格，用文字描述
- 🔴 图片决策：检查「可用图片」列表，只在正文内容涉及该图片时插入（如讲位置时放位置图，讲公示时放公示照）；没有相关图片则不插入
- 🔴 图片标记格式：![图X-X 描述](path) 放在相关段落后，不要堆在章节末尾
- 所有数据必须来自「可用数据」，没有的不编造
- 🔴 法规引用：法规名称、文号必须来自上面的「评估依据」，知识库没提供的法规一律不写，绝不编造文号
- 如有数据缺失 → 标注【待补充：XX数据】，不要编造
- 禁用AI套词：具有重要意义、切实保障、多措并举、统筹推进、综上所述、有力支撑
- 写短句，像老工程师写报告
- 涉及村组：根据实际材料中的村组名称
"""
    return prompt


def _summarize_previous(chapters: dict) -> str:
    """Summarize previous chapters for context."""
    if not chapters:
        return ""
    lines = []
    for num in sorted(chapters.keys()):
        md = chapters[num].get("markdown", "") if isinstance(chapters[num], dict) else str(chapters[num])
        # Extract first 200 chars as summary
        summary = md[:200].replace("\n", " ").replace("#", "")
        lines.append(f"第{num}章: {summary}...")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# Chapter Review — check quality, provide feedback for retry
# ═══════════════════════════════════════════════════════════

def review_chapter(chapter_num: int, markdown: str, chapter_def: dict, image_guide: str) -> Optional[str]:
    """审查章节质量，返回feedback字符串（None表示通过）。

    检查项：
    - AI套词
    - 必需的数据引用
    - 图片标记（如果有可用图片）
    - 表格标记（如果章节需要）
    - 基本字数
    """
    issues = []

    buzzwords = ['具有重要意义', '切实保障', '多措并举', '统筹推进', '综上所述', '有力支撑', '奠定了坚实基础']
    found = [bw for bw in buzzwords if bw in markdown]
    if found:
        issues.append(f"包含AI套词：{found}")

    if len(markdown) < 300:
        issues.append(f"字数过少({len(markdown)}字)")

    if image_guide and "（无图片）" not in image_guide:
        if '![' not in markdown:
            issues.append("有可用图片但未在正文中引用")

    # Ch3 must have survey tables
    if chapter_num == 3:
        for tbl in ['[TABLE:ch3_public_survey]', '[TABLE:ch3_dept_survey]']:
            if tbl not in markdown:
                issues.append(f"缺少{tbl}标记")

    # 🔴 Scoring validation: no negatives, scores in reasonable range
    if chapter_num in (6, 7, 8, 10):
        scores = __import__('re').findall(r'(-?\d+(?:\.\d+)?)\s*分', markdown)
        for s in scores:
            val = float(s)
            if val < 0:
                issues.append(f"评分出现负数({val}分)，评分不能为负")
            elif val > 100:
                issues.append(f"评分超过100({val}分)，请检查分值范围")
        if '分' not in markdown and len(markdown) > 500:
            issues.append("评分章节缺少分数")

    return "; ".join(issues) if issues else None


# ═══════════════════════════════════════════════════════════
# Full Pipeline
# ═══════════════════════════════════════════════════════════


# ── run_master_pipeline removed (replaced by LangGraph workflow in report_workflow.py) ──
