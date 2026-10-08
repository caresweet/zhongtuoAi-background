"""权限的两个轴：**内部人员判定** 与 **提权防护**。

🔴 背景
    多租户里有**两个独立的问题**，代码里一不小心就混成一个：
      ① 报告里的公司信息用谁的？ → `users.company_id` 指向的公司资料
      ② 谁能看知识库等内部工具？ → 「是不是我们自己人」
    原先后者只认 `role == 'admin'`，于是**我们自己公司的同事**连知识库都看不到。
    现在 ② = 管理员 **或** 挂内部公司的同事（`deps.is_staff`）。

🔴 为什么提权防护必须单独测
    账号管理的入口放宽成「自己人」之后，如果不额外设线，
    **任何一个同事都能给自己发管理员权限** —— 等于权限体系不存在。
"""

import pytest
from fastapi import HTTPException


class _U:
    def __init__(self, uid=1, cid=None, admin=False, internal=False):
        self.id = uid
        self.company_id = cid
        self.role = "admin" if admin else "user"
        self.is_superuser = admin
        self.is_active = True


INTERNAL_CID = 1


@pytest.fixture(autouse=True)
def _stub_internal_company(monkeypatch):
    """把「内部公司 id」钉成 1，避免依赖真实库。"""
    async def _fake(db=None):
        return INTERNAL_CID
    import app.services.company_service as cs
    monkeypatch.setattr(cs, "internal_company_id", _fake)
    yield


def test_admin_is_always_staff():
    import asyncio
    from app.deps import is_staff
    assert asyncio.run(is_staff(_U(cid=None, admin=True))) is True


def test_internal_company_member_is_staff():
    """我们自己公司的同事（不是管理员）也算内部人员 —— 这正是这次要改的点。"""
    import asyncio
    from app.deps import is_staff
    assert asyncio.run(is_staff(_U(cid=INTERNAL_CID))) is True


def test_external_customer_is_not_staff():
    """外部客户账号（含「还没填公司」的）一律不是内部人员。"""
    import asyncio
    from app.deps import is_staff
    assert asyncio.run(is_staff(_U(cid=2))) is False
    assert asyncio.run(is_staff(_U(cid=None))) is False


# ════════════════════════════════════════════════════════════
# 提权防护
# ════════════════════════════════════════════════════════════

def test_staff_cannot_grant_admin():
    """自己人（非管理员）不能造管理员，也不能把自己改成管理员。"""
    from app.routers.admin import _assert_can_grant_admin

    staff = _U(cid=INTERNAL_CID)
    for payload in ({"role": "admin"}, {"is_superuser": True}):
        with pytest.raises(HTTPException) as e:
            _assert_can_grant_admin(staff, payload)
        assert e.value.status_code == 403

    # 普通账号照常能建
    _assert_can_grant_admin(staff, {"role": "user"})


def test_admin_can_grant_admin():
    from app.routers.admin import _assert_can_grant_admin
    _assert_can_grant_admin(_U(admin=True), {"role": "admin"})


def test_staff_cannot_touch_admin_accounts():
    """自己人不得停用/删号/重置管理员密码。"""
    from app.routers.admin import _assert_can_modify

    staff, target_admin = _U(cid=INTERNAL_CID), _U(uid=99, admin=True)
    with pytest.raises(HTTPException) as e:
        _assert_can_modify(staff, target_admin)
    assert e.value.status_code == 403

    # 改普通用户没问题
    _assert_can_modify(staff, _U(uid=5, cid=2))
    # 管理员之间可以互相操作
    _assert_can_modify(_U(admin=True), target_admin)
