"""Dashboard API routes — /api/v1/dashboard/*"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.knowledge_db import get_knowledge_db
from app.database.history_db import get_history_db
from app.deps import get_current_user, is_admin
from app.models.knowledge import Template
from app.models.history import Report
from app.models.user import User
from app.schemas.dashboard import DashboardStats, RecentReport, DailyTrend
from app.schemas.knowledge import ApiResponse

router = APIRouter(prefix="/api/v1/dashboard", tags=["数据展示"])


def _owner_where(user: User) -> list:
    """归属过滤条件，供 `.where(*_owner_where(user), …)` 展开。

    非管理员只看自己的报告 —— 统计数字同样是信息（「别人出了多少份」），
    不隔离就等于把全公司的产出量摊给每个账号看。管理员看全部。
    """
    return [] if is_admin(user) else [Report.user_id == str(user.id)]


@router.get("/stats", response_model=ApiResponse)
async def get_stats(
    user: User = Depends(get_current_user),
    db_knowledge: AsyncSession = Depends(get_knowledge_db),
    db_history: AsyncSession = Depends(get_history_db),
):
    """获取仪表盘统计数据（**按当前账号**，管理员为全量）。"""
    mine = _owner_where(user)
    # Total reports
    result = await db_history.execute(select(func.count(Report.id)).where(*mine))
    total_reports = result.scalar() or 0

    # Completed reports
    result = await db_history.execute(
        select(func.count(Report.id)).where(*mine, Report.status == "completed")
    )
    completed = result.scalar() or 0

    # Failed reports
    result = await db_history.execute(
        select(func.count(Report.id)).where(*mine, Report.status == "failed")
    )
    failed = result.scalar() or 0

    # In progress
    result = await db_history.execute(
        select(func.count(Report.id)).where(
            *mine,
            Report.status.in_(["created", "analyzing", "interviewing", "filling", "reviewing"])
        )
    )
    in_progress = result.scalar() or 0

    # Total templates
    result = await db_knowledge.execute(
        select(func.count(Template.id)).where(Template.is_active == True)
    )
    total_templates = result.scalar() or 0

    # Reports this month
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0)
    result = await db_history.execute(
        select(func.count(Report.id)).where(*mine, Report.created_at >= month_start)
    )
    reports_this_month = result.scalar() or 0

    # Average generation time
    result = await db_history.execute(
        select(func.avg(Report.generation_duration_sec)).where(
            *mine,
            Report.status == "completed",
            Report.generation_duration_sec.isnot(None),
        )
    )
    avg_time = result.scalar()

    return ApiResponse(
        data=DashboardStats(
            total_reports=total_reports,
            completed_reports=completed,
            failed_reports=failed,
            in_progress_reports=in_progress,
            total_templates=total_templates,
            reports_this_month=reports_this_month,
            avg_generation_time_sec=round(avg_time, 1) if avg_time else None,
        ).model_dump(),
    )


@router.get("/recent", response_model=ApiResponse)
async def get_recent(
    limit: int = 10,
    user: User = Depends(get_current_user),
    db_history: AsyncSession = Depends(get_history_db),
):
    """获取最近的报告列表（**只看自己的**，管理员为全量）。"""
    from sqlalchemy import desc
    query = (
        select(Report)
        .where(*_owner_where(user))
        .order_by(desc(Report.created_at))
        .limit(limit)
    )
    result = await db_history.execute(query)
    reports = list(result.scalars().all())

    items = [
        RecentReport(
            id=r.id,
            title=r.title,
            template_name=r.template_name,
            status=r.status,
            created_at=r.created_at,
        ).model_dump()
        for r in reports
    ]
    return ApiResponse(data=items)


@router.get("/trends", response_model=ApiResponse)
async def get_trends(
    days: int = 30,
    user: User = Depends(get_current_user),
    db_history: AsyncSession = Depends(get_history_db),
):
    """获取最近N天每日报告趋势（**只看自己的**，管理员为全量）。"""
    from sqlalchemy import text

    mine = _owner_where(user)
    trends = []
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for i in range(days - 1, -1, -1):
        day_start = today - timedelta(days=i)
        day_end = day_start + timedelta(days=1)

        # Count for this day
        result = await db_history.execute(
            select(func.count(Report.id)).where(
                *mine,
                Report.created_at >= day_start,
                Report.created_at < day_end,
            )
        )
        total = result.scalar() or 0

        result = await db_history.execute(
            select(func.count(Report.id)).where(
                *mine,
                Report.created_at >= day_start,
                Report.created_at < day_end,
                Report.status == "completed",
            )
        )
        completed = result.scalar() or 0

        trends.append(DailyTrend(
            date=day_start.strftime("%Y-%m-%d"),
            count=total,
            completed=completed,
        ).model_dump())

    return ApiResponse(data=trends)
