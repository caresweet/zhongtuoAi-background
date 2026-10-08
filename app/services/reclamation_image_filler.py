# -*- coding: utf-8 -*-
"""缺图时从网络检索补齐（业主 2026-10-08 决定要做）。

- **少步骤**：检索到即落到位置，不追加确认步骤。
- **可溯源**：每条记录 url / source_page / fetched_at。
- **不阻断**：未配凭证或供应商故障时返回空候选，绝不抛异常。

⚠️ 已知风险（业主已知悉并接受）：报审文件用网络图片存在版权风险，
地图类另涉及审图号。本实现按要求不拦截。
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ImageSearchProvider(ABC):
    """图片检索供应商抽象 —— 便于替换与测试。"""

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """返回 [{url, source_page}, ...]。"""


class _NullProvider(ImageSearchProvider):
    """未配置凭证时的空实现：永远返回空，不报错。"""

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        return []


def build_default_provider() -> ImageSearchProvider:
    """按配置构造供应商；没配凭证就返回空实现（不返回 None，免得调用方判空）。"""
    try:
        from app.config import settings
    except Exception:
        return _NullProvider()

    url = getattr(settings, "IMAGE_SEARCH_API_URL", "") or ""
    key = getattr(settings, "IMAGE_SEARCH_API_KEY", "") or ""
    if not url or not key:
        logger.info("未配置图片检索供应商，缺图补齐返回空候选（不影响生成）")
    # 具体供应商实现待凭证到位后补；此处一律空实现，保证链路可用。
    return _NullProvider()


class ReclamationImageFiller:
    """按缺图清单检索候选图片，并附来源记录。"""

    def __init__(self, provider: Optional[ImageSearchProvider] = None):
        self._provider = provider or build_default_provider()

    async def fill(self, missing: str, project_name: str = "") -> List[Dict[str, Any]]:
        """为一张缺图检索候选。返回 [] 表示没找到（调用方照常继续）。"""
        query = f"{project_name} {missing}".strip()
        try:
            hits = await self._provider.search(query, limit=5)
        except Exception as e:                     # 供应商故障不得阻断生成
            logger.warning("图片检索失败（已降级为空候选）：%s", e)
            return []

        now = datetime.now().isoformat(timespec="seconds")
        return [
            {"url": h.get("url", ""), "source_page": h.get("source_page", ""),
             "fetched_at": now, "for_figure": missing}
            for h in hits if h.get("url")
        ]
