#!/usr/bin/env python3
"""Migrate SQLite data to PostgreSQL.

Usage:
    # Set target PostgreSQL URL
    export DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/zhongtuo_report

    # Run migration
    python scripts/migrate_sqlite_to_pg.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


SQLITE_DBS = [
    ("knowledge_base.db", ["templates", "placeholder_definitions", "knowledge_documents", "company_assets", "asset_images"]),
    ("history_reports.db", ["reports"]),
]


async def migrate():
    target_url = os.environ.get("DATABASE_URL", "")
    if not target_url:
        print("ERROR: DATABASE_URL not set. Export it before running.")
        print("Example: export DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db")
        sys.exit(1)

    data_dir = Path(__file__).resolve().parent.parent / "data"

    pg_engine = create_async_engine(target_url, echo=False)

    async with pg_engine.begin() as pg_conn:
        # Create tables in PostgreSQL
        from app.models.knowledge import Base as KnowledgeBase
        from app.models.history import Base as HistoryBase
        from app.models.user import Base as UserBase

        await pg_conn.run_sync(KnowledgeBase.metadata.create_all)
        await pg_conn.run_sync(HistoryBase.metadata.create_all)
        await pg_conn.run_sync(UserBase.metadata.create_all)
        print("✓ PostgreSQL tables created")

        for db_file, tables in SQLITE_DBS:
            db_path = data_dir / db_file
            if not db_path.exists():
                print(f"⊘ {db_file} not found — skipping")
                continue

            sqlite_url = f"sqlite+aiosqlite:///{db_path}"
            sqlite_engine = create_async_engine(sqlite_url, echo=False)

            async with sqlite_engine.connect() as sq_conn:
                for table in tables:
                    # Read from SQLite
                    result = await sq_conn.execute(text(f"SELECT * FROM {table}"))
                    rows = result.fetchall()
                    if not rows:
                        print(f"  {table}: 0 rows — skipping")
                        continue

                    columns = list(result.keys())

                    # Build INSERT for PostgreSQL
                    placeholders = ", ".join(f":{c}" for c in columns)
                    cols = ", ".join(columns)
                    insert_sql = text(
                        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
                        f"ON CONFLICT DO NOTHING"
                    )

                    for row in rows:
                        values = dict(zip(columns, row))
                        # Convert datetime objects to strings for cross-DB compat
                        for k, v in values.items():
                            if hasattr(v, 'isoformat'):
                                values[k] = v.isoformat()
                        try:
                            await pg_conn.execute(insert_sql, values)
                        except Exception as e:
                            print(f"  ⚠ {table} row insert failed: {e}")

                    print(f"  {table}: {len(rows)} rows migrated ✓")

            await sqlite_engine.dispose()

    await pg_engine.dispose()
    print("\n✓ Migration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
