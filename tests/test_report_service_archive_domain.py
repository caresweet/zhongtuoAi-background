"""`archive_filled_data`（草稿期落库）写入报告类型域 —— 无域回落稳评、复垦写对域。

这是 `report_service` 里**第二处** `Report(...)` 构造（第一处是 persist_report，
已有覆盖）。草稿期不写对域的话，同会话后续 persist_report 走更新分支不会覆盖，
复垦草稿会一直挂在稳评域下、历史列表按域过滤就漏。
"""
import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.history import Base, Report
from app.services.report_service import ReportSession, report_service


def _sessionmaker(tmp_path):
    db_file = tmp_path / "hist.db"
    e = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(e)
    e.dispose()
    ae = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return async_sessionmaker(ae, expire_on_commit=False)


def _archive(tmp_path, monkeypatch, state):
    """在临时库上跑一遍 archive_filled_data，返回 (ok, 落库的行)。"""
    import app.database.history_db as hdb

    Session = _sessionmaker(tmp_path)
    # archive_filled_data 内部 `from app.database.history_db import async_session`
    # → 打桩模块属性即可指向临时库，**绝不碰真实 data/*.db**。
    monkeypatch.setattr(hdb, "async_session", Session)

    s = ReportSession(session_id="s1", template_id=None, template_name=None,
                      template_path=None, state=state, user_id="7")
    ok = asyncio.run(report_service.archive_filled_data(s))

    async def _rows():
        async with Session() as db:
            return (await db.execute(select(Report))).scalars().all()

    return ok, asyncio.run(_rows())


def test_archive_without_domain_defaults_to_stability(tmp_path, monkeypatch):
    """state 里没有 `_domain` → 落稳评（既有行为不变）。"""
    ok, rows = _archive(tmp_path, monkeypatch, {"filled_data": {"project_name": "X"}})
    assert ok is True
    assert len(rows) == 1
    assert rows[0].domain == "stability"


def test_archive_with_reclamation_domain(tmp_path, monkeypatch):
    """state 里 `_domain="reclamation"` → 草稿行写复垦域。"""
    ok, rows = _archive(tmp_path, monkeypatch,
                        {"_domain": "reclamation",
                         "filled_data": {"project_name": "X"}})
    assert ok is True
    assert len(rows) == 1
    assert rows[0].domain == "reclamation"
