"""按「报告域 + 地区」挑模板 —— 不同地区用不同模板（业主口径）。

背景
----
报告模板是**分区县**的：洪泽的征地稳评模板、金湖的稳评模板、盐业复垦方案模板、
投标文件模板……都是各自地区/发标单位发布的，版式（封面、目录域、页边距、评审表）
并不通用。所以模板在 `templates` 表里带两列：

    domain  —— stability / reclamation / bidding
    region  —— 洪泽 / 金湖 / 盱眙 / 涟水 …（空串 = 通用，不参与匹配）

生成时拿项目文本（区县名 + 项目名 + 需求原文）去匹配 region：

  · 命中多条 → 取地区名**最长**的那条（「淮安区」优先于「淮安」）
  · 一条都不命中：
      - 复垦 / 投标 → 用该域**通用模板**（region 为空的那条），因为这两类没有
        「多地混版」的问题，一份通用模板就该顶上；
      - 稳评 → 返回空串，交给装配器原有兜底（洪泽优先的目录域扫描）——
        稳评的老模板里混着「格式规范」「评审表」等非正文模板，按更新时间挑会挑错，
        所以**不改既有行为**。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 没命中地区时允许退化到「通用模板」的域（见模块注释）
_REGION_FALLBACK_DOMAINS = ("reclamation", "bidding")


async def resolve_template_path(domain: str, text: str) -> str:
    """按域 + 地区挑模板，返回模板文件绝对路径；没有匹配返回 ""。

    Args:
        domain: 报告域（stability / reclamation / bidding）
        text: 用于匹配地区词的项目文本（地名、项目名、需求原文拼在一起即可）
    """
    text = (text or "").strip()

    try:
        from sqlalchemy import select
        from app.database.knowledge_db import async_session
        from app.models.knowledge import Template

        async with async_session() as db:
            rows = (await db.execute(
                select(Template).where(
                    Template.is_active == True,  # noqa: E712 — SQLAlchemy 需要 ==
                    Template.domain == domain,
                ).order_by(Template.updated_at.desc())
            )).scalars().all()
    except Exception as e:
        logger.warning(f"[模板] 查询 {domain} 模板失败：{e}")
        return ""

    best_region, best = pick_region_match(rows, text)

    if not best and domain in _REGION_FALLBACK_DOMAINS:
        # 复垦/投标：没有地区模板就用该域的通用模板（region 为空的那条）
        for t in rows:
            if not (t.region or "").strip():
                best_region, best = "", t
                break

    if not best:
        return ""

    rel = best.template_file_path or ""
    if not rel.lower().endswith(".docx"):
        logger.info(f"[模板] {domain}/{best_region} 命中《{best.name}》，但文件不是 docx，跳过")
        return ""

    try:
        from app.services.file_service import file_service
        abs_path = str(file_service.get_absolute_path(rel))
    except Exception as e:
        logger.warning(f"[模板] 解析模板路径失败 {rel}: {e}")
        return ""

    logger.info(f"[模板] {domain} 按地区「{best_region}」选用《{best.name}》（{rel}）")
    return abs_path


def pick_region_match(items, text: str):
    """在 items 里按地区词匹配，返回 (命中的地区, 对应条目)；没命中返回 ("", None)。

    多个命中取地区名**最长**的那条：「淮安区」比「淮安」更具体。
    """
    best_region, best = "", None
    for t in items:
        r = (getattr(t, "region", "") or "").strip()
        if not r or r not in (text or ""):
            continue
        if len(r) > len(best_region):
            best_region, best = r, t
    return best_region, best


def build_match_text(*parts: Optional[str]) -> str:
    """把若干段项目文本拼成匹配用的字符串（去空、去重顺序拼接）。"""
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())
