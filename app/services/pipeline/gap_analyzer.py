"""Phase 6: Gap analysis — structural comparison and auto-fill.

After content generation, systematically compare the generated report
with the template's example report. Find missing sections, empty paragraphs,
missing table rows, and unfilled image slots. Auto-fill what can be filled.

Key innovation: Heading-aligned comparison, NOT paragraph-index-based.
"""

from typing import List, Dict, Any, Optional, Tuple

from app.services.pipeline.pipeline_context import (
    Gap, DocumentStructure, SectionDef, PipelineContext, SectionType,
)


class GapAnalyzer:
    """Phase 6: Detect and fill gaps between generated report and template."""

    MAX_ITERATIONS = 3

    def __init__(self, content_generator=None, table_processor=None):
        """Initialize with services needed for gap filling.

        Args:
            content_generator: ContentGenerator for filling text gaps.
            table_processor: TableProcessor for filling table gaps.
        """
        self.content_gen = content_generator
        self.table_proc = table_processor

    # ── Main entry point ──

    def detect_gaps(
        self,
        doc_structure: DocumentStructure,
        filled_sections: Dict[int, str],
        filled_tables: Dict[int, List[List[str]]],
        filled_images: Dict[str, Any],
        context: PipelineContext,
    ) -> List[Gap]:
        """Detect all gaps between generated content and expected content.

        Args:
            doc_structure: From Phase 2.
            filled_sections: section_index → generated_text (from Phase 4).
            filled_tables: table_index → 2D cell data (from Phase 5).
            filled_images: slot_id → ImageSlot (from Phase 3).
            context: Project data.

        Returns:
            List of Gap objects (empty = perfect).
        """
        gaps = []

        # ── 1. Check sections ──
        agent_sections = [
            s for s in doc_structure.sections
            if s.section_type == SectionType.AGENT_GENERATE
        ]

        for section in agent_sections:
            if section.index not in filled_sections:
                gaps.append(Gap(
                    gap_type="missing_section",
                    section_path=[section.title],
                    description=f"章节 '{section.title}' 内容缺失",
                    severity="critical",
                    fill_strategy="agent_generate",
                ))
            else:
                content = filled_sections[section.index]
                if not content or len(content) < 20:
                    gaps.append(Gap(
                        gap_type="missing_section",
                        section_path=[section.title],
                        description=f"章节 '{section.title}' 内容过短（<20字）",
                        severity="critical",
                        fill_strategy="agent_generate",
                    ))

        # ── 2. Check paragraphs within sections ──
        for section in agent_sections:
            if section.index not in filled_sections:
                continue

            content = filled_sections[section.index]
            content_paras = [p.strip() for p in content.split("\n\n") if p.strip()]

            # Compare paragraph count with expected
            expected_count = sum(
                1 for p in section.paragraphs
                if not p.heading_level and len(p.text_preview) < 200
            )

            if len(content_paras) < max(1, expected_count * 0.5):
                gaps.append(Gap(
                    gap_type="empty_paragraph",
                    section_path=[section.title],
                    description=(
                        f"章节 '{section.title}' 段落数不足: "
                        f"生成{len(content_paras)}段, 预计{expected_count}段"
                    ),
                    severity="warning",
                    fill_strategy="agent_generate",
                ))

        # ── 3. Check tables ──
        for table_def in doc_structure.tables:
            if table_def.table_type == "copy_full":
                continue

            if table_def.table_index not in filled_tables:
                gaps.append(Gap(
                    gap_type="missing_table_row",
                    section_path=[table_def.caption],
                    description=f"表格 '{table_def.caption}' 未填充",
                    severity="critical",
                    fill_strategy="agent_generate",
                    table_index=table_def.table_index,
                ))
                continue

            data = filled_tables[table_def.table_index]
            # Check if last column has empty cells
            fill_col = table_def.fill_column
            if fill_col >= 0:
                empty_rows = [
                    r for r, row in enumerate(data)
                    if fill_col < len(row) and not row[fill_col].strip()
                ]
                if empty_rows and table_def.table_type == "copy_n_minus_1":
                    gaps.append(Gap(
                        gap_type="missing_table_row",
                        section_path=[table_def.caption],
                        description=(
                            f"表格 '{table_def.caption}' "
                            f"有{len(empty_rows)}行最后一列为空"
                        ),
                        severity="warning",
                        fill_strategy="agent_generate",
                        table_index=table_def.table_index,
                    ))

        # ── 4. Check images ──
        for slot in doc_structure.image_slots:
            if slot.priority == 1 and not slot.matched_image:
                gaps.append(Gap(
                    gap_type="missing_image",
                    section_path=[slot.section_title, slot.caption_template[:50]],
                    description=f"必需图片 '{slot.slot_id}' 未找到匹配",
                    severity="warning",
                    fill_strategy="fill_from_project_data",
                ))

        return gaps

    # ── Auto-fill ──

    async def fill_gaps(
        self,
        gaps: List[Gap],
        doc_structure: DocumentStructure,
        context: PipelineContext,
        filled_sections: Dict[int, str],
        filled_tables: Dict[int, List[List[str]]],
    ) -> Tuple[Dict[int, str], Dict[int, List[List[str]]], List[Gap]]:
        """Attempt to auto-fill detected gaps.

        Returns:
            (updated filled_sections, updated filled_tables, unfillable gaps).
        """
        unfillable = []

        for gap in gaps:
            print(f"  Filling gap: {gap.gap_type} - {gap.description[:80]}")

            if gap.gap_type == "missing_section":
                success = await self._fill_missing_section(
                    gap, doc_structure, context, filled_sections,
                )
                if not success:
                    unfillable.append(gap)

            elif gap.gap_type == "empty_paragraph":
                success = await self._fill_empty_paragraph(
                    gap, doc_structure, context, filled_sections,
                )
                if not success:
                    unfillable.append(gap)

            elif gap.gap_type == "missing_table_row":
                success = await self._fill_table_rows(
                    gap, doc_structure, context, filled_tables,
                )
                if not success:
                    unfillable.append(gap)

            elif gap.gap_type == "missing_image":
                # Can't auto-fill images — report as unfillable
                unfillable.append(gap)

        return filled_sections, filled_tables, unfillable

    async def _fill_missing_section(
        self,
        gap: Gap,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        filled_sections: Dict[int, str],
    ) -> bool:
        """Regenerate a missing section."""
        if not self.content_gen:
            return False

        # Find the section by title
        section = None
        for s in doc_structure.sections:
            if gap.section_path and s.title == gap.section_path[0]:
                section = s
                break

        if not section:
            return False

        content = await self.content_gen.generate_section(
            section, doc_structure, context,
        )
        if content:
            filled_sections[section.index] = content
            return True
        return False

    async def _fill_empty_paragraph(
        self,
        gap: Gap,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        filled_sections: Dict[int, str],
    ) -> bool:
        """Fill single empty paragraphs."""
        if not self.content_gen:
            return False

        # Find the section
        section = None
        for s in doc_structure.sections:
            if gap.section_path and s.title == gap.section_path[0]:
                section = s
                break

        if not section or section.index not in filled_sections:
            return False

        # Generate additional paragraph
        current = filled_sections[section.index]
        new_para = await self.content_gen.generate_paragraph(
            section,
            prev_text=current[-200:] if current else "",
            context=context,
        )

        if new_para:
            filled_sections[section.index] = current + "\n\n" + new_para
            return True
        return False

    async def _fill_table_rows(
        self,
        gap: Gap,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        filled_tables: Dict[int, List[List[str]]],
    ) -> bool:
        """Re-fill empty table cells."""
        if not self.table_proc or gap.table_index is None:
            return False

        # Find the table definition
        table_def = None
        for t in doc_structure.tables:
            if t.table_index == gap.table_index:
                table_def = t
                break

        if not table_def:
            return False

        # Re-process this specific table
        # (simplified: mark as needing re-generation)
        return False  # Will be caught in next iteration

    # ── Iterative gap filling ──

    async def analyze_and_fill(
        self,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        filled_sections: Dict[int, str],
        filled_tables: Dict[int, List[List[str]]],
        filled_images: Dict[str, Any],
    ) -> Tuple[Dict[int, str], List[Gap]]:
        """Iteratively detect and fill gaps (up to MAX_ITERATIONS).

        Returns:
            (final filled_sections, remaining unfillable gaps).
        """
        remaining = []

        for iteration in range(self.MAX_ITERATIONS):
            print(f"\n  Gap analysis iteration {iteration + 1}/{self.MAX_ITERATIONS}")

            gaps = self.detect_gaps(
                doc_structure, filled_sections,
                filled_tables, filled_images, context,
            )

            if not gaps:
                print("  ✅ No gaps found!")
                break

            print(f"  Found {len(gaps)} gaps:")
            for g in gaps:
                print(f"    [{g.severity}] {g.gap_type}: {g.description[:80]}")

            # Try to fill
            filled_sections, filled_tables, unfillable = await self.fill_gaps(
                gaps, doc_structure, context,
                filled_sections, filled_tables,
            )

            remaining = unfillable
            if not unfillable:
                break

        if remaining:
            print(f"\n  ⚠️ {len(remaining)} gaps could not be auto-filled:")
            for g in remaining:
                print(f"    - {g.description}")

        return filled_sections, remaining
