"""Phase 4: Section-by-section content generation with RAG-enhanced LLM.

Replaces hardcoded CHAPTER_PROMPTS with dynamic prompts that include:
- RAG context (local standards + example reports)
- Project data from PipelineContext
- Section structure and sub-headings
- Multi-modal context (image descriptions from knowledge base)

Each section is generated independently, scoped to its paragraph boundaries.
"""

import re
import asyncio
from typing import List, Dict, Any, Optional

from app.services.pipeline.pipeline_context import (
    DocumentStructure, SectionDef, PipelineContext, SectionType,
)


# ── Dynamic generation prompt template ──

SECTION_GENERATION_PROMPT = """你是江苏众拓项目代理咨询有限公司的稳评工程师，在淮安做征地稳评报告。

## 地方评估标准
{standard_context}

## 参考示例报告
{example_context}

## 项目数据
{project_data}

## 当前章节
章节标题: {section_title}
子标题层级:
{sub_headings}

## 生成要求：
1. 正式公文语体但不要像AI生成的论文，要有基层工作痕迹
2. 段落长短错落：有1-2句短段，也有多句长段
3. 不要输出markdown标记（不要用 #、**、- 等）
4. 不要输出"第X章"标题（标题已存在于文档中）
5. 确保覆盖该章节下所有子标题的内容
6. 使用项目数据中的具体数值、日期、文号
7. 法规引用自然嵌入论述，不要单独罗列法条清单
8. 禁用：具有重要意义、切实保障、多措并举、综上所述、有力支撑、奠定了坚实基础

请直接输出章节正文内容："""


SINGLE_PARAGRAPH_PROMPT = """你是社会稳定风险评估报告编制专家。请生成以下段落内容。

## 上下文
章节: {section_title}
前一段落: {prev_paragraph}
后一段落: {next_paragraph}

## 项目数据
{project_data}

## 要求：
1. 正式公文语体
2. 1-3句话，50-200字
3. 与前后段落自然衔接
4. 不输出markdown标记

请直接输出段落内容："""


