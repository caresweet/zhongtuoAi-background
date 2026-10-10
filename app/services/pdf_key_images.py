"""PDF 关键图提取 —— **按文档类型与用途限量**抽取，不是无脑全抽。

背景：早先把 PDF 每页内嵌图全部并入报告插图，导致报告膨胀到 700 多页，于是一刀切禁用了
PDF 抽图；结果**位置示意图、勘测定界图**这些正文本来就要用的图也一起没了。

本模块只取「报告确实要用的那几张」，按类型限量：

| 文档 | 取什么 | 去向 | 上限 |
|---|---|---|---|
| 征收土地预公告 | 附图页（拟征收土地位置示意图） | 第3章（三）决策地理位置 | 1 |
| 征收土地预公告 | 盖章/落款页扫描 | 附件「拟征地公告」 | 1 |
| 勘测定界报告 | 勘测定界图 | 第3章（三）决策地理位置（附图） | 1 |

其余内嵌图（地图图例图标等）一律不要。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 渲染 DPI（够清晰又能控体积）
_RENDER_DPI = 150

# 每类上限
_MAX_PER_KIND = 1


def _largest_embedded_image(doc, page_no: int) -> Optional[bytes]:
    """取该页**面积最大**的内嵌图片原始字节（地图通常是整页图里最大那张）。

    Returns:
        图片字节；该页无内嵌图时返回 None。
    """
    try:
        pg = doc[page_no]
        infos = pg.get_images(full=True)
        if not infos:
            return None
        best, best_area = None, 0
        for info in infos:
            xref = info[0]
            try:
                base = doc.extract_image(xref)
            except Exception:
                continue
            w, h = int(base.get("width") or 0), int(base.get("height") or 0)
            if w * h > best_area:
                best, best_area = base, w * h
        # 太小的（图例图标）不要
        if best is not None and best_area >= 200 * 200:
            return best
    except Exception as e:
        logger.warning(f"取内嵌图失败 第{page_no+1}页: {e}")
    return None


def _shrink_if_huge(path: Path, max_side: int = 2200, max_bytes: int = 2_500_000) -> None:
    """图片过大时等比缩小（控报告体积，避免「几百 MB 的 docx」）。"""
    try:
        if not path.exists():
            return
        from PIL import Image
        im = Image.open(path)
        w, h = im.size
        need = max(w, h) > max_side or path.stat().st_size > max_bytes
        if not need:
            return
        scale = min(1.0, max_side / float(max(w, h)))
        im = im.convert("RGB") if im.mode in ("RGBA", "P", "LA") else im
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        im.save(path, format="JPEG", quality=85, optimize=True)
    except Exception as e:
        logger.warning(f"图片压缩失败 {path}: {e}")


def _save_map_image(doc, page_no: int, out_path: Path) -> bool:
    """优先保存该页最大的内嵌图（干净的地图）；没有则整页渲染兜底。"""
    raw = _largest_embedded_image(doc, page_no)
    if raw:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(raw["image"])
            if out_path.exists() and out_path.stat().st_size > 3000:
                _shrink_if_huge(out_path)
                return True
        except Exception as e:
            logger.warning(f"保存内嵌图失败: {e}")
    return _render_page(doc, page_no, out_path)


def _render_page(doc, page_no: int, out_path: Path) -> bool:
    """把某一页渲染成 PNG。"""
    try:
        pg = doc[page_no]
        pix = pg.get_pixmap(dpi=_RENDER_DPI)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return out_path.exists() and out_path.stat().st_size > 3000
    except Exception as e:
        logger.warning(f"渲染第{page_no+1}页失败: {e}")
        return False


# ── 整页渲染（附件用）─────────────────────────────────────────
# 与 `extract_key_images` 的分工：那边是「只取报告正文要用的那一两张」，
# 这里是「**整份**逐页出图」——附件的素材是原件扫描，一页都不能少。
_APPENDIX_PAGE_DPI = 140
_APPENDIX_MAX_SIDE = 2000
_APPENDIX_MAX_BYTES = 900_000       # 单页上限，控 docx 体积


def render_all_pages(pdf_path: str, out_dir: Optional[str] = None,
                     max_pages: Optional[int] = None) -> List[str]:
    """把一份 PDF **逐页**渲染成 JPEG，返回按页序的**绝对路径**。

    用途：复垦附件的「勘测报告 / 红线图 / 批复文件…」要按原件整页顺排。

    输出目录缺省 `storage/images/appendix/`；**文件已存在即跳过**（幂等 ——
    重复生成不会反复渲染，也不会把 docx 撑大）。渲染失败/文件坏的页跳过，不中断。
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return []
    try:
        import fitz
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.warning(f"打开 PDF 失败 {pdf_path}: {e}")
        return []

    base = (Path(out_dir) if out_dir
            else Path(__file__).resolve().parents[2] / "storage" / "images" / "appendix")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"创建附件图目录失败 {base}: {e}")
        return []

    stem = _safe_name(Path(pdf_path).stem)
    out: List[str] = []
    try:
        n = doc.page_count if max_pages is None else min(doc.page_count, int(max_pages))
        for i in range(n):
            p = base / f"pdf_{stem}_page{i + 1}.jpg"
            if not (p.exists() and p.stat().st_size > 3000):
                if not _render_page_jpeg(doc, i, p):
                    continue
            out.append(str(p))
    finally:
        try:
            doc.close()
        except Exception:
            pass
    if out:
        logger.info(f"[附件] 整页渲染 {Path(pdf_path).name}：{len(out)} 页")
    return out


