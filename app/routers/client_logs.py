"""前端日志上报 —— `/api/v1/logs/frontend`。

🔴 为什么需要
    前端日志原先**只进浏览器控制台**（28 处 `console.*`），用户一句「报错了」
    我们什么都拿不到。现在统一上报到后端，落到与后端日志同目录的
    `backend/logs/frontend.log`，排查时可以和后端日志对时间线。

🔴 为什么要求登录
    这是个**写接口**，匿名开放等于给任何人一个往服务器磁盘写文件的口子。
    前端也只在登录后才上报（见 `frontend/src/utils/logger.js`）。
    另外做了三重限流：单批条数、单条长度、每分钟批数 —— 防止前端出死循环
    把日志刷爆（日志灌满磁盘本身就是一种故障）。
"""

from __future__ import annotations

import logging
import time
from typing import List

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.models.user import User
from app.schemas.knowledge import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/logs", tags=["前端日志"])

_MAX_BATCH = 50          # 单批最多 50 条
_MAX_LINE = 2000         # 单条最多 2000 字
_MAX_PER_MINUTE = 12     # 每个进程每分钟最多收 12 批（≈600 条）

_recent: List[float] = []


def _rate_limited() -> bool:
    now = time.time()
    _recent[:] = [t for t in _recent if now - t < 60]
    if len(_recent) >= _MAX_PER_MINUTE:
        return True
    _recent.append(now)
    return False


@router.post("/frontend", response_model=ApiResponse)
async def report_frontend_logs(payload: dict, user: User = Depends(get_current_user)):
    """接收前端的错误/关键日志批次，追加到 `logs/frontend.log`。"""
    from app.utils.logging_config import log_frontend

    raw = payload.get("lines") or []
    if not isinstance(raw, list) or not raw:
        return ApiResponse(message="empty")

    if _rate_limited():
        # 静默丢弃：这是限流，不是错误，不该再让前端重试
        return ApiResponse(message="throttled")

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    who = getattr(user, "username", "") or "?"
    out = []
    for item in raw[:_MAX_BATCH]:
        if isinstance(item, dict):
            lvl = str(item.get("level") or "log")[:8]
            page = str(item.get("page") or "")[:120]
            msg = str(item.get("message") or "")[:_MAX_LINE]
        else:
            lvl, page, msg = "log", "", str(item)[:_MAX_LINE]
        if not msg.strip():
            continue
        out.append(f"[{stamp}] [{who}] [{lvl}] [{page}] {msg}")

    if out:
        log_frontend(out)
    return ApiResponse(message=f"logged {len(out)}")
