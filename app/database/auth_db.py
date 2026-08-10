"""SQLAlchemy session for auth (users) database — uses shared engine pool.

Uses a shared PostgreSQL server in production (via AUTH_DB_URL) or falls back
to a local SQLite file for development.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import auth_async_session as async_session
from app.database.engine import _auth_engine as engine


async def get_auth_db() -> AsyncSession:
    """Dependency: yield an async session for the auth database."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_auth_db():
    """Create all tables in the auth database."""
    from app.models.user import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
