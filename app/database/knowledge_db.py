"""SQLAlchemy session for knowledge_base.db — uses shared engine pool."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import knowledge_async_session as async_session
from app.database.engine import knowledge_async_session
from app.database.engine import _knowledge_engine as engine


async def get_knowledge_db() -> AsyncSession:
    """Dependency: yield an async session for knowledge_base.db."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_knowledge_db():
    """Create all tables in knowledge_base.db."""
    from app.models.knowledge import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_add_domain_columns)
        await conn.run_sync(_migrate_add_extraction_columns)
        await conn.run_sync(_migrate_add_cleaning_columns)


def _migrate_add_domain_columns(conn):
    """Idempotent migration: add the `domain` column to existing tables.

    SQLAlchemy's create_all never ALTERs existing tables, so pre-existing
    knowledge_base.db files won't get the new `domain` column. Add it here.
    Existing rows default to "stability" (the original single-domain data).
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    for table in ("knowledge_documents", "templates"):
        if not inspector.has_table(table):
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "domain" not in cols:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN domain "
                f"VARCHAR(50) NOT NULL DEFAULT 'stability'"
            ))


def _migrate_add_extraction_columns(conn):
    """Idempotent migration for analysis-first knowledge-document fields."""
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    table = "knowledge_documents"
    if not inspector.has_table(table):
        return

    cols = {c["name"] for c in inspector.get_columns(table)}
    additions = {
        "extraction_status": "ALTER TABLE knowledge_documents ADD COLUMN extraction_status VARCHAR(20) DEFAULT 'pending'",
        "extraction_version": "ALTER TABLE knowledge_documents ADD COLUMN extraction_version VARCHAR(50)",
        "extracted_text": "ALTER TABLE knowledge_documents ADD COLUMN extracted_text TEXT",
        "retrieval_text": "ALTER TABLE knowledge_documents ADD COLUMN retrieval_text TEXT",
        "structured_data_json": "ALTER TABLE knowledge_documents ADD COLUMN structured_data_json TEXT",
        "image_summary": "ALTER TABLE knowledge_documents ADD COLUMN image_summary TEXT",
    }
    for column_name, ddl in additions.items():
        if column_name not in cols:
            conn.execute(text(ddl))


def _migrate_add_cleaning_columns(conn):
    """Idempotent migration for Data Cleaning Workbench fields."""
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    table = "knowledge_documents"
    if not inspector.has_table(table):
        return

    cols = {c["name"] for c in inspector.get_columns(table)}
    additions = {
        "raw_text": "ALTER TABLE knowledge_documents ADD COLUMN raw_text TEXT",
        "cleaned_text": "ALTER TABLE knowledge_documents ADD COLUMN cleaned_text TEXT",
        "clean_config_snapshot": "ALTER TABLE knowledge_documents ADD COLUMN clean_config_snapshot TEXT",
        "clean_status": "ALTER TABLE knowledge_documents ADD COLUMN clean_status VARCHAR(20) DEFAULT 'raw'",
    }
    for column_name, ddl in additions.items():
        if column_name not in cols:
            conn.execute(text(ddl))
