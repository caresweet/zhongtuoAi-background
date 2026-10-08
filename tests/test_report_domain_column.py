"""reports 表新增 domain 列 —— 迁移幂等，既有行默认 stability。"""
import sqlite3

from sqlalchemy import create_engine, inspect

from app.models.history import Base


def _inspector(db_file):
    return inspect(create_engine(f"sqlite:///{db_file}"))


def test_report_model_has_domain_column(tmp_path):
    """新建的库里 reports 表应有 domain 列。"""
    db_file = tmp_path / "hist.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()

    cols = {c["name"] for c in _inspector(db_file).get_columns("reports")}
    assert "domain" in cols


def test_migration_adds_domain_to_legacy_table(tmp_path):
    """没有 domain 列的老表，迁移后补上。"""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, title VARCHAR(255))")
    conn.commit()
    conn.close()

    from app.database.history_db import _migrate_report_domain

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as c:
        _migrate_report_domain(c)

    cols = {c["name"] for c in inspect(engine).get_columns("reports")}
    assert "domain" in cols


def test_migration_is_idempotent(tmp_path):
    """连跑两次不得抛错 —— 服务每次启动都会跑一遍。"""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, title VARCHAR(255))")
    conn.commit()
    conn.close()

    from app.database.history_db import _migrate_report_domain

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as c:
        _migrate_report_domain(c)
        _migrate_report_domain(c)


def test_legacy_rows_default_to_stability(tmp_path):
    """该列加入之前只有稳评落 reports（投标走 MasterAgent 不落表），故老行视为稳评。"""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, title VARCHAR(255))")
    conn.execute("INSERT INTO reports (id, title) VALUES (1, '老报告')")
    conn.commit()
    conn.close()

    from app.database.history_db import _migrate_report_domain

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as c:
        _migrate_report_domain(c)
        row = c.exec_driver_sql("SELECT domain FROM reports WHERE id = 1").fetchone()
    assert row[0] == "stability"
