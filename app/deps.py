"""FastAPI 认证依赖 —— 全站鉴权的**唯一入口**。

🔴 为什么要有这个文件
    在此之前 `routers/auth.py` 里有一个 `_get_current_user`，但**只有 auth 自己用**，
    其余 13 个 router 全部裸奔（带不带 token 都能调）。多租户下这是致命的：
    报告列表全量返回、`/reports/{id}/download` 知道 id 就能下别人的报告。

    现在 `main.py` 用**登记表式装配**给每个 router 挂上 `get_current_user`，
    并有 `tests/test_auth_coverage.py` 强制断言「除白名单外每条路由都挂了鉴权」——
    以后新增 router 忘了挂会**直接测试失败**，不靠纪律。

用法：
    普通登录即可：`include_router(m.router, dependencies=[Depends(get_current_user)])`
    仅管理员：   `include_router(m.router, dependencies=[Depends(require_admin)])`
    需要拿到用户对象：在 handler 形参写 `user: User = Depends(get_current_user)`
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.auth_db import get_auth_db, async_session as auth_session_factory
from app.models.user import User
from app.security import load_user_by_token


def is_admin(user: User) -> bool:
    """管理员判定 —— 后端**唯一**口径（前端 `authStore.isAdmin` 与之保持一致）。"""
    return bool(user and (user.role == "admin" or user.is_superuser))


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_auth_db),
) -> User:
    """必须登录。缺 header / 格式不对 / token 失效 / 账号停用 → **401**。

    🔴 这里必须 `Header(None)` 而不是 `Header(...)`：
       `Header(...)` 是「必填参数」，缺失时 FastAPI 直接返回 **422**，
       而前端 axios 拦截器**只在 401 时**清 token 跳登录（见 `api/client.js`）——
       422 会让用户看到「莫名其妙报错」而不是被请去登录。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="未登录或认证格式错误，需 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await load_user_by_token(authorization[7:], db)


async def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_auth_db),
) -> Optional[User]:
    """**可选**登录 —— 只给「未登录也能用、登录后行为不同」的公开接口用。

    ⚠️ 只允许用在 `main.py` 的公开白名单路由上；用它在受保护接口上等于把鉴权放空。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return await load_user_by_token(authorization[7:], db)
    except HTTPException:
        return None


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """必须登录**且**是管理员。"""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def is_staff(user: User, db: Optional[AsyncSession] = None) -> bool:
    """内部人员 = 管理员，**或**所属公司是我们自己（`companies.is_internal`）。

    🔴 与「报告里用哪家公司的资料」是两个**独立的轴**，别混：
         `users.company_id` → 报告里的公司信息用谁的；
         这个判定          → 能不能看知识库等内部工具。
       原先后者只认 `role == 'admin'`，于是**我们自己公司的同事**也看不到知识库。

    ⚠️ 普通员工（不是管理员）即使算「内部人员」，也**不能**给自己或别人提权 ——
       提权相关的判断一律仍走 `is_admin`（见 `routers/admin.py`）。
    """
    if is_admin(user):
        return True
    cid = getattr(user, "company_id", None)
    if not cid:
        return False
    from app.services.company_service import internal_company_id
    return await internal_company_id(db) == cid


async def require_staff(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_auth_db),
) -> User:
    """必须登录**且**是我们自己人（管理员，或挂内部公司的同事）。

    用于放行内部工具：知识库、知识问答、问卷校对、校验中心、审批回灌、账号管理。
    """
    if not await is_staff(user, db):
        raise HTTPException(status_code=403, detail="该功能仅内部人员可用，请联系管理员")
    return user


# WebSocket 关闭码 —— 前端 `stores/report.js` 的 onclose 依据它决定是否重连。
WS_CLOSE_UNAUTHORIZED = 4001
WS_CLOSE_SESSION_NOT_FOUND = 4004


async def authenticate_websocket(websocket) -> Optional[User]:
    """WebSocket 认证 —— 浏览器**不能**给 WS 设请求头，token 只能走 query 参数。

    返回 None 表示未登录/令牌无效，调用方应 `close(WS_CLOSE_UNAUTHORIZED)`。
    注意必须在 `accept()` **之前**调用：先 close 会直接拒绝握手。
    """
    token = websocket.query_params.get("token") or ""
    if not token:
        return None
    try:
        async with auth_session_factory() as db:
            return await load_user_by_token(token, db)
    except HTTPException:
        return None
