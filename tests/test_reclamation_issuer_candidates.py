"""出具单位候选：内部账号出我司各机构；外部账号出自己那家；无关联不报错。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.user import Base, Company


class _U:
    def __init__(self, uid, company_id=None):
        self.id = uid
        self.username = f"u{uid}"
        self.role = "user"
        self.is_active = True
        self.is_superuser = False
        self.company_id = company_id


def _make_client(tmp_path, user, companies):
    from app.database.auth_db import get_auth_db
    from app.deps import get_current_user
    from app.main import app

    db_file = tmp_path / "auth.db"
    e = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(e)
    e.dispose()

    ae = create_async_engine(f"sqlite+aiosqlite:///{db_file}",
                             connect_args={"check_same_thread": False},
                             poolclass=StaticPool)
    Session = async_sessionmaker(ae, expire_on_commit=False)

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_auth_db] = _override
    app.dependency_overrides[get_current_user] = lambda: user

    import asyncio

    async def _seed():
        async with Session() as s:
            for name, internal in companies:
                s.add(Company(name=name, is_internal=internal))
            await s.commit()
    asyncio.run(_seed())

    return TestClient(app), app


def test_internal_user_sees_all_internal_companies(tmp_path):
    client, app = _make_client(
        tmp_path, _U(1, company_id=1),
        [("江苏众拓项目代理咨询有限公司", True), ("江苏众拓测绘有限公司", True),
         ("淮安示例工程咨询有限公司", False)],
    )
    try:
        r = client.get("/api/v1/company/issuer-candidates")
        assert r.status_code == 200
        names = [c["name"] for c in r.json()["data"]["companies"]]
        assert "江苏众拓项目代理咨询有限公司" in names
        assert "江苏众拓测绘有限公司" in names
        assert "淮安示例工程咨询有限公司" not in names
    finally:
        app.dependency_overrides.clear()


def test_external_user_sees_only_own_company(tmp_path):
    client, app = _make_client(
        tmp_path, _U(2, company_id=2),
        [("江苏众拓项目代理咨询有限公司", True), ("外部公司", False)],
    )
    try:
        r = client.get("/api/v1/company/issuer-candidates")
        names = [c["name"] for c in r.json()["data"]["companies"]]
        assert names == ["外部公司"]
    finally:
        app.dependency_overrides.clear()


def test_user_without_company_does_not_error(tmp_path):
    """账号未关联公司时不得抛异常阻断生成 —— 返回空列表。"""
    client, app = _make_client(
        tmp_path, _U(3, company_id=None),
        [("江苏众拓项目代理咨询有限公司", True)],
    )
    try:
        r = client.get("/api/v1/company/issuer-candidates")
        assert r.status_code == 200
        assert r.json()["data"]["companies"] == []
    finally:
        app.dependency_overrides.clear()
