# -*- coding: utf-8 -*-
"""网络舆情检索（项目名/地名 → 公开网络检索摘要）。

业主 2026-11-12：「网络舆情方面的图片一般都是要让 agent 自己去找的」。
本服务负责**检索**这一半（文字结论 + 来源链接）；检索结果页截图仍以
用户上传的「网络舆情搜索截图」为准（无浏览器环境，不在服务端截图）。

配置（.env，任选其一；不配则视为「未开展公开网络检索」）：
    WEB_SEARCH_PROVIDER=serper      # 或 tavily
    WEB_SEARCH_API_KEY=xxxx
    # 可选：WEB_SEARCH_ENDPOINT（自建/代理网关）

结果写回 `state["_web_search_results"]`，供表 3-1 / 第9章使用。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _query(project_name: str, location: str, doc_reference: str = "") -> str:
    parts = [str(x or "").strip() for x in (project_name, location, doc_reference)]
    base = " ".join(p for p in parts if p)[:120]
    return f"{base} 舆情 投诉 负面 征地"


async def search_opinion(project_name: str = "", location: str = "",
                         doc_reference: str = "") -> Dict[str, Any]:
    """返回 {available, query, summary, results:[{title,link,snippet}]}。"""
    provider = (os.getenv("WEB_SEARCH_PROVIDER") or "").strip().lower()
    key = (os.getenv("WEB_SEARCH_API_KEY") or "").strip()
    query = _query(project_name, location, doc_reference)
    if not provider or not key:
        return {"available": False, "query": query, "summary": "",
                "results": [], "reason": "未配置 WEB_SEARCH_PROVIDER/API_KEY"}
    try:
        import httpx
        if provider == "serper":
            url = os.getenv("WEB_SEARCH_ENDPOINT") or "https://google.serper.dev/search"
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(url, headers={"X-API-KEY": key,
                                               "Content-Type": "application/json"},
                                 json={"q": query, "num": 10, "gl": "cn", "hl": "zh-cn"})
                r.raise_for_status()
                data = r.json()
            items = [{"title": x.get("title", ""), "link": x.get("link", ""),
                      "snippet": x.get("snippet", "")}
                     for x in (data.get("organic") or [])][:10]
        elif provider == "tavily":
            url = os.getenv("WEB_SEARCH_ENDPOINT") or "https://api.tavily.com/search"
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(url, json={"api_key": key, "query": query,
                                            "max_results": 10,
                                            "search_depth": "basic"})
                r.raise_for_status()
                data = r.json()
            items = [{"title": x.get("title", ""), "link": x.get("url", ""),
                      "snippet": x.get("content", "")}
                     for x in (data.get("results") or [])][:10]
        else:
            return {"available": False, "query": query, "summary": "",
                    "results": [], "reason": f"不支持的 WEB_SEARCH_PROVIDER={provider}"}
    except Exception as e:
        logger.warning(f"[舆情检索] 失败（按未检索处理）: {e}")
        return {"available": False, "query": query, "summary": "",
                "results": [], "reason": str(e)[:120]}

    if not items:
        summary = "经公开网络检索，未发现与本决策直接相关的负面舆情信息。"
    else:
        neg = [x for x in items
               if any(k in (x["title"] + x["snippet"])
                      for k in ("投诉", "反对", "负面", "上访", "纠纷", "不满"))]
        if neg:
            summary = (f"经公开网络检索，发现 {len(neg)} 条可能相关的信息，"
                       f"如：{neg[0]['title']}（{neg[0]['link']}）。")
        else:
            summary = (f"经公开网络检索（{len(items)} 条结果），"
                       f"未发现与本决策直接相关的负面舆情。")
    return {"available": True, "query": query, "summary": summary,
            "results": items}
