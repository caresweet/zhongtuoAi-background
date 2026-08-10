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
        candidates = {
            "location": ["位置", "坐落", "地址", "地理位置"],
            "responsible_unit": ["责任单位", "征收主体", "实施单位", "稳评单位"],
            "project_name": ["项目名称", "决策名称", "报告标题"],
            "doc_reference": ["文号", "公告号", "批文号"],
            "area": ["面积", "征收面积", "用地面积"],
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
                            result[key] = parts[1].strip()
                            break
        return result

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
