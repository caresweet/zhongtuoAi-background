"""Shared SQLAlchemy engine — single connection pool for all databases.

When all DB URLs point to the same PostgreSQL server (production),
this module ensures they share a single engine + connection pool
instead of creating one per DB module (auth, history, knowledge).

Pool config:
- pool_size: 10 (shared across all DB operations)
- max_overflow: 10 (extra connections under load)
- pool_recycle: 3600s (prevent stale connections)
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

# Map URL string → engine instance — when all three point to the same PG,
# they share one pool.
_engine_cache: dict[str, object] = {}


def _get_engine(db_url: str):
    """Return a cached engine for the given URL, creating one if needed."""
    if db_url not in _engine_cache:
        connect_args = {}
        if "sqlite" in db_url:
            connect_args = {"check_same_thread": False}

        _engine_cache[db_url] = create_async_engine(
            db_url,
            echo=settings.DEBUG,
            connect_args=connect_args,
            pool_size=10,
            max_overflow=10,
            pool_recycle=3600,
            pool_pre_ping=True,  # Verify connections before use
        )
    return _engine_cache[db_url]


# Per-module engines — reuse the same underlying engine when URLs match
_knowledge_engine = _get_engine(settings.knowledge_db_url)
_history_engine = _get_engine(settings.history_db_url)
_auth_engine = _get_engine(settings.auth_db_url)

# Session factories
knowledge_async_session = async_sessionmaker(
    _knowledge_engine, class_=AsyncSession, expire_on_commit=False,
)
history_async_session = async_sessionmaker(
    _history_engine, class_=AsyncSession, expire_on_commit=False,
)
auth_async_session = async_sessionmaker(
    _auth_engine, class_=AsyncSession, expire_on_commit=False,
)
