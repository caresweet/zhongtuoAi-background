"""账号与公司管理 —— `/api/v1/admin/*`（**内部人员**：管理员或挂内部公司的同事）。

🔴 为什么要有后台建号
    业主口径：外部用户会**自助注册**，但那只是「申请」—— 注册出来的账号
    `is_active=False`，必须由内部人员在这里放行才能登录。也能直接建号
    （客户电话里报个名字，我们代建，省得他填错）。

🔴 谁能进这个模块：`require_staff` = 管理员 **或** 挂内部公司的同事。
   但**发放管理员权限**仍然只有管理员能做（见下面的 `_assert_can_grant_admin`）——
   否则任何一个同事都能给自己提权。

🔴 与「内外有别」的关系
    建号时可以指定 `company_id`：
      · 我们自己人 → 挂内部公司（江苏众拓），报告用我们的简介/资质；
      · 外部客户   → 挂他自己那家公司（或先留空，让他登录后在「公司信息」里填）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select

from app.database.auth_db import get_auth_db
from app.deps import require_staff
from app.models.user import Company, User
from app.schemas.auth import UserInfo
from app.schemas.knowledge import ApiResponse
from app.security import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["账号管理"])


def _user_row(u: User) -> dict:
    """用户 → 响应 dict。**绝不返回 hashed_password**。"""
    return {
        "id": u.id, "username": u.username, "email": u.email or "",
        "display_name": u.display_name or "", "role": u.role,
        "is_active": bool(u.is_active), "is_superuser": bool(u.is_superuser),
        "company_id": u.company_id, "phone": u.phone or "",
        "review_note": u.review_note or "",
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "status": "active" if u.is_active else "pending",
    }


def _company_row(c: Company) -> dict:
    return {
        "id": c.id, "name": c.name or "", "is_internal": bool(c.is_internal),
        "short_name": c.short_name or "", "implement_unit": c.implement_unit or "",
        "address": c.address or "", "contact": c.contact or "",
        "status": c.status or "active",
    }


async def _get_user_or_404(uid: int, db) -> User:
    u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return u


# ═══════════════════════════════════════════════════════════════
# 提权防护 —— 整个模块**唯一**的权限分界线
# ═══════════════════════════════════════════════════════════════
# 🔴 本模块的入口是 `require_staff`（管理员 **或** 内部公司的同事），
#    也就是「自己人」都能进来建号。但**权限本身**不能让普通员工随便发出去：
#      · 造管理员 / 改角色 / 动管理员账号 —— 一律仍要求 `is_admin`
#    否则任何一个同事都能给自己提权，等于没有权限体系。

def _assert_can_grant_admin(caller: User, payload: dict) -> None:
    """凡是要把别人变成管理员的，必须调用者自己就是管理员。"""
    from app.deps import is_admin
    if payload.get("role") == "admin" or payload.get("is_superuser"):
        if not is_admin(caller):
            raise HTTPException(status_code=403, detail="只有管理员可以授予管理员权限")


def _assert_can_modify(caller: User, target: User) -> None:
    """普通员工不得改动管理员账号（停用/删号/重置密码/改角色都不行）。"""
    from app.deps import is_admin
    if is_admin(target) and not is_admin(caller):
        raise HTTPException(status_code=403, detail="只有管理员可以操作管理员账号")


# ═══════════════════════════════════════════════════════════════
# 账号
# ═══════════════════════════════════════════════════════════════

@router.get("/users", response_model=ApiResponse)
async def list_users(
    status: str = Query("", description="pending=待审核 / active=已启用，留空=全部"),
    q: str = Query("", description="按用户名/姓名/公司模糊搜"),
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """账号列表（含待审核）。"""
    query = select(User)
    if status == "pending":
        query = query.where(User.is_active.is_(False))
    elif status == "active":
        query = query.where(User.is_active.is_(True))
    if q.strip():
        kw = f"%{q.strip()}%"
        query = query.where(or_(User.username.like(kw), User.display_name.like(kw)))

    users = (await db.execute(query.order_by(User.id))).scalars().all()
    companies = {c.id: c.name for c in
                 (await db.execute(select(Company))).scalars().all()}
    rows = []
    for u in users:
        r = _user_row(u)
        r["company_name"] = companies.get(u.company_id, "")
        rows.append(r)
    pending = sum(1 for r in rows if not r["is_active"])
    return ApiResponse(message=f"共 {len(rows)} 个账号，其中待审核 {pending}",
                       data={"users": rows, "total": len(rows), "pending": pending})


@router.post("/users", response_model=ApiResponse)
async def create_user(
    payload: dict,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """直接建号 —— **建立即可用**（`is_active=True`，与自助注册的待审核不同）。

    内部同事都能建普通账号；**只有管理员能造管理员**。
    """
    _assert_can_grant_admin(caller, payload)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if len(username) < 2:
        raise HTTPException(status_code=400, detail="用户名至少 2 个字符")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    exists = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="用户名已存在")

    u = User(
        username=username,
        email=(str(payload.get("email") or "").strip() or None),
        hashed_password=hash_password(password),
        display_name=str(payload.get("display_name") or username).strip(),
        role="admin" if payload.get("role") == "admin" else "user",
        is_superuser=bool(payload.get("is_superuser")),
        company_id=payload.get("company_id"),
        phone=str(payload.get("phone") or "").strip(),
        is_active=True,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return ApiResponse(message=f"✅ 已创建账号 {username}", data={"user": _user_row(u)})


@router.put("/users/{user_id}", response_model=ApiResponse)
async def update_user(
    user_id: int,
    payload: dict,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """改资料 / 角色 / 归属公司。

    🔴 两道线：不能改管理员账号；改角色/超管必须自己就是管理员。
    """
    u = await _get_user_or_404(user_id, db)
    _assert_can_modify(caller, u)
    if ("role" in payload and payload.get("role") != u.role) or "is_superuser" in payload:
        _assert_can_grant_admin(caller, payload)
    for key in ("email", "display_name", "phone", "review_note"):
        if key in payload and payload[key] is not None:
            setattr(u, key, str(payload[key]).strip())
    if "company_id" in payload:
        u.company_id = payload["company_id"]
    if payload.get("role") in ("admin", "user"):
        u.role = payload["role"]
    if "is_superuser" in payload:
        u.is_superuser = bool(payload["is_superuser"])
    await db.flush()
    return ApiResponse(message="已更新", data={"user": _user_row(u)})


@router.post("/users/{user_id}/approve", response_model=ApiResponse)
async def approve_user(
    user_id: int,
    payload: dict = None,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """**开通账号** —— 自助注册的账号必须走这一步才能登录。"""
    u = await _get_user_or_404(user_id, db)
    u.is_active = True
    u.approved_at = datetime.now(timezone.utc)
    u.approved_by = caller.id
    if payload and payload.get("company_id") is not None:
        u.company_id = payload["company_id"]
    await db.flush()
    return ApiResponse(message=f"✅ 已开通 {u.username}", data={"user": _user_row(u)})


@router.post("/users/{user_id}/disable", response_model=ApiResponse)
async def disable_user(
    user_id: int,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """停用账号（登录与所有已签发 token 立即失效 —— `get_current_user` 每次都回查）。"""
    u = await _get_user_or_404(user_id, db)
    _assert_can_modify(caller, u)
    if u.id == caller.id:
        raise HTTPException(status_code=400, detail="不能停用自己")
    u.is_active = False
    await db.flush()
    return ApiResponse(message=f"已停用 {u.username}", data={"user": _user_row(u)})


@router.post("/users/{user_id}/reset-password", response_model=ApiResponse)
async def reset_password(
    user_id: int,
    payload: dict,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """重置密码。"""
    new = str(payload.get("password") or "")
    if len(new) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    u = await _get_user_or_404(user_id, db)
    _assert_can_modify(caller, u)
    u.hashed_password = hash_password(new)
    await db.flush()
    return ApiResponse(message=f"已重置 {u.username} 的密码")


@router.delete("/users/{user_id}", response_model=ApiResponse)
async def delete_user(
    user_id: int,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    """**软删**（= 停用）。

    🔴 不做物理删除：这个用户名下可能有报告（`reports.user_id` 存的是账号 id），
       硬删会让那些报告变成「找不到主人」的孤儿数据。
    """
    u = await _get_user_or_404(user_id, db)
    _assert_can_modify(caller, u)
    if u.id == caller.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    u.is_active = False
    await db.flush()
    return ApiResponse(message=f"已停用 {u.username}（保留其历史报告）",
                       data={"user": _user_row(u)})


# ═══════════════════════════════════════════════════════════════
# 公司
# ═══════════════════════════════════════════════════════════════

@router.get("/companies", response_model=ApiResponse)
async def list_companies(
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    rows = (await db.execute(select(Company).order_by(Company.id))).scalars().all()
    return ApiResponse(message=f"共 {len(rows)} 家公司", data={"companies": [_company_row(c) for c in rows]})


@router.post("/companies", response_model=ApiResponse)
async def create_company(
    payload: dict,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写公司名称")
    c = Company(
        name=name,
        is_internal=bool(payload.get("is_internal")),
        short_name=str(payload.get("short_name") or "").strip(),
        implement_unit=str(payload.get("implement_unit") or "").strip(),
        intro_text=str(payload.get("intro_text") or "").strip(),
        address=str(payload.get("address") or "").strip(),
        contact=str(payload.get("contact") or "").strip(),
    )
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return ApiResponse(message=f"✅ 已创建公司 {name}", data={"company": _company_row(c)})


@router.put("/companies/{company_id}", response_model=ApiResponse)
async def update_company(
    company_id: int,
    payload: dict,
    caller: User = Depends(require_staff),
    db=Depends(get_auth_db),
):
    c = (await db.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="公司不存在")
    for key in ("name", "short_name", "implement_unit", "intro_text",
                "business_license_no", "qualification_text", "address", "contact", "status"):
        if key in payload and payload[key] is not None:
            setattr(c, key, str(payload[key]).strip())
    await db.flush()
    return ApiResponse(message="已更新", data={"company": _company_row(c)})
