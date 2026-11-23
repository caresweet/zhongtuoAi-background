# -*- coding: utf-8 -*-
"""版式审核接口 —— 上传 DOCX / PDF / 图片，返回区块结构 + 章节树 + 异常清单。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.layout_audit import audit_document

router = APIRouter(prefix="/api/v1/layout-audit", tags=["版式审核"])

_ALLOWED = {".docx", ".pdf", ".png", ".jpg", ".jpeg", ".bmp",
            ".webp", ".tif", ".tiff"}
_MAX_BYTES = 80 * 1024 * 1024


@router.post("/file")
async def audit_file(file: UploadFile = File(...)):
    """上传文件做版式审核。

    - DOCX / 原生 PDF：以文档原生样式属性为真值；
    - 图片/扫描件：视觉估算（style.source=visual_estimate，confidence ≤0.7）。
    """
    name = file.filename or "upload.docx"
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED:
        raise HTTPException(400, f"不支持的文件类型：{ext}")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        size = os.path.getsize(tmp.name)
        if size > _MAX_BYTES:
            raise HTTPException(400, "文件超过 80MB，请拆分后再审")
        if size == 0:
            raise HTTPException(400, "上传文件为空")
        result = await audit_document(tmp.name, original_name=name)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"版式审核失败：{e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
