"""Pydantic schemas for Knowledge Base API."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ---- Placeholder ----
class PlaceholderBase(BaseModel):
    placeholder_key: str
    display_name: Optional[str] = None
    section_index: Optional[int] = None
    section_title: Optional[str] = None
    paragraph_index: Optional[int] = None
    run_index: Optional[int] = None
    expected_type: str = "text"
    expected_format: Optional[str] = None
    options_json: Optional[str] = None
    description: Optional[str] = None
    is_required: bool = True
    default_value: str = "需后期提供"
    sort_order: int = 0


class PlaceholderCreate(PlaceholderBase):
    pass


class PlaceholderResponse(PlaceholderBase):
    id: int
    template_id: int

    model_config = {"from_attributes": True}


# ---- Template ----
class TemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = ""
    category: str = Field(default="通用", max_length=100)
    domain: str = Field(default="stability", max_length=50)


class TemplateCreate(TemplateBase):
    pass


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    domain: Optional[str] = None


class TemplateResponse(TemplateBase):
    id: int
    template_file_path: str
    example_file_path: Optional[str] = None
    analysis_status: str = "pending"
    file_size: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    is_active: bool = True

    model_config = {"from_attributes": True}


class TemplateDetailResponse(TemplateResponse):
    placeholders: List[PlaceholderResponse] = []
    placeholders_json: Optional[str] = None
    sections_json: Optional[str] = None


class TemplateListResponse(BaseModel):
    items: List[TemplateResponse]
    total: int
    page: int
    page_size: int


# ---- Knowledge Document (RAG) ----
class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    document_type: str = Field(default="regulation", max_length=50)  # regulation, standard, example_report


class KnowledgeDocumentResponse(BaseModel):
    id: int
    title: str
    document_type: str
    domain: str = "stability"
    file_path: str
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    indexed_status: str = "pending"
    chunk_count: Optional[int] = 0
    index_error: Optional[str] = None
    extraction_status: Optional[str] = "pending"
    extraction_version: Optional[str] = None
    extracted_text: Optional[str] = None
    retrieval_text: Optional[str] = None
    structured_data_json: Optional[str] = None
    image_summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_active: bool = True

    model_config = {"from_attributes": True}


class KnowledgeDocumentListResponse(BaseModel):
    items: List[KnowledgeDocumentResponse]
    total: int
    page: int
    page_size: int


# ---- API Envelope ----
class ApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[object] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Data Cleaning Workbench Schemas
# ═══════════════════════════════════════════════════════════════════════════════

class CleanConfigRequest(BaseModel):
    """User-configurable cleaning rule toggles."""
    remove_headers_footers: bool = True
    remove_page_numbers: bool = True
    remove_watermarks: bool = True
    normalize_dates: bool = True
    normalize_punctuation: bool = False
    compress_blank_lines: bool = True
    table_linearization: bool = False
    sensitive_masking: bool = False
    custom_regex: Optional[str] = None
    custom_replacement: Optional[str] = None
    chunk_strategy: str = "paragraph"  # paragraph, fixed_512, fixed_1024


class CleanPreviewResponse(BaseModel):
    """Side-by-side preview: raw vs cleaned text with stats."""
    raw_text: str
    cleaned_text: str
    raw_chars: int
    cleaned_chars: int
    removed_chars: int
    token_estimate: int  # rough estimate for cost prediction
    issues_found: List[dict] = []  # [{type, line, description, severity}]


class CleanApplyRequest(BaseModel):
    """Confirm cleaning: save config + cleaned text."""
    config: CleanConfigRequest
    cleaned_text: Optional[str] = None  # if None, re-run pipeline


class CleanStatusResponse(BaseModel):
    """Current cleaning status for a document."""
    document_id: int
    clean_status: str  # raw | cleaning | cleaned | confirmed
    has_raw_text: bool
    has_cleaned_text: bool
    clean_config: Optional[dict] = None
    stats: Optional[dict] = None  # {raw_chars, cleaned_chars, token_estimate}
