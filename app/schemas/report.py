"""Pydantic schemas for Report Generation API."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class StartGenerationRequest(BaseModel):
    template_id: Optional[int] = None
    initial_message: str = Field(..., min_length=1, description="用户初始需求描述（项目基本信息）")
    intent: str = Field(default="", description="用户选择的意图类型: stability | bidding | question | table | image_analysis")
    domain: str = Field(default="", description="报告领域: stability | bidding")
    style: str = Field(default="jinhu", description="报告风格: jinhu(金湖模板) | standard(DB32标准格式)")


class StartGenerationResponse(BaseModel):
    session_id: str
    status: str
    agent_message: str
    domain: str = "stability"
    template_name: Optional[str] = None
    next_placeholder: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    message: str = Field(default="")
    attachments: Optional[List[str]] = None
    folder_structure: Optional[dict] = None  # Folder upload tree structure
    current_step: Optional[int] = Field(None, ge=1, le=12, description="当前 StepWizard 步骤 (1-12)")
    intent: str = Field(default="", description="用户选择的意图类型: stability | bidding | question | table | image_analysis")
    domain: str = Field(default="", description="报告领域: stability | bidding")


class ChatResponse(BaseModel):
    session_id: str
    status: str
    agent_message: Optional[str] = None
    current_section: Optional[int] = None
    total_sections: Optional[int] = None
    section_title: Optional[str] = None


class GenerationStatusResponse(BaseModel):
    session_id: str
    status: str
    current_section_index: Optional[int] = None
    total_sections: Optional[int] = None
    total_placeholders: Optional[int] = None
    filled_placeholders: Optional[int] = None
    section_progress: Optional[List[object]] = None
    last_activity: Optional[str] = None
    report_file_path: Optional[str] = None
    download_url: Optional[str] = None


class SkipResponse(BaseModel):
    skipped_key: str
    value: str = "需后期提供"
    agent_message: str


class ActionResponse(BaseModel):
    success: bool = True
    message: str = ""
    session_id: str


# ═══════════════════════════════════════════════════════════════════════════════
# Chapter-by-Chapter Generation Schemas
# ═══════════════════════════════════════════════════════════════════════════════

class ChapterConfirmRequest(BaseModel):
    """Request to confirm/revise/skip a generated chapter."""
    chapter: int = Field(..., ge=1, le=10)
    action: str = Field(..., description="approve | revise | skip")
    revision_text: Optional[str] = Field(None, description="Revision instructions (required for revise)")


class ChapterDataRequest(BaseModel):
    """Request to provide missing data for a chapter."""
    chapter: int = Field(..., ge=1, le=10)
    data: Dict[str, str] = Field(default_factory=dict, description="Key-value pairs of missing data")


class ChapterStatusResponse(BaseModel):
    """Response with a chapter's current status and content."""
    chapter: int
    title: str
    status: str  # pending | generating | review | approved | revised
    markdown: str = ""
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    revision_history: List[Dict[str, Any]] = Field(default_factory=list)
    confirmed_at: Optional[str] = None


class ChapterListResponse(BaseModel):
    """Response with all chapters' status."""
    session_id: str
    generation_mode: str
    current_chapter: int
    chapters: Dict[int, ChapterStatusResponse] = Field(default_factory=dict)


class ChapterGenerateRequest(BaseModel):
    """Request to manually trigger generation of a specific chapter."""
    chapter: int = Field(..., ge=1, le=10)


# ═══════════════════════════════════════════════════════════════
# Workflow API — 请求/响应强类型模型
# ═══════════════════════════════════════════════════════════════

class WorkflowStartRequest(BaseModel):
    """启动报告生成工作流。"""
    materials_dir: str = Field(default="", description="资料目录路径（可选）")
    project_context: str = Field(default="", description="项目背景说明")
    report_title: str = Field(default="社会稳定风险评估报告", description="报告标题")


class WorkflowResumeRequest(BaseModel):
    """用户补充缺失字段后继续生成。"""
    data: Dict[str, str] = Field(default_factory=dict, description="用户填写的缺失字段 {key: value}")


class MissingField(BaseModel):
    """缺失字段描述。"""
    model_config = {"extra": "allow"}  # 容忍 description/category 等额外字段
    key: str
    label: str = ""
    desc: str = ""
    example: str = ""


class WorkflowStatusData(BaseModel):
    """工作流状态响应的 data 部分（强类型）。"""
    phase: str = Field(..., description="idle | generating | paused | human_review | complete | error")
    running: bool = False
    paused: bool = False
    error: Optional[str] = ""
    total_chapters: int = 0
    current_chapter: int = 0
    missing_fields: List[MissingField] = Field(default_factory=list)
    # 🔴 人工复核队列（phase=human_review 时由前端展示待复核章节+违规）
    human_queue: List[int] = Field(default_factory=list)
    human_items: Dict[str, Any] = Field(default_factory=dict)
    chapter_audits: Dict[str, Any] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    step_statuses: Dict[str, Any] = Field(default_factory=dict)
    output_path: Optional[str] = ""
    download_url: Optional[str] = ""

