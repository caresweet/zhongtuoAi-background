"""公司档案读取 —— 报告里「公司信息」的唯一来源。

🔴 为什么要有这个模块
    报告的**公司简介、编写单位、营业执照、资质证书、人员名册**原先全部**写死**
    在 `report_assembler` 里（江苏众拓的四段简介、编写单位字符串、`stability_cert_*`
    固定图片文件）。多租户下这是最直接的错误：**外部用户出报告会把客户抬头
    印成我们的公司**。

    现在这些都从 `companies` 表按**归属公司**取；取不到就显示「待填写」，
    **绝不回落成江苏众拓**。

🔴 为什么在这里读而不是装配器里读
    装配器 `assemble(state)` 是**同步**的，而 auth 库是异步会话 ——
    在同步函数里跑 `asyncio.run` 会因为「事件循环已在运行」直接炸。
    所以在**异步环节**（工作流节点）先把公司读成普通 dict，放进 state 传下去。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 公司字段缺失时报告里显示的占位（业主口径：「没有就填空显示这边待填写」）
PLACEHOLDER = "待填写"

# 参与报告渲染的公司字段
_COMPANY_FIELDS = (
    "id", "name", "is_internal", "short_name",
    "intro_text", "implement_unit", "business_license_no",
    "qualification_text", "address", "contact",
    # 🔴 评审表「稳评实施单位·负责人 / 职务」—— 按公司走（每家负责人不同）
    "leader_name", "leader_title",
)


async def load_company(company_id: Optional[int]) -> Dict[str, Any]:
    """按 id 读公司档案；`company_id` 为空或读不到 → 返回空 dict。"""
    if not company_id:
        return {}
    try:
        from sqlalchemy import select

        from app.database.auth_db import async_session
        from app.models.user import Company

        async with async_session() as db:
            row = (await db.execute(
                select(Company).where(Company.id == int(company_id))
            )).scalar_one_or_none()
            if row is None:
                return {}
            return {f: getattr(row, f, None) for f in _COMPANY_FIELDS}
    except Exception as e:
        logger.warning(f"读取公司档案失败（company_id={company_id}）：{e}")
        return {}


def company_value(company: Dict[str, Any], key: str, default: str = PLACEHOLDER) -> str:
    """取公司字段；空/缺失一律给占位，**不做任何『回落成本公司』的兜底**。

    🔴 这是「绝不套用中拓信息」的**结构性保证** —— 所有公司字段都必须经过它，
      而不是各处各写一句 `or "江苏众拓…"`（实测就是这样散出去的）。
    """
    v = (company or {}).get(key)
    s = str(v).strip() if v is not None else ""
    return s or default


def intro_paragraphs(company: Dict[str, Any]) -> list:
    """公司简介 → 段落列表（按空行切）。取不到返回空列表，由调用方决定怎么占位。"""
    text = str((company or {}).get("intro_text") or "").strip()
    if not text:
        return []
    return [seg.strip() for seg in text.split("\n") if seg.strip()]


# ═══════════════════════════════════════════════════════════════
# 内部公司判定 —— 权限体系的第二个轴
# ═══════════════════════════════════════════════════════════════
# 🔴 为什么需要它
#    多租户里有**两个独立的问题**，别混为一谈：
#      ① 报告里的公司信息用谁的？  → 看 `users.company_id` 指向的公司资料
#      ② 谁能看知识库等内部工具？  → 看「是不是我们自己人」
#    原先 ② 只认 `role == 'admin'`，于是**我们自己公司的同事**
#    （挂了内部公司、但不是 admin）连知识库都看不到 —— 这在只有一个人运维时
#    看不出问题，人一多就是错的。
#    现在 ② = `role == 'admin'` **或** 所属公司 `is_internal`。
#
# 缓存：内部公司那一行是种子数据、基本不变，但每次请求都查一次太浪费。
#      这里缓存首次查询结果（进程级）。改了 `is_internal` 需要重启生效 ——
#      这条数据本来就不该在运行时改。

_INTERNAL_CID_CACHE: Dict[str, Any] = {"loaded": False, "id": None}


async def internal_company_id(db=None) -> Optional[int]:
    """我们自己公司的 id（`companies.is_internal = 1`）；没有则 None。"""
    if _INTERNAL_CID_CACHE["loaded"]:
        return _INTERNAL_CID_CACHE["id"]
    if db is None:
        from app.database.auth_db import async_session
        async with async_session() as _db:
            return await internal_company_id(_db)
    try:
        from sqlalchemy import select

        from app.models.user import Company

        cid = (await db.execute(
            select(Company.id).where(Company.is_internal.is_(True)).order_by(Company.id).limit(1)
        )).scalar_one_or_none()
        _INTERNAL_CID_CACHE.update({"loaded": True, "id": cid})
        return cid
    except Exception as e:
        logger.warning(f"读取内部公司 id 失败：{e}")
        return None


def reset_internal_company_cache() -> None:
    """测试/迁移后清缓存。"""
    _INTERNAL_CID_CACHE.update({"loaded": False, "id": None})
