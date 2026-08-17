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

from app.validation.content_guardrails import AI_BUZZWORDS

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
根据上述材料的实际内容，决定报告章节结构，并**设计清晰的论证主线**——章节之间要形成逻辑递进，像人写报告一样层层推进。

## 必须包含（根据材料按需组织）
- 项目基本概况（位置、面积、用途等）
- 风险调查分析（如有问卷/座谈数据）
- 评估依据（法律法规，可独立成章或融入各章）
- 风险识别与等级判定
- 防范化解措施
- 评估结论与建议
- 评审表（独立文件）
- 附件（图片、问卷等分类整理）

## 🔴 论证主线要求（最重要）
章节之间必须形成逻辑链条，每章承担一个论证任务，前后承接：
```
概况(是什么) → 评估过程(怎么评估) → 调查(了解什么) → 分析(判断什么)
→ 风险识别(发现什么) → 措施前研判(风险多大) → 措施(怎么化解)
→ 措施后评估(是否有效) → 结论(定论) → 应急预案(兜底)
```
- 每章要**承接前序章节的结论**（如风险识别章要引用调查章的数据）
- 每章要为**后序章节铺垫**（如调查章收集的数据要支撑后续风险分析）

## 输出JSON格式
```json
{{
  "chapters": [
    {{"num": 1, "title": "根据内容自拟标题", "key_points": ["本重要点1", "本重要点2"], "data_needed": ["需要的字段key"], "depends_on": [依赖的章节号], "argument_note": "本章在论证链中的任务：承接什么、为后序铺垫什么"}},
    ...
  ],
  "has_review_table": true,
  "has_appendix": true,
  "has_legal_basis": true,
  "argument_flow": "整份报告的论证主线一句话说明（从概况到结论的逻辑路径）",
  "note": "说明为什么这样设计（基于什么材料特征）"
}}
```

## 说明
- 章节数根据材料丰富度决定，通常5-8章
- 材料多 → 可以细分章节；材料少 → 合并相关主题
- 标题要具体，不能所有项目都一样（如用"XX地块征收概况"而非"拟征收决策基本概况"）
- data_needed写真实的字段key（project_name, org_name, location, area_mu, land_use, compensation_standard, household_count, total_samples等）
- depends_on写本章依赖的前序章节号（如第6章措施前评分依赖第3章调查、第5章风险识别）
- argument_note写本章的论证任务（承接什么结论、为后序铺垫什么）"""

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

def get_table_format_reference(chapter_num: int) -> str:
    """从模板表格注册表提取本章表格的列结构，仅作为格式参考。

    只暴露「列结构 + 表格用途」，不暴露固定内容。章节 agent 根据实际数据
    决定写哪些表格、填什么内容——有数据才写，没数据不写。

    Returns:
        格式参考文本（markdown），无表格时返回空串。
    """
    try:
        from app.services.table_registry import TABLE_REGISTRY
    except Exception:
        return ""

    lines = []
    for name, tdef in TABLE_REGISTRY.items():
        if tdef.get("chapter") != chapter_num:
            continue
        columns = tdef.get("columns", [])
        desc = tdef.get("description", name)
        # 清洗列名里的换行符
        clean_cols = [c.replace('\n', '') for c in columns]
        col_str = " | ".join(clean_cols)
        lines.append(f"  - 「{desc}」列结构：{col_str}")

    if not lines:
        return ""

    return (
        "## 📐 表格格式参考（仅格式参考，数量与内容由你根据数据决定）\n"
        "以下列结构来自历史模板，供你参考表头和列安排。是否写表格、写几张、填什么内容，"
        "完全由你根据「可用数据」决定：有数据支撑才写表格，没有数据就写文字描述 + 【待补充】。\n"
        + "\n".join(lines) + "\n"
    )


def build_chapter_prompt(
    chapter_def: dict,
    filled_data: dict,
    image_guide: str,
    previous_chapters: dict = None,
    feedback: str = None,
    rag_context: dict = None,
    materials_summary: str = "",
    outline_context: dict = None,
) -> str:
    """主Agent为单个章节编写专属提示词。"""
    num = chapter_def.get("num", 0)
    title = chapter_def.get("title", f"第{num}章")
    key_points = chapter_def.get("key_points", [])
    data_needed = chapter_def.get("data_needed", [])
    depends_on = chapter_def.get("depends_on", []) or []
    argument_note = chapter_def.get("argument_note", "") or ""

    # 🔴 字段中文标注：让 LLM 明确知道每个字段对应报告里的哪个小节
    _KEY_LABELS = {
        "org_name": "责任单位（稳评责任单位）",
        "implement_unit": "稳评实施单位",
        "project_name": "项目名称",
        "location": "项目位置",
        "area_mu": "征收面积（亩）",
        "area_m2": "征收面积（平方米）",
        "land_use": "土地用途",
        "compensation_standard": "补偿标准",
        "household_count": "涉及户数",
        "population_count": "涉及人数",
        "total_samples": "问卷总数",
        "support_rate": "支持率",
        "doc_reference": "公告文号",
    }
    data_lines = []
    pdf_table_data = ""
    # 🔴 直接从 filled_data 里找 _pdf_table_data（不管是否在 data_needed 里）
    if filled_data.get("_pdf_table_data"):
        pdf_table_data = str(filled_data["_pdf_table_data"])
    for key in data_needed:
        val = filled_data.get(key, "")
        if not val:
            continue
        label = _KEY_LABELS.get(key, key)
        data_lines.append(f"  {label}: {val}")

    # 🔴 论证主线：本章在报告中的逻辑位置
    arg_flow = ""
    if argument_note:
        arg_flow += f"\n**本章论证任务**：{argument_note}\n"
    if depends_on:
        dep_titles = []
        if outline_context:
            for ch in (outline_context.get("chapters", []) if isinstance(outline_context, dict) else []):
                if isinstance(ch, dict) and ch.get("num") in depends_on:
                    dep_titles.append(f"第{ch['num']}章{ch.get('title','')}")
        if dep_titles:
            arg_flow += f"**必须承接前序章节**：{'、'.join(dep_titles)} 的结论和数据。引用前序章节识别的风险因素、评分、调查数据时，数字必须与前序一致，不得矛盾。\n"
    if outline_context:
        overall = outline_context.get("argument_flow", "")
        if overall:
            arg_flow += f"\n**整份报告论证主线**：{overall}\n"

    prompt = f"""你是社会稳定风险评估报告的资深编写专家。
