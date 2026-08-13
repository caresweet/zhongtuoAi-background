"""Learning API — agent continuous improvement endpoints."""

from fastapi import APIRouter, Query
from app.services.learning_service import learning_service

router = APIRouter(prefix="/api/learning", tags=["学习"])


@router.get("/stats")
async def get_learning_stats(domain: str = Query("stability"), limit: int = Query(20)):
    """Get learning statistics: avg score, pass rate, common issues."""
    stats = await learning_service.get_recent_stats(domain, limit)
    common = await learning_service.get_common_issues(domain, limit)
    return {"code": 0, "data": {"stats": stats, "common_issues": common}}


@router.post("/analyze")
async def trigger_analysis(domain: str = Query("stability")):
    """Trigger experience analysis and return learning insights."""
    stats = await learning_service.get_recent_stats(domain)
    common = await learning_service.get_common_issues(domain)
    hints = await learning_service.build_learning_hints(domain)
    examples = await learning_service.get_excellent_examples(domain)
    return {
        "code": 0,
        "data": {
            "stats": stats,
            "common_issues": common,
            "learning_hints": hints,
            "excellent_examples": [{"title": e["title"]} for e in examples],
        },
    }
