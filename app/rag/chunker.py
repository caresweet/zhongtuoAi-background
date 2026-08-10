"""Chinese legal/technical document chunking strategy.

Designed for 社会稳定风险评估报告 and related regulation documents.
Respects heading hierarchy, preserves paragraph integrity, and maintains
table completeness.
"""

import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ChunkMetadata:
    """Metadata attached to each document chunk."""
    document_type: str = ""       # "regulation" | "standard" | "example_report" | "project_material"
    chapter_number: Optional[int] = None  # 1-10 for report content
    section_title: str = ""
    heading_level: int = 0        # 0=body text, 1=一级标题, 2=二级标题
    source_file: str = ""
    article_number: str = ""      # For regulations with numbered articles
    chunk_index: int = 0
    total_chunks: int = 0
    # RAG metadata tags
    region: str = ""              # 属地: 全国/江苏省/淮安市/洪泽区 etc.
    year: str = ""                # 年份/版本
    risk_tags: str = ""           # 风险标签: 补偿争议/程序风险/群体性事件 etc.
    doc_category: str = ""        # 文档大类: 政策法规/技术标准/人工范本/本地案例/工作指南/理论文献/固定资料


@dataclass
class Chunk:
    """A single document chunk with text and metadata."""
    text: str
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata)


class ChineseReportChunker:
    """Chunking strategy for Chinese legal/technical report documents.

    Rules:
    1. HEADING-AWARE: Never split within a section defined by a heading.
       Heading 1 (第X章) defines major chunk boundaries.
       Heading 2 (X.Y or 一/二/三) defines sub-chunk boundaries.

    2. PARAGRAPH-PRESERVING: Each paragraph is an atomic unit.
       Never split mid-paragraph.

    3. CHUNK SIZE: Target 800-1200 Chinese characters per chunk.

    4. OVERLAP: 100-150 character overlap between adjacent chunks
       at sentence boundaries.

    5. TABLE PRESERVATION: Tables are kept intact within a single chunk.
    """

    # Heading patterns for Chinese legal documents
    HEADING_PATTERNS = [
        # 第X章 / 第X章 XXX
        (re.compile(r'^第[一二三四五六七八九十\d]+章'), 1),
        # X.Y / X.Y.Z section numbers
        (re.compile(r'^\d+\.\d+'), 2),
        # 一、二、三、... (Chinese numbered subsections)
        (re.compile(r'^[一二三四五六七八九十]+[、．.]'), 2),
        # (一)(二)(三)...
        (re.compile(r'^[（(][一二三四五六七八九十\d]+[）)]'), 3),
        # 1. 2. 3. ...
        (re.compile(r'^\d+[、．.]'), 2),
    ]

    # Pattern to detect table rows in markdown
    TABLE_ROW_PATTERN = re.compile(r'^\|.+\|$')

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        max_chunk_size: int = 4000,  # Hard cap for embedding API (8192 tokens ≈ 4000 Chinese chars)
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunk_size = max_chunk_size

    def chunk_text(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Chunk]:
        """Chunk a plain text document into overlapping sections.

        Args:
            text: The full document text.
            metadata: Base metadata dict to attach to all chunks.

        Returns:
            List of Chunk objects with text and metadata.
        """
        base_meta = ChunkMetadata(**(metadata or {}))

        # Step 1: Split into paragraphs
        paragraphs = self._split_paragraphs(text)

        # Step 2: Detect headings and build section hierarchy
        sections = self._build_sections(paragraphs)

        # Step 3: Group sections into size-appropriate chunks
        chunks = self._group_into_chunks(sections, base_meta)

        # Step 4: Number chunks
        for i, chunk in enumerate(chunks):
            chunk.metadata.chunk_index = i
            chunk.metadata.total_chunks = len(chunks)

        return chunks

    def chunk_markdown(
        self,
        markdown: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Chunk]:
        """Chunk a markdown document, preserving heading structure and tables.

        Args:
            markdown: The full markdown document.
            metadata: Base metadata to attach to all chunks.

        Returns:
            List of Chunk objects.
        """
        base_meta = ChunkMetadata(**(metadata or {}))

        # Split markdown into blocks (headings, paragraphs, tables, code blocks)
        blocks = self._split_markdown_blocks(markdown)
        sections = self._build_sections_from_blocks(blocks)
        chunks = self._group_into_chunks(sections, base_meta)

        for i, chunk in enumerate(chunks):
            chunk.metadata.chunk_index = i
            chunk.metadata.total_chunks = len(chunks)

        return chunks

    def _split_paragraphs(self, text: str) -> List[str]:
        """Split text into paragraphs at double-newline boundaries."""
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # Split on 2+ newlines
        paragraphs = re.split(r'\n{2,}', text)
        # Filter and strip
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_markdown_blocks(self, markdown: str) -> List[Dict[str, Any]]:
        """Split markdown into typed blocks: heading, paragraph, table, code."""
        lines = markdown.split('\n')
        blocks: List[Dict[str, Any]] = []
        current_lines: List[str] = []
        in_table = False
        in_code = False

        def flush_block():
            nonlocal current_lines
            if current_lines:
                text = '\n'.join(current_lines).strip()
                if text:
                    block_type = 'paragraph'
                    if in_table:
                        block_type = 'table'
                    elif in_code:
                        block_type = 'code'
                    blocks.append({'type': block_type, 'text': text})
                current_lines = []

        for line in lines:
            # Track table state
            if line.strip().startswith('|') and line.strip().endswith('|'):
                if not in_table:
                    flush_block()
                    in_table = True
                current_lines.append(line)
                continue
            elif line.strip().startswith('|---') or line.strip().startswith('| --'):
                if in_table or (blocks and blocks[-1]['type'] == 'paragraph' and blocks[-1]['text'].strip().startswith('|')):
                    in_table = True
                    current_lines.append(line)
                    continue

            if in_table:
                flush_block()
                in_table = False

            # Track code blocks
            if line.strip().startswith('```'):
                if not in_code:
                    flush_block()
                    in_code = True
                else:
                    current_lines.append(line)
                    flush_block()
                    in_code = False
                    continue
            if in_code:
                current_lines.append(line)
                continue

            # Heading detection
            if line.strip().startswith('#'):
                flush_block()
                blocks.append({'type': 'heading', 'text': line.strip()})
                continue

            # Empty line = paragraph separator
            if not line.strip():
                flush_block()
                continue

            current_lines.append(line)

        flush_block()
        return blocks

    def _build_sections(self, paragraphs: List[str]) -> List[Dict[str, Any]]:
        """Group paragraphs into sections based on heading detection."""
        sections: List[Dict[str, Any]] = []
        current_section: Optional[Dict[str, Any]] = None

        for para in paragraphs:
            heading_level = self._detect_heading_level(para)

            if heading_level is not None:
                # Start a new section
                if current_section and current_section['paragraphs']:
                    sections.append(current_section)
                current_section = {
                    'title': para,
                    'level': heading_level,
                    'paragraphs': [],
                    'chapter_number': self._extract_chapter_number(para),
                }
            elif current_section is not None:
                current_section['paragraphs'].append(para)
            else:
                # Text before any heading
                current_section = {
                    'title': '',
                    'level': 0,
                    'paragraphs': [para],
                    'chapter_number': None,
                }

        if current_section and (current_section['paragraphs'] or current_section['title']):
            sections.append(current_section)

        return sections

    def _build_sections_from_blocks(
        self, blocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Group markdown blocks into sections."""
        sections: List[Dict[str, Any]] = []
        current_section: Optional[Dict[str, Any]] = None

        for block in blocks:
            if block['type'] == 'heading':
                if current_section:
                    sections.append(current_section)

                level = self._detect_md_heading_level(block['text'])
                current_section = {
                    'title': self._strip_md_heading(block['text']),
                    'level': level,
                    'paragraphs': [],
                    'chapter_number': self._extract_chapter_number(block['text']),
                }
            elif current_section is not None:
                current_section['paragraphs'].append(block['text'])
            else:
                current_section = {
                    'title': '',
                    'level': 0,
                    'paragraphs': [block['text']],
                    'chapter_number': None,
                }

        if current_section:
            sections.append(current_section)

        return sections

    def _detect_heading_level(self, text: str) -> Optional[int]:
        """Detect if a paragraph is a heading, return its level (1-3) or None."""
        for pattern, level in self.HEADING_PATTERNS:
            if pattern.match(text.strip()):
                return level
        return None

    def _detect_md_heading_level(self, text: str) -> int:
        """Detect markdown heading level from # count."""
        match = re.match(r'^(#+)\s', text.strip())
        if match:
            return len(match.group(1))
        return 0

    def _strip_md_heading(self, text: str) -> str:
        """Remove markdown heading markers."""
        return re.sub(r'^#+\s*', '', text.strip())

    def _extract_chapter_number(self, text: str) -> Optional[int]:
        """Extract chapter number from heading text."""
        # Pattern: 第X章 or 第X章 ...
        match = re.match(r'第([一二三四五六七八九十\d]+)章', text)
        if match:
            num_str = match.group(1)
            return self._chinese_num_to_int(num_str)
        # Pattern: just a digit at start
        match = re.match(r'^(\d+)', text.strip())
        if match:
            return int(match.group(1))
        return None

    def _chinese_num_to_int(self, chinese_num: str) -> int:
        """Convert Chinese numeral to integer."""
        if chinese_num.isdigit():
            return int(chinese_num)
        mapping = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        }
        if chinese_num in mapping:
            return mapping[chinese_num]
        # Handle 十一 to 十九
        if chinese_num.startswith('十') and len(chinese_num) > 1:
            return 10 + mapping.get(chinese_num[1], 0)
        return 0

    def _group_into_chunks(
        self, sections: List[Dict[str, Any]], base_meta: ChunkMetadata
    ) -> List[Chunk]:
        """Group sections into chunks respecting size limits."""
        chunks: List[Chunk] = []

        for section in sections:
            # Determine section metadata
            section_meta = ChunkMetadata(
                document_type=base_meta.document_type,
                chapter_number=section.get('chapter_number') or base_meta.chapter_number,
                section_title=section.get('title', ''),
                heading_level=section.get('level', 0),
                source_file=base_meta.source_file,
                region=base_meta.region,
                year=base_meta.year,
                risk_tags=base_meta.risk_tags,
                doc_category=base_meta.doc_category,
            )

            section_text = section['title'] + '\n\n' if section['title'] else ''
            section_text += '\n\n'.join(section['paragraphs'])

            # If section fits in one chunk, add it
            if len(section_text) <= self.chunk_size:
                chunks.append(Chunk(text=section_text, metadata=section_meta))
                continue

            # Split long sections paragraph by paragraph
            current_text = section['title'] + '\n\n' if section['title'] else ''
            current_meta = section_meta

            for para in section['paragraphs']:
                if len(current_text) + len(para) + 2 <= self.chunk_size:
                    if current_text:
                        current_text += '\n\n' + para
                    else:
                        current_text = para
                else:
                    if current_text.strip():
                        chunks.append(Chunk(text=current_text, metadata=current_meta))
                    # Start new chunk with overlap
                    current_text = para
                    if self.chunk_overlap > 0 and chunks:
                        # Add overlap from previous chunk's last sentence
                        prev_text = chunks[-1].text
                        overlap = self._get_sentence_overlap(prev_text, self.chunk_overlap)
                        if overlap:
                            current_text = overlap + '\n\n' + para

            if current_text.strip():
                chunks.append(Chunk(text=current_text, metadata=current_meta))

        # Force-split any remaining oversized chunks (for PDFs without heading structure)
        chunks = self._force_split_oversized(chunks)

        return chunks

    def _get_sentence_overlap(self, text: str, max_chars: int) -> str:
        """Get trailing sentences from text for overlap, up to max_chars."""
        # Chinese sentence boundaries
        sentences = re.split(r'(?<=[。！？；])', text)
        overlap = ''
        for sent in reversed(sentences):
            if len(overlap) + len(sent) <= max_chars:
                overlap = sent + overlap
            else:
                break
        return overlap

    @staticmethod
    def _copy_meta(meta: ChunkMetadata) -> ChunkMetadata:
        """Create an independent copy of chunk metadata (avoids shared-object bug)."""
        return ChunkMetadata(
            document_type=meta.document_type,
            chapter_number=meta.chapter_number,
            section_title=meta.section_title,
            heading_level=meta.heading_level,
            source_file=meta.source_file,
            region=meta.region,
            year=meta.year,
            risk_tags=meta.risk_tags,
            doc_category=meta.doc_category,
        )

    def _force_split_oversized(self, chunks: List[Chunk]) -> List[Chunk]:
        """Force-split any chunk larger than max_chunk_size at sentence boundaries.

        This handles PDFs without heading structure where the normal chunker
        produces single massive chunks. Also ensures chunks fit embedding API limits.
        """
        result = []
        for chunk in chunks:
            if len(chunk.text) <= self.max_chunk_size:
                result.append(chunk)
                continue

            # Force-split at sentence boundaries
            text = chunk.text
            sentences = re.split(r'(?<=[。！？；\n])', text)
            current = ""
            sub_idx = 0
            for sent in sentences:
                if len(current) + len(sent) <= self.chunk_size:
                    current += sent
                else:
                    if current.strip():
                        result.append(Chunk(text=current.strip(), metadata=self._copy_meta(chunk.metadata)))
                        sub_idx += 1
                    # If single sentence exceeds max_chunk_size, hard-split by char count
                    if len(sent) > self.max_chunk_size:
                        for i in range(0, len(sent), self.chunk_size - self.chunk_overlap):
                            piece = sent[i:i + self.chunk_size]
                            if piece.strip():
                                result.append(Chunk(text=piece.strip(), metadata=self._copy_meta(chunk.metadata)))
                                sub_idx += 1
                        current = ""
                    else:
                        # Overlap: keep last sentence as context
                        if self.chunk_overlap > 0 and result:
                            prev = result[-1].text
                            overlap = self._get_sentence_overlap(prev, self.chunk_overlap)
                            current = (overlap + sent) if overlap else sent
                        else:
                            current = sent
            if current.strip():
                result.append(Chunk(text=current.strip(), metadata=self._copy_meta(chunk.metadata)))

        return result

    @staticmethod
    def format_rag_tag(meta: ChunkMetadata) -> str:
        """Build a RAG metadata tag string from chunk metadata.

        Format: 【文档大类/属地/年份/章节/风险标签】
        Falls back gracefully for missing fields.
        """
        category = meta.doc_category or "其他"
        region = meta.region or "通用"
        year = meta.year or "-"
        section = meta.section_title or ""
        if not section and meta.chapter_number:
            section = f"第{meta.chapter_number}章"
        risk = meta.risk_tags or "通用风险"

        # Build compact tag
        if section:
            return f"【{category}/{region}/{year}/{section}/{risk}】"
        return f"【{category}/{region}/{year}/{risk}】"

    def inject_rag_tags(self, chunks: List[Chunk]) -> List[Chunk]:
        """Inject RAG metadata tag as the first line of each chunk text.

        This ensures every chunk is self-describing for retrieval.
        """
        for chunk in chunks:
            tag = self.format_rag_tag(chunk.metadata)
            chunk.text = f"{tag}\n{chunk.text}"
        return chunks
