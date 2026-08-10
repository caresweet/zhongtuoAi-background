"""Phase 5: Table processing — N-1 column copy + Agent last-column fill.

For each table in the document:
1. Classify as copy_full / copy_n_minus_1 / agent_generate
2. copy_n_minus_1: Copy first N-1 columns from example, agent-fill last column
3. Evidence/reasoning recorded in agent_log, NOT written to report
4. Batch LLM call: entire table in one call, not row-by-row
"""

import json
import re
from typing import List, Dict, Any, Optional

from app.services.pipeline.pipeline_context import (
    TableDef, DocumentStructure, PipelineContext,
)


# ── Agent fill prompt ──

TABLE_FILL_PROMPT = """你是社会稳定风险评估数据分析专家。请分析下表最后一列的数据。

## 表格信息
表格标题: {table_caption}
所在章节: {section_title}

## 表格前N-1列（已填充，仅供参考）：
{table_preview}

## 项目数据：
{project_data}

## 评估标准：
{standard_context}

## 要求：
1. 分析每一行的最后一列应填什么值
2. 分析依据基于项目数据和评估标准，要有理有据
3. 输出JSON格式：
{{
  "rows": [
    {{
      "row_index": 0,
      "value": "具体数值或文字",
      "reasoning": "分析依据（不会写入报告）"
    }},
    ...
  ]
}}

注意：
- 如果某行是"评分"列，数值需符合DB32/T4013-2021计分标准
- 如果某行是"比例"列，基于项目问卷数据计算
- 如果是合计/汇总行，自动求和前几行
- 保持与原报告相同的数值格式
- reasoning必须写清楚推导过程，但不会出现在报告中
"""