撰写报告第{num}章「{title}」。

## 🧭 本章在报告中的逻辑位置（必须遵循，保证整篇逻辑连贯）
{arg_flow if arg_flow else '（本章是报告开篇，负责总览项目基本情况）'}

## 📋 用户提供的全部资料（必须在报告中充分使用）
{materials_summary if materials_summary else '（用户未上传资料）'}

## 本章要点
{chr(10).join(f'- {p}' for p in key_points)}

## 可用数据
{chr(10).join(data_lines) if data_lines else '（从项目资料和专业知识中获取）'}

## 📊 PDF 提取的表格数据（真实数据，填表时直接用这些数据，不要编造）
{pdf_table_data if pdf_table_data else '（无提取的表格数据）'}

## 图片
{image_guide if image_guide else '（本章无图片）'}

## 前序章节关键信息（必须与前序结论保持一致）
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

    # 🔴 表格格式参考（仅格式，内容与数量由 agent 根据数据决定）
    table_ref = get_table_format_reference(num)
    if table_ref:
        prompt += "\n" + table_ref + "\n"

    prompt += f"""
## 要求
- 🔴 本章必须达到{min_words}字以上
- 根据DB32/T4013-2021规范和你的专业经验组织内容
- 每个要点至少写2-3段（150-300字/段），详细深入分析
- 🔴 小节内容必须与标题相符（一票否决）：每个小节标题写什么，正文就写什么具体内容，禁止写"XX需要负责/XX应当具备"这种与标题无关的空话。例如：
    * 标题「稳评责任单位」→ 正文必须写：责任单位具体名称（取「可用数据」里的"责任单位"）+ 其在该项目中的具体职责
    * 标题「稳评实施单位」→ 正文直接写实施单位名称：当地政府（取「可用数据」里的"责任单位"，如XX区人民政府）+ 江苏众拓项目代理咨询有限公司。只写两个单位名称即可，不要写资质、专业能力、人员等说明
    * 标题「征收位置」→ 正文必须写具体位置（取「可用数据」里的"项目位置"），不写泛泛的"位置合理"
- 🔴 表格铁律（一票否决）：表格不固定，由你根据实际数据灵活设计。**有数据支撑才写表格，没有数据就写文字描述 + 【待补充：XX数据未提供】，绝不写空表格或编造数据填表**。规则：
    * 用 markdown 表格语法（| 列1 | 列2 |）写表格，格式参考下面的「表格格式参考」
    * 每张表格必须每个单元格都有真实数据来源，缺数据的单元格写【待补充】
    * 禁止手写"表X-X XXX"这种孤立的表格标题（表格标题紧跟表格内容，不单独成段）
    * 没有问卷数据 → 不写调查统计表；没有勘测数据 → 不写勘测定界表
    * 填表数据直接取自上面「PDF 提取的表格数据」，不要抄范文里的数据{'；勘测定界数据表（表1-1）和土地分类面积表由系统从 PDF 自动渲染，正文不要重复手写这两个表' if num == 1 else ''}
- 🔴 数据铁律（一票否决）：所有数字（份数、人数、户数、百分比、评分）只能来自「可用数据」和「用户提供的全部资料」。用户没提供的数字一律不准出现，用【待补充：XX数据未提供】代替。严禁推算、估计、假设任何数值。例如：
    * 用户没给问卷份数 → 不能说"发放150份问卷" → 说"【待补充：问卷调查数据未提供】"
    * 用户没给支持率 → 不能说"支持率89.5%" → 说"【待补充：群众支持率数据未提供】"
    * 用户没给户数 → 不能说"涉及78户" → 说"【待补充：涉及户数未提供】"
    * 用户没给年龄分布 → 不能说"50岁以上42户" → 说"【待补充：年龄分布数据未提供】"
- 🔴 缺失处理：用户未提供的数据 → 仍然写出完整的章节结构（含标题），在内容处标注【待补充：具体缺什么数据及建议获取方式】。不能因为数据缺失就跳过整个小节或留空
- 🔴 图片缺失：用户未上传对应类型的图片 → 在图片位置标注【待插入：图X-X 描述】，不要用其他图片替代
- 🔴 法规引用：法规名称、文号必须来自上面的「评估依据」，知识库没提供的法规一律不写，绝不编造文号
- 禁用AI套词：{', '.join(AI_BUZZWORDS)}
- 写短句，像老工程师写报告
- 涉及村组：根据实际材料中的村组名称
{_get_learning_hints()}
{_get_chapter_antipatterns(num)}
{_get_expert_skill_hints(num)}
"""
    return prompt


