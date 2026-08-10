"""Pydantic schemas for Dashboard API."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_reports: int = 0
    completed_reports: int = 0
    failed_reports: int = 0
    in_progress_reports: int = 0
    total_templates: int = 0
    reports_this_month: int = 0
    avg_generation_time_sec: Optional[float] = None


class RecentReport(BaseModel):
    id: int
    title: str
    template_name: Optional[str] = None
    status: str
    created_at: datetime


class DailyTrend(BaseModel):
    date: str
    count: int
    completed: int
