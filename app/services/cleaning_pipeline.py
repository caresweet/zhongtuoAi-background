"""Data Cleaning Pipeline — Chain of Responsibility pattern.

Provides a configurable text cleaning pipeline for documents before RAG ingestion.
Users can toggle individual cleaning rules via UI checkboxes; each rule is a
separate Handler in the chain.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Dict, List


# ═══════════════════════════════════════════════════════════════════════════════
# Base Handler
# ═══════════════════════════════════════════════════════════════════════════════

class CleaningHandler(ABC):
    """Base class for a cleaning step in the Chain of Responsibility."""

    name: str = ""          # identifier matching UI config keys
    label: str = ""          # human-readable name for UI display
    default_enabled: bool = True

    @abstractmethod
    def process(self, text: str, config: dict) -> str:
        """Apply this cleaning step to `text`. Returns cleaned text."""
        ...

    def find_issues(self, text: str) -> List[dict]:
        """Pre-scan text to find issues this handler would fix.

        Returns list of {type, line, position, description, severity}.
        """
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Concrete Handlers
# ═══════════════════════════════════════════════════════════════════════════════

class TOCRemover(CleaningHandler):
    """Remove table of contents (目录) sections from documents."""

    name = "remove_toc"
    label = "移除目录"
    default_enabled = True

    def process(self, text: str, config: dict) -> str:
        lines = text.split('\n')
        result = []
        in_toc = False
        toc_start_patterns = ['目  录', '目录', '目 录', 'TABLE OF CONTENTS', 'Contents']

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Detect TOC start
            if not in_toc and stripped in toc_start_patterns:
                in_toc = True
                continue

            if in_toc:
                # Is this line a TOC entry? Has dot leaders or ends with page number
                has_dot_leaders = bool(re.search(r'[.…·]{2,}', stripped))
                ends_with_page = bool(re.search(r'\d{1,4}\s*$', stripped))
                is_toc_line = has_dot_leaders or (len(stripped) < 80 and ends_with_page and re.search(r'[.…·\s]{3,}', stripped))

                if is_toc_line:
                    continue  # Skip this TOC line

                # Detect TOC end: a real heading/content (not a TOC entry)
                # Real headings start at column 0, have no dot leaders, no trailing page numbers
                if stripped and len(stripped) > 5:
                    # This is content, end TOC
                    in_toc = False
                    result.append(line)
                # Otherwise (blank line in TOC), skip it
                continue
            else:
                result.append(line)

        return '\n'.join(result)

    def find_issues(self, text: str) -> List[dict]:
        issues = []
        for i, line in enumerate(text.split('\n'), 1):
            if line.strip() in ('目  录', '目录', '目 录'):
                issues.append({
                    "type": "toc",
                    "line": i,
                    "description": f"目录起始: {line.strip()}",
                    "severity": "info",
                })
        return issues


class HeaderFooterRemover(CleaningHandler):
    """Remove page headers, footers, and running titles."""

    name = "remove_headers_footers"
    label = "移除页眉页脚"
    default_enabled = True

    # Common Chinese government document header/footer patterns
    _PATTERNS = [
        # Page numbers: "第 X 页 共 Y 页", "- X -", "— X —"
        re.compile(r'第\s*\d+\s*页[，,\s]*共\s*\d+\s*页', re.IGNORECASE),
        re.compile(r'^[-—–]\s*\d+\s*[-—–]$', re.MULTILINE),
        # Running headers: document number references repeated across pages
        re.compile(r'^[\s]*\{\{.*\}\}[\s]*$', re.MULTILINE),
        # "机密" / "秘密" / "内部资料" markers
        re.compile(r'^[\s]*(机密|秘密|绝密|内部资料|内部文件|注意保存)[\s]*$', re.MULTILINE),
        # PDF page markers from rendering
        re.compile(r'^[\s]*Page\s+\d+[\s]*$', re.MULTILINE | re.IGNORECASE),
    ]

    def process(self, text: str, config: dict) -> str:
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if any(p.search(stripped) for p in self._PATTERNS):
                continue
            cleaned.append(line)
        return '\n'.join(cleaned)

    def find_issues(self, text: str) -> List[dict]:
        issues = []
        for i, line in enumerate(text.split('\n'), 1):
            if any(p.search(line.strip()) for p in self._PATTERNS):
                issues.append({
                    "type": "header_footer",
                    "line": i,
                    "description": f"疑似页眉/页脚: {line.strip()[:40]}",
                    "severity": "info",
                })
        return issues


class PageNumberRemover(CleaningHandler):
    """Remove standalone page numbers (often appear between pages in extracted text)."""

    name = "remove_page_numbers"
    label = "移除页码"
    default_enabled = True

    _PATTERNS = [
        re.compile(r'^\s*\d{1,4}\s*$'),                       # standalone number
        re.compile(r'^\s*[-—–]\s*\d{1,4}\s*[-—–]\s*$'),       # "- 5 -"
        re.compile(r'^\s*\d{1,4}\s*/\s*\d{1,4}\s*$'),         # "5 / 20"
    ]

    def process(self, text: str, config: dict) -> str:
        lines = text.split('\n')
        cleaned = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip if line is ONLY a page number
            if any(p.search(stripped) for p in self._PATTERNS):
                # Check context: skip only if surrounded by blank lines (isolated)
                prev_blank = i == 0 or not lines[i-1].strip()
                next_blank = i >= len(lines)-1 or not lines[i+1].strip()
                if prev_blank and next_blank:
                    continue
            cleaned.append(line)
        return '\n'.join(cleaned)

    def find_issues(self, text: str) -> List[dict]:
        issues = []
        lines = text.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(p.search(stripped) for p in self._PATTERNS):
                prev_blank = i == 0 or not lines[i-1].strip()
                next_blank = i >= len(lines)-1 or not lines[i+1].strip()
                if prev_blank and next_blank:
                    issues.append({
                        "type": "page_number",
                        "line": i + 1,
                        "description": f"孤立页码: {stripped}",
                        "severity": "info",
                    })
        return issues


class WatermarkRemover(CleaningHandler):
    """Remove repetitive watermark text (e.g. 'Confidential', 'Draft', company names)."""

    name = "remove_watermarks"
    label = "移除水印文字"
    default_enabled = True

    _WATERMARK_KEYWORDS = [
        "草稿", "DRAFT", "机密", "CONFIDENTIAL", "内部资料",
        "仅供评审", "禁止外传", "样本", "SAMPLE",
    ]

    def process(self, text: str, config: dict) -> str:
        # Watermarks usually appear as faint repeated text across pages
        # In extracted text, they show up as repeated short lines
        lines = text.split('\n')
        # Detect repeated lines (watermarks appear frequently)
        line_freq: Dict[str, int] = {}
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) < 60:
                line_freq[stripped] = line_freq.get(stripped, 0) + 1

        # Remove lines that are: (a) watermark-like, (b) appear many times
        threshold = max(3, len(lines) * 0.1)  # at least 10% of lines
        cleaned = []
        for line in lines:
            stripped = line.strip()
            is_watermark = (
                any(kw in stripped for kw in self._WATERMARK_KEYWORDS)
                and line_freq.get(stripped, 0) >= 2
            )
            if is_watermark:
                continue
            cleaned.append(line)
        return '\n'.join(cleaned)

    def find_issues(self, text: str) -> List[dict]:
        issues = []
        lines = text.split('\n')
        line_freq: Dict[str, List[int]] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and len(stripped) < 60:
                line_freq.setdefault(stripped, []).append(i + 1)

        for text_val, positions in line_freq.items():
            if len(positions) >= 3 and any(
                kw in text_val for kw in self._WATERMARK_KEYWORDS
            ):
                issues.append({
                    "type": "watermark",
                    "line": positions[0],
                    "description": f"水印文字(出现{len(positions)}次): {text_val[:40]}",
                    "severity": "warning",
                })
        return issues


class DateNormalizer(CleaningHandler):
    """Normalize various Chinese date formats to ISO YYYY-MM-DD."""

    name = "normalize_dates"
    label = "日期归一化"
    default_enabled = True

    # Match: 2026年4月13日, 2026-04-13, 2026/04/13, 2026.04.13, 26.4.13
    _DATE_RE = re.compile(
        r'(\d{2,4})\s*[年.\-/]\s*(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*[日号]?'
    )

    def process(self, text: str, config: dict) -> str:
        def _replacer(m):
            y = m.group(1)
            if len(y) == 2:
                y = '20' + y
            return f'{y}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
        return self._DATE_RE.sub(_replacer, text)


class PunctuationNormalizer(CleaningHandler):
    """Normalize fullwidth ASCII chars to halfwidth, normalize Chinese quotes."""

    name = "normalize_punctuation"
    label = "全角标点转半角"
    default_enabled = False  # OFF by default for OCR safety

    def process(self, text: str, config: dict) -> str:
        result = []
        for ch in text:
            code = ord(ch)
            # Fullwidth ASCII: FF01-FF5E → shift to 21-7E
            if 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            # Fullwidth space
            elif code == 0x3000:
                result.append(' ')
            else:
                result.append(ch)
        return ''.join(result)


class BlankLineCompressor(CleaningHandler):
    """Compress 3+ consecutive blank lines down to 2."""

    name = "compress_blank_lines"
    label = "连续空行压缩"
    default_enabled = True

    def process(self, text: str, config: dict) -> str:
        # Replace 3+ newlines with 2
        return re.sub(r'\n{3,}', '\n\n', text)


class TableLinearizer(CleaningHandler):
    """Convert table-like text blocks to linear descriptive text.

    Useful when tables in PDFs get garbled and are better represented
    as key-value descriptions for RAG retrieval.
    """

    name = "table_linearization"
    label = "表格转文字描述"
    default_enabled = False  # OFF by default — keep tables as-is

    def process(self, text: str, config: dict) -> str:
        # Detect markdown table blocks (| ... | ... | with separator row)
        lines = text.split('\n')
        result = []
        in_table = False
        table_lines = []

        for line in lines:
            is_table_row = bool(re.match(r'^\|.+\|$', line.strip()))
            is_separator = bool(re.match(r'^\|[\s\-:|—]+\|$', line.strip()))

            if is_table_row and not is_separator:
                if not in_table:
                    in_table = True
                    table_lines = []
                table_lines.append(line)
            else:
                if in_table and table_lines:
                    # Convert collected table to linear text
                    result.append(self._linearize(table_lines))
                    table_lines = []
                    in_table = False
                if not is_separator:
                    result.append(line)

        if in_table and table_lines:
            result.append(self._linearize(table_lines))

        return '\n'.join(result)

    def _linearize(self, rows: List[str]) -> str:
        """Convert table rows to linear key-value description."""
        if not rows:
            return ""
        # Parse header
        header_cells = [c.strip() for c in rows[0].split('|') if c.strip()]
        if len(rows) < 2:
            return " | ".join(header_cells)

        parts = []
        for row in rows[1:]:
            cells = [c.strip() for c in row.split('|') if c.strip()]
            if len(cells) >= 2:
                key = cells[0]
                values = cells[1:]
                parts.append(f"{key}: {', '.join(values)}")

        return "；".join(parts)


class OCRArtifactCleaner(CleaningHandler):
    """Remove common OCR artifacts from scanned Chinese documents.

    Handles: garbled characters, fragmented text remnants, PDF rendering noise,
    stray punctuation marks, and orphaned single characters.
    """

    name = "ocr_cleanup"
    label = "OCR噪点清理"
    default_enabled = True

    # Common OCR garbage patterns in Chinese documents
    _ARTIFACT_PATTERNS = [
        # Lines that are just special characters / rendering noise
        re.compile(r'^[\s\-\*\.·•※◎○●△▲☆★◇◆□■△▽▼▽▶▷●○]{3,}$'),
        # Repeated single character (OCR hallucination)
        re.compile(r'^(.)\1{4,}$'),
        # Stray unicode private-use / control chars
        re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f-]'),
        # OCR confusions: "口" repeated meaninglessly
        re.compile(r'^口{3,}$'),
    ]

    def process(self, text: str, config: dict) -> str:
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Skip purely artifact lines
            if any(p.search(stripped) for p in self._ARTIFACT_PATTERNS):
                continue
            # Skip lines that are mostly non-Chinese/non-ASCII garbage
            if stripped and len(stripped) > 5:
                chinese_chars = sum(1 for c in stripped if '一' <= c <= '鿿' or '　' <= c <= '〿')
                ratio = chinese_chars / len(stripped) if len(stripped) > 0 else 0
                if ratio < 0.1 and not any(c.isalpha() for c in stripped):
                    continue  # Skip lines < 10% Chinese with no Latin letters
            cleaned.append(line)
        return '\n'.join(cleaned)


class DocumentStructureCleaner(CleaningHandler):
    """Clean Chinese government document structural elements.

    - Normalize multiple blank signature/date areas
    - Remove "（此页无正文）" markers
    - Consolidate scattered page-break artifacts
    """

    name = "doc_structure_cleanup"
    label = "公文结构清理"
    default_enabled = True

    _REMOVE_PATTERNS = [
        re.compile(r'（此页无正文）'),
        re.compile(r'^\s*[-=—_]{5,}\s*$'),  # separator lines
        re.compile(r'^\s*[装订线|密封线|准考证号].*$'),  # exam/administrative lines
    ]

    def process(self, text: str, config: dict) -> str:
        lines = text.split('\n')
        cleaned = []
        blank_count = 0
        for line in lines:
            stripped = line.strip()
            # Remove structural markers
            if any(p.search(stripped) for p in self._REMOVE_PATTERNS):
                continue
            # Compress excessive blank areas (signature blocks at end)
            if not stripped:
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)
            else:
                blank_count = 0
                cleaned.append(line)
        return '\n'.join(cleaned)


class SensitiveDataMasker(CleaningHandler):
    """Mask sensitive personal information (phone numbers, ID numbers, etc.)."""

    name = "sensitive_masking"
    label = "敏感信息脱敏"
    default_enabled = True  # ON by default for RAG safety

    _PATTERNS = [
        # Chinese mobile phone: 1[3-9]XXXXXXXXX
        (re.compile(r'(1[3-9]\d)(\d{4})(\d{4})'), r'\1****\3'),
        # Chinese ID card: 18 digits (or 17 + X)
        (re.compile(r'(\d{6})(\d{8,10})(\d{3}[\dXx])'), r'\1********\3'),
        # Landline with area code: 0XXX-XXXXXXXX
        (re.compile(r'(0\d{2,3}-\d{3,4})(\d{4})'), r'\1****'),
    ]

    def process(self, text: str, config: dict) -> str:
        for pattern, replacement in self._PATTERNS:
            text = pattern.sub(replacement, text)
        return text


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class CleaningPipeline:
    """Orchestrate cleaning handlers in a Chain of Responsibility."""

    def __init__(self):
        self.handlers: List[CleaningHandler] = [
            TableExtractionHandler(),
            TOCRemover(),
            HeaderFooterRemover(),
            PageNumberRemover(),
            WatermarkRemover(),
            OCRArtifactCleaner(),
            DocumentStructureCleaner(),
            DateNormalizer(),
            PunctuationNormalizer(),
            BlankLineCompressor(),
            TableLinearizer(),
            SensitiveDataMasker(),
        ]
        self._handler_map: Dict[str, CleaningHandler] = {
            h.name: h for h in self.handlers
        }

    def execute(self, raw_text: str, config: dict) -> str:
        """Execute enabled handlers sequentially. Returns cleaned text."""
        text = raw_text
        for handler in self.handlers:
            if config.get(handler.name, handler.default_enabled):
                text = handler.process(text, config)
        return text

    def analyze(self, raw_text: str) -> List[dict]:
        """Pre-scan text to find all detectable issues across all handlers.

        Returns [{type, line, description, severity}] for UI highlighting.
        """
        all_issues = []
        for handler in self.handlers:
            try:
                issues = handler.find_issues(raw_text)
                all_issues.extend(issues)
            except Exception:
                pass  # Analysis failure shouldn't block the pipeline
        return all_issues

    def get_default_config(self) -> Dict[str, Any]:
        """Return the recommended default config for the UI."""
        return {
            h.name: h.default_enabled
            for h in self.handlers
        }

    def get_handler_metadata(self) -> List[Dict[str, Any]]:
        """Return handler metadata for the UI (labels, defaults, descriptions)."""
        return [
            {
                "name": h.name,
                "label": h.label,
                "default_enabled": h.default_enabled,
            }
            for h in self.handlers
        ]

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~2 chars per token for Chinese text."""
        return max(1, len(text) // 2)


class TableExtractionHandler(CleaningHandler):
    """Extract tables from documents and replace with position markers in text.

    Tables are extracted separately and stored as structured data.
    Text gets 【📊 表X】markers at table positions.
    """

    name = "extract_tables"
    label = "表格分离提取"
    default_enabled = True

    def process(self, text: str, config: dict) -> str:
        # Replace markdown tables with position markers
        import re

        table_count = [0]  # mutable counter

        def _replace_table(m):
            table_count[0] += 1
            tbl_text = m.group(0)
            # Extract headers from the markdown table
            headers = []
            lines = tbl_text.strip().split('\n')
            if lines:
                headers = [c.strip() for c in lines[0].split('|') if c.strip()]
            header_preview = ' | '.join(headers[:4]) if headers else '表格'
            return f'\n【📊 表{table_count[0]}：{header_preview}】\n'

        # Replace markdown tables
        text = re.sub(
            r'(\|[^\n]+\|\n\|[\s\-:|—]+\|\n(?:\|[^\n]+\|\n?)+)',
            _replace_table, text
        )
        return text


# Module-level singleton
cleaning_pipeline = CleaningPipeline()

# Shared in-memory cache: file_path → extracted raw_text
# LRU-bounded to prevent unbounded memory growth from cached file contents.
# Each entry stores up to ~100KB of text; max 200 entries ≈ 20MB.
_MAX_CACHE_ENTRIES = 200
_text_cache: "OrderedDict[str, str]" = OrderedDict()


def _cache_get(file_path: str) -> str | None:
    """Get cached text for a file path, moving to MRU position."""
    if file_path in _text_cache:
        _text_cache.move_to_end(file_path)
        return _text_cache[file_path]
    return None


def _cache_set(file_path: str, text: str) -> None:
    """Cache text for a file path, evicting LRU if at capacity."""
    if file_path in _text_cache:
        _text_cache.move_to_end(file_path)
    _text_cache[file_path] = text
    while len(_text_cache) > _MAX_CACHE_ENTRIES:
        _text_cache.popitem(last=False)


# Backward-compatible dict-like interface for existing code that uses
# `text_cache[fp] = ...` / `text_cache.get(fp)` / `fp in text_cache`.
class _TextCacheProxy:
    """Dict-like proxy that enforces LRU eviction on the underlying cache."""

    def __contains__(self, key: str) -> bool:
        return key in _text_cache

    def __getitem__(self, key: str) -> str:
        return _cache_get(key)  # type: ignore[return-value]

    def __setitem__(self, key: str, value: str) -> None:
        _cache_set(key, value)

    def get(self, key: str, default=None) -> str | None:
        return _cache_get(key) if key in _text_cache else default

    def __repr__(self) -> str:
        return f"<_TextCacheProxy entries={len(_text_cache)}/{_MAX_CACHE_ENTRIES}>"


text_cache: _TextCacheProxy = _TextCacheProxy()  # type: ignore[no-redef]
