# -*- coding: utf-8 -*-
"""网络舆情检索：没配搜索 API 时必须明确「未开展」，不能假装查过。"""
import asyncio


def test_web_opinion_without_api_key(monkeypatch):
    from app.services.web_opinion import search_opinion
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("WEB_SEARCH_API_KEY", raising=False)
    # 单测不走外网：关掉免 key 的公开检索兜底
    monkeypatch.setenv("WEB_SEARCH_FREE", "0")
    r = asyncio.run(search_opinion("测试项目", "测试街道"))
    assert r["available"] is False
    assert "测试项目" in r["query"] and "舆情" in r["query"]
    assert r["summary"] == ""


def test_search_web_free_fallback(monkeypatch):
    """通用检索：无 key 时走必应公开页（行政区划核验也走这条路）。"""
    import app.services.web_opinion as wo

    async def fake_free(q):
        return [{"title": "马头镇人民政府", "link": "https://www.gov.cn/x",
                 "snippet": "淮阴区马头镇人民政府"}]

    monkeypatch.setattr(wo, "_bing_free", fake_free)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("WEB_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("WEB_SEARCH_FREE", "1")
    items = asyncio.run(wo.search_web("马头镇人民政府"))
    assert items and "马头镇" in items[0]["title"]
