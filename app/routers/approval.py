# -*- coding: utf-8 -*-
"""审批意见回灌 API —— 员工录入 送审单位/审批人/审批意见/是否通过，
沉淀到 expert_reviews（带审批人标签），供「审批偏好画像」离线蒸馏使用。

POST /api/v1/approval/reviews    录入一次送审反馈（可含多条意见）
GET  /api/v1/approval/reviews    查询（可按 reviewer/unit 过滤）
GET  /api/v1/approval/reviewers  审批人维度统计（偏好画像）
"""
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "knowledge_base.db"

router = APIRouter(prefix="/api/v1/approval", tags=["审批回灌"])

# 确保表含审批人字段
def _conn():
    conn = sqlite3.connect(str(DB))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(expert_reviews)").fetchall()}
    for c, ddl in (
        ("reviewer", "TEXT"),
        ("submit_unit", "TEXT"),
        ("passed", "INTEGER"),
    ):
        if c not in cols:
            conn.execute(f"ALTER TABLE expert_reviews ADD COLUMN {c} {ddl}")
    conn.commit()
    return conn


class IssueItem(BaseModel):
    chapter_num: int = Field(0, ge=0, le=15)
    issue_type: str = ""
    issue_desc: str = ""
    suggestion: str = ""
    severity: str = "warning"


class ApprovalBody(BaseModel):
    report_title: str = ""
    session_id: str = ""
    report_file_path: str = ""
    reviewer: str = ""          # 审批人/审核单位经办人
    submit_unit: str = ""       # 送审单位（如某区自然资源局）
    passed: bool = True         # 本次送审是否通过
    round_no: int = 1
    issues: list[IssueItem] = []


@router.post("/reviews")
async def add_review(body: ApprovalBody):
    if not body.issues:
        raise HTTPException(status_code=400, detail="请至少填一条审批意见")
    if not (body.reviewer or body.submit_unit):
        raise HTTPException(status_code=400, detail="请填写审批人或送审单位")
    conn = _conn()
    cur = conn.cursor()
    n = 0
    for it in body.issues:
        cur.execute(
            "INSERT INTO expert_reviews(report_title,domain,chapter_num,issue_type,issue_desc,"
            "suggestion,severity,session_id,report_file_path,reviewer,submit_unit,passed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.report_title or "未命名", "stability", it.chapter_num, it.issue_type,
             it.issue_desc, it.suggestion, it.severity, body.session_id,
             body.report_file_path, body.reviewer, body.submit_unit, 1 if body.passed else 0),
        )
        n += 1
    conn.commit()
    conn.close()
    return {"code": 0, "message": f"已回灌 {n} 条审批意见", "data": {"count": n}}


@router.get("/reviews")
async def list_reviews(reviewer: str = "", submit_unit: str = "", limit: int = 100):
    conn = _conn()
    q = "SELECT report_title,chapter_num,issue_type,issue_desc,suggestion,severity,reviewer,submit_unit,passed,created_at FROM expert_reviews WHERE 1=1"
    args = []
    if reviewer:
        q += " AND reviewer LIKE ?"; args.append(f"%{reviewer}%")
    if submit_unit:
        q += " AND submit_unit LIKE ?"; args.append(f"%{submit_unit}%")
    q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    rows = conn.execute(q, args).fetchall()
    conn.close()
    keys = ["report_title", "chapter_num", "issue_type", "issue_desc", "suggestion",
            "severity", "reviewer", "submit_unit", "passed", "created_at"]
    return {"code": 0, "data": [dict(zip(keys, r)) for r in rows]}


@router.get("/reviewers")
async def reviewer_stats():
    conn = _conn()
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(reviewer,''),'（未填）') reviewer, "
        "COALESCE(NULLIF(submit_unit,''),'（未填）') unit, COUNT(*) cnt, "
        "SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END) passed_cnt "
        "FROM expert_reviews GROUP BY reviewer, unit ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return {"code": 0, "data": [
        {"reviewer": r[0], "submit_unit": r[1], "total": r[2], "passed": r[3] or 0,
         "pass_rate": round((r[3] or 0) / r[2] * 100, 1)} for r in rows]}
