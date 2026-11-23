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

    # 法定代表人固定取公司档案（陈春），被授权人由前端询问（其他都不固定）
    from app.services.bidding_bid_builder import _asset_texts, _auth_fields
    from app.services.company_service import load_company
    company = await load_company(company_id) or {}
    af = _auth_fields(_asset_texts(str(company.get("name") or "")),
                      company=company)

    # 三家公司共用档案库，按公司隔离：这家公司有没有自己的资料/人员，
    # 前端据此提示（不能拿别家公司的证书去投标）。
    _p_count = _a_count = 0
    try:
        import sqlite3
        _c = sqlite3.connect(str(Path(settings.DATA_DIR) / "knowledge_base.db"))
        _p_count = _c.execute(
            "select count(*) from company_personnel where company_id=?",
            (company_id,)).fetchone()[0]
        _cols = {r[1] for r in _c.execute("PRAGMA table_info(asset_images)")}
        if "company_id" in _cols:
            _a_count = _c.execute(
                "select count(*) from asset_images where company_id=? and is_active=1",
                (company_id,)).fetchone()[0]
        _c.close()
    except Exception:
        pass

    max_price = req.get("max_price_wan") or req.get("budget_wan") or ""
    suggested, price_hint = _price_advice(req)
    return ApiResponse(
        message="招标文件解析完成",
        data={
            "tender_path": rel,
            "file_name": name,
            "requirements": req,
            "personnel": people,
            "company_id": company_id,
            "company_name": str(company.get("name") or ""),
            # 授权代表：前端弹窗询问（可改）
            "agent_name": af.get("agent") or "",
            "legal_name": af.get("legal") or "",
            "company_personnel_count": _p_count,
            "company_asset_count": _a_count,
            "company_ready": bool(_p_count and _a_count),
            # 报价交互：前端据此弹窗询问
            "need_price": True,
            "max_price_wan": max_price,
            # 报价参考：招标金额 + 建议报价（前端弹窗里直接展示）
            "suggested_price_wan": suggested,
            "price_hint": price_hint,
            "price_prompt": price_hint or (
                f"本项目预算/最高限价为 {max_price} 万元，请输入本次投标报价（万元）。"
                if max_price else "未在招标文件中识别到预算/最高限价，请输入本次投标报价（万元）。"),
        },
    )


def _price_advice(req: Dict[str, Any]):
    """按招标限价与评标办法给出建议报价（万元）和说明。

    口径：
      · 最低评标价法：价格是主要竞争因素，建议下浮 10% 左右；
      · 报价评分「低于等于基准价得满分」（基准价=平均价）：建议限价的 95%；
      · 其余综合评分法：建议限价的 95%，留出利润空间。
    取到 0.5 万元整数，不写死数值。
    """
    try:
        maxp = float(str(req.get("max_price_wan") or req.get("budget_wan") or 0)
                     .replace(",", ""))
    except (TypeError, ValueError):
        maxp = 0.0
    if maxp <= 0:
        return None, "未在招标文件中识别到预算/最高限价，请结合成本自行确定报价。"
    ratio, note = 0.95, "在最高限价基础上下浮 5% 左右，既有竞争力又留出利润空间"
    if req.get("price_rule") == "lowest" or req.get("bid_format") == "compact":
        ratio, note = 0.90, "本项目为最低评标价法，价格是主要竞争因素，建议下浮 10% 左右"
    if req.get("price_rule") == "benchmark_full":
        ratio, note = 0.95, "报价评分以平均价为基准价、低于等于基准价即得满分，建议报限价的 95% 左右"
    price = round(maxp * ratio * 2) / 2        # 取 0.5 万元整数
    return price, (f"招标文件最高限价（含税）{maxp:g} 万元；"
                   f"建议报价 {price:g} 万元——{note}。")


class BidGenerateRequest(BaseModel):
    tender_path: str
    company_id: int = 4
    price_wan: float = 0
    seed: Optional[int] = None
    use_llm: bool = True
    personnel: Optional[List[Dict[str, Any]]] = None
    agent_name: str = ""
    # 竞争性磋商按亩计价：报省/报市单价（元/亩）；不传按磋商文件最高限价
    unit_price_province: Optional[float] = None
    unit_price_city: Optional[float] = None


@router.post("/generate", response_model=ApiResponse)
async def generate_bid(request: BidGenerateRequest):
    """按招标格式生成投标文件 docx，返回下载链接。"""
    tender = Path(settings.STORAGE_DIR) / request.tender_path
    if not tender.exists():
        tender = Path(request.tender_path)
    if not tender.exists():
        raise HTTPException(status_code=404, detail="招标文件不存在，请重新上传")

    from app.services.bidding_bid_builder import build_bid_document
    from app.services.bidding_tender import parse_tender

    req = parse_tender(tender)
    _is_cs = req.get("bid_format") == "cuoshang"
    if not _is_cs and request.price_wan <= 0:
        raise HTTPException(status_code=400, detail="投标报价必须大于 0")
    if _is_cs and not (request.unit_price_province or request.unit_price_city):
        # 没填单价时按磋商文件最高限价出件（等于不降价，前端应提示）
        if not (req.get("unit_prices") or {}):
            raise HTTPException(status_code=400,
                                detail="本项目按亩计价，请填写报省、报市单价")
    safe = "".join(ch for ch in str(req.get("project_name") or "投标文件")
                   if ch not in '\\/:*?"<>|')[:40]
    # 询比项目叫「投选文件」、磋商项目叫「响应文件」，其余叫「投标文件」
    _kind = ("投选文件" if req.get("bid_format") == "xunbi"
             else "响应文件" if _is_cs else "投标文件")
    out_name = f"{safe}_{_kind}_{uuid.uuid4().hex[:6]}.docx"
    out = Path(settings.STORAGE_DIR) / "generated" / out_name
    try:
        await build_bid_document(
            tender_path=tender, company_id=request.company_id,
            price_wan=request.price_wan, out_path=out, seed=request.seed,
            personnel=request.personnel, use_llm=request.use_llm,
            agent_name=request.agent_name,
            unit_prices={"province": request.unit_price_province,
                         "city": request.unit_price_city}
            if (request.unit_price_province or request.unit_price_city) else None)
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
            "unit_prices": req.get("unit_prices") or {},
            "format_chapter": req.get("format_chapter"),
        },
    )
