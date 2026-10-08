"""Unified material ingestion for knowledge documents and report-session uploads.

Normalizes PDF/Word/text/image materials into a consistent structure so the
knowledge-base flow and report-generation flow can reuse the same extraction
results.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.file_service import file_service
from app.services.image_analyzer import image_analyzer
from app.services.pdf_data_extractor import PDFDataExtractor


EXTRACTION_VERSION = "material-ingestion-v1"

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_TEXT_EXTENSIONS = {".txt", ".md"}
_WORD_EXTENSIONS = {".doc", ".docx"}


# ═══════════════════════════════════════════════════════════════════════════════
# 关键信息识别 —— 日期 / 结论性数值
# ═══════════════════════════════════════════════════════════════════════════════
# 🔴 用户上传的材料**不只有固定的那几项**：除了预公告，还可能是施工/调查类文件，
#    里面夹着各类日期（公示、勘察、座谈、评审…）和结论性数据（群众支持率、户数、
#    问卷份数）。这些信息此前一律识别不出来，用户上传了材料仍被弹窗逐项追问。
#    这里按「标签就近取日期」识别，且只认**带明确标签**的日期 —— 不按裸日期乱猜，
#    避免把勘测定界图、图纸比例尺上的无关日期串成报告字段。
_DATE_LABEL_ALIASES = [
    ("commission_date",       ("委托日期", "委托时间")),
    ("plan_date",             ("制定方案日期", "方案制定日期", "工作方案日期", "工作方案时间")),
    ("plan_approve_date",     ("方案审核日期", "方案审核时间")),
    ("notice_publish_date",   ("公告发布日期", "预公告发布日期", "发布日期", "公告日期")),
    ("notice_date",           ("现场公示开始", "现场公示时间", "现场公示日期", "现场公示", "张贴公示")),
    ("notice_end_date",       ("现场公示结束", "公示结束日期")),
    ("publicity_start_date",  ("稳评公示开始", "稳评公示日期", "稳评公示时间", "稳评公示")),
    ("survey_date",           ("实地勘察日期", "实地勘察时间", "勘察日期", "勘察时间", "现场勘察")),
    ("symposium_date",        ("座谈会日期", "座谈会时间", "座谈日期", "座谈时间")),
    ("risk_predict_date",     ("风险预测开始", "风险预测日期")),
    ("report_draft_date",     ("报告编制开始", "报告编制日期", "编制开始日期")),
    ("report_draft_end_date", ("报告编制结束", "报告编制完成", "编制完成日期")),
    ("review_date",           ("专家评审日期", "专家评审时间", "评审日期", "评审时间")),
    ("review_end_date",       ("专家评审结束", "评审结束日期", "评审结束时间")),
]

# 起止同属一个活动 —— 只要标签处给的是区间（「6月4日至6月10日」）就顺带补结束日
_DATE_RANGE_PAIRS = {
    "notice_date": "notice_end_date",
    "publicity_start_date": "publicity_end_date",
    "risk_predict_date": "risk_predict_end_date",
    "report_draft_date": "report_draft_end_date",
    "review_date": "review_end_date",
}

# 核验清单：这些是「用户到底有没有正确上传」的判据
KEY_RECOGNITION_FIELDS = [
    ("project_name",          "项目名称"),
    ("doc_reference",         "公告/批复文号"),
    ("location",              "项目位置"),
    ("org_name",              "稳评责任单位"),
    ("area_mu",               "用地面积"),
    ("household_count",       "涉及户数"),
    ("total_samples",         "问卷份数"),
    ("support_rate",          "群众支持率"),
    ("compensation_standard", "补偿标准"),
    ("commission_date",       "委托日期"),
    ("notice_publish_date",   "公告发布日期"),
    ("notice_date",           "现场公示时间"),
    ("survey_date",           "实地勘察日期"),
    ("symposium_date",        "座谈会日期"),
    ("review_date",           "专家评审日期"),
]

# 中文 / 短横线 / 点分年份日期，可选「日」
_DATE_PAT = r'(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?'
_DATE_PAT_MD = r'(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?'


def _norm_date(y: int, mo: int, d: int) -> str:
    """归一化成 ISO 日期；不合法返回空串（宁可空着，也不要造一个错日期）。"""
    if not (1900 <= y <= 2200 and 1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _re_date_labeled(alias: str, text: str) -> Dict[str, str]:
    """在标签后 40 字内就近取日期；返回 {'start': ISO, 'end': ISO?}。"""
    for am in re.finditer(re.escape(alias), text):
        seg = text[am.end(): am.end() + 40]
        dm = re.search(_DATE_PAT, seg)
        if not dm:
            continue
        start = _norm_date(*(int(x) for x in dm.groups()))
        if not start:
            continue
        out: Dict[str, str] = {"start": start}
        # 区间：「至 / 到 / ~ / — / -」后跟第二个日期（可省略年份）
        rest = seg[dm.end(): dm.end() + 16]
        rm = re.match(r'\s*(?:至|到|—|–|~|－|-)\s*' + _DATE_PAT, rest)
        if rm:
            end = _norm_date(*(int(x) for x in rm.groups()))
        else:
            rm2 = re.match(r'\s*(?:至|到|—|–|~|－|-)\s*' + _DATE_PAT_MD, rest)
            end = _norm_date(int(start[:4]), int(rm2.group(1)), int(rm2.group(2))) if rm2 else ""
        if end and end >= start:
            out["end"] = end
        return out
    return {}


@dataclass
class MaterialArtifact:
    source_path: str
    source_name: str
    source_type: str
    scope: str
    title: str = ""
    text_content: str = ""
    retrieval_text: str = ""
    structured_data: Dict[str, Any] | None = None
    image_summary: str = ""
    analysis_type: str = "general"
    chunk_count: int = 0
    status: str = "completed"
    error: str = ""
    extraction_version: str = EXTRACTION_VERSION
    metadata: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["structured_data"] = data.get("structured_data") or {}
        data["metadata"] = data.get("metadata") or {}
        return data


class MaterialIngestionService:
    """Orchestrates material extraction across file types."""

    def __init__(self):
        from app.services.llm_service import llm_service

        self.pdf_extractor = PDFDataExtractor(llm_service=llm_service)

    async def ingest_material(
        self,
        relative_path: str,
        *,
        scope: str,
        title: str = "",
        document_type: str = "",
        domain: str = "stability",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = Path(relative_path)
        ext = path.suffix.lower()
        source_name = title or path.name
        meta = {
            "document_type": document_type or "other",
            "domain": domain,
            **(metadata or {}),
        }

        try:
            if ext == ".pdf":
                artifact = await self._ingest_pdf(relative_path, source_name, scope, meta)
            elif ext in _WORD_EXTENSIONS:
                artifact = await self._ingest_word(relative_path, source_name, scope, meta)
            elif ext in _TEXT_EXTENSIONS:
                artifact = self._ingest_text(relative_path, source_name, scope, meta)
            elif ext in _IMAGE_EXTENSIONS:
                artifact = await self._ingest_image(relative_path, source_name, scope, meta)
            else:
                artifact = self._ingest_generic(relative_path, source_name, scope, meta)
        except Exception as exc:
            artifact = MaterialArtifact(
                source_path=relative_path,
                source_name=source_name,
                source_type=self._infer_source_type(ext),
                scope=scope,
                title=source_name,
                status="failed",
                error=str(exc),
                metadata=meta,
            )

        return artifact.to_dict()

    async def ingest_many(
        self,
        relative_paths: List[str],
        *,
        scope: str,
        title_lookup: Optional[Dict[str, str]] = None,
        document_type: str = "",
        domain: str = "stability",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not relative_paths:
            return []

        concurrency = 2 if domain == "stability" else 3
        sem = asyncio.Semaphore(concurrency)

        async def _ingest_one(relative_path: str) -> Dict[str, Any]:
            async with sem:
                return await self.ingest_material(
                    relative_path,
                    scope=scope,
                    title=(title_lookup or {}).get(relative_path, ""),
                    document_type=document_type,
                    domain=domain,
                    metadata=metadata,
                )

        return await asyncio.gather(*(_ingest_one(path) for path in relative_paths))

    async def _ingest_pdf(self, relative_path: str, source_name: str, scope: str, metadata: Dict[str, Any]) -> MaterialArtifact:
        abs_path = str(file_service.get_absolute_path(relative_path))
        doc = await self.pdf_extractor.extract_pdf(abs_path)
        text_content = (doc.full_text or "").strip()
        structured_data = doc.key_data or {}

        # 🔴 Collect extracted images and tables for report reuse
        extracted_images = doc.extracted_images or []
        extracted_tables = doc.extracted_tables or []

        # Build image summary from extracted images and table data
        image_parts = []
        if extracted_images:
            image_parts.append(f"从PDF提取了{len(extracted_images)}张图片")
        if extracted_tables:
            table_names = []
            for t in extracted_tables:
                h = t.get("headers", [])
                if h:
                    table_names.append("、".join(h[:3]))
            image_parts.append(f"从PDF提取了{len(extracted_tables)}个表格: {'; '.join(table_names[:5])}")
        image_summary = "；".join(image_parts) if image_parts else ""

        retrieval_text = self._build_retrieval_text(
            title=source_name,
            source_type="pdf",
            text_content=text_content,
            structured_data=structured_data,
            image_summary=image_summary,
        )
        return MaterialArtifact(
            source_path=relative_path,
            source_name=source_name,
            source_type="pdf",
            scope=scope,
            title=source_name,
            text_content=text_content,
            retrieval_text=retrieval_text,
            structured_data=structured_data,
            image_summary=image_summary,
            analysis_type="general",
            chunk_count=max(len(doc.pages), 1),
            metadata={
                **metadata,
                "document_kind": doc.document_type or metadata.get("document_type") or "other",
                "fillable_paragraphs": doc.fillable_paragraphs or {},
                "total_pages": doc.total_pages,
                "extracted_images": extracted_images,
                "extracted_tables": extracted_tables,
            },
        )

    async def _ingest_word(self, relative_path: str, source_name: str, scope: str, metadata: Dict[str, Any]) -> MaterialArtifact:
        text_content = (file_service.extract_docx_text(relative_path) or "").strip()
        structured_data = self._extract_structured_fields(text_content)
        retrieval_text = self._build_retrieval_text(
            title=source_name,
            source_type="word",
            text_content=text_content,
            structured_data=structured_data,
            image_summary="",
        )
        return MaterialArtifact(
            source_path=relative_path,
            source_name=source_name,
            source_type="word",
            scope=scope,
            title=source_name,
            text_content=text_content,
            retrieval_text=retrieval_text,
            structured_data=structured_data,
            image_summary="",
            metadata=metadata,
        )

    def _ingest_text(self, relative_path: str, source_name: str, scope: str, metadata: Dict[str, Any]) -> MaterialArtifact:
        text_content = (file_service.read_text_file(relative_path) or "").strip()
        structured_data = self._extract_structured_fields(text_content)
        retrieval_text = self._build_retrieval_text(
            title=source_name,
            source_type="text",
            text_content=text_content,
            structured_data=structured_data,
            image_summary="",
        )
        return MaterialArtifact(
            source_path=relative_path,
            source_name=source_name,
            source_type="text",
            scope=scope,
            title=source_name,
            text_content=text_content,
            retrieval_text=retrieval_text,
            structured_data=structured_data,
            image_summary="",
            metadata=metadata,
        )

    async def _ingest_image(self, relative_path: str, source_name: str, scope: str, metadata: Dict[str, Any]) -> MaterialArtifact:
        analysis_type = self._infer_image_analysis_type(source_name, metadata.get("document_type", ""))
        result = await image_analyzer.analyze(relative_path, analysis_type=analysis_type)
        summary = self._summarize_image_result(result)
        retrieval_text = self._build_retrieval_text(
            title=source_name,
            source_type="image",
            text_content="",
            structured_data=result if isinstance(result, dict) else {},
            image_summary=summary,
        )
        status = "completed" if not result.get("error") else "failed"
        return MaterialArtifact(
            source_path=relative_path,
            source_name=source_name,
            source_type="image",
            scope=scope,
            title=source_name,
            text_content=result.get("raw_response", "") if isinstance(result, dict) else "",
            retrieval_text=retrieval_text,
            structured_data=result if isinstance(result, dict) else {},
            image_summary=summary,
            analysis_type=analysis_type,
            status=status,
            error=result.get("error", "") if isinstance(result, dict) else "",
            metadata=metadata,
        )

    def _ingest_generic(self, relative_path: str, source_name: str, scope: str, metadata: Dict[str, Any]) -> MaterialArtifact:
        text_content = ""
        try:
            text_content = (file_service.read_text_file(relative_path) or "").strip()
        except Exception:
            text_content = ""
        structured_data = self._extract_structured_fields(text_content)
        retrieval_text = self._build_retrieval_text(
            title=source_name,
            source_type=self._infer_source_type(Path(relative_path).suffix.lower()),
            text_content=text_content,
            structured_data=structured_data,
            image_summary="",
        )
        return MaterialArtifact(
            source_path=relative_path,
            source_name=source_name,
            source_type=self._infer_source_type(Path(relative_path).suffix.lower()),
            scope=scope,
            title=source_name,
            text_content=text_content,
            retrieval_text=retrieval_text,
            structured_data=structured_data,
            image_summary="",
            metadata=metadata,
        )

    def merge_project_facts(self, artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
        aggregated: Dict[str, Any] = {}
        all_images: List[str] = []
        all_tables: List[Dict[str, Any]] = []

        for artifact in artifacts:
            structured = artifact.get("structured_data") or {}
            if not isinstance(structured, dict):
                continue
            for key, value in structured.items():
                if value in (None, "", [], {}):
                    continue
                if key not in aggregated:
                    aggregated[key] = value

            # 🔴 Collect extracted images from PDF artifacts
            meta = artifact.get("metadata") or {}
            if isinstance(meta, dict):
                imgs = meta.get("extracted_images") or []
                if isinstance(imgs, list):
                    for img in imgs:
                        if img not in all_images:
                            all_images.append(img)
                tbls = meta.get("extracted_tables") or []
                if isinstance(tbls, list):
                    for tbl in tbls:
                        if tbl not in all_tables:
                            all_tables.append(tbl)

        aggregated["_extracted_images"] = all_images
        aggregated["_extracted_tables"] = all_tables
        return aggregated

    def summarize_analysis(self, artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {
            "total_files": len(artifacts),
            "completed_files": 0,
            "failed_files": 0,
            "by_type": {},
            "missing_fields": [],
            "facts": self.merge_project_facts(artifacts),
            "extracted_image_count": 0,
            "extracted_table_count": 0,
        }
        for artifact in artifacts:
            source_type = artifact.get("source_type", "file")
            summary["by_type"][source_type] = summary["by_type"].get(source_type, 0) + 1
            if artifact.get("status") == "failed":
                summary["failed_files"] += 1
            else:
                summary["completed_files"] += 1
        if not summary["facts"].get("location"):
            summary["missing_fields"].append("location")
        if not summary["facts"].get("responsible_unit"):
            summary["missing_fields"].append("responsible_unit")
        # Count extracted assets
        facts = summary["facts"]
        summary["extracted_image_count"] = len(facts.get("_extracted_images", []))
        summary["extracted_table_count"] = len(facts.get("_extracted_tables", []))
        return summary

    @staticmethod
    def copy_images_to_storage(source_paths: List[str], session_id: str = "") -> List[str]:
        """Copy local image files to storage/images for report embedding.

        Args:
            source_paths: List of absolute paths to local image files.
            session_id: Optional session ID for namespacing.

        Returns:
            List of relative paths (e.g., "images/xxx.jpg") for report use.
        """
        import shutil, uuid
        from app.config import settings

        images_dir = settings.STORAGE_DIR / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}

        for src in source_paths:
            src_path = Path(src)
            if not src_path.exists() or src_path.suffix.lower() not in image_exts:
                continue
            try:
                # Generate unique filename preserving original name hint
                safe_name = re.sub(r'[\\/:*?"<>|]', '_', src_path.stem[:30])
                ext = src_path.suffix.lower()
                unique_name = f"{safe_name}_{uuid.uuid4().hex[:8]}{ext}"
                dest = images_dir / unique_name

                if not dest.exists():
                    shutil.copy2(str(src_path), str(dest))

                relative = f"images/{unique_name}"
                saved.append(relative)
            except Exception as e:
                print(f"    ⚠️ Copy image failed: {src_path.name} — {e}")

        return saved

    def _build_retrieval_text(
        self,
        *,
        title: str,
        source_type: str,
        text_content: str,
        structured_data: Dict[str, Any],
        image_summary: str,
    ) -> str:
        sections = [f"标题：{title}", f"资料类型：{source_type}"]
        if structured_data:
            try:
                sections.append("结构化信息：" + json.dumps(structured_data, ensure_ascii=False))
            except TypeError:
                sections.append(f"结构化信息：{structured_data}")
        if image_summary:
            sections.append(f"图片摘要：{image_summary}")
        if text_content:
            sections.append(f"正文摘录：{text_content[:4000]}")
        return "\n\n".join(section for section in sections if section)

    def _extract_structured_fields(self, text: str) -> Dict[str, Any]:
        if not text:
            return {}
        # ── 文本型字段：按「标签：值」逐行抽取 ──────────────────────────────
        candidates = {
            "location": ["位置", "坐落", "地址", "地理位置"],
            "responsible_unit": ["责任单位", "征收主体", "实施单位", "稳评单位"],
            "project_name": ["项目名称", "决策名称", "报告标题"],
            "doc_reference": ["文号", "公告号", "批文号"],
            "area": ["面积", "征收面积", "用地面积"],
            # 🔴 用户上传的材料里常把关键结论单列成行（「群众支持率：98.5%」），
            #    这些值直接决定第 9 章（问卷调查情况）能不能写实，必须识别出来。
            "support_rate": ["群众支持率", "支持率", "赞成率"],
            "total_samples": ["问卷份数", "问卷数量", "发放问卷", "回收问卷", "调查问卷"],
            "household_count": ["涉及户数", "被征收户数", "征收户数", "户数"],
            "contact_name": ["联系人", "经办人"],
            "contact_phone": ["联系电话", "联系方式", "电话"],
            "compensation_standard": ["补偿标准", "补偿单价", "安置补偿标准"],
        }
        result: Dict[str, Any] = {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            for key, aliases in candidates.items():
                if key in result:
                    continue
                for alias in aliases:
                    if alias in line and ("：" in line or ":" in line):
                        parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                        if len(parts) == 2 and parts[1].strip():
                            result[key] = self._clean_field_value(key, parts[1].strip())
                            break

        # ── 日期字段：标签就近取日期，中文日期归一化为 ISO ────────────────
        # 🔴 日期是报告里最容易出错的地方（报告编制 / 现场公示 / 座谈会 / 评审…），
        #    原来一个日期都不识别，用户上传了材料也照样被弹窗逐项追问。
        #    这里只认**带明确标签**的日期，避免把勘测定界图上的无关日期串成字段。
        for key, aliases in _DATE_LABEL_ALIASES:
            if key in result:
                continue
            for alias in aliases:
                m = _re_date_labeled(alias, text)
                if not m:
                    continue
                result[key] = m["start"]
                # 同一处给的是区间（「2026年6月4日至6月10日」）→ 顺带补结束日
                if m.get("end") and key in _DATE_RANGE_PAIRS:
                    result.setdefault(_DATE_RANGE_PAIRS[key], m["end"])
                break

        # ── 数值兜底：整句正则（「支持率达 98.5%」这类不带冒号的写法）──────
        if "support_rate" not in result:
            m = re.search(r'(?:群众|公众|村民|被征地群众)?\s*支持率[^0-9%％]{0,8}(\d{1,3}(?:\.\d+)?)\s*[%％]', text)
            if m:
                result["support_rate"] = m.group(1)
        if "household_count" not in result:
            # 🔴 必须有**征收/涉及**语境才认 —— 裸「共 120 户」在村里是总户数，
            #    当「涉及户数」写进报告就是错的（错数字比空着更糟）。
            m = re.search(r'(?:涉及|被征收|征收|拟征收|需征收|搬迁)\s*(?:农户|居民|住户|户数)?\s*(\d{1,6})\s*户', text)
            if m:
                result["household_count"] = m.group(1)
        if "total_samples" not in result:
            # 🔴 同上：必须与**问卷/调查表**同现，避免「发放资料 500 份」被当成问卷数
            m = re.search(r'(?:发放|回收|收回|印制)[^。\n]{0,12}?(?:问卷|调查表)[^。\n]{0,8}?(\d{1,6})\s*份', text) \
                or re.search(r'(\d{1,6})\s*份\s*(?:有效|调查)?\s*问卷', text) \
                or re.search(r'(?:问卷|调查表)[^。\n]{0,10}?(\d{1,6})\s*份', text)
            if m:
                result["total_samples"] = m.group(1)
        if "doc_reference" not in result:
            # 文号（各机关都可能不同前缀）：如「洪拟征告〔2026〕10号」
            m = re.search(r'([\u4e00-\u9fff]{2,12}\s*[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*第?\s*\d{1,4}\s*号)', text)
            if m:
                result["doc_reference"] = re.sub(r'\s+', '', m.group(1))
        return result

    @staticmethod
    def _clean_field_value(key: str, raw: str) -> str:
        """抽出来的值做最小清洗：去尾部标点；数值字段只保留数字。

        🔴 一行里常并排多项（「联系人：张主任  联系电话：0517-12345678」），
        按冒号切会**把后面那项一起吞进来** —— 所以电话/人名要各自定位截取。
        """
        v = str(raw or "").strip().strip("。；;,，")
        if key in ("support_rate", "household_count", "total_samples"):
            m = re.search(r'(\d+(?:\.\d+)?)', v)
            if m:
                return m.group(1)
        if key == "contact_phone":
            m = re.search(r'(?:0\d{2,3}[-\s]?\d{7,8}|1[3-9]\d{9})', v)
            return m.group(0) if m else v
        if key in ("contact_name", "doc_reference", "project_name", "location",
                   "responsible_unit", "compensation_standard"):
            # 截到下一项标签之前（多空格 / 下一个「XX：」）
            v = re.split(r'\s{2,}|\s+(?=(?:联系人|联系电话|联系方式|电话|地址|备注)\s*[:：])', v)[0]
        return v.strip()

    @staticmethod
    def recognition_report(source: Dict[str, Any]) -> Dict[str, List[Any]]:
        """核验「关键信息有没有从上传材料里识别出来」。

        🔴 用户少传/传错时，系统此前**没有任何反馈** —— 一路生成到弹窗才逐项问，
        用户不知道是材料没传对还是系统没认出来。这里给出明确的两栏清单：
        `recognized`（已识别，含值）与 `missing`（材料里没认到，生成时会问）。
        """
        src = source or {}
        # 别名：材料侧键名 → 报告字段名
        _alias = {
            "area_mu": ("area_mu", "area"),
            "org_name": ("org_name", "responsible_unit"),
        }
        recognized, missing = [], []
        for key, label in KEY_RECOGNITION_FIELDS:
            _keys = _alias.get(key, (key,))
            val = ""
            for _k in _keys:
                _v = src.get(_k)
                if _v not in (None, "", [], {}):
                    val = _v
                    break
            if val:
                recognized.append({"key": key, "label": label, "value": str(val)[:40]})
            else:
                missing.append({"key": key, "label": label})
        return {"recognized": recognized, "missing": missing}

    def _summarize_image_result(self, result: Dict[str, Any]) -> str:
        if not isinstance(result, dict):
            return ""
        parts: List[str] = []
        for key in ("caption", "key_info", "scope", "responsible_unit", "main_opinions", "raw_response"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        if not parts:
            try:
                parts.append(json.dumps(result, ensure_ascii=False))
            except TypeError:
                pass
        return "；".join(parts[:3])[:2000]

    def _infer_image_analysis_type(self, source_name: str, document_type: str) -> str:
        lower_name = (source_name or "").lower()
        lower_doc_type = (document_type or "").lower()
        if any(keyword in lower_name for keyword in ("公告", "公示", "批文", "通知", "征收")) or lower_doc_type in {"regulation", "standard"}:
            return "notice"
        if any(keyword in lower_name for keyword in ("问卷", "调查", "统计", "意见", "表")) or lower_doc_type == "survey":
            return "survey"
        if any(keyword in lower_name for keyword in ("照片", "现场", "座谈", "走访", "会议")):
            return "photo"
        return "general"

    def _infer_source_type(self, ext: str) -> str:
        if ext == ".pdf":
            return "pdf"
        if ext in _WORD_EXTENSIONS:
            return "word"
        if ext in _TEXT_EXTENSIONS:
            return "text"
        if ext in _IMAGE_EXTENSIONS:
            return "image"
        return "file"


material_ingestion_service = MaterialIngestionService()
