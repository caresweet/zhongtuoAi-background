"""历史报告列表：不传 domain 行为不变；传 domain 只出该域。"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.history import Base, Report


class _U:
    def __init__(self, uid):
        self.id = uid
        self.username = f"u{uid}"
        self.role = "user"
        self.is_active = True
        self.is_superuser = False
        self.company_id = None


@pytest.fixture
def env(tmp_path):
    from app.database.history_db import get_history_db
    from app.deps import get_current_user
    from app.main import app

    db_file = tmp_path / "hist.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    aengine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = async_sessionmaker(aengine, expire_on_commit=False)

    async def _override_db():
        async with Session() as s:
            yield s

    user = _U(7)
    app.dependency_overrides[get_history_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user

    async def _seed():
        async with Session() as s:
            s.add(Report(title="稳评", session_id="s1", user_id="7",
                         status="completed", domain="stability"))
            s.add(Report(title="复垦", session_id="s2", user_id="7",
                         status="completed", domain="reclamation"))
            await s.commit()

    asyncio.run(_seed())

    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_history_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_without_domain_returns_all(env):
    r = env.get("/api/v1/history/reports")
    assert r.status_code == 200
    # 列表响应的分页字段是 items（见 ReportListResponse），非 reports。
    assert len(r.json()["data"]["items"]) == 2


def test_filter_by_reclamation(env):
    r = env.get("/api/v1/history/reports?domain=reclamation")
    assert r.status_code == 200
    rows = r.json()["data"]["items"]
    assert [x["title"] for x in rows] == ["复垦"]


# ══════════════════════════════════════════════════════════════════
# 落库写入 domain（过滤的前提 —— 没写对，过滤就无从谈起）
# ══════════════════════════════════════════════════════════════════

def _temp_history_db(tmp_path):
    """临时 history 库 + 异步 sessionmaker（绝不碰真实 data/*.db）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_file = tmp_path / "hist.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    aengine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return async_sessionmaker(aengine, expire_on_commit=False), aengine


def _persist_and_read_domain(tmp_path, monkeypatch, state):
    """用临时库跑一遍 persist_report，回读该行的 domain。"""
    from sqlalchemy import select

    from app.database import history_db
    from app.services import report_service as rs
    from app.services.report_service import ReportSession

    Session, aengine = _temp_history_db(tmp_path)
    # persist_report 在函数内 `from app.database.history_db import async_session`,
    # 故替换模块属性即可让落库走临时库。
    monkeypatch.setattr(history_db, "async_session", Session)

    sess = ReportSession(session_id="sess-x", template_id=0, template_name="",
                         template_path="", state=state, user_id="7")

    async def _run():
        rid = await rs.report_service.persist_report(sess)
        async with Session() as s:
            row = (await s.execute(select(Report).where(Report.id == rid))).scalar_one()
            return row.domain

    try:
        return asyncio.run(_run())
    finally:
        asyncio.run(aengine.dispose())


def test_persist_report_writes_pinned_domain(tmp_path, monkeypatch):
    """会话已钉住复垦域时，落库应写 reclamation。"""
    assert _persist_and_read_domain(tmp_path, monkeypatch,
                                    {"_domain": "reclamation"}) == "reclamation"


def test_persist_report_domain_defaults_to_stability(tmp_path, monkeypatch):
    """会话没钉域（如稳评老流程）时，落库应回落 stability。"""
    assert _persist_and_read_domain(tmp_path, monkeypatch, {}) == "stability"

