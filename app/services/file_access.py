"""文件访问归属判断 —— `/api/v1/files/{path}` 的隔离依据。

🔴 背景
    这个接口原先**完全免鉴权**：拼路径就能读到任何上传件与任何生成的报告
    （多租户下等于把别家的营业执照图、报告正文直接交出去）。
    光要求登录还不够 —— 同一实例里的 A 账号仍能猜路径读到 B 的文件。

🔴 判断依据（两类，覆盖该接口的全部真实用途）
    ① **报告产物**：`storage/generated/*.docx` 等，由 `Report.report_file_path` /
       `review_table_path` 反查到所属报告，再比对报告归属。
    ② **会话材料**：上传的图片/附件，记录在会话 state 的 `_uploaded_files` 里，
       会话本身带 `user_id`（见 `ReportSession`）。

    两类都查不到 → **拒绝**（非管理员一律 404）。宁可少放一个，不可多放一个。

    管理员放行：运维与客服需要能取到任意文件（与报告可见性口径一致）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _norm(rel_path: str) -> str:
    return str(rel_path or "").replace("\\", "/").lstrip("/")


def _same_file(stored: str, rel: str) -> bool:
    """把存储侧记的路径（可能是绝对路径）与请求的相对路径比对。"""
    if not stored:
        return False
    s = str(stored).replace("\\", "/")
    if s == rel:
        return True
    return s.endswith("/" + rel)


async def can_read_file(rel_path: str, user) -> bool:
    """当前用户能否读取 `rel_path`（相对 storage 根）。"""
    from app.deps import is_admin

    if user is None:
        return False
    if is_admin(user):
        return True

    rel = _norm(rel_path)
    if not rel:
        return False

    if await _from_owned_report(rel, str(user.id)):
        return True
    return _from_owned_session(rel, str(user.id))


async def _from_owned_report(rel: str, user_id: str) -> bool:
    """该文件是不是当前用户名下某份报告的产物。"""
    try:
        from sqlalchemy import or_, select

        from app.database.history_db import async_session
        from app.models.history import Report

        async with async_session() as db:
            hit = (await db.execute(
                select(Report.id).where(
                    Report.user_id == user_id,
                    or_(
                        Report.report_file_path == rel,
                        Report.report_file_path == "/" + rel,
                        Report.review_table_path == rel,
                        Report.review_table_path == "/" + rel,
                    ),
                ).limit(1)
            )).first()
            return hit is not None
    except Exception as e:
        logger.warning(f"文件归属查询失败（按报告）: {e}")
        return False


def _from_owned_session(rel: str, user_id: str) -> bool:
    """该文件是不是当前用户某个会话的上传材料。"""
    try:
        from app.services.report_service import report_service

        for s in list(report_service._sessions.values()):
            if getattr(s, "user_id", "") != user_id:
                continue
            for f in (s.state.get("_uploaded_files") or []):
                p = f.get("path") if isinstance(f, dict) else f
                if p and _same_file(p, rel):
                    return True
    except Exception as e:
        logger.warning(f"文件归属查询失败（按会话）: {e}")
    return False