def _render_page_jpeg(doc, page_no: int, out_path: Path) -> bool:
    """整页渲染成 **JPEG**（PNG 太胖，整份 PDF 进附件会把 docx 撑到几百 MB）。"""
    try:
        from PIL import Image
        pix = doc[page_no].get_pixmap(dpi=_APPENDIX_PAGE_DPI)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        w, h = im.size
        if max(w, h) > _APPENDIX_MAX_SIDE:
            s = _APPENDIX_MAX_SIDE / float(max(w, h))
            im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, format="JPEG", quality=82, optimize=True)
        return out_path.exists() and out_path.stat().st_size > 3000
    except Exception as e:
        logger.warning(f"渲染第{page_no + 1}页失败: {e}")
        return False


def _find_page(doc, *keywords: str) -> Optional[int]:
    """找第一个正文含任一关键词的页；找不到返回 None。"""
    for i in range(doc.page_count):
        txt = doc[i].get_text()
        if any(k in txt for k in keywords):
            return i
    return None


def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', '_', str(s or ""))[:60]


def extract_key_images(pdf_path: str, doc_type: str = "",
                       out_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """按文档类型抽取报告要用的关键图。

    Args:
        pdf_path: PDF 路径。
        doc_type: "announcement" | "survey" | ""（自动判断）。
        out_dir: 输出目录；缺省用 `storage/extracted_imgs/pdf_key/`。

    Returns:
        `[{path, caption, chapter, kind, source}]`；无可用图时返回 []。
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return []
    try:
        import fitz
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.warning(f"打开 PDF 失败 {pdf_path}: {e}")
        return []

    if not doc_type:
        name = os.path.basename(pdf_path)
        if any(k in name for k in ("公告", "预公告", "征收土地")):
            doc_type = "announcement"
        elif any(k in name for k in ("勘测", "测定界", "定界", "面积")):
            doc_type = "survey"

    if out_dir:
        base = Path(out_dir)
    else:
        base = Path(__file__).resolve().parents[2] / "storage" / "extracted_imgs" / "pdf_key"
    stem = _safe_name(Path(pdf_path).stem)
    out: List[Dict[str, Any]] = []

    try:
        if doc_type == "announcement":
            # ① 附图页 → 位置示意图（第3章）
            pg = _find_page(doc, "位置示意图", "征收土地位置", "附图")
            if pg is None:
                pg = doc.page_count - 1              # 多数公告附图在末页
            p = base / f"pdf_{stem}_位置示意图.png"
            if _save_map_image(doc, pg, p):
                out.append({"path": str(p), "caption": "拟征收土地位置示意图",
                            "chapter": 6, "kind": "location_map", "source": pdf_path})

            # ② 盖章/落款页 → 附件「拟征地公告」
            p2 = base / f"pdf_{stem}_盖章公告页.png"
            if _render_page(doc, 0, p2):
                out.append({"path": str(p2), "caption": "征收土地预公告（盖章）",
                            "chapter": 0, "kind": "announcement_scan", "source": pdf_path})

        elif doc_type == "survey":
            pg = _find_page(doc, "勘测定界图", "勘测定界")
            if pg is None:
                pg = doc.page_count - 1
            p = base / f"pdf_{stem}_勘测定界图.png"
            if _save_map_image(doc, pg, p):
                out.append({"path": str(p), "caption": "勘测定界图",
                            "chapter": 6, "kind": "survey_map", "source": pdf_path})
    finally:
        try:
            doc.close()
        except Exception:
            pass

    # 按 kind 限量
    seen: Dict[str, int] = {}
    limited: List[Dict[str, Any]] = []
    for item in out:
        k = item["kind"]
        if seen.get(k, 0) >= _MAX_PER_KIND:
            continue
        seen[k] = seen.get(k, 0) + 1
        limited.append(item)

    if limited:
        logger.info(f"PDF 关键图提取 {Path(pdf_path).name}: {[x['kind'] for x in limited]}")
    return limited


def collect_key_images(materials: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从已解析材料列表里批量抽取关键图。

    Args:
        materials: `_project_materials`（每项含 source_path / source_type）。

    Returns:
        合并去重后的关键图列表。
    """
    out: List[Dict[str, Any]] = []
    seen_paths = set()
    for m in (materials or []):
        if not isinstance(m, dict):
            continue
        sp = m.get("source_path") or ""
        if not sp.lower().endswith(".pdf"):
            continue
        for item in extract_key_images(sp):
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            out.append(item)
    return out
