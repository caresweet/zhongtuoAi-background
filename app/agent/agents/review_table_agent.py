"""ReviewTableAgent — generates the standalone review table (评审表) from confirmed chapters.

Runs AFTER all 10 chapters are confirmed and quality-reviewed.
Extracts key data from each chapter and synthesizes into the review table format.

The review table (评审表) is the official summary document submitted alongside
the full report. It contains:
- Section 1: Project basic info (from Chapter 1)
- Section 2: Survey/public opinion summary (from Chapter 3)
- Section 3: Pre-measure risk scoring (from Chapter 6)
- Section 4: Post-measure risk scoring (from Chapter 8)
- Section 5: Conclusions and recommendations (from Chapter 9)
- Section 6: Risk mitigation summary (from Chapter 7)
"""

import re
import logging
from typing import Dict, List, Any

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ReviewTableAgent(BaseAgent):
    """Generates the review table (评审表) from all 10 confirmed chapters."""

    name = "ReviewTableAgent"
    description = "从10章已确认内容提取数据生成评审表"
    covered_steps = [17]

    # Mapping: review table section → source chapters + extraction keys
    REVIEW_SECTIONS = [
        {
            "name": "项目基本情况",
            "source_chapters": [1],
            "extract_keys": [
                "决策名称", "责任单位", "实施单位", "项目位置",
                "征收面积", "土地用途", "资金测算",
            ],
        },
        {
            "name": "公众参与及意见调查情况",
            "source_chapters": [3],
            "extract_keys": [
                "调查方式", "调查人数", "支持人数", "反对人数",
                "支持率", "主要诉求",
            ],
        },
        {
            "name": "风险因素识别情况",
            "source_chapters": [5],
            "extract_keys": [
                "风险因素", "风险等级", "发生概率", "影响程度",
            ],
        },
        {
            "name": "措施前风险等级评估",
            "source_chapters": [6],
            "extract_keys": [
                "合法性得分", "合理性得分", "可行性得分", "可控性得分",
                "措施前总分", "措施前风险等级",
            ],
        },
        {
            "name": "风险防范化解措施",
            "source_chapters": [7],
            "extract_keys": [
                "措施名称", "责任主体", "完成时限",
            ],
        },
        {
            "name": "措施后风险等级评估",
            "source_chapters": [8],
            "extract_keys": [
                "措施后总分", "得分变化", "措施后风险等级",
            ],
        },
        {
            "name": "评估结论与建议",
            "source_chapters": [9],
            "extract_keys": [
                "综合结论", "风险等级", "工作建议",
            ],
        },
    ]

    async def think(self, state: dict) -> Dict[str, Any]:
        """Plan the review table generation."""
        chapters = state.get("chapters", {})
        confirmed_count = sum(
            1 for ch_num, ch in chapters.items()
            if isinstance(ch, dict) and ch.get("status") == "approved"
        )

        steps = [
            f"📋 从已确认 {confirmed_count}/10 章中提取评审数据...",
            "📊 提取项目基本信息（第1章）...",
            "📊 提取公众意见调查数据（第3章）...",
            "📊 提取风险评分数据（第5-8章）...",
            "📊 提取评估结论（第9章）...",
            "📝 合成评审表Markdown...",
        ]

        if confirmed_count < 10:
            steps.insert(0, f"⚠️ 仅 {confirmed_count}/10 章已确认，评审表可能不完整")

        return {
            "summary": f"从已确认章节提取数据生成评审表（{confirmed_count}/10章）",
            "steps": steps,
            "actions": [{"type": "extract_and_synthesize"}],
            "confirmed_count": confirmed_count,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Extract data from chapters and synthesize the review table."""
        await self._emit_review_table_start()

        chapters = state.get("chapters", {})
        generated = state.get("generated_sections", {})
        filled = state.get("filled_data", {})

        # Extract data from each section
        extracted = {}
        for section in self.REVIEW_SECTIONS:
            section_name = section["name"]
            section_data = {}

            for ch_num in section["source_chapters"]:
                ch_data = chapters.get(ch_num, {})
                if not isinstance(ch_data, dict):
                    continue
                markdown = ch_data.get("markdown", "")

                for key in section["extract_keys"]:
                    value = self._extract_value(markdown, key)
                    if value:
                        section_data[key] = value

            # 🔴 Fallback: extract from chapter data packages and filled_data
            for key in section["extract_keys"]:
                if key not in section_data or not section_data[key]:
                    # Try chapter data packages
                    for ch_num in section["source_chapters"]:
                        ch_pkg = (state.get("_chapter_data_packages") or {}).get(ch_num, {})
                        if isinstance(ch_pkg, dict) and key in ch_pkg:
                            section_data[key] = str(ch_pkg[key])[:200]
                            break
                if key not in section_data or not section_data[key]:
                    if key in filled:
                        section_data[key] = str(filled[key])[:200]
                # Try generated_sections markdown
                if key not in section_data or not section_data[key]:
                    for ch_num in section["source_chapters"]:
                        gen = generated.get(f"chapter_{ch_num}", {})
                        if isinstance(gen, dict):
                            val = self._extract_value(gen.get("markdown", ""), key)
                            if val:
                                section_data[key] = val
                                break

            extracted[section_name] = section_data

        # Build review table as Markdown
        review_md = self._build_review_markdown(extracted, state)

        # Also try to generate actual .docx if ReviewTableService is available
        review_table_path = None
        try:
            from app.services.review_table_service import ReviewTableService
            service = ReviewTableService()
            review_table_path = await service.generate_from_chapters(state)
        except Exception as e:
            logger.warning(f"ReviewTableService.generate_from_chapters() failed: {e}")

        # Emit the review table content
        await self._emit_review_table(review_md, review_table_path)

        # Emit completion
        await self._emit_review_table_complete(review_table_path)

        return {
            "markdown": review_md,
            "extracted_data": extracted,
            "review_table_path": review_table_path,
            "sections_count": len(extracted),
        }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """Validate the review table."""
        issues = []
        extracted = result.get("extracted_data", {})
        markdown = result.get("markdown", "")

        # Check each section has data
        for section_name, data in extracted.items():
            if not data or len(data) == 0:
                issues.append(f"评审表「{section_name}」章节未提取到数据")

        if len(markdown) < 500:
            issues.append("评审表内容过短（<500字）")

        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """Store review table data in state."""
        state["_review_table_markdown"] = result.get("markdown", "")
        state["_review_table_extracted"] = result.get("extracted_data", {})
        state["review_table_path"] = result.get("review_table_path")

        # Also store in generated_sections for assembly
        state.setdefault("generated_sections", {})["review_table"] = {
            "title": "评审表",
            "markdown": result.get("markdown", ""),
        }

        return state

    # ---- Internal Methods ----

    def _extract_value(self, markdown: str, key: str) -> str:
        """Extract a specific value from markdown text by key name.

        Uses multi-strategy extraction: key-value pairs, table cells, paragraphs.
        """
        if not markdown:
            return ""

        # Strategy 1: Direct key-value pattern (plain + bold)
        patterns = [
            rf'(?:^|\n)\s*\*?\*?{key}\*?\*?\s*[：:]\s*(.+?)(?:\n|$)',
            rf'\|\s*\*?\*?{key}\*?\*?\s*\|\s*(.+?)\s*\|',  # Table cell
            rf'{key}\s*[是为即]\s*(.+?)(?:\n|[，。；])',
        ]

        for pattern in patterns:
            match = re.search(pattern, markdown)
            if match:
                value = match.group(1).strip()
                # Clean up
                value = re.sub(r'[【［].*?[】］]', '', value)
                value = re.sub(r'\*\*', '', value)  # Remove bold markers
                value = value.strip('，。；：、 |')
                if len(value) > 1:
                    return value[:200]

        # Strategy 2: Search in table rows (| key | value | format)
        for line in markdown.split('\n'):
            if '|' not in line:
                continue
            cells = [c.strip() for c in line.split('|') if c.strip()]
            if len(cells) >= 2:
                for i, cell in enumerate(cells):
                    clean_cell = re.sub(r'\*\*', '', cell)
                    if key in clean_cell and i + 1 < len(cells):
                        val = re.sub(r'\*\*', '', cells[i + 1])
                        val = re.sub(r'[【［].*?[】］]', '', val)
                        if len(val.strip()) > 1:
                            return val.strip()[:200]

        # Strategy 3: Keyword aliases (different naming conventions)
        aliases = {
            "决策名称": ["项目名称", "报告标题", "决策事项"],
            "责任单位": ["征收主体", "决策单位", "实施主体", "组织单位"],
            "实施单位": ["稳评单位", "评估单位"],
            "项目位置": ["征收位置", "坐落", "地理位置", "拟征地位置"],
            "征收面积": ["面积", "用地面积", "拟征收土地面积"],
            "调查方式": ["调查方法", "公众参与方式"],
            "调查人数": ["样本数", "参与人数", "受访人数"],
            "支持人数": ["赞成人数", "同意人数"],
            "支持率": ["赞成率", "同意率", "满意度"],
            "风险等级": ["综合风险等级", "评估等级"],
        }
        for alias in aliases.get(key, []):
            result = self._extract_value(markdown, alias)
            if result:
                return result

        # Strategy 4: For numeric fields, search broadly
        if any(kw in key for kw in ["面积", "人数", "得分", "总分", "率"]):
            num_patterns = {
                "面积": [r'(?:面积|占地)[^\d]*(\d[\d,.]*\s*(?:㎡|平方米|亩))',
                         r'(\d{5,7})\s*(?:㎡|平方米)',
                         r'(\d+\.?\d*)\s*亩'],
                "人数": [r'(\d+)\s*(?:人|户|份)'],
                "得分": [r'(\d{1,2})\s*分'],
                "率": [r'(\d{2,3}\.?\d*)\s*%'],
            }
            for kw_part, pats in num_patterns.items():
                if kw_part in key:
                    for pat in pats:
                        m = re.search(pat, markdown)
                        if m:
                            return m.group(0)[:100]

        return ""

    def _build_review_markdown(
        self, extracted: Dict[str, Dict[str, str]], state: dict
    ) -> str:
        """Build the review table as structured Markdown."""
        title = state.get("report_title", "社会稳定风险评估报告")
        parts = [f"# {title}\n"]
        parts.append("## 社会稳定风险评估评审表\n")

        # Section 1: Basic Info
        basic = extracted.get("项目基本情况", {})
        parts.append("### 一、项目基本情况\n")
        parts.append("| 项目 | 内容 |\n|------|------|\n")
        for key in ["决策名称", "责任单位", "实施单位", "项目位置", "征收面积", "土地用途", "资金测算"]:
            value = basic.get(key, "【待补充】")
            parts.append(f"| {key} | {value} |\n")
        parts.append("\n")

        # Section 2: Public Opinion
        survey = extracted.get("公众参与及意见调查情况", {})
        parts.append("### 二、公众参与及意见调查情况\n")
        parts.append("| 项目 | 内容 |\n|------|------|\n")
        for key in ["调查方式", "调查人数", "支持人数", "反对人数", "支持率", "主要诉求"]:
            value = survey.get(key, "【待补充】")
            parts.append(f"| {key} | {value} |\n")
        parts.append("\n")

        # Section 3: Risk Identification
        risk_id = extracted.get("风险因素识别情况", {})
        parts.append("### 三、风险因素识别情况\n")
        risk_text = risk_id.get("风险因素", "")
        if risk_text:
            parts.append(f"{risk_text}\n\n")
        else:
            parts.append("【待从第5章提取】\n\n")

        # Section 4: Pre-measure Scoring
        pre_score = extracted.get("措施前风险等级评估", {})
        parts.append("### 四、措施前风险等级评估\n")
        parts.append("| 维度 | 得分 |\n|------|------|\n")
        for dim in ["合法性得分", "合理性得分", "可行性得分", "可控性得分"]:
            value = pre_score.get(dim, "【待补充】")
            parts.append(f"| {dim} | {value} |\n")
        total_pre = pre_score.get("措施前总分", "【待补充】")
        level_pre = pre_score.get("措施前风险等级", "【待补充】")
        parts.append(f"| **总分** | **{total_pre}** |\n")
        parts.append(f"| **风险等级** | **{level_pre}** |\n\n")

        # Section 5: Mitigation Measures
        measures = extracted.get("风险防范化解措施", {})
        parts.append("### 五、风险防范化解措施\n")
        measure_text = measures.get("措施名称", "")
        if measure_text:
            parts.append(f"{measure_text}\n\n")
        else:
            parts.append("【待从第7章提取】\n\n")

        # Section 6: Post-measure Scoring
        post_score = extracted.get("措施后风险等级评估", {})
        parts.append("### 六、措施后风险等级评估\n")
        parts.append("| 项目 | 内容 |\n|------|------|\n")
        parts.append(f"| 措施后总分 | {post_score.get('措施后总分', '【待补充】')} |\n")
        parts.append(f"| 得分变化 | {post_score.get('得分变化', '【待补充】')} |\n")
        parts.append(f"| 措施后风险等级 | {post_score.get('措施后风险等级', '【待补充】')} |\n\n")

        # Section 7: Conclusions
        conclusions = extracted.get("评估结论与建议", {})
        parts.append("### 七、评估结论与建议\n")
        conclusion_text = conclusions.get("综合结论", "")
        if conclusion_text:
            parts.append(f"**综合结论**：{conclusion_text}\n\n")
        else:
            parts.append("【待从第9章提取】\n\n")

        suggestions = conclusions.get("工作建议", "")
        if suggestions:
            parts.append(f"**工作建议**：{suggestions}\n\n")

        # Footer
        parts.append("---\n")
        parts.append("**编制单位**：江苏众拓项目代理咨询有限公司\n")
        parts.append("**编制日期**：2026年4月\n")

        return "\n".join(parts)

    async def _emit_review_table_start(self) -> None:
        """Emit review_table_start event."""
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "review_table_start",
                "data": {"message": "正在从已确认的10章内容中提取数据生成评审表..."},
            })

    async def _emit_review_table(
        self, markdown: str, docx_path: str = None
    ) -> None:
        """Emit the review table content."""
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "message",
                "data": {
                    "role": "agent",
                    "content": markdown,
                    "message_type": "review_table",
                },
            })

    async def _emit_review_table_complete(self, docx_path: str = None) -> None:
        """Emit review_table_complete event."""
        if self._stream_queue:
            data = {"message": "评审表生成完成"}
            if docx_path:
                data["path"] = docx_path
                # Build download URL
                import os
                filename = os.path.basename(docx_path)
                data["download_url"] = f"/api/v1/history/reports/by-path/{filename}"

            await self._stream_queue.put({
                "event": "review_table_complete",
                "data": data,
            })
