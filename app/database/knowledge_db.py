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
        await conn.run_sync(_migrate_add_chat_tables)


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


def _migrate_add_chat_tables(conn):
    """Idempotent migration: create tables for knowledge chat (learned_corrections + conversation_memory).

    These tables are used by knowledge_chat.py via raw SQL. Since they have no ORM models,
    Base.metadata.create_all() won't create them — we must create them here.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)

    # ── learned_corrections: stores user corrections for self-learning ──
    if not inspector.has_table("learned_corrections"):
        conn.execute(text("""
            CREATE TABLE learned_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_query TEXT,
                original_answer TEXT,
                user_correction TEXT,
                corrected_knowledge TEXT,
                topic_keywords VARCHAR(500),
                domain VARCHAR(50) NOT NULL DEFAULT 'stability',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_lc_domain ON learned_corrections(domain)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_lc_active ON learned_corrections(is_active)"
        ))

    # ── conversation_memory: stores multi-turn chat history for context ──
    if not inspector.has_table("conversation_memory"):
        conn.execute(text("""
            CREATE TABLE conversation_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id VARCHAR(100) NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT,
                message_type VARCHAR(20) DEFAULT 'chat',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_cm_session ON conversation_memory(session_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_cm_created ON conversation_memory(created_at)"
        ))

    # ── generation_feedback: stores quality audit results for continuous learning ──
    if not inspector.has_table("generation_feedback"):
        conn.execute(text("""
            CREATE TABLE generation_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                report_title TEXT,
                domain TEXT DEFAULT 'stability',
                overall_score REAL DEFAULT 0,
                data_score REAL DEFAULT 0,
                format_score REAL DEFAULT 0,
                total_issues INTEGER DEFAULT 0,
                fabricated_count INTEGER DEFAULT 0,
                validity_count INTEGER DEFAULT 0,
                regulation_count INTEGER DEFAULT 0,
                blocking_count INTEGER DEFAULT 0,
                rewrite_count INTEGER DEFAULT 0,
                rewrite_chapters TEXT,
                passed BOOLEAN DEFAULT 0,
                output_path TEXT,
                full_text TEXT,
                feedback_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_gf_session ON generation_feedback(session_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_gf_domain ON generation_feedback(domain)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_gf_created ON generation_feedback(created_at)"
        ))

    # ── expert_reviews: 专家评估反馈（人工专家对报告的优化点/不足）──
    if not inspector.has_table("expert_reviews"):
        conn.execute(text("""
            CREATE TABLE expert_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_title TEXT,
                session_id TEXT,
                report_file_path TEXT,
                domain TEXT DEFAULT 'stability',
                chapter_num INTEGER DEFAULT 0,
                issue_type TEXT,
                issue_desc TEXT,
                suggestion TEXT,
                severity TEXT DEFAULT 'warning',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    else:
        # 补 session_id / report_file_path 字段（关联具体报告，方便识别）
        cols = {c["name"] for c in inspector.get_columns("expert_reviews")}
        if "session_id" not in cols:
            conn.execute(text("ALTER TABLE expert_reviews ADD COLUMN session_id TEXT"))
        if "report_file_path" not in cols:
            conn.execute(text("ALTER TABLE expert_reviews ADD COLUMN report_file_path TEXT"))

    # ── review_skills: 蒸馏出的审核 skill（规则 + 文本）──
    if not inspector.has_table("review_skills"):
        conn.execute(text("""
            CREATE TABLE review_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT DEFAULT 'stability',
                chapter_num INTEGER DEFAULT 0,
                skill_type TEXT DEFAULT 'text',
                rule_pattern TEXT,
                rule_desc TEXT,
                severity TEXT DEFAULT 'warning',
                correction TEXT,
                source_review_ids TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rs_domain ON review_skills(domain)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rs_active ON review_skills(is_active)"
        ))
