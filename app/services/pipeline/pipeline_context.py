"""Shared data classes for the report generation pipeline.

All phases communicate through these dataclasses, enabling clean
separation of concerns and phase-by-phase state persistence.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


# ── Section Classification ──

class SectionType(str, Enum):
    PRESERVED = "preserved"              # Keep as-is (license, certificates, team)
    TEMPLATE_FILL = "template_fill"      # Has placeholders → fill with project data
    AGENT_GENERATE = "agent_generate"    # Body content → LLM generate
    TABLE_ONLY = "table_only"            # Section whose body is just tables
    IMAGE_REQUIRED = "image_required"    # Section that needs images


# ── Pipeline Context (Phase 1 output) ──

@dataclass
class PipelineContext:
    """All project data extracted from user materials."""
    # Core project info
    region: str = ""                      # e.g. "淮安市洪泽区"
    province: str = "江苏省"               # e.g. "江苏省"
    project_name: str = ""                # e.g. "洪拟征告〔2026〕7号片区开发地块项目"
    doc_reference: str = ""               # e.g. "洪拟征告〔2026〕7号"
    decision_name: str = ""               # e.g. "...土地征收决策"
    decision_unit: str = ""               # e.g. "江苏洪泽经济开发区管理委员会"
    implementation_unit: str = "江苏众拓项目代理咨询有限公司"

    # Location & scale
    land_location: str = ""               # e.g. "朱坝街道三圩社区"
    land_area_sqm: float = 0.0            # Total area in ㎡
    land_area_mu: float = 0.0             # Total area in 亩
    land_use: str = ""                    # e.g. "商业服务业设施用地"
    boundary_points: int = 0

    # Timeline
    announcement_date: str = ""           # e.g. "2026-06-27"
    announcement_period: str = ""         # e.g. "2026.6.27-2026.7.3"
    survey_date: str = ""                 # e.g. "2026-07-03"
    symposium_date: str = ""
    symposium_location: str = ""

    # Survey data
    public_survey_total: int = 0
    public_survey_support: int = 0
    public_survey_oppose: int = 0
    public_survey_support_rate: float = 0.0
    unit_survey_total: int = 0

    # Risk assessment
    pre_measure_score: float = 0.0
    post_measure_score: float = 0.0
    risk_level: str = "低风险"

    # Budget
    total_budget: str = ""                # e.g. "约2800万元"

    # Raw extracted data
    land_classification: List[List[str]] = field(default_factory=list)
    survey_details: Dict[str, Any] = field(default_factory=dict)
    extracted_texts: Dict[str, str] = field(default_factory=dict)  # filename → text
    fillable_paragraphs: Dict[str, str] = field(default_factory=dict)  # PDF paragraphs → report
    image_folders: Dict[str, List[str]] = field(default_factory=dict)  # folder → [paths]
    image_files: List[str] = field(default_factory=list)  # All image paths flat

    # RAG results
    standard_context: str = ""
    example_context: str = ""
    local_regulation_context: str = ""
    rag_sources: List[Dict[str, str]] = field(default_factory=list)


# ── Document Structure (Phase 2 output) ──

@dataclass
class ParagraphInfo:
    """Info about a single paragraph in the document."""
    para_index: int
    style: str
    text_preview: str
    heading_level: int = 0
    section_index: int = -1


@dataclass
class SectionDef:
    """A document section defined by heading boundaries."""
    index: int                           # 0-based index in document
    title: str                           # Heading text
    level: int                           # 1=章, 2=节, 3=小节
    start_para_index: int
    end_para_index: int
    section_type: SectionType = SectionType.AGENT_GENERATE
    paragraphs: List[ParagraphInfo] = field(default_factory=list)
    sub_sections: List[str] = field(default_factory=list)  # Sub-heading texts


@dataclass
class TableDef:
    """A table in the document."""
    table_index: int
    rows: int
    cols: int
    section_index: int = -1              # Which section it belongs to
    caption: str = ""                     # Table caption text
    table_type: str = ""                  # "copy_full" | "copy_n_minus_1" | "agent_generate"
    fill_column: int = -1                 # 0-based index of column to agent-fill (-1 = none)
    preview: List[List[str]] = field(default_factory=list)


@dataclass
class ImageSlot:
    """A position in the document where an image should be placed.

    Uses section-relative positioning instead of absolute paragraph index.
    """
    slot_id: str                         # e.g. "img_ch4_location_map"
    section_title: str                   # e.g. "第四章 决策综合分析"
    section_index: int                   # 0-based index in DocumentStructure.sections
    position_type: str = "before_caption"  # "before_caption" | "after_heading" | "inline"
    anchor_text: str = ""                # Anchor text (caption or heading) for positioning
    relative_offset: int = -1            # Offset from anchor (-1 = before, +1 = after)
    caption_template: str = ""           # Template for auto-generated caption
    suggested_type: str = ""             # "location_map" | "site_photo" | "notice" | "meeting" | "attachment"
    priority: int = 1                    # 1=required, 2=optional
    matched_image: Optional[str] = None  # Filled by Phase 3: path to matched image

    def resolve(self, doc_structure: "DocumentStructure") -> Optional[int]:
        """Resolve this slot to an absolute paragraph index."""
        if self.section_index < 0 or self.section_index >= len(doc_structure.sections):
            return None
        section = doc_structure.sections[self.section_index]
        # Search for anchor text within section paragraphs
        for para in section.paragraphs:
            if self.anchor_text and self.anchor_text in para.text_preview:
                return para.para_index + self.relative_offset
        # Fallback: position relative to section start
        return section.start_para_index + max(1, self.relative_offset)


@dataclass
class DocumentStructure:
    """Complete structural analysis of a template document."""
    sections: List[SectionDef] = field(default_factory=list)
    tables: List[TableDef] = field(default_factory=list)
    image_slots: List[ImageSlot] = field(default_factory=list)
    total_paragraphs: int = 0
    total_sections: int = 0
    template_path: str = ""
    example_path: str = ""


# ── Generation Recipe (Phase 1 output) ──

@dataclass
class GenerationRecipe:
    """The complete recipe for generating a report."""
    standard_docs: List[str] = field(default_factory=list)
    example_report_path: str = ""
    template_path: str = ""
    project_context: PipelineContext = field(default_factory=PipelineContext)
    doc_structure: Optional[DocumentStructure] = None


# ── Gap Analysis (Phase 6) ──

@dataclass
class Gap:
    """A detected gap between generated report and template."""
    gap_type: str                        # "missing_section" | "empty_paragraph" | "missing_table_row" | "missing_image"
    section_path: List[str] = field(default_factory=list)  # e.g. ["第四章", "（一）合法性分析"]
    description: str = ""
    severity: str = "warning"            # "critical" | "warning" | "info"
    fill_strategy: str = "agent_generate"  # "agent_generate" | "copy_from_example" | "fill_from_project_data"
    para_index: Optional[int] = None
    table_index: Optional[int] = None


# ── Quality Report (Phase 7) ──

@dataclass
class QualityIssue:
    """A single quality issue found."""
    category: str                        # "format" | "content" | "completeness" | "compliance"
    description: str
    location: str = ""                   # e.g. "段落 P42" or "表3-1"
    severity: str = "warning"
    suggestion: str = ""


@dataclass
class QualityReport:
    """Comprehensive quality assessment of a generated report."""
    # Coverage
    sections_total: int = 0
    sections_filled: int = 0
    sections_missing: int = 0
    paragraphs_total: int = 0
    paragraphs_filled: int = 0
    paragraphs_empty: int = 0
    tables_total: int = 0
    tables_fully_filled: int = 0
    tables_partial: int = 0
    images_expected: int = 0
    images_filled: int = 0
    images_missing: int = 0

    # Scores
    completeness_pct: float = 0.0
    format_compliance_pct: float = 0.0

    # Details
    issues: List[QualityIssue] = field(default_factory=list)
    gaps: List[Gap] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.completeness_pct >= 95.0 and self.format_compliance_pct >= 90.0
