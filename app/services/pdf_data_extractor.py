"""Comprehensive PDF data extraction using multi-modal VL for scanned documents.

For each PDF in the 稳评资料:
1. Try pdfplumber text extraction first (fast)
2. For scanned/image-based PDFs: render pages as images → Qwen-VL OCR
3. Extract structured data: 文号, 面积, 位置, 日期, 责任单位, etc.
4. Extract fillable paragraphs: full text blocks that can be used in report
5. Extract table data from the PDFs

Output: Filled PipelineContext with comprehensive project data.
"""

import re
import os
import io
import base64
import asyncio
from pathlib import Path

# 🔴 磁盘缓存版本号：提取逻辑升级（如新增图片提取）时递增，
# 使旧的（extracted_images 为空等）缓存自动失效，避免返回过期结果。
CACHE_VERSION = 3
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class PDFPageData:
    """Data extracted from a single PDF page."""
    page_num: int
    raw_text: str = ""           # Text from pdfplumber (may be empty for scans)
    ocr_text: str = ""           # Text from VL model (for scanned pages)
    is_scanned: bool = False
    structured_data: Dict[str, str] = field(default_factory=dict)
    paragraphs: List[str] = field(default_factory=list)  # Full paragraphs for report


@dataclass
class PDFDocumentData:
    """Complete data extracted from a PDF document."""
    filename: str
    filepath: str
    total_pages: int
    pages: List[PDFPageData] = field(default_factory=list)
    document_type: str = ""      # "announcement" | "survey" | "meeting" | "expert" | "other"
    full_text: str = ""
    key_data: Dict[str, Any] = field(default_factory=dict)
    fillable_paragraphs: Dict[str, str] = field(default_factory=dict)
    extracted_images: List[str] = field(default_factory=list)   # Paths to saved images
    extracted_tables: List[Dict[str, Any]] = field(default_factory=list)  # Structured tables


# ── OCR Prompt Templates ──

ANNOUNCEMENT_OCR_PROMPT = """请**逐字**提取这份征收土地预公告的**全部文字**（OCR 转写，禁止摘要、禁止省略、禁止概括）。

🔴 第一要务：`full_text` 必须是公告**全文逐字转写**——标题、文号、正文每一段、征收范围里的所有村组名称、面积地类数字、公示期限、责任单位、联系人电话、发布日期、落款与盖章文字，**一字不漏**照抄。找不到的不写，绝不编造。

请以 JSON 输出，**`full_text` 放在最前面**（避免被截断）：
{
  "full_text": "公告完整文字内容（逐字逐行，不省略、不概括）",
  "title": "公告标题",
  "doc_ref": "文号",
  "purpose": "征收目的完整段落",
  "scope": "征收范围完整段落",
  "area": "面积信息（原文照抄，无则写「未列明」）",
  "land_types": "地类信息（原文照抄，无则写「未列明」）",
  "announcement_period": "公示期限",
  "responsible_unit": "责任单位",
  "contact": "联系方式",
  "publish_date": "发布日期"
}"""

SURVEY_REPORT_OCR_PROMPT = """请精确提取这份勘测定界报告的表格数据。

请提取：
1. 地块号
2. 土地坐落（完整）
3. 土地用途
4. 总面积（数值）
5. 界址点数
6. 土地分类面积表中的各权属单位及其面积数据
7. 勘测定界单位名称

输出JSON（`full_text` 放最前，必须逐字全文）：
{
  "full_text": "报告完整文字（逐字，含表格文字）",
  "plot_number": "",
  "location": "",
  "land_use": "",
  "total_area_sqm": 0,
  "boundary_points": 0,
  "land_classification": [{"unit": "权属单位", "category": "类别", "area": 面积}],
  "survey_unit": ""
}"""

MEETING_OCR_PROMPT = """请提取这份座谈会记录/签到表的内容。

提取：
1. 会议时间、地点
2. 参会人员
3. 主要讨论内容
4. 群众诉求（完整段落）
5. 会议结论
6. 🔴 问卷统计（如果文档里有问卷/调查统计数据，必须提取）：
   - 问卷发放总数、回收总数、有效数
   - 支持人数、反对人数、无所谓人数
   - 支持率、反对率、知晓率（百分比数值）
   - 各选项的人数分布

输出JSON（`full_text` 放最前，必须逐字全文）：
{
  "full_text": "座谈会记录/签到表完整文字（逐字，不省略）",
  "meeting_date": "",
  "meeting_location": "",
  "attendees": "",
  "discussion": "讨论内容完整段落",
  "public_demands": "群众诉求完整段落",
  "conclusion": "会议结论",
  "total_samples": "问卷发放总数（无则空）",
  "support_count": "支持人数（无则空）",
  "oppose_count": "反对人数（无则空）",
  "support_rate": "支持率（无则空）",
  "oppose_rate": "反对率（无则空）",
  "awareness_rate": "知晓率（无则空）"
}"""

