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
    """Create all tables in history_reports.db."""
    from app.models.history import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
