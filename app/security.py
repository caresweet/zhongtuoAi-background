"""密码哈希与 JWT —— 认证的**单一事实源**。

🔴 为什么单独成文件
    原先这套只写在 `routers/auth.py` 里。鉴权生效后 `app/deps.py` 也要用同一套
    密钥/算法/过期时间，两份实现迟早漂移（签发用一个 key、验签用另一个，
    表现是「登录成功但下一个请求 401」）。这里收敛成一份，两边都 import 它。

🔴 JWT_SECRET 的生产约束
    未设置时旧实现**每个进程各自随机生成一个 key**。单 worker 看不出问题，
    多 worker 下 A 签发的 token 到 B 直接失效（用户表现为「随机掉线」），
    且重启即全员登出。所以除 DEBUG 外**启动即失败**，不留下这个坑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def jwt_secret() -> str:
    """取签名密钥；生产环境未配置则直接失败（见模块 docstring）。"""
    if settings.JWT_SECRET:
        return settings.JWT_SECRET
    if not settings.DEBUG:
        raise RuntimeError(
            "JWT_SECRET 未配置。生产环境必须显式设置 —— 否则每个 worker 会各自"
            "随机生成密钥，导致 token 跨 worker 失效、重启即全员登出。"
        )
    return settings.jwt_secret  # DEBUG：保留旧的「随机 + 告警」行为，方便本地起服务


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_superuser": user.is_superuser,
        "exp": now + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": now,
    }
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的Token")


def user_id_from_payload(payload: dict) -> int:
    """从 token 载荷取用户 id。

    🔴 `sub` 是**外部输入**：畸形/伪造的 sub 直接 `int()` 会抛 ValueError，
       被 FastAPI 当 500 返回（应为 401）。这里统一容错。
    """
    try:
        return int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="无效的Token")


async def load_user_by_token(token: str, db: AsyncSession) -> User:
    """校验 token 并回查用户行；失效/停用一律 401。"""
    payload = decode_token(token)
    user_id = user_id_from_payload(payload)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    if not user.is_active:
        # 区分「待审核」与「被停用」：前者是自助注册还没放行，后者是被管理员停掉
        _msg = "账号待管理员审核" if user.approved_at is None else "账号已被停用"
        raise HTTPException(status_code=401, detail=_msg)
    return user
