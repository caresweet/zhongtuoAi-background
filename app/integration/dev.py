"""Dev Skill Integration — Multi-Agent Generation Engine.

Wraps the zhongtuo-report-dev skill's:
- template_parser.py: Parse .docx templates to find placeholders
- report_filler.py: Fill data into template to produce final .docx
- multi_agent.py: Coordinate multiple AI agents to generate report sections
"""

import os
import json
import asyncio
from typing import Dict, Any, Optional, AsyncGenerator, List
from pathlib import Path


class DevIntegration:
    """Integrates the dev skill's generation engine into the backend workflow.

    Provides:
    1. Template parsing → identify what data is needed
    2. Field extraction → from user input using LLM
    3. Multi-agent content generation → parallel AI writers
    4. Report assembly → fill data into template .docx
    """

    def __init__(self):
        self._llm_service = None

    @property
    def llm_service(self):
        if self._llm_service is None:
            from app.services.llm_service import llm_service
            self._llm_service = llm_service
        return self._llm_service

    # ═══════════════════════════════════════════════════════════════
    # Template Analysis
    # ═══════════════════════════════════════════════════════════════

    def parse_template(self, template_path: str) -> Dict[str, Any]:
        """Parse a .docx template to extract structure and placeholders.

        Returns a dict compatible with the existing template_placeholders format
        used by the project, plus extra metadata from the skill.
        """
        try:
            from app.integration.template_parser import analyze_template
            raw = analyze_template(template_path)
        except Exception:
            raw = {"fields": {}, "paragraph_map": [], "summary": {}}

        # Convert to project-compatible placeholder format
        placeholders = []
        # Structured fields from template_parser
        fields_info = raw.get("fields", {})
        for cat_key in ["basic_info", "survey_data"]:
            items = fields_info.get(cat_key, [])
            if isinstance(items, dict):
                items = list(items.values())
            if isinstance(items, str):
                items = [items]
            for item in items:
                if isinstance(item, dict):
                    key = item.get("key", item.get("name", ""))
                    text = item.get("text", item.get("content", ""))
                    desc = item.get("description", "")
                elif isinstance(item, str):
                    key = item
                    text = desc = ""
                else:
                    continue
                placeholders.append({
                    "key": key,
                    "display_name": key,
                    "default_value": text or desc,
                    "section": cat_key,
                    "paragraph_index": -1,
                })

        # Also collect from paragraph_map for text-level placeholders
        para_map = raw.get("paragraph_map", [])
        for p in para_map:
            if isinstance(p, dict) and p.get("text"):
                text = p.get("text", "")
                # Check for template markers like "XX", "___", placeholders
                if "XX" in text or "___" in text or "需" in text:
                    placeholders.append({
                        "key": f"p{p.get('index', 0)}",
                        "display_name": f"段落{p.get('index', 0)}",
                        "default_value": text[:80],
                        "section": f"{p.get('section', '未知')}",
                        "paragraph_index": p.get("index", -1),
                    })

        return {
            "placeholders": placeholders,
            "total_placeholders": len(placeholders),
            "paragraph_count": raw.get("total_paragraphs", len(raw.get("paragraph_map", []))),
            "raw": raw,
        }

    def generate_data_template(self, template_path: str) -> Dict[str, Any]:
        """Generate an empty data template JSON from a .docx template.

        This is used to create the initial data structure that users
        need to fill in.
        """
        try:
            from app.integration.report_filler import generate_empty_data_template
            return generate_empty_data_template(template_path)
        except Exception:
            return {}

    # ═══════════════════════════════════════════════════════════════
    # Field Extraction from User Input
    # ═══════════════════════════════════════════════════════════════

    async def extract_fields_from_input(
        self, user_input: str, existing_data: Dict[str, Any] = None
    ) -> Dict[str, str]:
        """Use LLM to extract structured field data from free-text user input.

        Args:
            user_input: Free-text user message (e.g. "项目在金湖县黎城街道，征收150亩...")
            existing_data: Already collected data

        Returns:
            Dict of {field_key: extracted_value}
        """
        existing = existing_data or {}

        prompt = f"""从用户提供的项目信息中提取以下字段的值。

用户输入：{user_input}

请识别并提取：
- decision_name: 决策名称/项目名称
- responsibility_unit: 稳评责任单位
- location_community: 征收地点（社区/村）
- area_mu: 面积（亩）
- area_hectares: 面积（公顷）- 1公顷=15亩
- land_type: 土地性质
- land_use: 征地用途
- num_plots: 地块数量
- compensation_standard: 补偿标准（如有提及）

只输出已有数据的字段，格式为JSON：
{{"field_key": "提取的值", ...}}
如果用户输入中未提及某个字段，不要输出该字段。
如果是多地块，area_mu和area_hectares取合计值。"""

        try:
            response = await self.llm_service.chat(
                messages=[
                    {"role": "system", "content": "你是一个项目信息提取助手，只输出有效的JSON格式结果。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            content = response.get("content", response.get("text", ""))
            # Extract JSON from response
            json_match = content.strip("`").strip()
            if json_match.startswith("json"):
                json_match = json_match[4:].strip()
            extracted = json.loads(json_match)
            return {k: str(v) for k, v in extracted.items() if v}
        except Exception:
            return {}

    # ═══════════════════════════════════════════════════════════════
    # Multi-Agent Content Generation
    # ═══════════════════════════════════════════════════════════════

    async def generate_chapter_content(
        self,
        chapter_number: int,
        chapter_title: str,
        context: Dict[str, Any],
        stream_queue: Optional[asyncio.Queue] = None,
    ) -> str:
        """Generate content for a specific chapter using the AI agent.

        Maps chapters 1-10 to the appropriate agents from the dev skill:
        - Ch1-2: BasicInfoWriter (data filling)
        - Ch3-5: AnalysisWriter (survey + risk + analysis)
        - Ch6-10: ConclusionWriter (measures + conclusion + plan)
        """
        # Build chapter-specific prompt
        prompt = self._build_chapter_prompt(chapter_number, chapter_title, context)

        try:
            response = await self.llm_service.chat(
                messages=[
                    {"role": "system",
                     "content": f"你是一个专业的社会稳定风险评估报告编制专家。"
                               f"正在撰写第{chapter_number}章：{chapter_title}。"
                               f"请使用正式、严谨的公文语言，符合江苏省地方标准DB32/T 4013-2021。"
                               f"内容要具体、有数据支撑，避免空泛表述。"
                               f"直接输出章节正文，不要包含章节标题（标题由模板提供）。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=3000,
                timeout=120,
            )
            content = response.get("content", response.get("text", ""))

            if stream_queue:
                await stream_queue.put({
                    "event": "chapter_generated",
                    "data": {"chapter": chapter_number, "content_length": len(content)},
                })
            return content
        except Exception as e:
            if stream_queue:
                await stream_queue.put({
                    "event": "chapter_error",
                    "data": {"chapter": chapter_number, "error": str(e)},
                })
            raise

    async def generate_all_ai_chapters(
        self, context: Dict[str, Any],
        stream_queue: Optional[asyncio.Queue] = None,
    ) -> Dict[int, str]:
        """Generate content for all AI-generation chapters (C-category).

        Runs chapters 3-10 generation in parallel groups.
        Returns {chapter_number: generated_content}.
        """
        chapter_configs = {
            3: "社会稳定风险因素调查",
            4: "决策综合分析",
            5: "风险因素识别与初始等级表",
            6: "措施前风险等级研判",
            7: "风险防范与化解措施",
            8: "措施后风险等级评估",
            9: "评估结论与建议",
            10: "应急预案",
        }

        results = {}

        # Group 1: Chapters 3-5 (survey + analysis + risk) — parallel
        group1 = []
        for ch in [3, 4, 5]:
            group1.append(self.generate_chapter_content(
                ch, chapter_configs[ch], context, stream_queue
            ))

        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {"agent": "多Agent协调器", "status": "running",
                         "message": "并行生成第3-5章（调查分析+综合分析+风险识别）..."},
            })

        gen1 = await asyncio.gather(*group1, return_exceptions=True)
        for i, ch in enumerate([3, 4, 5]):
            if not isinstance(gen1[i], Exception):
                results[ch] = gen1[i]

        # Group 2: Chapters 6-8 (risk scoring) — parallel
        group2 = []
        for ch in [6, 7, 8]:
            group2.append(self.generate_chapter_content(
                ch, chapter_configs[ch], context, stream_queue
            ))

        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {"agent": "多Agent协调器", "status": "running",
                         "message": "并行生成第6-8章（风险研判+防范措施+措施后评估）..."},
            })

        gen2 = await asyncio.gather(*group2, return_exceptions=True)
        for i, ch in enumerate([6, 7, 8]):
            if not isinstance(gen2[i], Exception):
                results[ch] = gen2[i]

        # Group 3: Chapters 9-10 (conclusion + plan) — parallel
        group3 = []
        for ch in [9, 10]:
            group3.append(self.generate_chapter_content(
                ch, chapter_configs[ch], context, stream_queue
            ))

        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {"agent": "多Agent协调器", "status": "running",
                         "message": "并行生成第9-10章（结论建议+应急预案）..."},
            })

        gen3 = await asyncio.gather(*group3, return_exceptions=True)
        for i, ch in enumerate([9, 10]):
            if not isinstance(gen3[i], Exception):
                results[ch] = gen3[i]

        if stream_queue:
            await stream_queue.put({
                "event": "agent_status",
                "data": {"agent": "多Agent协调器", "status": "completed",
                         "message": f"所有AI生成章节完成（{len(results)}/8章）"},
            })

        return results

    # ═══════════════════════════════════════════════════════════════
    # Report Assembly
    # ═══════════════════════════════════════════════════════════════

    def assemble_report(
        self,
        template_path: str,
        data: Dict[str, Any],
        output_path: str,
        ai_content: Dict[int, str] = None,
    ) -> str:
        """Fill data into the template and produce the final .docx.

        Args:
            template_path: Path to the .docx template
            data: Dict of field_key -> value (A + B category)
            output_path: Where to save the output
            ai_content: Optional AI-generated chapter content

        Returns:
            Path to the generated .docx file
        """
        try:
            from app.integration.report_filler import fill_template
            result = fill_template(template_path, data, output_path)
            return result
        except Exception:
            # Fallback: use python-docx directly
            from app.services.docx_service import docx_service
            docx_service.fill_template(template_path, data, output_path)
            return output_path

    def strip_markdown_for_docx(self, text: str) -> str:
        """Strip markdown formatting from generated text before inserting into .docx."""
        import re
        # Remove headings
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove bold/italic
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        # Remove markdown tables (keep as plain text)
        text = re.sub(r'\|(.+?)\|', r'\1', text)
        text = re.sub(r'^[\-\|]+$', '', text, flags=re.MULTILINE)
        # Remove bullet markers
        text = re.sub(r'^[\-\*\+]\s+', '', text, flags=re.MULTILINE)
        # Remove numbered lists
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        return text

    # ── Internal helpers ──

    def _build_chapter_prompt(self, ch_num: int, ch_title: str,
                               context: Dict[str, Any]) -> str:
        """Build a chapter-specific generation prompt."""
        base_info = context.get("filled_data", context.get("basic_info", {}))
        title = context.get("report_title", "")

        prompts = {
            1: f"""请撰写社会稳定风险评估报告第1章「决策基本概况」。

项目信息：{json.dumps(base_info, ensure_ascii=False)}

要求：
1. 描述拟征收土地的决策名称、责任单位
2. 说明征收地块数量、面积、土地权属和性质
3. 说明征地用途，引用《土地管理法》相关条款
4. 语言正式规范，使用公文格式""",

            2: f"""请撰写社会稳定风险评估报告第2章「评估过程、方法和依据」。

项目信息：{json.dumps(base_info, ensure_ascii=False)}

要求：
1. 说明评估工作过程（成立评估组→资料收集→社会调查→风险评估→报告编制）
2. 列出采用的评估方法（问卷调查法、座谈访谈法、文献分析法等）
3. 列出评估依据（法律法规、技术规范、项目文件）
4. 引用DB32/T 4013-2021标准""",

            3: f"""请撰写社会稳定风险评估报告第3章「社会稳定风险因素调查」。

报告标题：{title}
项目信息：{json.dumps(base_info, ensure_ascii=False)}

要求：
1. 描述调查方式（公示、问卷调查、座谈会、走访等）
2. 说明调查对象和样本量
3. 汇总调查结果（利益相关者诉求、主要关切）
4. 引用相关法律条文支撑分析
5. 数据要具体，例如'收回问卷XX份，有效XX份'""",

            4: f"""请撰写社会稳定风险评估报告第4章「决策综合分析」。

报告标题：{title}
项目信息：{json.dumps(base_info, ensure_ascii=False)}

要求：
1. 从四个方面分析：合法性、合理性、可行性、可控性
2. 合法性：引用《土地管理法》《江苏省土地管理条例》等
3. 合理性：分析征收必要性，是否符合公共利益
4. 可行性：分析补偿标准是否合理，安置方案是否可行
5. 可控性：分析社会风险是否在可控范围内
6. 每个方面约300-500字，逻辑严密""",

            5: f"""请撰写社会稳定风险评估报告第5章「风险因素识别与初始等级表」。

要求：
1. 识别项目可能引发的社会稳定风险因素（至少5项）
2. 对每个风险因素描述风险表现、风险成因
3. 给出初始风险等级（用高中低三档）
4. 使用表格形式列出风险因素及初始等级
5. 基于土地征收项目常见风险进行专业判断""",

            6: f"""请撰写社会稳定风险评估报告第6章「措施前风险等级研判」。

要求：
1. 从合法性、合理性、可行性、可控性四个维度
2. 判断在未采取措施情况下的综合风险等级
3. 如果存在高风险因素，重点说明
4. 给出研判结论""",

            7: f"""请撰写社会稳定风险评估报告第7章「风险防范与化解措施」。

要求：
1. 针对识别的每个风险因素，提出具体防范措施
2. 措施要具体可行、可操作
3. 明确责任主体和实施时限
4. 风险防范措施要有针对性""",

            8: f"""请撰写社会稳定风险评估报告第8章「措施后风险等级评估」。

要求：
1. 在采纳第7章防范措施后的风险等级再评估
2. 说明措施的有效性和预期效果
3. 评估措施后各风险因素的等级变化
4. 给出综合风险等级判断""",

            9: f"""请撰写社会稳定风险评估报告第9章「评估结论与建议」。

要求：
1. 总结评估过程和主要发现
2. 给出明确的评估结论（低风险/中风险/高风险）
3. 提出建议（是否建议实施、注意事项）
4. 结论要简洁明了，建议要有操作性""",

            10: f"""请撰写社会稳定风险评估报告第10章「应急预案」。

要求：
1. 制定社会稳定风险应急预案
2. 包括：组织机构、预警机制、响应流程、处置措施
3. 明确各环节责任人
4. 预案要符合江苏省应急管理规定""",
        }

        return prompts.get(ch_num, f"请撰写「{ch_title}」的正文内容。\n项目信息：{json.dumps(base_info, ensure_ascii=False)}")


# Singleton
dev_integration = DevIntegration()
