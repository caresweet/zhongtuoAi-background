# -*- coding: utf-8 -*-
"""投标文件编写：招标文件解析 → 询问报价 → 生成响应文件。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.schemas.knowledge import ApiResponse

router = APIRouter(prefix="/api/v1/bidding/bid", tags=["投标文件"])


@router.post("/analyze-tender", response_model=ApiResponse)
async def analyze_tender(
    file: UploadFile = File(...),
    company_id: int = Form(4),
    region_override: str = Form(""),
):
    """上传招标文件 → 解析项目信息/资格/人员/评分/投标文件格式，并提示报价。

    报价由前端弹窗询问（业主 2026-11-05）：先告诉用户招标文件里的预算/最高限价，
    用户填完报价后再调 `/generate`。
    """
    name = os.path.basename(file.filename or "tender")
    ext = Path(name).suffix.lower()
    if ext not in (".doc", ".docx", ".pdf"):
        raise HTTPException(status_code=400, detail="只支持 .doc / .docx / .pdf 招标文件")
    raw = await file.read()
    if len(raw) > 80 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="招标文件不能超过 80MB")

    rel = f"templates/bid_tender_{uuid.uuid4().hex}{ext}"
    dest = Path(settings.STORAGE_DIR) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)

    from app.services.bidding_tender import parse_tender, select_bid_personnel
    try:
        req = parse_tender(dest)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"招标文件解析失败：{e}")
    if not req.get("project_name"):
        raise HTTPException(status_code=422, detail="未能识别招标文件内容，请确认文件格式")

    try:
        people = select_bid_personnel(company_id=company_id)
    except Exception:
        people = []

    max_price = req.get("max_price_wan") or req.get("budget_wan") or ""
    return ApiResponse(
        message="招标文件解析完成",
        data={
            "tender_path": rel,
            "file_name": name,
            "requirements": req,
            "personnel": people,
            "company_id": company_id,
            # 报价交互：前端据此弹窗询问
            "need_price": True,
            "max_price_wan": max_price,
            "price_prompt": (
                f"本项目预算/最高限价为 {max_price} 万元，请输入本次投标报价（万元）。"
                if max_price else "未在招标文件中识别到预算/最高限价，请输入本次投标报价（万元）。"),
        },
    )


class BidGenerateRequest(BaseModel):
    tender_path: str
    company_id: int = 4
    price_wan: float
    seed: Optional[int] = None
    use_llm: bool = True
    personnel: Optional[List[Dict[str, Any]]] = None


@router.post("/generate", response_model=ApiResponse)
async def generate_bid(request: BidGenerateRequest):
    """按招标格式生成投标文件 docx，返回下载链接。"""
    tender = Path(settings.STORAGE_DIR) / request.tender_path
    if not tender.exists():
        tender = Path(request.tender_path)
    if not tender.exists():
        raise HTTPException(status_code=404, detail="招标文件不存在，请重新上传")
    if request.price_wan <= 0:
        raise HTTPException(status_code=400, detail="投标报价必须大于 0")

    from app.services.bidding_bid_builder import build_bid_document
    from app.services.bidding_tender import parse_tender

    req = parse_tender(tender)
    safe = "".join(ch for ch in str(req.get("project_name") or "投标文件")
                   if ch not in '\\/:*?"<>|')[:40]
    out_name = f"{safe}_投标文件_{uuid.uuid4().hex[:6]}.docx"
    out = Path(settings.STORAGE_DIR) / "generated" / out_name
    try:
        await build_bid_document(
            tender_path=tender, company_id=request.company_id,
            price_wan=request.price_wan, out_path=out, seed=request.seed,
            personnel=request.personnel, use_llm=request.use_llm)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"投标文件生成失败：{e}")

    return ApiResponse(
        message="投标文件已生成",
        data={
            "file_name": out_name,
            "output_path": f"generated/{out_name}",
            "download_url": f"/api/v1/files/generated/{out_name}",
            "project_name": req.get("project_name"),
            "project_no": req.get("project_no"),
            "price_wan": request.price_wan,
            "format_chapter": req.get("format_chapter"),
        },
    )
