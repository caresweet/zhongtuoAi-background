"""Authentication API routes — /api/v1/auth/*"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.auth_db import get_auth_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserInfo, UserUpdateRequest
from app.schemas.knowledge import ApiResponse

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def _create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_superuser": user.is_superuser,
        "exp": now + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": now,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的Token")


def _user_to_info(user: User) -> UserInfo:
    """Convert User ORM to UserInfo schema — single source of truth."""
    return UserInfo(
        id=user.id, username=user.username, email=user.email,
        display_name=user.display_name, role=user.role,
        is_superuser=user.is_superuser, created_at=user.created_at,
    )


async def _get_current_user(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_auth_db),
) -> User:
    """Dependency: extract and validate the current user from the Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="认证格式错误，需 Bearer token")
    payload = _decode_token(authorization[7:])
    user_id = int(payload.get("sub", 0))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post("/login", response_model=ApiResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_auth_db)):
    """用户登录，返回 JWT Token。"""
    result = await db.execute(
        select(User).where(User.username == request.username.strip())
    )
    user = result.scalar_one_or_none()

    if user is None or not _verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被停用")

    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()

    token = _create_token(user)
    return ApiResponse(
        message="登录成功",
        data=TokenResponse(access_token=token, user=_user_to_info(user)).model_dump(),
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
        hashed_password=_hash_password(request.password),
        display_name=request.display_name or request.username,
        role="user",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = _create_token(user)
    return ApiResponse(
        message="注册成功",
        data=TokenResponse(access_token=token, user=_user_to_info(user)).model_dump(),
    )


@router.get("/me", response_model=ApiResponse)
async def get_current_user_info(current_user: User = Depends(_get_current_user)):
    """获取当前登录用户的信息。"""
    return ApiResponse(data=_user_to_info(current_user).model_dump())


@router.put("/me", response_model=ApiResponse)
async def update_current_user(
    request: UserUpdateRequest,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_auth_db),
):
    """更新当前用户信息。"""
    if request.email is not None:
        current_user.email = request.email.strip()
    if request.display_name is not None:
        current_user.display_name = request.display_name.strip()
    if request.password is not None and len(request.password) >= 6:
        current_user.hashed_password = _hash_password(request.password)

    await db.flush()
    await db.refresh(current_user)
    return ApiResponse(message="更新成功", data=_user_to_info(current_user).model_dump())


@router.post("/refresh", response_model=ApiResponse)
async def refresh_token(current_user: User = Depends(_get_current_user)):
    """刷新 Token（延长有效期）。"""
    token = _create_token(current_user)
    return ApiResponse(
        message="Token已刷新",
        data={"access_token": token, "token_type": "bearer"},
    )
