# -*- coding: utf-8 -*-
"""门户公开统计接口（无鉴权）：供官网/门户首屏展示数据规模。"""
import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "knowledge_base.db"
router = APIRouter(prefix="/api/v1/public", tags=["门户公开"])


@router.get("/stats")
async def stats():
    out = {
        "kb_docs": 0, "kb_examples": 0, "regulations": 0, "templates": 0,
        "skills": 0, "reports": 0, "reviewers": 0,
    }
    try:
        conn = sqlite3.connect(str(DB))
        out["kb_docs"] = conn.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE is_active=1").fetchone()[0]
        out["kb_examples"] = conn.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE is_active=1 AND document_type='example_report'").fetchone()[0]
        out["regulations"] = conn.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE is_active=1 AND document_type IN ('regulation','standard','local_regulation','政策法规')").fetchone()[0]
        out["templates"] = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE is_active=1").fetchone()[0]
        out["skills"] = conn.execute(
            "SELECT COUNT(*) FROM review_skills").fetchone()[0]
        conn.close()
    except Exception:
        pass
    try:
        s = json.loads((ROOT / "storage" / "sessions.json").read_text(encoding="utf-8"))
        out["reports"] = len(s.get("sessions", []))
    except Exception:
        pass
    return {"code": 0, "data": out}