# Cached learning hints (updated every 5 minutes)
_learning_hints_cache = {"hints": "", "updated": 0}


def _get_expert_skill_hints(chapter_num: int) -> str:
    """加载专家蒸馏的审核 skill（规则 + 文本），注入生成 prompt 预防问题。

    规则型 skill：告诉 LLM「禁止出现某模式」，从源头避免犯错
    文本型 skill：告诉 LLM「应该怎么写」，给出纠正示例
    """
    try:
        import sqlite3
        from app.config import settings
        db_path = settings.DATA_DIR / "knowledge_base.db"
        if not db_path.exists():
            return ""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT chapter_num, skill_type, rule_pattern, rule_desc, correction FROM review_skills "
            "WHERE is_active=1 AND (chapter_num=0 OR chapter_num=?) "
            "ORDER BY id LIMIT 20",
            (chapter_num,)
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = ["\n## ⚠️ 历史专家反馈（生成时必须避免，这是专家反复指出的问题）"]
        for r in rows:
            ch = f"第{r['chapter_num']}章 " if r["chapter_num"] else ""
            if r["skill_type"] == "rule" and r["rule_pattern"]:
                # 规则型：禁止出现某模式
                corr = f"（正确写法：{r['correction']}）" if r["correction"] else ""
                lines.append(f"- 禁止出现「{r['rule_pattern']}」：{r['rule_desc']}{corr}")
            elif r["skill_type"] == "text" and (r["rule_desc"] or r["correction"]):
                # 文本型：优化建议/纠正示例
                lines.append(f"- {ch}{r['rule_desc']}：{r['correction']}")
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


def _get_learning_hints() -> str:
    """Get cached learning hints for prompt injection. Updates every 5 min."""
    import time as _time
    now = _time.time()
    if now - _learning_hints_cache["updated"] > 300:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't run async in sync context; use latest cache
                return _learning_hints_cache["hints"]
        except RuntimeError:
            pass
    return _learning_hints_cache["hints"]


async def refresh_learning_hints(domain: str = "stability"):
    """Refresh the cached learning hints. Call periodically or on startup."""
    try:
        from app.services.learning_service import learning_service
        hints = await learning_service.build_learning_hints(domain)
        _learning_hints_cache["hints"] = hints
        _learning_hints_cache["updated"] = __import__("time").time()
    except Exception:
        pass