class ContentGenerator:
    """Phase 4: Generate body content section by section."""

    def __init__(
        self,
        llm_service=None,
        retriever=None,
        cross_modal_retriever=None,
    ):
        """Initialize with LLM and retrieval services.

        Args:
            llm_service: LLMService for chat() calls.
            retriever: RetrieverService for text retrieval.
            cross_modal_retriever: CrossModalRetriever for multi-modal retrieval.
        """
        self.llm = llm_service
        self.retriever = retriever
        self.cross_modal = cross_modal_retriever or retriever

    # ── Main entry point ──

    async def generate_all_sections(
        self,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        session_id: str = "",
        stream_callback: Optional[callable] = None,
    ) -> Dict[int, str]:
        """Generate content for all agent_generate sections.

        Args:
            doc_structure: From Phase 2.
            context: From Phase 1.
            session_id: Session for RAG lookup.
            stream_callback: Optional async callback(phase, message) for SSE.

        Returns:
            Dict mapping section_index → generated_text.
        """
        generated = {}

        agent_sections = [
            s for s in doc_structure.sections
            if s.section_type == SectionType.AGENT_GENERATE
        ]

        if not agent_sections:
            print("  No sections need generation")
            return generated

        print(f"  Generating content for {len(agent_sections)} sections...")

        for section in agent_sections:
            await self._notify(stream_callback, "generating",
                               f"生成: {section.title}")

            try:
                content = await self.generate_section(
                    section, doc_structure, context, session_id
                )
                if content:
                    generated[section.index] = content
                    await self._notify(stream_callback, "done",
                                       f"✅ {section.title} ({len(content)} chars)")
                else:
                    await self._notify(stream_callback, "warning",
                                       f"⚠️ {section.title}: 生成失败")
            except Exception as e:
                print(f"  ❌ Error generating {section.title}: {e}")
                await self._notify(stream_callback, "error",
                                   f"❌ {section.title}: {e}")

        return generated

    async def generate_section(
        self,
        section: SectionDef,
        doc_structure: DocumentStructure,
        context: PipelineContext,
        session_id: str = "",
    ) -> Optional[str]:
        """Generate content for a single section.

        Args:
            section: The section to generate.
            doc_structure: Full document structure (for context).
            context: Project data.
            session_id: For RAG lookup.

        Returns:
            Generated text string, or None on failure.
        """
        if not self.llm:
            print(f"  ⚠️ No LLM available, skipping {section.title}")
            return None

        # ── Retrieve RAG context ──
        chapter_num = self._extract_chapter_number(section.title)
        rag_context = await self._get_rag_context(
            chapter_num, section.title, context, session_id
        )

        # ── Build project data summary ──
        project_data = self._build_project_summary(context)

        # ── Build sub-headings list ──
        sub_headings = "\n".join(
            f"  - {sh}" for sh in section.sub_sections[:15]
        ) if section.sub_sections else "（无子标题）"

        # ── Build prompt ──
        prompt = SECTION_GENERATION_PROMPT.format(
            standard_context=rag_context.get("standard", context.standard_context or "参考DB32/T4013-2021"),
            example_context=rag_context.get("example", context.example_context or ""),
            project_data=project_data,
            section_title=section.title,
            sub_headings=sub_headings,
        )

        # ── Call LLM ──
        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.3,
            )
            return self._clean_response(response)
        except Exception as e:
            print(f"  LLM error for {section.title}: {e}")
            return None

    # ── Single paragraph generation (for gap filling) ──

    async def generate_paragraph(
        self,
        section: SectionDef,
        prev_text: str = "",
        next_text: str = "",
        context: PipelineContext = None,
    ) -> Optional[str]:
        """Generate a single paragraph for gap filling."""
        if not self.llm:
            return None

        project_data = self._build_project_summary(context) if context else ""

        prompt = SINGLE_PARAGRAPH_PROMPT.format(
            section_title=section.title,
            prev_paragraph=prev_text[:200] if prev_text else "（开头）",
            next_paragraph=next_text[:200] if next_text else "（结尾）",
            project_data=project_data,
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.3,
            )
            return self._clean_response(response)
        except Exception as e:
            print(f"  LLM paragraph error: {e}")
            return None

    # ── RAG context ──

    async def _get_rag_context(
        self,
        chapter_number: Optional[int],
        section_title: str,
        context: PipelineContext,
        session_id: str,
    ) -> Dict[str, str]:
        """Get RAG context for a section."""
        result = {"standard": "", "example": ""}

        # Use already retrieved context if available
        if context.standard_context:
            result["standard"] = context.standard_context[:3000]
        if context.example_context:
            result["example"] = context.example_context[:3000]

        # Try chapter-aware retrieval
        if self.retriever and chapter_number:
            try:
                rag = await self.retriever.retrieve_for_chapter(
                    chapter_number, session_id, context.project_name or "",
                )
                if rag:
                    cc = rag.get("chapter_context", "")
                    if cc:
                        result["standard"] = cc[:3000]
                    ec = rag.get("example_context", "")
                    if ec:
                        result["example"] = ec[:3000]
            except Exception:
                pass

        return result

    # ── Helpers ──

    @staticmethod
    def _build_project_summary(context: PipelineContext) -> str:
        """Build a concise project data summary for the prompt."""
        parts = []
        if context.project_name:
            parts.append(f"项目名称: {context.project_name}")
        if context.doc_reference:
            parts.append(f"文号: {context.doc_reference}")
        if context.decision_unit:
            parts.append(f"责任单位: {context.decision_unit}")
        if context.land_location:
            parts.append(f"位置: {context.land_location}")
        if context.land_area_sqm > 0:
            parts.append(f"面积: {context.land_area_sqm:.0f}㎡ ({context.land_area_mu:.2f}亩)")
        if context.land_use:
            parts.append(f"用途: {context.land_use}")
        if context.public_survey_total > 0:
            parts.append(f"问卷: {context.public_survey_total}份, 支持{context.public_survey_support}人")
        if context.announcement_period:
            parts.append(f"公示期: {context.announcement_period}")
        return "\n".join(parts)

    @staticmethod
    def _clean_response(text: str) -> str:
        """Clean LLM response: strip markdown, fix common issues."""
        # Remove markdown headers
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        # Remove bold markers
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        # Remove leading/trailing whitespace
        text = text.strip()
        # Collapse multiple blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text

    @staticmethod
    def _extract_chapter_number(title: str) -> Optional[int]:
        """Extract chapter number from heading text."""
        match = re.match(r'第([一二三四五六七八九十\d]+)章', title)
        if match:
            num_str = match.group(1)
            mapping = {
                '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
                '十一': 11,
            }
            if num_str in mapping:
                return mapping[num_str]
            if num_str.isdigit():
                return int(num_str)
        return None

    @staticmethod
    async def _notify(callback, event_type: str, message: str):
        """Send a progress notification via callback."""
        if callback:
            try:
                await callback(event_type, message)
            except Exception:
                pass
