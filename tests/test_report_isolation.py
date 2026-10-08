"""报告归属隔离 —— 「每个账号只能看到自己生成的报告」。

🔴 为什么必须有这个文件
    `/api/v1/history/*` 原先 7 个端点全部只按 id 查：列表全量返回、
    `/download` 连 token 都不校验 —— **知道 id 就能下别人的报告**。
    而 `Report.user_id` 虽然一直有这一列，却从来没人写过值（全是 "default"），
    所以「按账号隔离」在数据层根本无从落地。这一组用例把这个洞钉死。

🔴 测试怎么做到不碰真实数据
    用 FastAPI 的 `dependency_overrides` 把「数据库会话」与「当前用户」
    都换成测试自己造的那一份 —— 不写用户的 auth.db / history_reports.db。
    （仓库里有过「按条件扫删测试数据」的事故，那是不允许的。）
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def _make_users():
    """两个普通账号 + 一个管理员。用简单对象替掉 ORM 行（端点只读这几个属性）。"""
    class _U:
        def __init__(self, uid, name, role="user", superuser=False, active=True):
            self.id = uid
            self.username = name
            self.role = role
            self.is_superuser = superuser
            self.is_active = active
            self.company_id = 1

    return (_U(101, "user_a"),
            _U(202, "user_b"),
            _U(1, "admin", role="admin", superuser=True))


@pytest.fixture
def env(tmp_path):
    """临时 history 库 + 依赖覆盖 + 一条属于 A 的报告。"""
    from app.database.history_db import get_history_db
    from app.deps import get_current_user
    from app.main import app
    from app.models.history import Base, Report

    db_file = tmp_path / "hist.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    aengine = create_async_engine(f"sqlite+aiosqlite:///{db_file}",
                                  connect_args={"check_same_thread": False},
                                  poolclass=StaticPool)
    Session = async_sessionmaker(aengine, expire_on_commit=False)

    async def _override_db():
        async with Session() as s:
            yield s

    user_a, user_b, admin = _make_users()
    current = {"user": user_a}
    app.dependency_overrides[get_history_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current["user"]

    # 造数据：一条属于 A 的报告
    import asyncio

    async def _seed():
        async with Session() as s:
            s.add(Report(title="A的报告", session_id="sess-a", user_id=str(user_a.id),
                         status="completed", report_file_path="generated/a.docx"))
            await s.commit()
    asyncio.run(_seed())

    client = TestClient(app)
    try:
        yield {"client": client, "current": current, "user_a": user_a,
               "user_b": user_b, "admin": admin}
    finally:
        app.dependency_overrides.pop(get_history_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _ids(resp):
    return [r["id"] for r in resp.json()["data"]["items"]]


def test_owner_sees_own_report(env):
    env["current"]["user"] = env["user_a"]
    r = env["client"].get("/api/v1/history/reports")
    assert r.status_code == 200
    assert _ids(r), "本人应该看得到自己的报告"


def test_other_user_sees_empty_list(env):
    env["current"]["user"] = env["user_b"]
    r = env["client"].get("/api/v1/history/reports")
    assert r.status_code == 200
    assert _ids(r) == [], "B 不该看到 A 的报告"


def test_other_user_gets_404_on_detail_and_download(env):
    """按 id 直取也必须是 404 —— 用 403 等于告诉对方「这个 id 存在」。"""
    env["current"]["user"] = env["user_b"]
    for path in ("/api/v1/history/reports/1",
                 "/api/v1/history/reports/1/data",
                 "/api/v1/history/reports/1/conversation",
                 "/api/v1/history/reports/1/download",
                 "/api/v1/history/reports/1/review-table"):
        r = env["client"].get(path)
        assert r.status_code == 404, f"{path} → {r.status_code}，应为 404"


def test_other_user_cannot_delete(env):
    env["current"]["user"] = env["user_b"]
    r = env["client"].delete("/api/v1/history/reports/1")
    assert r.status_code == 404
    # 报告仍在
    env["current"]["user"] = env["user_a"]
    assert _ids(env["client"].get("/api/v1/history/reports"))


def test_admin_sees_everything(env):
    env["current"]["user"] = env["admin"]
    assert _ids(env["client"].get("/api/v1/history/reports")), "管理员应看到全部"


# ════════════════════════════════════════════════════════
# 会话归属（`/reports/generate/{session_id}/*` 的同类漏洞）
# ════════════════════════════════════════════════════════

def test_session_is_not_readable_by_other_user():
    """猜到 session_id 也读不到别人的会话。"""
    from fastapi import HTTPException
    from app.services.report_service import ReportSession, ReportService

    svc = ReportService.__new__(ReportService)
    svc._sessions = {}
    sess = ReportSession(session_id="s1", template_id=0, template_name="",
                         template_path="", state={}, user_id="101")
    svc._sessions["s1"] = sess

    user_a, user_b, admin = _make_users()
    assert svc.get_session_for_user("s1", user_a).session_id == "s1"       # 本人
    assert svc.get_session_for_user("s1", admin).session_id == "s1"       # 管理员

    with pytest.raises(HTTPException) as e:
        svc.get_session_for_user("s1", user_b)                            # 别人
    assert e.value.status_code == 404

    with pytest.raises(HTTPException):
        svc.get_session_for_user("不存在", user_a)


def test_unowned_legacy_session_is_admin_only():
    """无主的老会话（脚本/历史遗留）只有管理员能取。"""
    from fastapi import HTTPException
    from app.services.report_service import ReportSession, ReportService

    svc = ReportService.__new__(ReportService)
    svc._sessions = {}
    svc._sessions["legacy"] = ReportSession(
        session_id="legacy", template_id=0, template_name="",
        template_path="", state={}, user_id="")

    user_a, _, admin = _make_users()
    assert svc.get_session_for_user("legacy", admin).session_id == "legacy"
    with pytest.raises(HTTPException):
        svc.get_session_for_user("legacy", user_a)


def test_session_endpoints_enforce_ownership_through_http(env):
    """**走 HTTP** 打会话端点：别人的 session_id 必须 404。

    🔴 这一条是补的 —— 前面那批 `get_session_for_user` 的调用点批量替换时
       因为行号移位**全部静默没生效**，而当时的单测只覆盖 `/history/*`，
       完全没发现。所以这里必须从 HTTP 入口再钉一遍。
    """
    from app.services.report_service import ReportSession, report_service

    sid = "sess-isolation-x"
    report_service._sessions[sid] = ReportSession(
        session_id=sid, template_id=0, template_name="", template_path="",
        state={"status": "created", "phase": "created", "chapters": {}}, user_id="101")
    try:
        env["current"]["user"] = env["user_b"]
        r = env["client"].get(f"/api/v1/reports/generate/{sid}/status")
        assert r.status_code == 404, f"B 拿到了 A 的会话状态（HTTP {r.status_code}）"

        env["current"]["user"] = env["user_a"]
        r = env["client"].get(f"/api/v1/reports/generate/{sid}/status")
        assert r.status_code == 200, f"本人应能取到自己的会话（HTTP {r.status_code}）"
    finally:
        report_service._sessions.pop(sid, None)


def test_session_ownership_survives_restart():
    """归属必须能落盘再还原 —— `to_dict` 漏写字段 = 重启后报告变「无主」。"""
    from app.services.report_service import ReportSession

    s = ReportSession(session_id="s9", template_id=3, template_name="t",
                      template_path="/p/t.docx", state={"a": 1},
                      user_id="101", company_id=7)
    d = s.to_dict()
    assert d["user_id"] == "101" and d["company_id"] == 7
    assert d["template_name"] == "t" and d["template_path"] == "/p/t.docx"
