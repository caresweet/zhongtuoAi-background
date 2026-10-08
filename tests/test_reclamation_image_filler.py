"""缺图网络补齐：记录来源与时间；无凭证安全降级；检索词带项目名。

注：**本仓未装 pytest-asyncio**，异步用例一律用 `asyncio.run`，不用
`@pytest.mark.asyncio`（仓库既有约定）。
"""
import asyncio
from datetime import datetime

from app.services.reclamation_image_filler import (
    ImageSearchProvider,
    ReclamationImageFiller,
    build_default_provider,
)


class _FakeProvider(ImageSearchProvider):
    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    async def search(self, query, limit=5):
        self.queries.append(query)
        return self.hits[:limit]


def test_fill_records_provenance():
    p = _FakeProvider([{"url": "https://x/a.jpg", "source_page": "https://x/page"}])
    out = asyncio.run(ReclamationImageFiller(p).fill("项目地理位置示意图"))
    assert len(out) == 1
    assert out[0]["url"] == "https://x/a.jpg"
    assert out[0]["source_page"] == "https://x/page"
    datetime.fromisoformat(out[0]["fetched_at"])   # 必须记时间，供溯源


def test_no_credentials_degrades_to_empty():
    filler = ReclamationImageFiller(build_default_provider())
    assert asyncio.run(filler.fill("项目地理位置示意图")) == []


def test_query_includes_project_name():
    p = _FakeProvider([{"url": "u", "source_page": "s"}])
    asyncio.run(ReclamationImageFiller(p).fill("现状图", project_name="某扩建项目"))
    assert "某扩建项目" in p.queries[0]


def test_provider_failure_does_not_raise():
    class _Boom(ImageSearchProvider):
        async def search(self, query, limit=5):
            raise RuntimeError("供应商挂了")

    assert asyncio.run(ReclamationImageFiller(_Boom()).fill("现状图")) == []
