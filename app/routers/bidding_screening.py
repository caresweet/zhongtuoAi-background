"""招标筛选 API — 上传千里马 excel，二次筛选返回可做项目 + 链接。"""

from __future__ import annotations

import os
import tempfile
from typing import List

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.services.bidding_screener.excel_parser import parse_excel
from app.services.bidding_screener.capability import get_capability_summary
from app.services.bidding_screener.matcher import screen_projects

router = APIRouter(prefix="/api/v1/bidding-screening", tags=["招标筛选"])


@router.get("/capabilities")
async def capabilities():
    """返回公司能力清单（供前端展示/确认）。"""
    return JSONResponse({
        "code": 0,
        "data": {
            "company": "江苏众拓项目代理咨询有限公司",
            "capabilities": get_capability_summary(),
        },
    })


@router.post("/upload")
async def upload_and_screen(file: UploadFile = File(...), use_llm: bool = True):
    """上传千里马导出 excel → 解析 → 规则粗筛 → LLM 精筛 → 返回匹配项目。

    Args:
        file: .xlsx/.xls/.csv 文件
        use_llm: 是否用 LLM 精筛（默认 true）
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xlsx", ".xls", ".xlsm", ".csv"):
        return JSONResponse({
            "code": 1, "message": f"不支持的文件类型 {ext}，请上传 .xlsx/.xls/.csv",
        })

    # 落盘到临时文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        tmp.write(await file.read())
        tmp.close()

        # 解析
        projects = parse_excel(tmp.name)
        if not projects:
            return JSONResponse({
                "code": 1, "message": "未能解析出招标项目，请检查 excel 表头是否为标准列名",
            })

        # 筛选
        llm_service = None
        if use_llm:
            from app.services.llm_service import LLMService
            llm_service = LLMService()

        matched = await screen_projects(projects, llm_service=llm_service, use_llm=use_llm)

        return JSONResponse({
            "code": 0,
            "data": {
                "total": len(projects),
                "matched": len(matched),
                "projects": matched,
            },
        })
    except Exception as e:
        return JSONResponse({"code": 1, "message": f"筛选失败: {e}"})
    finally:
        try:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        except Exception:
            pass
