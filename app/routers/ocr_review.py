# -*- coding: utf-8 -*-
"""问卷 OCR 校对工作台 API。

数据源：data/ocr_review_dataset.json（由 scripts/build_ocr_review_set.py 生成）
图像：storage/ocr_review/<doc_id>/page_<n>.png

- GET  /ocr-review/projects   项目概览
- GET  /ocr-review/records    记录列表（可带 ?doc_id=）
- PUT  /ocr-review/records    保存校对 {doc_id,page,text,status}
- GET  /ocr-review/image/{doc_id}/{page}   问卷页图像
- GET  /ocr-review/export     导出已校对数据（JSONL，供训练）
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_FILE = ROOT / "data" / "ocr_review_dataset.json"
IMG_ROOT = ROOT / "storage" / "ocr_review"

router = APIRouter(prefix="/api/v1/ocr-review", tags=["OCR校对工作台"])


def _load() -> list:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return []


def _save(records: list):
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")


@router.get("/projects")
async def list_projects():
    records = _load()
    by = {}
    for r in records:
        p = by.setdefault(r["doc_id"], {"doc_id": r["doc_id"], "doc": r["doc"], "pages": 0, "done": 0, "text_only": bool(r.get("text_only"))})
        p["pages"] += 1
        if r.get("status") == "done":
            p["done"] += 1
    return {"code": 0, "data": sorted(by.values(), key=lambda x: x["doc_id"])}


@router.get("/records")
async def list_records(doc_id: int | None = Query(None)):
    records = _load()
    if doc_id is not None:
        records = [r for r in records if r["doc_id"] == doc_id]
    return {"code": 0, "data": records}


@router.put("/records")
async def save_record(body: dict):
    doc_id = int(body.get("doc_id"))
    page = int(body.get("page"))
    text = (body.get("text") or "").strip()
    status = body.get("status", "done")
    records = _load()
    for r in records:
        if r["doc_id"] == doc_id and r["page"] == page:
            r["corrected"] = text
            r["status"] = status
            _save(records)
            return {"code": 0, "message": "已保存", "data": r}
    raise HTTPException(status_code=404, detail="记录不存在")


@router.get("/image/{doc_id}/{page}")
async def get_image(doc_id: int, page: int):
    p = IMG_ROOT / str(doc_id) / f"page_{page}.png"
    if not p.exists():
        raise HTTPException(status_code=404, detail="图像不存在")
    return FileResponse(str(p), media_type="image/png")


@router.get("/export")
async def export():
    lines = []
    for r in _load():
        if r.get("status") != "done":
            continue
        lines.append(json.dumps({
            "doc_id": r["doc_id"], "page": r["page"], "doc": r["doc"],
            "image": r["image"],
            "text": r.get("corrected") or r.get("text", ""),
        }, ensure_ascii=False))
    if not lines:
        raise HTTPException(status_code=400, detail="暂无已校对数据")
    return PlainTextResponse("\n".join(lines), media_type="application/x-ndjson")
