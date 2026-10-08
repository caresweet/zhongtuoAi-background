"""SQLAlchemy session for history_reports.db — uses shared engine pool."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import history_async_session as async_session
from app.database.engine import _history_engine as engine


async def get_history_db() -> AsyncSession:
    """Dependency: yield an async session for history_reports.db."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_history_db():
    """Create all tables in history_reports.db, then run idempotent migrations."""
    from app.models.history import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_report_owner)


def _migrate_report_owner(conn):
    """幂等迁移：报告的归属列 + 索引。

    `user_id` 在**开发机上物理已存在**（历史遗留），但从不保证 —— 所以判一下再补。
    `company_id` 与索引是新加的。create_all 不会 ALTER 已存在的表，必须手写。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    if not inspector.has_table("reports"):
        return
    cols = {c["name"] for c in inspector.get_columns("reports")}
    if "user_id" not in cols:
        conn.execute(text("ALTER TABLE reports ADD COLUMN user_id VARCHAR(100)"))
    if "company_id" not in cols:
        conn.execute(text("ALTER TABLE reports ADD COLUMN company_id INTEGER"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id)"))


async def backfill_report_owners() -> int:
    """把**没有归属**的历史报告划给引导管理员，返回改动行数。

    🔴 为什么在 Python 里做而不是一条 SQL
        开发机上 auth.db / history_reports.db / knowledge_base.db 是**三个独立
        SQLite 文件**，跨库 JOIN 根本跑不起来；生产是同一个 PG 才能 JOIN。
        这里先读 auth 库拿管理员 id，再对 history 库发**单表 UPDATE** —— 两边都成立。

    🔴 为什么不删不藏
        这些是业主真实的产出。归属到管理员后：管理员仍然看得见全部历史，
        普通账号看不到 —— **一行都不会丢**。

    幂等：条件是「没有归属或指向不存在的用户」，第二次执行无行可改。
    """
    import logging
    from sqlalchemy import bindparam, select, text

    from app.models.user import User
    from app.database.auth_db import async_session as auth_session

    log = logging.getLogger(__name__)

    async with auth_session() as adb:
        # 引导管理员：优先 is_superuser，其次 role='admin'，再退到最早建的账号
        admin = (await adb.execute(
            select(User).where(User.is_superuser.is_(True)).order_by(User.id)
        )).scalars().first()
        if admin is None:
            admin = (await adb.execute(
                select(User).where(User.role == "admin").order_by(User.id)
            )).scalars().first()
        if admin is None:
            admin = (await adb.execute(select(User).order_by(User.id))).scalars().first()
        if admin is None:
            log.warning("回填报告归属：库里一个用户都没有，跳过")
            return 0
        valid_ids = [str(r) for r in (await adb.execute(select(User.id))).scalars().all()]

    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE reports SET user_id = :uid, "
                "company_id = COALESCE(company_id, :cid) "
                "WHERE user_id IS NULL OR user_id = '' OR user_id = 'default' "
                "OR user_id NOT IN :valid"
            ).bindparams(bindparam("valid", expanding=True)),
            {"uid": str(admin.id), "cid": admin.company_id, "valid": valid_ids},
        )
        n = res.rowcount or 0
    if n:
        log.warning(f"回填报告归属：{n} 份历史报告划给管理员 {admin.username!r}（id={admin.id}）")
    return n
