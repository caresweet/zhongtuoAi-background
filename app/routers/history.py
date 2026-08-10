"""History Reports API routes — /api/v1/history/*"""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.history_db import get_history_db
from app.models.history import Report, ConversationMessage
from app.schemas.history import (
    ReportResponse, ReportDetailResponse, ReportListResponse, ReportDataResponse,
    MessageResponse,
)
from app.schemas.knowledge import ApiResponse
from app.services.file_service import file_service

router = APIRouter(prefix="/api/v1/history", tags=["历史报告"])


@router.get("/reports", response_model=ApiResponse)
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_history_db),
):
    """获取历史报告列表（分页）。"""
    query = select(Report)

    if status:
        query = query.where(Report.status == status)
    if search:
        query = query.where(
            or_(
                Report.title.contains(search),
                Report.template_name.contains(search),
            )
        )

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(desc(Report.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    reports = list(result.scalars().all())

    items = [ReportResponse.model_validate(r).model_dump() for r in reports]
    return ApiResponse(
        data=ReportListResponse(
            items=items, total=total, page=page, page_size=page_size
        ).model_dump(),
    )


@router.get("/reports/{report_id}", response_model=ApiResponse)
async def get_report(
    report_id: int,
    db: AsyncSession = Depends(get_history_db),
):
    """获取报告详情。"""
    query = (
        select(Report)
        .where(Report.id == report_id)
        .options(selectinload(Report.messages))
    )
    result = await db.execute(query)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    return ApiResponse(data=ReportDetailResponse.model_validate(report).model_dump())


@router.get("/reports/{report_id}/data", response_model=ApiResponse)
async def get_report_data(
    report_id: int,
    db: AsyncSession = Depends(get_history_db),
):
    """获取报告的填充数据。"""
    query = select(Report).where(Report.id == report_id)
    result = await db.execute(query)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    filled_data = None
    section_progress = None
    if report.filled_data_json:
        try:
            filled_data = json.loads(report.filled_data_json)
        except json.JSONDecodeError:
            pass
    if report.section_progress_json:
        try:
            section_progress = json.loads(report.section_progress_json)
        except json.JSONDecodeError:
            pass

    return ApiResponse(
        data=ReportDataResponse(
            report_id=report.id,
            title=report.title,
            filled_data=filled_data,
            section_progress=section_progress,
        ).model_dump(),
    )


@router.get("/reports/{report_id}/conversation", response_model=ApiResponse)
async def get_report_conversation(
    report_id: int,
    db: AsyncSession = Depends(get_history_db),
):
    """获取报告的对话记录。"""
    query = (
        select(ConversationMessage)
        .where(ConversationMessage.report_id == report_id)
        .order_by(ConversationMessage.created_at)
    )
    result = await db.execute(query)
    messages = list(result.scalars().all())

    return ApiResponse(
        data=[MessageResponse.model_validate(m).model_dump() for m in messages],
    )


@router.delete("/reports/{report_id}", response_model=ApiResponse)
async def delete_report(
    report_id: int,
    db: AsyncSession = Depends(get_history_db),
):
    """删除报告记录及其文件。"""
    query = select(Report).where(Report.id == report_id)
    result = await db.execute(query)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    # Delete file
    if report.report_file_path:
        file_service.delete_file(report.report_file_path)

    # Delete DB record
    await db.delete(report)
    await db.flush()

    return ApiResponse(message="报告已删除", data={"deleted": True})


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: int,
    db: AsyncSession = Depends(get_history_db),
):
    """下载生成的报告docx文件。"""
    query = select(Report).where(Report.id == report_id)
    result = await db.execute(query)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if not report.report_file_path:
        raise HTTPException(status_code=404, detail="报告文件不存在")

    file_path = file_service.get_absolute_path(report.report_file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")

    safe_title = report.title.replace("/", "_").replace("\\", "_")[:50]
    return FileResponse(
        path=str(file_path),
        filename=f"{safe_title}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/reports/{report_id}/review-table")
async def download_review_table(
    report_id: int,
    db: AsyncSession = Depends(get_history_db),
):
    """下载独立生成的评审表docx文件。

    评审表与主报告分离，包含：
    - 决策事项基本情况表
    - 表3-1 公众意见调查分析表
    - 表3-3 部门意见调查分析表
    - 表3-4 利益相关者意见汇总表
    - 表6-2 措施前风险等级量化评分表
    - 表6-3 反对率风险概率得分表
    - 表8-1 措施后风险等级量化评分表
    - 表8-2 措施前后得分对比表
    - 评审结论
    """
    query = select(Report).where(Report.id == report_id)
    result = await db.execute(query)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    # Try the review_table_path field first
    review_path = getattr(report, 'review_table_path', None)

    # Fallback: look for file in storage/generated/ by naming convention
    if not review_path:
        from pathlib import Path as _Path
        gen_dir = _Path("storage") / "generated"
        session_id = report.session_id
        candidate = gen_dir / f"{session_id}_评审表.docx"
        if candidate.exists():
            review_path = str(candidate)

    if not review_path:
        raise HTTPException(status_code=404, detail="评审表文件不存在，请先生成报告")

    file_path = file_service.get_absolute_path(review_path)
    if not file_path.exists():
        # Try alternate path resolution
        from pathlib import Path as _Path
        alt = _Path("storage") / review_path
        if alt.exists():
            file_path = alt
        else:
            raise HTTPException(status_code=404, detail="评审表文件不存在")

    safe_title = (report.title or "评审表").replace("/", "_").replace("\\", "_")[:40]
    return FileResponse(
        path=str(file_path),
        filename=f"{safe_title}_评审表.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
