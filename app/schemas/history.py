"""Pydantic schemas for History Reports API."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    message_type: str = "text"
    metadata_json: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportBase(BaseModel):
    title: str
    template_id: Optional[int] = None
    template_name: Optional[str] = None


class ReportResponse(ReportBase):
    id: int
    session_id: str
    status: str
    error_message: Optional[str] = None
    report_file_path: Optional[str] = None
    generation_duration_sec: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReportDetailResponse(ReportResponse):
    filled_data_json: Optional[str] = None
    section_progress_json: Optional[str] = None
    conversation_json: Optional[str] = None
    messages: List[MessageResponse] = []


class ReportListResponse(BaseModel):
    items: List[ReportResponse]
    total: int
    page: int
    page_size: int


class ReportDataResponse(BaseModel):
    report_id: int
    title: str
    filled_data: Optional[object] = None
    section_progress: Optional[object] = None
