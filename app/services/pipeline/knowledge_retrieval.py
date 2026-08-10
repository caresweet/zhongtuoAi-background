"""Phase 1: Multi-modal knowledge retrieval.

Given user project materials and a region identifier:
1. Parse uploaded files → PipelineContext (project data extraction)
2. RAG query for local standards and example reports
3. Assemble GenerationRecipe for the pipeline
"""

import re
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.services.pipeline.pipeline_context import PipelineContext, GenerationRecipe


class KnowledgeRetrievalService:
    """Phase 1: Region-aware, multi-modal knowledge retrieval."""

    def __init__(self, retriever=None, cross_modal_retriever=None):
        """Initialize with retriever services.

        Args:
            retriever: RetrieverService instance (text-only).
            cross_modal_retriever: CrossModalRetriever instance (multi-modal).
        """
        self.retriever = retriever
        self.cross_modal = cross_modal_retriever or retriever

    # ── Main entry point ──

    async def retrieve(
        self,
        materials_dir: str,
        region: str = "",
        session_id: str = "",
    ) -> GenerationRecipe:
        """Run Phase 1: extract project data and find matching knowledge.

        Args:
            materials_dir: Path to the 稳评资料 folder.
            region: Region identifier (e.g. "淮安市洪泽区").
            session_id: Session ID for project materials lookup.

        Returns:
            GenerationRecipe with project context and knowledge references.
        """
        # Step 1: Parse user materials
        context = await self._parse_materials(materials_dir, region)

        # Step 2: Retrieve local standards and examples
        if self.cross_modal and region:
            await self._retrieve_knowledge(context, region, session_id)

        # Step 3: Find template and example report paths
        template_path, example_path = await self._find_templates(context, materials_dir)

        return GenerationRecipe(
            standard_docs=[],  # Will be populated by Phase 2 from RAG results
            example_report_path=example_path,
            template_path=template_path,
            project_context=context,
        )

    # ── Material parsing ──

    async def _parse_materials(
        self, materials_dir: str, region: str
    ) -> PipelineContext:
        """Parse user-provided materials into structured PipelineContext."""
        context = PipelineContext(region=region)
        base = Path(materials_dir)

        if not base.exists():
            print(f"  ⚠️ Materials directory not found: {materials_dir}")
            return context

        # Scan all files in the directory and subdirectories
        all_files = list(base.rglob("*"))
        pdf_files = [f for f in all_files if f.suffix.lower() == ".pdf"]
        image_files = [f for f in all_files if f.suffix.lower() in
                       {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}]

        # Collect image files by folder
        image_folders: Dict[str, List[str]] = {}
        for img in image_files:
            folder_name = img.parent.name or "root"
            if folder_name not in image_folders:
                image_folders[folder_name] = []
            image_folders[folder_name].append(str(img))
        context.image_folders = image_folders
        context.image_files = [str(f) for f in image_files]

        # Extract text from PDFs
        for pdf_path in pdf_files:
            try:
                text = self._extract_pdf_text(str(pdf_path))
                context.extracted_texts[pdf_path.name] = text

                # Try to extract project data from the text
                self._extract_project_data(context, text, pdf_path.name)
            except Exception as e:
                print(f"  ⚠️ Failed to parse PDF {pdf_path.name}: {e}")

        # Auto-detect region from text if not provided
        if not context.region and context.extracted_texts:
            all_text = " ".join(context.extracted_texts.values())
            context.region = self._detect_region(all_text)

        return context

    def _extract_pdf_text(self, pdf_path: str) -> str:
        """Extract text from a PDF file."""
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                texts = []
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        texts.append(t)
                return "\n".join(texts)
        except ImportError:
            return ""
        except Exception:
            return ""

    def _extract_project_data(
        self, context: PipelineContext, text: str, filename: str
    ) -> None:
        """Extract structured project data from PDF text using regex patterns."""
        # ── Project name / document reference ──
        # Match patterns like 洪拟征告〔2026〕7号
        ref_match = re.search(r'([一-鿿]+〔?\d{4}〕?\d+号)', text)
        if ref_match:
            context.doc_reference = ref_match.group(1)
        if not context.doc_reference:
            ref_match = re.search(r'(\d{4}〕?\d+号)', text)
            if ref_match:
                context.doc_reference = ref_match.group(1)

        # ── Decision name / Project name ──
        name_match = re.search(r'项目名称[：:\s]*([^。\n]{5,60})', text)
        if name_match:
            context.project_name = name_match.group(1).strip()
        if not context.project_name:
            # Try to derive from doc reference
            if context.doc_reference:
                context.project_name = f"{context.doc_reference}片区开发地块项目"

        if not context.decision_name:
            if context.project_name:
                context.decision_name = f"{context.project_name}土地征收决策"

        # ── Land area ──
        # Multiple patterns for different PDF formats
        area_patterns = [
            r'(?:总面积|征收面积|用地面积|征地面积)[：:\s]*(\d[\d,.]*\s*\.?\d*)\s*(?:平方米|㎡|m2)',
            r'(\d[\d,.]{3,})\s*(?:平方米|㎡)',
            r'(?:面积|合计)[：:\s]*(\d[\d,.]{3,})',
        ]
        for pat in area_patterns:
            area_match = re.search(pat, text)
            if area_match:
                area_str = area_match.group(1).replace(",", "").replace(" ", "")
                try:
                    val = float(area_str)
                    if val > 100:  # Reasonable minimum
                        context.land_area_sqm = val
                        context.land_area_mu = round(val / 666.67, 2)
                        break
                except ValueError:
                    continue

        # ── Location ──
        loc_patterns = [
            r'(?:土地坐落|坐落|位置|位于)[：:\s]*([^。\n]{5,50}(?:街道|镇|乡)[^。\n]{0,30}(?:社区|村|组)?)',
            r'((?:淮安|洪泽|金湖|盱眙|涟水|南京|南通)[^。\n]{2,15}(?:街道|镇)[^。\n]{2,20}(?:社区|村|组)?)',
            r'((?:朱坝|戴楼|牌楼|三圩)[^。\n]{0,10}(?:街道|社区|村|组))',
        ]
        for pat in loc_patterns:
            loc_match = re.search(pat, text)
            if loc_match:
                context.land_location = loc_match.group(1).strip()
                break

        # ── Land use ──
        use_match = re.search(r'(?:土地用途|规划用途|用途)[：:\s]*([^。\n]{3,20}(?:用地))', text)
        if use_match:
            context.land_use = use_match.group(1).strip()
        if not context.land_use:
            use_match = re.search(r'(商业服务业|工矿仓储|住宅|交通运输|公共管理)[^。\n]{0,5}用地', text)
            if use_match:
                context.land_use = use_match.group(0).strip()

        # ── Announcement dates ──
        date_patterns = [
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})',
        ]
        for pat in date_patterns:
            dates = re.findall(pat, text)
            if dates:
                context.announcement_date = f"{dates[0][0]}-{dates[0][1]}-{dates[0][2]}"
                if len(dates) >= 2:
                    context.announcement_period = (
                        f"{dates[0][0]}.{dates[0][1]}.{dates[0][2]}"
                        f"-{dates[-1][1]}.{dates[-1][2]}"
                    )
                break

        # ── Survey data ──
        survey_match = re.search(r'(?:发放|调查|回收)[^\d]*(\d+)\s*份', text)
        if survey_match:
            context.public_survey_total = int(survey_match.group(1))

        support_match = re.search(r'(?:支持|同意)[^\d]*(\d+)\s*(?:份|人)', text)
        if support_match:
            try:
                context.public_survey_support = int(support_match.group(1))
            except ValueError:
                pass

        # Calculate support rate
        if context.public_survey_total > 0 and context.public_survey_support > 0:
            context.public_survey_support_rate = (
                context.public_survey_support / context.public_survey_total * 100
            )

        # ── Decision unit (responsible party) ──
        unit_match = re.search(
            r'(?:责任单位|实施单位|委托单位|稳评责任单位)[：:\s]*'
            r'([^。\n]{5,40}(?:委员会|政府|局|公司))',
            text
        )
        if unit_match:
            context.decision_unit = unit_match.group(1).strip()

        # ── Boundary points ──
        bp_match = re.search(r'界址点[：:\s]*(\d+)\s*个', text)
        if bp_match:
            try:
                context.boundary_points = int(bp_match.group(1))
            except ValueError:
                pass

        # ── Fallback: extract from filename ──
        self._extract_from_filename(context, filename)

    def _extract_from_filename(
        self, context: PipelineContext, filename: str
    ) -> None:
        """Extract data from PDF filename as fallback for scanned documents."""
        # Doc reference from filename: e.g. "洪拟征告〔2026〕7号"
        ref_match = re.search(r'([一-鿿]+〔?\d{4}〕?\d+号)', filename)
        if ref_match and not context.doc_reference:
            context.doc_reference = ref_match.group(1)

    def _detect_region(self, text: str) -> str:
        """Auto-detect region from document text."""
        regions = [
            ("淮安市洪泽区", ["洪泽", "朱坝", "三圩"]),
            ("淮安市金湖县", ["金湖", "戴楼", "牌楼"]),
            ("淮安市盱眙县", ["盱眙"]),
            ("南京市六合区", ["六合", "龙华"]),
            ("南京市浦口区", ["浦口"]),
            ("南通市", ["南通", "崇川", "通州"]),
        ]
        for region, keywords in regions:
            if any(kw in text for kw in keywords):
                return region
        return ""

    # ── Knowledge retrieval ──

    async def _retrieve_knowledge(
        self, context: PipelineContext, region: str, session_id: str
    ) -> None:
        """Retrieve relevant standards and examples from knowledge base."""
        try:
            # Use cross-modal retriever if available
            if hasattr(self.cross_modal, 'retrieve_standards_by_region'):
                result = await self.cross_modal.retrieve_standards_by_region(region)
                if isinstance(result, dict):
                    context.standard_context = result.get("region_context", "")
                    context.rag_sources = result.get("sources", [])
                else:
                    # MultiModalRetrievalResult
                    context.standard_context = result.combined_text
                    context.rag_sources = [
                        {"file": s, "type": ""} for s in result.text_sources
                    ]
            elif hasattr(self.retriever, 'retrieve_standards_by_region'):
                result = await self.retriever.retrieve_standards_by_region(region)
                context.standard_context = result.get("region_context", "")
                context.rag_sources = result.get("sources", [])
        except Exception as e:
            print(f"  ⚠️ Knowledge retrieval failed: {e}")

    async def _find_templates(
        self, context: PipelineContext, materials_dir: str
    ) -> tuple:
        """Find the best template and example report for this project."""
        template_path = ""
        example_path = ""

        # Check templates database
        try:
            from app.database.knowledge import get_active_templates
            templates = await get_active_templates()
            if templates:
                # Prefer templates matching the region
                for tpl in templates:
                    tpl_name = (tpl.get("name") or "").lower()
                    tpl_cat = (tpl.get("category") or "").lower()
                    region_lower = context.region.lower() if context.region else ""

                    # Best match: same region
                    if any(r in tpl_name for r in region_lower.split("区")[0:1]):
                        template_path = tpl.get("template_file_path", "")
                        example_path = tpl.get("example_file_path", "")
                        break

                # Fallback: any active template
                if not template_path and templates:
                    template_path = templates[0].get("template_file_path", "")
        except ImportError:
            pass
        except Exception as e:
            print(f"  ⚠️ Template lookup failed: {e}")

        # Fallback: use known template paths
        if not template_path:
            storage = Path(__file__).resolve().parent.parent.parent.parent / "storage"
            template_dir = storage / "templates"
            if template_dir.exists():
                docx_files = list(template_dir.glob("*.docx"))
                if docx_files:
                    template_path = str(docx_files[0])

        return template_path, example_path
