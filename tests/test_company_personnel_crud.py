"""人员库的「增 / 改 / 删」与跨公司隔离。

🔴 背景
    人员原先只能**整体导入名册**，单个人改不了、删不掉（业主：「人员也能够修改」）。
    现在 `POST/PUT/DELETE /api/v1/company/personnel` 三条都补齐了。

🔴 为什么隔离也要测
    这三条是**写**接口，比读接口危险得多：越权改/删会把别家公司的人动掉。
    全部按 `company_id` 收口，管理员例外（运维代客户处理）。
"""

import sqlite3

import pytest


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    """把人员库指到临时文件，绝不碰真实的 knowledge_base.db。"""
    from app.services import personnel_store as ps

    dbfile = tmp_path / "kb.db"
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE _seed (a)")
    conn.commit(); conn.close()

    monkeypatch.setattr(ps, "_db_path", lambda: dbfile)
    ps.ensure_tables()
    return dbfile


class _U:
    def __init__(self, uid, cid, admin=False):
        self.id = uid
        self.company_id = cid
        self.role = "admin" if admin else "user"
        self.is_superuser = admin
        self.is_active = True


def test_crud_roundtrip(pdb):
    """新增 → 改 → 删 全链路。"""
    import asyncio
    from app.routers.company import create_personnel, delete_personnel, update_personnel
    from app.services.personnel_store import personnel_for

    a = _U(1, 10)

    async def flow():
        await create_personnel({"name": "李四", "title": "工程师", "role": "项目组成员",
                                "certs": ["社会稳定风险评估业务培训证书"]}, a)
        people = personnel_for(None, 10)
        assert [p["姓名"] for p in people] == ["李四"]
        assert people[0]["has_wenshen_cert"] is True, "含『培训』的证书应自动置持证"
        pid = people[0]["_id"]

        await update_personnel(pid, {"title": "高级工程师", "role": "项目负责人"}, a)
        p = personnel_for(None, 10)[0]
        assert p["职称/职务"] == "高级工程师" and p["拟任角色"] == "项目负责人"

        await delete_personnel(pid, a)
        assert personnel_for(None, 10) == []

    asyncio.run(flow())


def test_cannot_touch_another_company(pdb):
    """**写接口的越权防护**：改/删别家公司的人必须是 404。"""
    import asyncio
    from fastapi import HTTPException

    from app.routers.company import create_personnel, delete_personnel, update_personnel
    from app.services.personnel_store import personnel_for

    a, b = _U(1, 10), _U(2, 20)

    async def flow():
        await create_personnel({"name": "张三"}, a)
        pid = personnel_for(None, 10)[0]["_id"]

        for fn in (update_personnel, delete_personnel):
            with pytest.raises(HTTPException) as e:
                if fn is update_personnel:
                    await fn(pid, {"title": "X"}, b)
                else:
                    await fn(pid, b)
            assert e.value.status_code == 404, f"{fn.__name__} 越权未拦住"

        # A 的人还在、没被改
        p = personnel_for(None, 10)[0]
        assert p["姓名"] == "张三" and p["职称/职务"] == ""

    asyncio.run(flow())


def test_admin_can_operate_across_companies(pdb):
    """管理员可代客户改/删（运维口径，与报告可见性一致）。"""
    import asyncio
    from app.routers.company import create_personnel, update_personnel
    from app.services.personnel_store import personnel_for

    a, admin = _U(1, 10), _U(99, None, admin=True)

    async def flow():
        await create_personnel({"name": "王五"}, a)
        pid = personnel_for(None, 10)[0]["_id"]
        await update_personnel(pid, {"title": "顾问"}, admin)
        assert personnel_for(None, 10)[0]["职称/职务"] == "顾问"

    asyncio.run(flow())


def test_same_name_allowed_across_companies_but_not_within(pdb):
    """同名跨公司可共存；同公司重名走「覆盖」语义（ON CONFLICT 更新）。"""
    import asyncio
    from app.routers.company import create_personnel
    from app.services.personnel_store import personnel_for

    a, b = _U(1, 10), _U(2, 20)

    async def flow():
        await create_personnel({"name": "张伟", "title": "工程师"}, a)
        await create_personnel({"name": "张伟", "title": "总监"}, b)
        assert len(personnel_for(None, 10)) == 1
        assert len(personnel_for(None, 20)) == 1

        # 同公司同名 → 覆盖，不新增
        await create_personnel({"name": "张伟", "title": "副总"}, a)
        rows = personnel_for(None, 10)
        assert len(rows) == 1 and rows[0]["职称/职务"] == "副总"

    asyncio.run(flow())
