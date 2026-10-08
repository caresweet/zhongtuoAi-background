"""阶段1：资产清单（只读盘点，不做任何移动/删除）。

对 下载/稳评资料/（本次测试材料）与 backend/storage/（现有资产）做只读盘点：
对每个文件记录 sha256、大小、类型、页数(PDF)、图片尺寸、来源路径，
并按「项目专属 / 公司固定 / 通用流程 / 法规知识 / 历史项目 / 待确认」分类。

输出：
  storage/asset_inventory/{timestamp}/materials_manifest.json   稳评资料清单
  storage/asset_inventory/{timestamp}/storage_manifest.json     storage 现有资产清单
  storage/asset_inventory/{timestamp}/classification_report.json 汇总分类 + 哈希去重
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

try:
    from PIL import Image as PILImage
except Exception:  # pragma: no cover
    PILImage = None

# ─────────────────────────────────────────────────────────────
# 分类判定（可人工复核后调整）
# ─────────────────────────────────────────────────────────────

# 公司固定资产（营业执照/资质/签名/公司通用图）
COMPANY_KEYWORDS = ["营业执照", "执照", "资质", "备案", "证书", "公司", "众拓", "盖章", "公章", "签名", "签字"]

# 通用流程/模板类（非项目专属的格式、流程图、模板）
GENERIC_KEYWORDS = ["模板", "格式", "流程图", "流程", "说明", "大纲", "体例"]

# 法规/标准知识（固定知识库）
REGULATION_KEYWORDS = ["DB32", "T4013", "T4937", "国家发改委", "暂行办法", "实施办法", "指南", "规范", "标准", "社会稳定风险评估与管理", "重大决策社会稳定风险评估"]

# 历史项目标志（其它项目名/文号，会污染当前朱坝项目）
HISTORY_PROJECT_MARKERS = ["洞庭湖路", "通港路", "淮安北", "盱眙", "盱预告", "金湖", "金征预告", "清浦",
                          "食品产业园", "三河镇", "八里社区", "高铁枢纽", "洪拟征告〔2026〕10号",
                          "2026〕10号", "2026〕5号", "2024〕35号", "2026〕3号", "清拟征告"]

# 当前朱坝测试项目专属
ZHUBA_PROJECT_MARKERS = ["朱坝", "三圩", "洪拟征告〔2026〕7号", "2026〕7号", "勘测定界", "座谈会", "公示",
                        "现场照片", "专家意见", "评审会", "问卷", "调查", "签到", "村民", "开会",
                        "稳评专家", "临时用地"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def pdf_page_count(path: Path) -> int:
    if fitz is None:
        return -1
    try:
        doc = fitz.open(str(path))
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return -1


def image_dims(path: Path):
    if PILImage is None:
        return None
    try:
        with PILImage.open(str(path)) as im:
            return {"width": im.width, "height": im.height, "format": im.format}
    except Exception:
        return None


def classify_file(path: Path, name_lower: str, area: str) -> str:
    """返回：project_specific / company_fixed / generic_process / regulation / historical_project / unknown"""
    if area == "materials":  # 稳评资料 = 本次测试项目材料（全部为项目专属或通用流程图片）
        # 图片-稳评（报告中/位置图11.jpg / 百度.jpg / 22.jpg：位置图属项目，百度截图为通用流程
        if "百度" in name_lower or "位置图" in name_lower or "22.jpg" in name_lower:
            return "generic_process"
        return "project_specific"
    if area == "uploads":  # 当前会话上传 = 本次测试材料
        return "project_specific"
    if area == "knowledge_docs":
        if any(k in name_lower for k in HISTORY_PROJECT_MARKERS):
            return "historical_project"
        if any(k in name_lower for k in REGULATION_KEYWORDS):
            return "regulation"
        if "食品产业园" in str(path):
            return "historical_project"
        return "unknown"
    if area == "templates":
        if any(k in name_lower for k in HISTORY_PROJECT_MARKERS):
            return "historical_project"
        if any(k in name_lower for k in GENERIC_KEYWORDS):
            return "generic_process"
        return "unknown"
    if area == "extracted_imgs":
        if "company" in name_lower:
            return "company_fixed"
        if "word" in str(path):
            return "company_fixed"
        return "unknown"
    if area == "generated":
        return "project_specific"
    return "unknown"


def scan_dir(root: Path, area: str, rel_root: Path) -> list:
    entries = []
    if not root.exists():
        return entries
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        if p.name in (".DS_Store", ".gitkeep"):
            continue
        rel = p.relative_to(rel_root)
        name_lower = p.name.lower()
        ext = p.suffix.lower()
        rec = {
            "path": str(rel),
            "abs_path": str(p),
            "name": p.name,
            "size": p.stat().st_size,
            "sha256": sha256(p),
            "ext": ext,
            "mtime": p.stat().st_mtime,
            "classification": classify_file(p, name_lower, area),
        }
        if ext in (".pdf",):
            rec["pages"] = pdf_page_count(p)
            rec["document_type"] = classify_pdf_doc_type(p.name)
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"):
            dims = image_dims(p)
            if dims:
                rec["image"] = dims
        entries.append(rec)
    return entries


def classify_pdf_doc_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["公告", "预公告", "征收土地"]):
        return "announcement"
    if any(k in n for k in ["勘测", "测定界"]):
        return "kd_report"  # 勘测定界
    if any(k in n for k in ["座谈", "会议"]):
        return "meeting"
    if any(k in n for k in ["专家", "评审", "意见"]):
        return "expert"
    return "other"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--materials", default=str(Path.home() / "Downloads" / "稳评资料"))
    ap.add_argument("--storage", default="storage")
    ap.add_argument("--out", default="storage/asset_inventory")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    storage_root = Path(args.storage).resolve()
    materials_root = Path(args.materials).resolve()

    mats = scan_dir(materials_root, "materials", materials_root)
    uploads = scan_dir(storage_root / "uploads", "uploads", storage_root)
    kdocs = scan_dir(storage_root / "knowledge_docs", "knowledge_docs", storage_root)
    templates = scan_dir(storage_root / "templates", "templates", storage_root)
    eximg = scan_dir(storage_root / "extracted_imgs", "extracted_imgs", storage_root)
    generated = scan_dir(storage_root / "generated", "generated", storage_root)

    # 汇总 + 按内容哈希去重（跨区域共享引用）
    all_entries = {
        "materials": mats,
        "uploads": uploads,
        "knowledge_docs": kdocs,
        "templates": templates,
        "extracted_imgs": eximg,
        "generated": generated,
    }
    by_hash: dict = {}
    for area, es in all_entries.items():
        for e in es:
            by_hash.setdefault(e["sha256"], []).append(f"{area}:{e['path']}")

    # 分类统计
    summary = {}
    for area, es in all_entries.items():
        cls: dict = {}
        for e in es:
            cls[e["classification"]] = cls.get(e["classification"], 0) + 1
        summary[area] = {"count": len(es), "classifications": cls}

    report = {
        "generated_at": ts,
        "materials_root": str(materials_root),
        "storage_root": str(storage_root),
        "summary": summary,
        "dup_files": {h: paths for h, paths in by_hash.items() if len(paths) > 1},
        "entries": all_entries,
    }

    with open(out_dir / "classification_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open(out_dir / "materials_manifest.json", "w", encoding="utf-8") as f:
        json.dump(mats, f, ensure_ascii=False, indent=1)
    with open(out_dir / "storage_manifest.json", "w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=1)

    print(f"✅ 盘点完成 → {out_dir}")
    for area, es in all_entries.items():
        print(f"  {area}: {len(es)} 个文件")
    print("\n分类统计:")
    for area, s in summary.items():
        print(f"  {area}: {s}")


if __name__ == "__main__":
    main()
