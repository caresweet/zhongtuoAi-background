# -*- coding: utf-8 -*-
"""校验兜底 API：阈值规则库 CRUD + 对某会话报告实时跑规则 + 必填核对单。"""
from fastapi import APIRouter, HTTPException

from app.validation import rule_checker
from app.services.report_service import report_service

router = APIRouter(prefix="/api/v1/check-rules", tags=["校验规则库"])

DEFAULTS = rule_checker.load_rules()


@router.get("")
async def list_rules():
    return {"code": 0, "data": rule_checker.load_rules()}


@router.put("/{rule_id}")
async def update_rule(rule_id: str, body: dict):
    rules = rule_checker.load_rules()
    for r in rules:
        if r.get("id") == rule_id:
            for k in ("enabled", "value", "severity", "basis", "note", "label", "metric"):
                if k in body:
                    r[k] = body[k]
            rule_checker.save_rules(rules)
            return {"code": 0, "message": "规则已更新", "data": r}
    raise HTTPException(status_code=404, detail="规则不存在")


@router.post("/reset")
async def reset_rules():
    rule_checker.save_rules(DEFAULTS)
    return {"code": 0, "message": "已恢复默认规则"}


@router.get("/report/{session_id}")
async def check_report(session_id: str):
    """对指定会话报告当前 filled_data 跑规则 + 输出必填核对单。"""
    try:
        session = report_service.get_session(session_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"会话不存在: {e}")
    filled = (session.state or {}).get("filled_data", {}) or {}
    issues = rule_checker.run_checks(filled)
    missing = rule_checker.missing_checklist(filled)
    return {
        "code": 0,
        "data": {
            "rule_issues": issues,
            "missing": missing,
            "metrics": rule_checker.extract_metrics(filled),
            "session_id": session_id,
        },
    }
