"""Authentication API routes — /api/v1/auth/*"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.auth_db import get_auth_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserInfo, UserUpdateRequest
from app.schemas.knowledge import ApiResponse
from app.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


def _user_to_info(user: User, company_is_internal: bool = False) -> UserInfo:
    """Convert User ORM to UserInfo schema — single source of truth."""
    return UserInfo(
        id=user.id, username=user.username, email=user.email,
        display_name=user.display_name, role=user.role,
        is_superuser=user.is_superuser, created_at=user.created_at,
        company_id=user.company_id,
        company_is_internal=company_is_internal,
    )


async def _user_info_with_company(user: User, db) -> UserInfo:
    """带「是否内部公司」的用户信息。

    🔴 前端靠 `company_is_internal` 决定**显不显示内部工具菜单**（知识库/校对/
       审批/账号管理），必须与后端 `deps.require_staff` 同口径 —— 否则会出现
       「菜单点进去 403」或「明明能用却藏起来」。
    """
    from app.services.company_service import internal_company_id
    is_internal = bool(user.company_id) and (await internal_company_id(db)) == user.company_id
    return _user_to_info(user, is_internal)


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post("/login", response_model=ApiResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_auth_db)):
    """用户登录，返回 JWT Token。"""
    result = await db.execute(
        select(User).where(User.username == request.username.strip())
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        # 区分「还没被审核」与「被停用」—— 文案不同，用户才知道该等还是该找管理员
        if user.approved_at is None:
            raise HTTPException(status_code=403, detail="账号待管理员审核，请稍后再试")
        raise HTTPException(status_code=403, detail="账号已被停用")

    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()

    token = create_token(user)
    return ApiResponse(
        message="登录成功",
        data=TokenResponse(
            access_token=token,
            user=await _user_info_with_company(user, db),
        ).model_dump(),
    )


@router.post("/register", response_model=ApiResponse)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_auth_db)):
    """用户注册。 Set ALLOW_REGISTRATION=false in .env to disable."""
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="当前不允许公开注册，请联系管理员")

    existing = await db.execute(
        select(User).where(User.username == request.username.strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")

    user = User(
        username=request.username.strip(),
        email=request.email.strip() if request.email else None,
        hashed_password=hash_password(request.password),
        display_name=request.display_name or request.username,
        role="user",
        # 🔴 **待审核**：自助注册只算「申请」，管理员在后台开通后才能登录。
        #    不签发 token —— 直接回登录页等管理员放行。
        is_active=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    return ApiResponse(
        message="注册成功，请等待管理员审核开通后登录",
        data={"status": "pending", "username": user.username},
    )


@router.get("/me", response_model=ApiResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_auth_db),
):
    """获取当前登录用户的信息（含「是否内部公司」，前端据此显示内部工具菜单）。"""
    return ApiResponse(data=(await _user_info_with_company(current_user, db)).model_dump())


@router.put("/me", response_model=ApiResponse)
async def update_current_user(
    request: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_auth_db),
):
    """更新当前用户信息。"""
    if request.email is not None:
        current_user.email = request.email.strip()
    if request.display_name is not None:
        current_user.display_name = request.display_name.strip()
    if request.password is not None and len(request.password) >= 6:
        current_user.hashed_password = hash_password(request.password)

    await db.flush()
    await db.refresh(current_user)
    return ApiResponse(message="更新成功", data=(await _user_info_with_company(current_user, db)).model_dump())


@router.post("/refresh", response_model=ApiResponse)
async def refresh_token(current_user: User = Depends(get_current_user)):
    """刷新 Token（延长有效期）。"""
    token = create_token(current_user)
    return ApiResponse(
        message="Token已刷新",
        data={"access_token": token, "token_type": "bearer"},
    )
