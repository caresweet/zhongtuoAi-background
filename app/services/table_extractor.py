"""Table Extraction Service — extract tables from PDFs and images.

Strategy:
1. Digital PDFs → pdfplumber.extract_tables() (fast, accurate)
2. Scanned PDFs → PyMuPDF render → Qwen-VL OCR + table detection
3. Images → Qwen-VL direct analysis

Returns structured table data + narrative text for RAG ingestion.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF
import pdfplumber

logger = logging.getLogger(__name__)


@dataclass
class ExtractedTable:
    """A single extracted table."""
    table_index: int
    page: int
    source: str  # "pdfplumber" | "vision" | "manual"
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    raw_markdown: str = ""
    narrative: str = ""       # Human-readable description for RAG
    col_count: int = 0
    row_count: int = 0
    confidence: float = 1.0   # 0-1, lower for vision-based extraction

    def to_markdown(self) -> str:
        if not self.headers and not self.rows:
            return ""
        lines = []
        if self.headers:
            lines.append("| " + " | ".join(self.headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(self.headers)) + " |")
        for row in self.rows:
            cells = [str(c).replace("\n", " ") for c in row]
            while len(cells) < len(self.headers):
                cells.append("")
            lines.append("| " + " | ".join(cells[:len(self.headers)]) + " |")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "table_index": self.table_index,
            "page": self.page,
            "source": self.source,
            "headers": self.headers,
            "rows": self.rows,
            "col_count": self.col_count,
            "row_count": self.row_count,
            "markdown": self.to_markdown(),
            "narrative": self.narrative,
            "confidence": self.confidence,
        }


class TableExtractor:
    """Extract tables from PDFs and images using multi-strategy approach."""

    def __init__(self, llm_service=None):
        self._llm = llm_service

    # ── Main API ──────────────────────────────────────────────────

    def extract_from_pdf(self, file_path: str) -> List[ExtractedTable]:
        """Extract all tables from a PDF. Auto-detects scanned vs digital."""
        abs_path = Path(file_path)
        if not abs_path.is_absolute():
            from app.config import settings
            # If path already starts with storage dir name, don't double-prefix
            path_str = str(abs_path)
            storage_str = str(settings.STORAGE_DIR)
            if path_str.startswith('storage/') or path_str.startswith('storage\\'):
                abs_path = settings.STORAGE_DIR.parent / file_path
            else:
                abs_path = settings.STORAGE_DIR / file_path

        if not abs_path.exists():
            logger.warning(f"File not found: {abs_path}")
            return []

        tables = []

        # Strategy 1: pdfplumber for digital PDFs
        try:
            digital_tables = self._extract_with_pdfplumber(str(abs_path))
            tables.extend(digital_tables)
        except Exception as e:
            logger.warning(f"pdfplumber extraction failed: {e}")

        # Strategy 2: If no tables found, try vision-based extraction
        if not tables:
            try:
                vision_tables = self._extract_with_vision(str(abs_path))
                tables.extend(vision_tables)
            except Exception as e:
                logger.warning(f"Vision extraction failed: {e}")

        # Assign indices and build narratives
        for i, t in enumerate(tables):
            t.table_index = i + 1
            if not t.narrative:
                t.narrative = self._build_narrative(t)

        return tables

    def extract_from_docx(self, file_path: str) -> List[ExtractedTable]:
        """Extract tables from a DOCX file using python-docx."""
        abs_path = Path(file_path)
        if not abs_path.is_absolute():
            from app.config import settings
            path_str = str(abs_path)
            if path_str.startswith('storage/'):
                abs_path = settings.STORAGE_DIR.parent / file_path
            else:
                abs_path = settings.STORAGE_DIR / file_path

        if not abs_path.exists():
            logger.warning(f"DOCX not found: {abs_path}")
            return []

        try:
            from docx import Document
            doc = Document(str(abs_path))
        except Exception as e:
            logger.warning(f"Failed to open DOCX: {e}")
            return []

        tables = []
        for tbl_idx, tbl in enumerate(doc.tables):
            rows_data = []
            for row in tbl.rows:
                cells = [self._clean_cell(c.text) for c in row.cells]
                if any(cells):
                    rows_data.append(cells)

            if len(rows_data) < 2:
                continue

            headers = rows_data[0]
            data_rows = rows_data[1:]

            # Build markdown
            md_lines = []
            md_lines.append("| " + " | ".join(headers) + " |")
            md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in data_rows:
                while len(row) < len(headers):
                    row.append("")
                md_lines.append("| " + " | ".join(row[:len(headers)]) + " |")

            t = ExtractedTable(
                table_index=len(tables),
                page=0,
                source="docx",
                headers=headers,
                rows=data_rows,
                raw_markdown="\n".join(md_lines),
                col_count=len(headers),
                row_count=len(data_rows),
                confidence=0.99,
            )
            t.narrative = self._build_narrative(t)
            tables.append(t)

        return tables

    def extract_from_image(self, image_path: str) -> List[ExtractedTable]:
        """Extract tables from an image file using vision."""
        if self._llm:
            return self._extract_image_tables_with_vision(image_path)
        return []

    # ── Strategy 1: pdfplumber ─────────────────────────────────────

    def _extract_with_pdfplumber(self, file_path: str) -> List[ExtractedTable]:
        """Use pdfplumber's built-in table extraction."""
        tables = []
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_tables = page.extract_tables()
                for tbl_idx, raw_table in enumerate(page_tables):
                    if not raw_table or len(raw_table) < 2:
                        continue

                    # Clean cells
                    cleaned = []
                    for row in raw_table:
                        cleaned_row = [
                            self._clean_cell(c) for c in (row or [])
                        ]
                        if any(cleaned_row):  # Skip empty rows
                            cleaned.append(cleaned_row)

                    if len(cleaned) < 2:
                        continue

                    headers = cleaned[0]
                    data_rows = cleaned[1:]

                    # Build markdown
                    md_lines = []
                    md_lines.append("| " + " | ".join(headers) + " |")
                    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                    for row in data_rows:
                        while len(row) < len(headers):
                            row.append("")
                        md_lines.append("| " + " | ".join(row[:len(headers)]) + " |")

                    tables.append(ExtractedTable(
                        table_index=len(tables),
                        page=page_num,
                        source="pdfplumber",
                        headers=headers,
                        rows=data_rows,
                        raw_markdown="\n".join(md_lines),
                        col_count=len(headers),
                        row_count=len(data_rows),
                        confidence=0.95,
                    ))

        return tables

    # ── Strategy 2: Vision-based ───────────────────────────────────

    def _extract_with_vision(self, file_path: str) -> List[ExtractedTable]:
        """Render PDF pages as images and use vision LLM to find tables."""
        if not self._llm:
            return []

        tables = []
        doc = fitz.open(file_path)

        for page_num in range(min(len(doc), 10)):  # Max 10 pages for vision
            page = doc[page_num]
            # Render page at 200 DPI
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")

            import base64
            img_b64 = base64.b64encode(img_bytes).decode()

            prompt = (
                "请识别这张图片中的所有表格。对每个表格：\n"
                "1. 提取表头（列名）\n"
                "2. 提取所有数据行\n"
                "3. 输出为Markdown表格格式\n\n"
                "如果没有表格，回复：NO_TABLES"
            )

            try:
                import asyncio
                result = asyncio.get_event_loop().run_until_complete(
                    self._llm.chat_with_image(
                        text=prompt,
                        image_base64=img_b64,
                        media_type="image/png",
                        max_tokens=2048,
                    )
                )
                if result and "NO_TABLES" not in result:
                    parsed = self._parse_vision_table_output(result, page_num + 1)
                    tables.extend(parsed)
            except Exception as e:
                logger.warning(f"Vision OCR page {page_num+1} failed: {e}")

        doc.close()
        return tables

    def _extract_image_tables_with_vision(self, image_path: str) -> List[ExtractedTable]:
        """Extract tables from a standalone image."""
        if not self._llm:
            return []

        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        prompt = (
            "请识别这张图片中的所有表格。对每个表格：\n"
            "1. 提取表头（列名）\n"
            "2. 提取所有数据行\n"
            "3. 输出为Markdown表格格式\n\n"
            "如果没有表格，回复：NO_TABLES"
        )

        try:
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                self._llm.chat_with_image(
                    text=prompt,
                    image_base64=img_b64,
                    media_type="image/png",
                    max_tokens=2048,
                )
            )
            if result and "NO_TABLES" not in result:
                return self._parse_vision_table_output(result, 0)
        except Exception as e:
            logger.warning(f"Image table extraction failed: {e}")

        return []

    def _parse_vision_table_output(self, text: str, page: int) -> List[ExtractedTable]:
        """Parse vision LLM output into ExtractedTable objects."""
        tables = []

        # Find markdown table patterns in the response
        pattern = r'(\|[^\n]+\|\n\|[\s\-:|—]+\|\n(?:\|[^\n]+\|\n?)+)'
        matches = re.findall(pattern, text)

        for i, match in enumerate(matches):
            lines = match.strip().split('\n')
            if len(lines) < 2:
                continue

            headers = [c.strip() for c in lines[0].split('|') if c.strip()]
            rows = []
            for line in lines[2:]:  # Skip header + separator
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if cells:
                    rows.append(cells)

            if headers and rows:
                tables.append(ExtractedTable(
                    table_index=i,
                    page=page,
                    source="vision",
                    headers=headers,
                    rows=rows,
                    raw_markdown=match,
                    col_count=len(headers),
                    row_count=len(rows),
                    confidence=0.7,
                ))

        return tables

    # ── Grid-based extraction (for images with clear lines) ────────

    def extract_with_grid(self, image_path: str) -> Optional[ExtractedTable]:
        """Extract table using grid line detection (PIL + numpy)."""
        try:
            from PIL import Image
            import numpy as np

            img = Image.open(image_path).convert("L")  # Grayscale
            arr = np.array(img)

            # Binary threshold
            binary = (arr < 128).astype(np.uint8) * 255

            # Detect horizontal lines
            h_kernel = np.ones((1, 40), np.uint8)
            h_lines = self._morph_open(binary, h_kernel)

            # Detect vertical lines
            v_kernel = np.ones((40, 1), np.uint8)
            v_lines = self._morph_open(binary, v_kernel)

            # Find line coordinates
            h_coords = self._find_line_coords(h_lines, axis=0)
            v_coords = self._find_line_coords(v_lines, axis=1)

            if len(h_coords) < 2 or len(v_coords) < 2:
                return None  # No clear grid detected

            # Use the line coords to identify cells
            # This is a best-effort approach without OpenCV's full morphology
            return None  # Placeholder — needs proper OCR per cell

        except Exception as e:
            logger.warning(f"Grid extraction failed: {e}")
            return None

    @staticmethod
    def _morph_open(arr, kernel):
        """Simple morphological opening using numpy (no OpenCV dependency)."""
        from scipy.ndimage import binary_erosion, binary_dilation
        eroded = binary_erosion(arr, structure=kernel)
        return binary_dilation(eroded, structure=kernel)

    @staticmethod
    def _find_line_coords(arr, axis=0):
        """Find line positions from binary line-detection array."""
        projection = arr.sum(axis=axis)
        threshold = arr.shape[1 - axis] * 0.3
        coords = list((projection > threshold).nonzero()[0])
        # Merge nearby coords
        merged = []
        for c in coords:
            if not merged or c - merged[-1] > 5:
                merged.append(c)
        return merged

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _clean_cell(cell: Any) -> str:
        """Clean a table cell value."""
        if cell is None:
            return ""
        text = str(cell).strip()
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove control chars
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        return text

    def _build_narrative(self, table: ExtractedTable) -> str:
        """Build a human-readable narrative from table data for RAG."""
        if not table.headers or not table.rows:
            return ""

        parts = []
        h_str = "、".join(table.headers)
        parts.append(f"表格包含 {table.row_count} 行 {table.col_count} 列，列名为：{h_str}。")

        for i, row in enumerate(table.rows[:10]):  # Max 10 rows for narrative
            desc_parts = []
            for j, cell in enumerate(row):
                if j < len(table.headers) and cell:
                    desc_parts.append(f"{table.headers[j]}为{cell}")
            if desc_parts:
                parts.append(f"第{i+1}行：{'，'.join(desc_parts)}。")

        return "".join(parts)


# Module-level singleton (lazy init with LLM)
_table_extractor: Optional[TableExtractor] = None


def get_table_extractor(llm_service=None) -> TableExtractor:
    global _table_extractor
    if _table_extractor is None or (llm_service and not _table_extractor._llm):
        _table_extractor = TableExtractor(llm_service=llm_service)
    return _table_extractor