GENERAL_OCR_PROMPT = """请**逐字**提取这份文档的**全部文字**（OCR 转写，禁止摘要、禁止省略）。

🔴 `full_text` 必须是**逐字全文**，放在最前面（避免被截断）；识别文档类型并抽取关键信息，抽不到不编造。
输出 JSON：
{
  "full_text": "完整文字内容（逐字逐行，不省略）",
  "document_type": "文档类型",
  "key_info": {"关键词": "值"}
}"""


class PDFDataExtractor:
    """Extract comprehensive data from PDFs using text + VL model."""

    def __init__(self, llm_service=None):
        """Initialize with LLM service for VL-based OCR.

        Args:
            llm_service: LLMService with chat_with_image() for Qwen-VL-Max.
        """
        self.llm = llm_service
        self._semaphore = asyncio.Semaphore(5)  # Limit concurrent VL calls
        self._cache: Dict[str, PDFDocumentData] = {}  # 🔴 In-memory cache by file path

    # ── 磁盘缓存（跨 session 持久化，避免重复 OCR）──

    @staticmethod
    def _cache_dir() -> Path:
        from app.config import settings
        d = settings.STORAGE_DIR / "ocr_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _disk_cache_key(self, pdf_path: str) -> str:
        """按文件内容 md5 生成缓存 key，文件变化时自动失效。"""
        try:
            import hashlib
            h = hashlib.md5()
            with open(pdf_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    def _load_disk_cache(self, pdf_path: str) -> Optional[PDFDocumentData]:
        """从磁盘加载 OCR 缓存。缓存带版本号，版本不符（代码升级）视为未命中。"""
        dk = self._disk_cache_key(pdf_path)
        if not dk:
            return None
        cache_file = self._cache_dir() / f"{dk}.pkl"
        if not cache_file.exists():
            return None
        try:
            import pickle
            with open(cache_file, 'rb') as f:
                payload = pickle.load(f)
            # 旧格式（无版本号，直接是 PDFDocumentData）→ 视为版本不符，重新提取
            if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == CACHE_VERSION:
                data = payload[1]
                # 🔴 缓存命中但图片为空 → 重新提取图片（旧缓存常见，升级后自动补）
                if isinstance(data, PDFDocumentData) and not data.extracted_images:
                    try:
                        data.extracted_images = self._extract_images_from_pdf(pdf_path, data.document_type)
                    except Exception:
                        pass
                return data
            return None
        except Exception:
            return None

    def _save_disk_cache(self, pdf_path: str, data: PDFDocumentData):
        """把 OCR 结果保存到磁盘（带版本号）。"""
        dk = self._disk_cache_key(pdf_path)
        if not dk:
            return
        try:
            import pickle
            cache_file = self._cache_dir() / f"{dk}.pkl"
            with open(cache_file, 'wb') as f:
                pickle.dump((CACHE_VERSION, data), f)
            print(f"    💾 OCR 缓存已保存: {cache_file.name}")
        except Exception:
            pass

    # ── Main entry point ──

    async def extract_all(self, materials_dir: str) -> List[PDFDocumentData]:
        """Extract data from all PDFs in the materials directory.

        Args:
            materials_dir: Path to 稳评资料 folder.

        Returns:
            List of PDFDocumentData for each PDF found.
        """
        base = Path(materials_dir)
        pdf_files = list(base.rglob("*.pdf"))
        results = []

        for pdf_path in pdf_files:
            print(f"\n📄 Processing: {pdf_path.name}")
            doc_data = await self.extract_pdf(str(pdf_path))
            results.append(doc_data)

            # Print summary
            if doc_data.key_data:
                print(f"  📋 Key data: {list(doc_data.key_data.keys())}")
            if doc_data.fillable_paragraphs:
                print(f"  📝 Fillable paragraphs: {list(doc_data.fillable_paragraphs.keys())}")

        return results

    async def extract_pdf(self, pdf_path: str) -> PDFDocumentData:
        """Extract all data from a single PDF. Cached by file path + mtime + disk."""
        import os as _os
        # 🔴 磁盘缓存：跨 session 持久化，同文件直接复用
        disk_cached = self._load_disk_cache(pdf_path)
        if disk_cached is not None:
            # 🔴 图片提取是本地廉价操作（PyMuPDF 渲染，无 LLM），
            # 每次缓存命中都刷新 extracted_images，杜绝"缓存里残留旧/空图片结果"。
            # 只有贵的 OCR 文本才值得被缓存复用。
            try:
                disk_cached.extracted_images = self._extract_images_from_pdf(pdf_path, disk_cached.document_type)
            except Exception:
                pass
            # 🔴 若缓存是"无 LLM 时保存的降级结果"（扫描页 OCR 为空），
            # 而现在有 LLM → 丢弃缓存，走完整 OCR 流程，避免一直拿空文本。
            if self._is_degraded(disk_cached):
                print(f"    ⚠️ 缓存为无LLM时保存（扫描页OCR为空），重新OCR: {Path(pdf_path).name}")
            else:
                print(f"    💿 OCR 缓存命中: {Path(pdf_path).name}")
                return disk_cached
        # 🔴 内存缓存（同 session 内）
        cache_key = f"{pdf_path}:{_os.path.getmtime(pdf_path) if _os.path.exists(pdf_path) else 0}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        filename = Path(pdf_path).name
        doc_data = PDFDocumentData(
            filename=filename,
            filepath=pdf_path,
            total_pages=0,
            document_type=self._classify_document(filename),
        )

        # Step 1: Try text extraction with pdfplumber
        text_pages = self._extract_text_pdf(pdf_path)

        # Step 2: Determine page count
        try:
            import fitz
            pdf_doc = fitz.open(pdf_path)
            doc_data.total_pages = len(pdf_doc)
            pdf_doc.close()
        except Exception:
            doc_data.total_pages = len(text_pages) if text_pages else 1

        # Step 3: Process pages — scanned pages get concurrent OCR
        page_data_list = []
        ocr_tasks = []

        for page_num in range(doc_data.total_pages):
            page_text = text_pages.get(page_num, "") if page_num < len(text_pages) else ""
            page_data = PDFPageData(page_num=page_num, raw_text=page_text)

            if not page_text or len(page_text.strip()) < 30:
                page_data.is_scanned = True
                if self.llm:
                    # Queue for concurrent OCR
                    ocr_tasks.append((page_num, page_data))
                else:
                    page_data_list.append((page_num, page_data))
            else:
                page_data.ocr_text = page_text
                page_data_list.append((page_num, page_data))

        # 🔴 Concurrent OCR: process scanned pages in parallel batches
        if ocr_tasks:
            import logging as _log_ocr
            _log_ocr.getLogger(__name__).info(
                f"Starting concurrent OCR for {len(ocr_tasks)} scanned pages..."
            )
            # 🔴 并发限制 + 单页超时：防止大批量扫描页（如54页）无上限并发打挂视觉网关，
            # 或单个页面请求挂起导致整批 gather 永远不返回（生成卡住）。
            ocr_sem = asyncio.Semaphore(4)
            async def _ocr_one(pdf_path, pn, doc_type, sem):
                async with sem:
                    return await asyncio.wait_for(
                        self._ocr_page(pdf_path, pn, doc_type), timeout=120.0
                    )
            ocr_results = await asyncio.gather(
                *[_ocr_one(pdf_path, pn, doc_data.document_type, ocr_sem) for pn, _ in ocr_tasks],
                return_exceptions=True,
            )
            for (page_num, page_data), ocr_text in zip(ocr_tasks, ocr_results):
                if isinstance(ocr_text, Exception):
                    print(f"    ⚠️ Page {page_num+1} OCR failed: {ocr_text}")
                    ocr_text = ""
                page_data.ocr_text = ocr_text or ""
                page_data_list.append((page_num, page_data))

        # Sort results back into page order
        page_data_list.sort(key=lambda x: x[0])

        for page_num, page_data in page_data_list:

            # Extract structured data from page text
            if page_data.ocr_text:
                page_data.structured_data = self._parse_structured_data(
                    page_data.ocr_text, doc_data.document_type
                )
                page_data.paragraphs = self._extract_paragraphs(page_data.ocr_text)

            doc_data.pages.append(page_data)
            doc_data.full_text += page_data.ocr_text + "\n"

        # Step 4: Aggregate key data across all pages
        doc_data.key_data = self._aggregate_key_data(doc_data)

        # Step 4.5: Extract embedded images from PDF pages (for report reuse)
        try:
            doc_data.extracted_images = self._extract_images_from_pdf(pdf_path, doc_data.document_type)
            if doc_data.extracted_images:
                print(f"    🖼️  Extracted {len(doc_data.extracted_images)} images from PDF")
        except Exception as e:
            print(f"    ⚠️  Image extraction skipped: {e}")

        # Step 4.6: Extract structured tables from PDF (for report replication)
        try:
            doc_data.extracted_tables = self._extract_tables_from_pdf(pdf_path)
            if doc_data.extracted_tables:
                print(f"    📊 Extracted {len(doc_data.extracted_tables)} tables from PDF")
        except Exception as e:
            print(f"    ⚠️  Table extraction skipped: {e}")

        # Step 5: Build fillable paragraphs
        doc_data.fillable_paragraphs = self._build_fillable_paragraphs(doc_data)

        # 🔴 Cache result（内存 + 磁盘）
        self._cache[cache_key] = doc_data
        # 🔴 降级结果（无 LLM 时扫描页 OCR 为空）不落盘，
        # 避免污染后续带 LLM 的调用（拿不到真实 OCR 文本）。
        if not self._is_degraded(doc_data):
            self._save_disk_cache(pdf_path, doc_data)
        else:
            print(f"    ⚠️ 无LLM扫描页OCR为空，跳过磁盘缓存（避免污染）: {Path(pdf_path).name}")
        return doc_data

    def _is_degraded(self, doc) -> bool:
        """判断提取结果是否"降级"：存在扫描页但 OCR 文本为空。

        多发生在保存时无 LLM 服务可用（扫描页无法 OCR），
        这样的结果不应写入磁盘缓存复用。
        """
        if not isinstance(doc, PDFDocumentData) or not doc.pages:
            return False
        scanned = [p for p in doc.pages if getattr(p, "is_scanned", False)]
        if not scanned:
            return False
        # 🔴 OCR 结果为空 → 降级；或 OCR 结果是 JSON 摘要块（而非逐字全文）→ 也算降级，
        #    让新 prompt（full_text 在前、要求逐字全文）重新 OCR，避免 JSON 摘要进报告。
        def _bad(t):
            t = (t or "").strip()
            return (not t) or t.startswith("{") or t.startswith("```")
        return all(_bad(getattr(p, "ocr_text", "")) for p in scanned)

    # ── Text extraction (pdfplumber) ──

    def _extract_text_pdf(self, pdf_path: str) -> Dict[int, str]:
        """Extract text from PDF using pdfplumber. Returns {page_num: text}."""
        try:
            import pdfplumber
            result = {}
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text()
                    if text:
                        result[i] = text.strip()
            return result
        except ImportError:
            return {}
        except Exception as e:
            print(f"    ⚠️ pdfplumber error: {e}")
            return {}

    # ── VL-based OCR for scanned pages ──

    async def _ocr_page(self, pdf_path: str, page_num: int, doc_type: str) -> str:
        """OCR a single PDF page using Qwen-VL-Max.

        Renders the page as an image, then sends to VL model with type-specific prompt.
        """
        async with self._semaphore:
            # Render page as image
            img_base64, media_type = self._render_page_as_image(pdf_path, page_num)
            if not img_base64:
                return ""

            # Select prompt based on document type
            if doc_type == "announcement":
                prompt = ANNOUNCEMENT_OCR_PROMPT
            elif doc_type == "survey":
                prompt = SURVEY_REPORT_OCR_PROMPT
            elif doc_type == "meeting":
                prompt = MEETING_OCR_PROMPT
            else:
                prompt = GENERAL_OCR_PROMPT

            # Call VL model
            try:
                response = await self.llm.chat_with_image(
                    text=prompt,
                    image_base64=img_base64,
                    media_type=media_type,
                    max_tokens=8192,  # 🔴 提高上限，保证 full_text 逐字全文不被截断
                )

                # Try to extract JSON or text from response
                text = self._extract_text_from_vl_response(response)
                print(f"    ✅ Page {page_num+1} OCR: {len(text)} chars")
                return text
            except Exception as e:
                print(f"    ❌ VL OCR page {page_num+1}: {e}")
                return ""

    def _render_page_as_image(self, pdf_path: str, page_num: int) -> Tuple[str, str]:
        """Render a PDF page as a base64-encoded JPEG image（压缩后，避免 413）。"""
        try:
            import fitz
            doc = fitz.open(pdf_path)
            if page_num >= len(doc):
                doc.close()
                return "", ""

            page = doc[page_num]
            # 用 150 DPI 渲染（够 OCR 用，比 200 DPI 快且小）
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            # 用 JPEG 格式（压缩率高，比 PNG 小 5-10 倍）
            img_bytes = pix.tobytes("jpeg")
            doc.close()

            # 🔴 压缩到 1200px 以内，避免 413
            from PIL import Image as PILImage
            import io
            try:
                pil_img = PILImage.open(io.BytesIO(img_bytes))
                w, h = pil_img.size
                if w > 1200 or h > 1200:
                    ratio = min(1200 / w, 1200 / h)
                    pil_img = pil_img.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)
                    buf = io.BytesIO()
                    pil_img.save(buf, format='JPEG', quality=80, optimize=True)
                    img_bytes = buf.getvalue()
            except Exception:
                pass

            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
            return img_base64, "image/jpeg"
        except ImportError:
            print("    ⚠️ PyMuPDF not installed, cannot render PDF pages")
            return "", ""
        except Exception as e:
            print(f"    ⚠️ Page render error: {e}")
            return "", ""

    # ── PDF Image Extraction ──

    def _extract_images_from_pdf(self, pdf_path: str, doc_type: str = "") -> List[str]:
        """Extract embedded images AND page renders from a PDF, save as reusable files.

        Uses two strategies:
        1. Extract embedded images via PyMuPDF (logos, maps, diagrams)
        2. For scanned PDFs or pages with important content, save page renders
           as images suitable for report figures and appendices.

        Returns list of relative paths (e.g., "images/pdf_extract_xxx.png").
        """
        saved_paths = []
        try:
            import fitz
            from app.config import settings

            images_dir = settings.STORAGE_DIR / "images"
            images_dir.mkdir(parents=True, exist_ok=True)

            doc = fitz.open(pdf_path)
            doc_basename = Path(pdf_path).stem[:20]

            # Strategy 1: Extract embedded images from each page
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img_idx, img_info in enumerate(image_list):
                    try:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        img_bytes = base_image["image"]
                        img_ext = base_image["ext"]  # png, jpg, jpeg, etc.

                        # Skip tiny images (< 5KB, likely icons/logos)
                        if len(img_bytes) < 5120:
                            continue

                        import uuid
                        safe_name = re.sub(r'[\\/:*?"<>|]', '_', doc_basename)
                        filename = f"pdf_{safe_name}_p{page_num+1}_img{img_idx}.{img_ext}"
                        filepath = images_dir / filename

                        # Avoid duplicates: 已存在则直接复用路径（图片仍可用，返回给调用方）
                        relative = f"images/{filename}"
                        if filepath.exists():
                            saved_paths.append(relative)
                            continue

                        filepath.write_bytes(img_bytes)
                        saved_paths.append(relative)

                    except Exception as e:
                        continue  # Skip problematic images

            # Strategy 2: Save full page renders for key pages in scanned docs
            if doc_type in ("announcement", "meeting", "survey"):
                total_pages = len(doc)
                # Render first 3 pages and last page (usually contain stamps/signatures)
                key_pages = set([0, 1, 2, total_pages - 1])
                key_pages = {p for p in key_pages if 0 <= p < total_pages}

                for page_num in key_pages:
                    try:
                        page = doc[page_num]
                        # Render at 150 DPI for reasonable file size
                        mat = fitz.Matrix(1.5, 1.5)
                        pix = page.get_pixmap(matrix=mat)
                        img_bytes = pix.tobytes("png")

                        if len(img_bytes) < 10240:  # Skip very small renders
                            continue

                        import uuid
                        safe_name = re.sub(r'[\\/:*?"<>|]', '_', doc_basename)
                        filename = f"pdf_{safe_name}_page{page_num+1}.png"
                        filepath = images_dir / filename
                        relative = f"images/{filename}"

                        if filepath.exists():
                            saved_paths.append(relative)
                            continue

                        filepath.write_bytes(img_bytes)
                        saved_paths.append(relative)

                    except Exception:
                        continue

            doc.close()
            return saved_paths

        except ImportError:
            print("    ⚠️ PyMuPDF not installed, cannot extract PDF images")
            return []
        except Exception as e:
            print(f"    ⚠️ Image extraction error: {e}")
            return []

    # ── PDF Table Extraction ──

    def _extract_tables_from_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        """Extract structured table data from PDF using pdfplumber.

        Each returned dict represents one table:
        {
            "page": int,
            "headers": ["列1", "列2", ...],
            "rows": [["val1", "val2", ...], ...],
            "raw_text": "表格原文",
            "table_index": int (0-based within page),
        }
        """
        tables = []
        try:
            import pdfplumber

            # 🔴 勘测定界报告：用专门的解析器一次性产出「地块汇总表 + 土地分类面积表」，
            #    比逐页横排流提取可靠；命中即整份返回，不再走逐页残缺副本。
            kd_tables = self._kd_tables(pdf_path)
            if kd_tables:
                return kd_tables

            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    page_tables = page.extract_tables()
                    if not page_tables:
                        continue

                    for t_idx, table in enumerate(page_tables):
                        if not table or len(table) < 2:  # Need at least header + 1 row
                            continue

                        # Clean up: remove None values, strip whitespace
                        cleaned = []
                        for row in table:
                            if row and any(cell for cell in row if cell and str(cell).strip()):
                                cleaned.append([str(cell).strip() if cell else "" for cell in row])

                        if len(cleaned) < 2:
                            continue

                        headers = cleaned[0] if cleaned else []
                        rows = cleaned[1:] if len(cleaned) > 1 else []

                        # Build raw text representation for AI reference
                        raw_lines = []
                        raw_lines.append(" | ".join(headers))
                        raw_lines.append(" | ".join(["---"] * len(headers)))
                        for row in rows:
                            # Pad row to match header length
                            padded = row + [""] * (len(headers) - len(row))
                            raw_lines.append(" | ".join(padded[:len(headers)]))

                        tables.append({
                            "page": page_num + 1,
                            "headers": headers,
                            "rows": rows,
                            "row_count": len(rows),
                            "col_count": len(headers),
                            "raw_markdown": "\n".join(raw_lines),
                            "table_index": t_idx,
                        })

            return tables

        except ImportError:
            print("    ⚠️ pdfplumber not installed")
            return []
        except Exception as e:
            print(f"    ⚠️ Table extraction error: {e}")
            return []

    def _kd_tables(self, pdf_path: str) -> List[Dict[str, Any]]:
        """用勘测定界专门解析器产出规范表格（地块汇总表 + 土地分类面积表）。

        返回 list[dict]（非勘测定界报告时为空列表）。

        🔴 **不再自己拼一份**：这里以前手写了一遍和 `land_survey_parser.to_extracted_tables()`
           几乎相同的转换，只有表题不同（这里「表3-1」、那边「表6-1」）——
           于是同一份材料会产出**同表头、不同表题**的两份表，
           去重按表头签名做，谁活下来**取决于调用顺序**，报告里的表号就飘。
           现在只此一处来源：直接复用解析器的产物。
        """
        try:
            from app.services.land_survey_parser import parse_land_survey, to_extracted_tables
            parsed = parse_land_survey(pdf_path)
            if not parsed:
                return []
            return [{**t, "page": 1, "table_index": i}
                    for i, t in enumerate(to_extracted_tables(parsed))]
        except Exception as e:
            print(f"    ⚠️ 勘测定界解析失败: {e}")
            return []

    def _extract_text_from_vl_response(self, response: str) -> str:
        """Extract usable text content from VL model JSON response."""
        # Try JSON first
        try:
            import json, re as _re
            _resp = (response or "").strip()
            # 🔴 去掉 ```json ... ``` 围栏：否则 json.loads 失败 → 整个 JSON 被当正文塞进报告
            _m = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', _resp)
            if _m:
                _resp = _m.group(1).strip()
            data = json.loads(_resp)
            # If it has full_text, return that
            if data.get("full_text"):
                return data["full_text"]
            # Otherwise, flatten all string values
            texts = []
            for k, v in data.items():
                if isinstance(v, str) and len(v) > 10:
                    texts.append(f"{k}: {v}")
            if texts:
                return "\n".join(texts)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, Exception):
            pass

        # Plain text response
        return response.strip()

    # ── Structured data parsing ──

    def _parse_structured_data(self, text: str, doc_type: str) -> Dict[str, str]:
        """Parse structured data from OCR/extracted text."""
        result = {}

        # Try JSON parsing first (VL model may return JSON or markdown-wrapped JSON)
        try:
            import json
            # Find JSON block — handle ```json ... ``` wrapping and bare JSON
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
            if not json_match:
                json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                json_str = json_match.group(1) if json_match.lastindex else json_match.group(0)
                # Clean common issues
                json_str = json_str.strip()
                data = json.loads(json_str)
                for k, v in data.items():
                    # 🔴 修复：不再丢弃短字符串（如 support_count:"1"、oppose_count:"0"），
                    #    只丢弃真正的空值。len>2 过滤会丢掉调查选项计数。
                    if isinstance(v, str) and v.strip():
                        result[k] = v.strip()
                    elif isinstance(v, (int, float)):
                        result[k] = str(v)
                if result:
                    return result
        except (json.JSONDecodeError, Exception):
            pass

        # Fallback: regex patterns
        patterns = {
            "doc_ref": r'([一-鿿]+〔?\d{4}〕?\d+号)',
            "area_sqm": r'(\d[\d,.]{3,})\s*(?:平方米|㎡)',
            "location": r'(?:位于|坐落)[：:\s]*([^。\n]{5,40}(?:街道|镇|乡|社区|村|组))',
            "land_use": r'(?:用途)[：:\s]*([^。\n]{3,20}(?:用地))',
            "publish_date": r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            "contact": r'(?:联系人|电话)[：:\s]*([^。\n]{3,30})',
            "purpose": r'(?:征收目的|为了)[：:\s]*([^。]{10,200}?)',
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                val = match.group(1).strip() if match.lastindex else match.group(0).strip()
                if len(val) > 1:
                    result[key] = val

        return result

    def _extract_paragraphs(self, text: str) -> List[str]:
        """Extract full paragraphs from text (for report filling)."""
        # Split by double newlines or Chinese paragraph breaks
        paras = re.split(r'\n\s*\n|。\s*\n', text)
        result = []
        for p in paras:
            p = p.strip()
            if len(p) > 30:  # Only meaningful paragraphs
                result.append(p)
        return result

    # ── Aggregation ──

    @staticmethod
    def _sum_numeric(values) -> int:
        """把每页的字符串/数字计数累加为 int（空/非数字忽略）。"""
        total = 0
        for v in values:
            if v is None:
                continue
            try:
                num = float(str(v).replace("%", "").replace("，", "").replace(",", "").strip())
                total += int(num)
            except (ValueError, TypeError):
                continue
        return total

    def _aggregate_key_data(self, doc_data: PDFDocumentData) -> Dict[str, Any]:
        """Aggregate key data across all pages of a document.

        🔴 修复：多页会议/调查记录不再 update() 覆盖，而是：
        - 单值字段取第一个非空
        - 问卷选项统计逐页累加（support/oppose/total），算出汇总支持率/反对率
        - 参会人/日期/地点/诉求 合并去重
        """
        aggregated = {}
        pages_structured = [
            page.structured_data
            for page in doc_data.pages
            if isinstance(page.structured_data, dict) and page.structured_data
        ]
        if not pages_structured:
            return aggregated

        # ── 单值字段：取第一个非空（不再被最后一页覆盖）──
        field_mapping = {
            "doc_ref": "doc_reference",
            "title": "project_name",
            "responsible_unit": "decision_unit",
            "location": "land_location",
            "land_use": "land_use",
            "publish_date": "announcement_date",
            "announcement_period": "announcement_period",
            "purpose": "purpose_text",
            "scope": "scope_text",
            "total_area_sqm": "land_area_sqm",
            "boundary_points": "boundary_points",
        }
        for src_key, dst_key in field_mapping.items():
            for sd in pages_structured:
                val = sd.get(src_key)
                if val not in (None, "", "无"):
                    aggregated[dst_key] = val
                    break

        # ── 会议/调查记录聚合 ──
        # 参会人：跨页去重合并
        attendees = []
        seen = set()
        for sd in pages_structured:
            att = str(sd.get("attendees", "") or "").strip()
            for name in re.split(r'[、，,;\n\r\s]+', att):
                name = name.strip()
                if name and name not in seen and len(name) >= 2 and not name.isdigit():
                    seen.add(name)
                    attendees.append(name)
        if attendees:
            aggregated["symposium_attendees"] = "、".join(attendees)

        # 日期/地点：去重合并
        dates = sorted({str(sd["meeting_date"]).strip() for sd in pages_structured if sd.get("meeting_date")})
        locations = sorted({str(sd["meeting_location"]).strip() for sd in pages_structured if sd.get("meeting_location")})
        if dates:
            aggregated["symposium_date"] = "、".join(dates)
        if locations:
            aggregated["symposium_location"] = "、".join(locations)

        # 诉求/讨论/结论：合并非空且非占位的内容
        _PLACEHOLDER = {"无", "无明确群众诉求内容。", "无明确群众诉求段落，仅包含单位意见与建议收集。",
                        "无明确会议结论。", "无明确会议结论内容。"}
        for src_key, dst_key in [("public_demands", "public_demands"),
                                  ("discussion", "discussion_text"),
                                  ("conclusion", "conclusion_text")]:
            vals = []
            for sd in pages_structured:
                v = str(sd.get(src_key, "") or "").strip()
                if v and v not in _PLACEHOLDER and v not in vals:
                    vals.append(v)
            if vals:
                aggregated[dst_key] = "；".join(vals)

        # ── 🔴 问卷选项统计：逐页累加 ──
        support = self._sum_numeric([sd.get("support_count") for sd in pages_structured])
        oppose = self._sum_numeric([sd.get("oppose_count") for sd in pages_structured])
        explicit_total = self._sum_numeric([sd.get("total_samples") for sd in pages_structured])
        aware_pages = sum(
            1 for sd in pages_structured
            if sd.get("awareness_rate") not in (None, "", "0%", "0", 0)
        )
        # 问卷总数：取「显式 total_samples」与「有表态记录页数」的较大值。
        # 🔴 个别页的 total_samples 字段可能只是局部值（如某页写"2"），
        #    而逐人逐页 PDF 的真实调查总数 = 有表态记录的页数，两者取 max 更可靠。
        surveyed_pages = [
            sd for sd in pages_structured
            if sd.get("support_count") not in (None, "", "0", 0)
            or sd.get("oppose_count") not in (None, "", "0", 0)
            or sd.get("awareness_rate") not in (None, "", "0%", "0", 0)
        ]
        total = max(explicit_total, len(surveyed_pages))

        if total > 0:
            aggregated["total_samples"] = total
            aggregated["support_count"] = support
            aggregated["oppose_count"] = oppose
            # 🔴 不带 %：与 deep_material_analyzer 的 support_rate 约定一致
            #   （"100.0" 而非 "100.0%"），避免下游 float() 转换崩溃
            aggregated["support_rate"] = str(round(support / total * 100, 1))
            aggregated["oppose_rate"] = str(round(oppose / total * 100, 1))
            aggregated["awareness_rate"] = str(round(aware_pages / total * 100, 1))
            aggregated["survey_data_source"] = (
                "逐页累加(每人一页)" if explicit_total == 0 else "问卷显式汇总"
            )

        # Convert area to float
        if "land_area_sqm" in aggregated:
            try:
                aggregated["land_area_sqm"] = float(
                    str(aggregated["land_area_sqm"]).replace(",", "").replace(" ", "")
                )
                aggregated["land_area_mu"] = round(aggregated["land_area_sqm"] / 666.67, 2)
            except (ValueError, TypeError):
                pass

        return aggregated

    def _build_fillable_paragraphs(self, doc_data: PDFDocumentData) -> Dict[str, str]:
        """Build a dict of {paragraph_type: paragraph_text} that can fill report placeholders.

        Extracts CLEAN text from structured data (not raw JSON).
        """
        fillable = {}

        # 注：预公告（announcement）不再在此解析。
        #     公告正文（征收目的/范围/期限/工作安排/其他事项/法律依据…）统一由
        #     `app/services/announcement_extract.parse_announcement()` 从逐字文本解析，
        #     避免两套逻辑各抽一半、互相不一致。

        if doc_data.document_type == "meeting":
            key_data = doc_data.key_data
            if key_data.get("public_demands"):
                fillable["public_demands_paragraph"] = str(key_data["public_demands"])
            if key_data.get("discussion_text"):
                fillable["discussion_paragraph"] = str(key_data["discussion_text"])
            if key_data.get("conclusion_text"):
                fillable["conclusion_paragraph"] = str(key_data["conclusion_text"])
            # 🔴 参会人（跨页去重合并）
            if key_data.get("symposium_attendees"):
                fillable["symposium_attendees"] = str(key_data["symposium_attendees"])
            if key_data.get("symposium_date"):
                fillable["symposium_date"] = str(key_data["symposium_date"])
            if key_data.get("symposium_location"):
                fillable["symposium_location"] = str(key_data["symposium_location"])
            # 🔴 座谈会 PDF 里的问卷统计数据（逐页累加后的汇总）→ 填入 filled_data
            if key_data.get("total_samples"):
                fillable["total_samples"] = str(key_data["total_samples"])
            if key_data.get("support_count"):
                fillable["support_count"] = str(key_data["support_count"])
            if key_data.get("oppose_count"):
                fillable["oppose_count"] = str(key_data["oppose_count"])
            if key_data.get("support_rate"):
                fillable["support_rate"] = str(key_data["support_rate"])
            if key_data.get("oppose_rate"):
                fillable["oppose_rate"] = str(key_data["oppose_rate"])
            if key_data.get("awareness_rate"):
                fillable["awareness_rate"] = str(key_data["awareness_rate"])

        elif doc_data.document_type == "survey":
            for page in doc_data.pages:
                for para in page.paragraphs:
                    if "面积" in para and any(c.isdigit() for c in para):
                        fillable["area_data_paragraph"] = para
                    if "用途" in para:
                        fillable["land_use_paragraph"] = para

        return fillable

    # ── Helpers ──

    @staticmethod
    def _classify_document(filename: str) -> str:
        """Classify PDF document type from filename."""
        name = filename.lower()
        if any(kw in name for kw in ["公告", "预公告", "征收土地"]):
            return "announcement"
        if any(kw in name for kw in ["勘测", "测定界", "面积"]):
            return "survey"
        if any(kw in name for kw in ["座谈", "会议", "纪要"]):
            return "meeting"
        if any(kw in name for kw in ["专家", "评审", "意见"]):
            return "expert"
        return "other"

    @staticmethod
    def apply_to_context(doc_data_list: List[PDFDocumentData], context) -> None:
        """Apply extracted PDF data to a PipelineContext.

        This enriches the context with data from ALL PDFs.
        """
        for doc_data in doc_data_list:
            # Apply key data
            for key, value in doc_data.key_data.items():
                if hasattr(context, key) and not getattr(context, key):
                    setattr(context, key, value)
                elif key == "doc_reference" and not context.doc_reference:
                    context.doc_reference = str(value)
                elif key == "project_name" and not context.project_name:
                    context.project_name = str(value)
                elif key == "decision_unit" and not context.decision_unit:
                    context.decision_unit = str(value)
                elif key == "land_location" and not context.land_location:
                    context.land_location = str(value)
                elif key == "land_area_sqm" and not context.land_area_sqm:
                    try:
                        context.land_area_sqm = float(str(value).replace(",", ""))
                        context.land_area_mu = round(context.land_area_sqm / 666.67, 2)
                    except (ValueError, TypeError):
                        pass
                elif key == "land_use" and not context.land_use:
                    context.land_use = str(value)
                elif key == "announcement_date" and not context.announcement_date:
                    context.announcement_date = str(value)

            # Store fillable paragraphs in context
            if doc_data.fillable_paragraphs:
                if not hasattr(context, 'fillable_paragraphs'):
                    context.fillable_paragraphs = {}
                context.fillable_paragraphs.update(doc_data.fillable_paragraphs)

            # Store raw text for reference
            context.extracted_texts[doc_data.filename] = doc_data.full_text
