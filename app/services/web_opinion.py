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
    # 🔴 关键词必须保留：先截项目名，再接检索词（否则长项目名把「舆情」挤掉，
    #    搜出来全是地区百科，截图看不出是在查舆情）。
    base = " ".join(p for p in parts if p)[:80]
    return f"{base} 舆情 投诉 上访 负面 征地"


async def search_opinion(project_name: str = "", location: str = "",
                         doc_reference: str = "") -> Dict[str, Any]:
    """返回 {available, query, summary, results:[{title,link,snippet}]}。"""
    provider = (os.getenv("WEB_SEARCH_PROVIDER") or "").strip().lower()
    key = (os.getenv("WEB_SEARCH_API_KEY") or "").strip()
    query = _query(project_name, location, doc_reference)
    if not provider or not key:
        # 没有账号/Key 时的兜底：必应公开检索页（无需 key）。
        # 业主 2026-11-12：「没有账号」—— 不能因此完全不查。
        if (os.getenv("WEB_SEARCH_FREE") or "1").strip() not in ("0", "false", "no"):
            items = await _bing_free(query)
            if items:
                return _summarize(True, query, items)
        return {"available": False, "query": query, "summary": "",
                "results": [], "reason": "未配置搜索 API，且公开检索无结果"}
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

    return _summarize(True, query, items)


def _summarize(available: bool, query: str, items) -> Dict[str, Any]:
    if not items:
        return {"available": False, "query": query, "summary": "",
                "results": [], "reason": "检索无结果"}
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
            "results": items, "provider": "free_bing"}


async def _bing_free(query: str):
    """无需 key 的必应公开检索页解析（失败返回空列表）。"""
    import html as _html
    import re as _re
    try:
        import httpx
        headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get("https://cn.bing.com/search",
                            params={"q": query, "count": 10, "setlang": "zh-CN"},
                            headers=headers)
            r.raise_for_status()
        text = r.text
    except Exception as e:
        logger.warning(f"[舆情检索] 公开检索失败（按未检索处理）: {e}")
        return []
    items = []
    for block in _re.findall(r'<li class="b_algo".*?</li>', text, _re.S)[:10]:
        m = _re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                       block, _re.S)
        if not m:
            continue
        link = _html.unescape(m.group(1))
        title = _html.unescape(_re.sub(r'<[^>]+>', '', m.group(2))).strip()
        sm = _re.search(r'<p[^>]*>(.*?)</p>', block, _re.S)
        snippet = (_html.unescape(_re.sub(r'<[^>]+>', '', sm.group(1))).strip()
                   if sm else "")
        if title:
            items.append({"title": title, "link": link, "snippet": snippet})
    return items



async def capture_search_screenshot(query: str, out_path: str) -> str:
    """用无头 Chromium 打开必应检索页并截图（真正的「网站搜索截图」）。

    服务器未安装 Playwright/Chromium 时返回空串，调用方回退到系统生成的
    检索记录图。安装：
        pip install playwright && playwright install chromium
    """
    if (os.getenv("WEB_OPINION_SCREENSHOT") or "1").strip() in ("0", "false", "no"):
        return ""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        logger.info("[舆情] Playwright 未安装，跳过网页截图")
        return ""
    from urllib.parse import quote
    url = f"https://cn.bing.com/search?q={quote(query)}&count=10&setlang=zh-CN"
    try:
        from pathlib import Path as _Path
        _Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page(
                viewport={"width": 1400, "height": 1100}, locale="zh-CN")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            await page.screenshot(path=out_path, full_page=False)
            await browser.close()
        return out_path
    except Exception as e:
        logger.warning(f"[舆情] 网页截图失败（回退记录图）: {e}")
        return ""

def render_opinion_record(result: Dict[str, Any], out_path: str) -> str:
    """把检索结果画成一张「网络舆情检索记录」PNG（无需浏览器/截图服务）。

    🔴 如实标注「系统检索记录」，不冒充搜索引擎页面截图；含检索词、时间、
    引擎、前 8 条结果（标题+链接+摘要），供报告第9章与附件留档。
    """
    from datetime import datetime
    from pathlib import Path as _Path
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return ""
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    fp = next((x for x in font_paths if _Path(x).exists()), None)
    def F(sz):
        try:
            return ImageFont.truetype(fp, sz) if fp else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    def wrap(text, font, width):
        lines, cur = [], ""
        for ch in str(text):
            if d.textlength(cur + ch, font=font) <= width:
                cur += ch
            else:
                lines.append(cur); cur = ch
        if cur: lines.append(cur)
        return lines

    y = 60
    d.text((60, y), "网络舆情检索记录", font=F(40), fill="black"); y += 70
    d.text((60, y), f"检索时间：{datetime.now():%Y-%m-%d %H:%M}", font=F(20), fill="black"); y += 34
    d.text((60, y), f"检索词：{result.get('query', '')}", font=F(20), fill="black"); y += 30
    d.text((60, y), f"检索方式：{result.get('provider') or '公开网络检索'}", font=F(20), fill="black"); y += 40
    d.text((60, y), str(result.get("summary") or ""), font=F(21), fill="black"); y += 50
    d.line([(60, y), (W - 60, y)], fill="black", width=2); y += 24
    for i, it in enumerate((result.get("results") or [])[:8], 1):
        title = f"{i}. {it.get('title', '')}"
        for ln in wrap(title, F(22), W - 140):
            d.text((60, y), ln, font=F(22), fill="black"); y += 32
        for ln in wrap(it.get("link", ""), F(17), W - 140)[:2]:
            d.text((72, y), ln, font=F(17), fill=(0, 60, 160)); y += 26
        for ln in wrap(it.get("snippet", ""), F(18), W - 150)[:3]:
            d.text((72, y), ln, font=F(18), fill=(60, 60, 60)); y += 26
        y += 14
        if y > H - 160:
            break
    d.text((60, H - 80), "说明：本页为系统按公开网络检索结果生成的记录，链接可自行核验。",
           font=F(16), fill=(90, 90, 90))
    try:
        _Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path
    except Exception:
        return ""
