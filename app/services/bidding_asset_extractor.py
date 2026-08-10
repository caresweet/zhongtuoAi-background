"""BiddingAssetExtractor — extract reusable company assets from a 投标文件 sample.

The generic parts of a bid document (营业执照, 财务报告, 社保/纳税, 法人证明,
授权委托书, 承诺函, 资质证书, 人员配备, 设备清单) recur across projects. This
service segments an uploaded 投标文件's text into typed assets and upserts them
into the company_assets table so future bid generation reuses them instead of
re-inventing them.

Dedup: keyed by (company, asset_type, title). Existing row → update, else insert.
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_COMPANY = "江苏众拓测绘有限公司"

# Asset types we recognize
ASSET_TYPES = [
    "营业执照", "财务报告", "社保纳税", "法人证明", "授权委托",
    "承诺函", "资质证书", "人员", "设备", "业绩", "其他",
]

_EXTRACT_PROMPT = """你是招投标文件解析专家。下面是一份《投标文件》的正文。请从中抽取出**通用的、可跨项目复用的**公司资料，忽略与具体项目强绑定的内容（如本次项目名称、报价、针对本项目的技术方案正文）。

按以下资料类型归类抽取（有则输出，无则跳过该类）：
- 营业执照：公司名称、统一社会信用代码、注册资本、成立日期、经营范围等
- 财务报告：财务状况说明（年度、主要财务数据）
- 社保纳税：依法缴纳税收和社保的说明
- 法人证明：法定代表人资格证明信息（姓名、职务）
- 授权委托：授权委托书的通用格式与受托人信息
- 承诺函：各类承诺函的完整正文（信用、无重大违法、中小企业声明等）
- 资质证书：测绘资质等资质证书信息（资质等级、证书编号、有效期）
- 人员：项目团队人员配备（每人：姓名、职称/职务、执业资格证书、拟任角色）
- 设备：公司拥有的设备清单
- 业绩：类似项目业绩

以 JSON 数组输出，每个元素：
{{"asset_type": "类型", "title": "简短标题", "content": "完整正文内容", "is_structured": false}}
其中「人员」类型请把 content 写成 JSON 字符串（人员数组），并令 is_structured=true。
只输出 JSON 数组，不要额外文字。

【投标文件正文】
{doc_text}"""


class BiddingAssetExtractor:
    def __init__(self, llm_service=None):
        if llm_service is None:
            from app.services.llm_service import llm_service as _llm
            llm_service = _llm
        self._llm = llm_service

    async def extract_and_store(
        self, doc_text: str, source_file: str = "", company: str = DEFAULT_COMPANY,
    ) -> Dict[str, Any]:
        """Extract reusable assets from a bid document and upsert to DB.

        Returns a summary dict: {extracted, inserted, updated, by_type}.
        """
        if not doc_text or len(doc_text.strip()) < 100:
            return {"extracted": 0, "inserted": 0, "updated": 0, "by_type": {}}

        assets = await self._llm_extract(doc_text)
        if not assets:
            return {"extracted": 0, "inserted": 0, "updated": 0, "by_type": {}}

        inserted, updated, by_type = await self._upsert(assets, company, source_file)
        return {
            "extracted": len(assets),
            "inserted": inserted,
            "updated": updated,
            "by_type": by_type,
        }

    async def _llm_extract(self, doc_text: str) -> List[Dict[str, Any]]:
        if not self._llm or not self._llm.is_available:
            return []
        prompt = _EXTRACT_PROMPT.format(doc_text=doc_text[:30000])
        try:
            resp = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=6000,
                temperature=0.1,
            )
        except Exception as e:
            logger.error(f"Asset extraction LLM call failed: {e}")
            return []
        return self._parse_json_array(resp)

    def _parse_json_array(self, text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        # strip code fences
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1)
        # find first [ ... ] block
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            text = m.group(0)
        try:
            data = json.loads(text)
            if isinstance(data, list):
                out = []
                for a in data:
                    if not isinstance(a, dict):
                        continue
                    at = str(a.get("asset_type", "其他")).strip()
                    if at not in ASSET_TYPES:
                        at = "其他"
                    out.append({
                        "asset_type": at,
                        "title": str(a.get("title", "")).strip()[:255],
                        "content": a.get("content", "") if isinstance(a.get("content"), str) else json.dumps(a.get("content"), ensure_ascii=False),
                        "is_structured": bool(a.get("is_structured", False)),
                    })
                return out
        except json.JSONDecodeError as e:
            logger.warning(f"Asset JSON parse failed: {e}")
        return []

    async def _upsert(self, assets, company, source_file):
        from sqlalchemy import select
        from app.database.knowledge_db import async_session
        from app.models.knowledge import CompanyAsset

        inserted = updated = 0
        by_type: Dict[str, int] = {}
        async with async_session() as db:
            for a in assets:
                title = a["title"] or a["asset_type"]
                existing = (
                    await db.execute(
                        select(CompanyAsset).where(
                            CompanyAsset.company == company,
                            CompanyAsset.asset_type == a["asset_type"],
                            CompanyAsset.title == title,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.content = a["content"]
                    existing.is_structured = a["is_structured"]
                    existing.source_file = source_file
                    existing.is_active = True
                    updated += 1
                else:
                    db.add(CompanyAsset(
                        company=company,
                        asset_type=a["asset_type"],
                        title=title,
                        content=a["content"],
                        is_structured=a["is_structured"],
                        source_file=source_file,
                    ))
                    inserted += 1
                by_type[a["asset_type"]] = by_type.get(a["asset_type"], 0) + 1
            await db.commit()
        return inserted, updated, by_type


# ── Read helpers for generation ──

async def get_assets(company: str = DEFAULT_COMPANY, asset_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch reusable assets for generation. Optionally filter by asset_type."""
    from sqlalchemy import select
    from app.database.knowledge_db import async_session
    from app.models.knowledge import CompanyAsset

    async with async_session() as db:
        q = select(CompanyAsset).where(
            CompanyAsset.company == company, CompanyAsset.is_active == True
        )
        if asset_type:
            q = q.where(CompanyAsset.asset_type == asset_type)
        rows = (await db.execute(q)).scalars().all()
        return [{
            "id": r.id, "asset_type": r.asset_type, "title": r.title,
            "content": r.content, "is_structured": r.is_structured,
        } for r in rows]


bidding_asset_extractor = BiddingAssetExtractor()