class TableProcessor:
    """Phase 5: Table data processing and agent-based column filling."""

    def __init__(self, llm_service=None, table_generation_service=None):
        """Initialize with LLM and table generation services.

        Args:
            llm_service: LLMService for agent-based column filling.
            table_generation_service: TableGenerationService for agent_generate tables.
        """
        self.llm = llm_service
        self.table_gen = table_generation_service
        self.agent_log: List[Dict[str, Any]] = []  # Evidence log (not in report)

    # ── Main entry point ──

    async def process_all_tables(
        self,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        doc_template,  # python-docx Document
        doc_example=None,  # python-docx Document (example report)
    ) -> Dict[int, List[List[str]]]:
        """Process all tables in the document.

        Args:
            doc_structure: From Phase 2.
            context: From Phase 1.
            doc_template: The template docx Document.
            doc_example: The example docx Document (for N-1 column copy).

        Returns:
            Dict mapping table_index → 2D array of filled cell values.
        """
        filled = {}

        for table_def in doc_structure.tables:
            print(f"  Processing table {table_def.table_index}: "
                  f"{table_def.caption[:50]} (type={table_def.table_type})")

            if table_def.table_type == "copy_full":
                # Copy entirely from example
                if doc_example and table_def.table_index < len(doc_example.tables):
                    data = self._read_table_data(doc_example.tables[table_def.table_index])
                    filled[table_def.table_index] = data
                    print(f"    ✅ Copied full table ({len(data)}r x {len(data[0]) if data else 0}c)")

            elif table_def.table_type == "copy_n_minus_1":
                # Copy first N-1 cols, agent-fill last column
                data = await self._process_n_minus_1(
                    table_def, context, doc_template, doc_example,
                )
                filled[table_def.table_index] = data
                print(f"    ✅ N-1 + agent fill ({len(data)}r)")

            elif table_def.table_type == "agent_generate":
                # Full agent generation
                data = await self._agent_generate_table(
                    table_def, context,
                )
                filled[table_def.table_index] = data
                print(f"    ✅ Agent generated ({len(data)}r)")

        return filled

    # ── N-1 column processing ──

    async def _process_n_minus_1(
        self,
        table_def: TableDef,
        context: PipelineContext,
        doc_template,
        doc_example,
    ) -> List[List[str]]:
        """Copy first N-1 columns from example, agent-fill last column."""
        fill_col = table_def.fill_column
        if fill_col < 0:
            fill_col = table_def.cols - 1  # Default: last column

        # Read template table structure
        if table_def.table_index >= len(doc_template.tables):
            return []

        tpl_table = doc_template.tables[table_def.table_index]
        ex_table = doc_example.tables[table_def.table_index] if (
            doc_example and table_def.table_index < len(doc_example.tables)
        ) else None

        # Build row data: copy N-1, leave last column empty
        row_data = []
        for r_idx in range(len(tpl_table.rows)):
            row = []
            for c_idx in range(len(tpl_table.rows[r_idx].cells)):
                if c_idx == fill_col:
                    row.append("")  # Agent fills this
                elif ex_table and r_idx < len(ex_table.rows) and c_idx < len(ex_table.rows[r_idx].cells):
                    # Copy from example
                    cell_text = ex_table.rows[r_idx].cells[c_idx].text.strip()
                    row.append(cell_text)
                else:
                    row.append("")
            row_data.append(row)

        # Agent fill the last column
        if self.llm and row_data:
            filled_values = await self._agent_fill_column(
                table_def, row_data, fill_col, context,
            )
            for r_idx, value in filled_values.items():
                if r_idx < len(row_data):
                    row_data[r_idx][fill_col] = str(value)

        return row_data

    # ── Agent column fill ──

    async def _agent_fill_column(
        self,
        table_def: TableDef,
        row_data: List[List[str]],
        fill_col: int,
        context: PipelineContext,
    ) -> Dict[int, str]:
        """Use LLM to fill the last column of a table.

        Sends the entire table in one call, gets all values back.
        Records reasoning in self.agent_log.
        """
        if not self.llm:
            return {}

        # Build preview of first N-1 columns
        preview_lines = []
        for r_idx, row in enumerate(row_data[:25]):  # Max 25 rows
            cols = [str(c) for c in row]
            # Highlight the column being filled
            if fill_col < len(cols):
                cols[fill_col] = "【待分析】"
            preview_lines.append(f"  Row {r_idx}: " + " | ".join(cols[:8]))

        table_preview = "\n".join(preview_lines)

        # Get section title
        section_title = ""
        if hasattr(table_def, 'section_index') and table_def.section_index >= 0:
            section_title = f"章节索引 {table_def.section_index}"

        # Project data summary
        project_data = self._build_table_project_data(context)

        # Standard context
        standard_context = context.standard_context[:2000] if context.standard_context else "DB32/T4013-2021"

        prompt = TABLE_FILL_PROMPT.format(
            table_caption=table_def.caption,
            section_title=section_title,
            table_preview=table_preview,
            project_data=project_data,
            standard_context=standard_context,
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.2,
            )

            # Robust JSON extraction: try multiple strategies
            data = None
            try:
                data = json.loads(response)
            except json.JSONDecodeError:
                # Try extracting JSON from response (handles markdown wrapping)
                json_match = re.search(r'\{[\s\S]*"rows"[\s\S]*\}', response)
                if json_match:
                    try:
                        data = json.loads(json_match.group(0))
                    except json.JSONDecodeError:
                        pass
                # Try fixing common JSON issues
                if not data:
                    try:
                        fixed = response.replace('\n', ' ').replace('\r', '')
                        json_match = re.search(r'\{[\s\S]*"rows"[\s\S]*\}', fixed)
                        if json_match:
                            data = json.loads(json_match.group(0))
                    except json.JSONDecodeError:
                        pass

            if not data:
                print(f"    ⚠️ Could not parse agent response as JSON")
                return {}

            rows = data.get("rows", [])

            # Log reasoning
            for row_info in rows:
                self.agent_log.append({
                    "table": table_def.caption,
                    "row_index": row_info.get("row_index", -1),
                    "value": row_info.get("value", ""),
                    "reasoning": row_info.get("reasoning", ""),
                })

            # Build result
            return {
                row_info.get("row_index", -1): str(row_info.get("value", ""))
                for row_info in rows
            }

        except Exception as e:
            print(f"    ⚠️ Agent fill failed: {e}")
            return {}

    # ── Full agent generation ──

    async def _agent_generate_table(
        self,
        table_def: TableDef,
        context: PipelineContext,
    ) -> List[List[str]]:
        """Generate a complete table from scratch using agent."""
        # Try to use existing TableGenerationService
        if self.table_gen:
            try:
                # Map table caption to known table types
                caption = table_def.caption
                if "公众问卷" in caption or "表3-1" in caption:
                    method = getattr(self.table_gen, 'generate_table_3_1', None)
                    if method:
                        return method()
                elif "单位问卷" in caption or "表3-3" in caption:
                    method = getattr(self.table_gen, 'generate_table_3_3', None)
                    if method:
                        return method()
                elif "措施前" in caption or "表6-2" in caption:
                    method = getattr(self.table_gen, 'generate_table_6_2', None)
                    if method:
                        return method()
                elif "措施后" in caption or "表8-1" in caption:
                    method = getattr(self.table_gen, 'generate_table_8_1', None)
                    if method:
                        return method()
            except Exception as e:
                print(f"    ⚠️ TableGenerationService fallback failed: {e}")

        # Fallback: return empty structure
        return [[""] * table_def.cols for _ in range(table_def.rows)]

    # ── Helpers ──

    @staticmethod
    def _read_table_data(table) -> List[List[str]]:
        """Read all cell text from a python-docx table."""
        data = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            data.append(row_data)
        return data

    @staticmethod
    def _build_table_project_data(context: PipelineContext) -> str:
        """Build project data specifically for table filling."""
        parts = []
        if context.project_name:
            parts.append(f"项目: {context.project_name}")
        if context.land_area_sqm > 0:
            parts.append(f"面积: {context.land_area_sqm:.0f}㎡ ({context.land_area_mu:.2f}亩)")
        if context.public_survey_total > 0:
            parts.append(f"公众问卷: {context.public_survey_total}份, "
                        f"支持{context.public_survey_support}人, "
                        f"支持率{context.public_survey_support_rate:.1f}%")
        if context.pre_measure_score > 0:
            parts.append(f"措施前评分: {context.pre_measure_score}")
        if context.post_measure_score > 0:
            parts.append(f"措施后评分: {context.post_measure_score}")
        return "\n".join(parts)

    def get_agent_log(self) -> List[Dict[str, Any]]:
        """Get the evidence log (for review, NOT for report inclusion)."""
        return self.agent_log