def _summarize_previous(chapters: dict) -> str:
    """Summarize previous chapters for context — 结构化提炼关键结论。

    不只取前200字符，而是用正则提取每章的关键数据点（面积/位置/风险等级/评分/
    风险因素等），让后序章节能承接前序结论，保证逻辑连贯。
    """
    if not chapters:
        return ""
    lines = []
    for num in sorted(chapters.keys()):
        ch = chapters[num]
        md = ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
        title = ch.get("title", "") if isinstance(ch, dict) else ""
        key_facts = _extract_key_facts(md)
        if key_facts:
            lines.append(f"第{num}章「{title}」关键结论：{key_facts}")
        else:
            # 退化为前150字
            summary = md[:150].replace("\n", " ").replace("#", "")
            lines.append(f"第{num}章「{title}」: {summary}...")
    return "\n".join(lines)


def _extract_key_facts(md: str) -> str:
    """从章节 markdown 提取关键数据点（面积/位置/风险等级/评分/风险因素）。"""
    facts = []
    patterns = [
        (r'(?:总面积|面积|规模)[为：:约]*\s*(\d+\.?\d*\s*(?:亩|㎡|平方米))', "面积"),
        (r'(?:位于|坐落|位置)[为：:约]*\s*([^\s，。]{2,20}(?:街道|镇|社区|村))', "位置"),
        (r'(?:风险等级|综合风险|判定为|属于)[为：:约]*\s*(低风险|中风险|高风险)', "风险等级"),
        (r'(?:措施前|措施后)?(?:综合)?(?:得分|评分)[为：:约]*\s*(\d+(?:\.\d+)?)\s*分', "评分"),
        (r'(?:支持率|知晓度)[为：:约]*\s*(\d+\.?\d*)\s*%', "调查率"),
        (r'(?:问卷|调查)[为：:约]*\s*(\d+)\s*份', "问卷数"),
        (r'(?:涉及|共涉及)\s*(\d+)\s*户', "户数"),
        (r'(?:识别出|主要风险)[为：:约]*\s*([^\n。]{5,40}?)(?:等)?风险', "风险因素"),
    ]
    for pattern, label in patterns:
        import re as _re
        m = _re.search(pattern, md)
        if m and not any(label in f for f in facts):
            facts.append(f"{label}:{m.group(1).strip()[:30]}")
    return "；".join(facts[:8]) if facts else ""


_CHAPTER_ANTIPATTERNS = {
    1: """## ⛔ 第1章常见错误（请避免）
❌ "征收面积约500亩" → 用户数据是489.51亩，必须精确，不得四舍五入
❌ "涉及农户若干户" → 必须写具体数字或标注【待补充】
❌ "项目位置优越" → 去掉"优越"等主观形容词，只写事实
❌ "资金测算约XX万元" → 用户未提供资金数据时必须标注【待补充：资金测算数据未提供】
""",
    3: """## ⛔ 第3章常见错误（请避免）
❌ "支持率89.5%" → 征地项目必须100%支持率。如用户数据非100%，标注【待补充】
❌ "反对率10.5%" → 征地项目反对率必须为0%，不得出现任何反对
❌ "发放问卷150份" → 用户未提供问卷数时必须标注【待补充】，不得编造
❌ 调查表数据与正文不一致 → 表格和正文的数值必须完全一致
""",
    6: """## ⛔ 第6章常见错误（请避免）
❌ "合法性得分-5分" → 评分不能为负数，所有评分在0-100之间
❌ "总分120分" → 评分不能超过100分
❌ 编造评分项 → 评分项必须来自DB32/T4013-2021标准
❌ 只说总分不说各维度分 → 必须列出合法性/合理性/可行性/可控性各维度得分
""",
    8: """## ⛔ 第8章常见错误（请避免）
❌ 措施后得分低于措施前 → 措施后评分应比措施前高5-15分
❌ 措施前后无变化 → 必须体现措施效果
❌ 编造措施后评分 → 措施后评分必须在措施前基础上合理提升
""",
    4: """## ⛔ 第4章常见错误（请避免）
❌ 编造法规文号 → 只能引用「评估依据」中列出的法规，知识库未提供的法规一律不写
❌ "依据相关法律法规" → 必须引用具体法规名称和文号
❌ 四性分析一句话带过 → 每项分析至少2-3段（150-300字/段）
""",
}


def _get_chapter_antipatterns(chapter_num: int) -> str:
    """Get chapter-specific anti-patterns (negative examples) for the prompt."""
    return _CHAPTER_ANTIPATTERNS.get(chapter_num, "")


