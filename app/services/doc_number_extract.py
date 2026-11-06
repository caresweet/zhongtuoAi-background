# -*- coding: utf-8 -*-
"""从用户材料里自动找**项目批复/备案文号**（复垦报告用）。

业主 2026-11-02：「这些文号不都是可以获取到的么」——
能拿到就别问用户。分两步：

  ① **文本层**：材料里的 PDF/Word/Excel 文本直接正则匹配 `XX〔2026〕12号`；
     书名号里的政策文号（《…通知》（自然资规〔2019〕1号））**不算项目文号**。
  ② **扫描件**：合同/批复/协议这类图片或 PDF 首页走**视觉识别**，按文件 md5 缓存，
     只问一次。

找到就写 `filled_data["doc_reference"]`（来源标记 material），找不到才走弹窗/待补充。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_PAT = re.compile(r'[一-龥]{2,10}〔\s*20\d{2}\s*〕\s*\d+\s*号')
_GOOD = ("批复", "备案", "核准", "批准", "同意", "临时用地", "项目", "函", "许可")
_BAD = ("通知", "意见", "规定", "办法", "条例", "标准", "规划", "细则", "指南")
_VISION_KW = ("批复", "备案", "核准", "许可", "合同", "协议", "函", "决定", "审查")


def _score(text: str, start: int) -> int:
    """文号的可信度：±25 字上下文里的「批复/备案」加分，政策文件名减分。"""
    ctx = text[max(0, start - 25):start + len(_PAT.pattern) + 25]
    s = sum(2 for k in _GOOD if k in ctx) - sum(3 for k in _BAD if k in ctx)
    # 书名号内的（政策引用）直接判负
    prev = text[max(0, start - 40):start]
    i = prev.rfind("《")
    if i >= 0 and (prev.rstrip().endswith(("（", "(")) or "》" not in prev[i:]):
        s -= 5
    return s


_LEADS = ("批复文号为", "备案文号为", "核准文号为", "批准文号为",
          "批复文号", "备案文号", "核准文号", "批准文号",
          "文号为", "编号为", "文号", "编号", "为", "是")


def _trim(tok: str) -> str:
    t = str(tok or "")
    changed = True
    while changed:
        changed = False
        for p in _LEADS:
            if t.startswith(p) and len(t) - len(p) >= 5:
                t = t[len(p):]
                changed = True
    return t


def from_text(text: str) -> str:
    """从一段文本里挑最像「项目文号」的一个；没有返回空串。"""
    text = str(text or "")
    best, best_score = "", -99
    for m in _PAT.finditer(text):
        sc = _score(text, m.start())
        if sc > best_score:
            tok = _trim(re.sub(r'\s+', '', m.group(0)))
            best, best_score = tok, sc
    return best if best_score >= 0 else ""


def _md5_file(p: Path) -> str:
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except Exception:
        return ""


def _cache_path(storage_dir: str, h: str) -> Path:
    return Path(storage_dir) / "doc_no_cache" / f"{h}.json"


def _read_cache(storage_dir: str, p: Path) -> Optional[str]:
    h = _md5_file(p)
    if not h:
        return None
    c = _cache_path(storage_dir, h)
    if c.exists():
        try:
            return str(json.loads(c.read_text()).get("doc_no") or "")
        except Exception:
            return None
    return None


def _write_cache(storage_dir: str, p: Path, val: str) -> None:
    h = _md5_file(p)
    if not h:
        return
    try:
        c = _cache_path(storage_dir, h)
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_text(json.dumps({"doc_no": val}, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"[文号] 写缓存失败: {e}")


async def from_vision(path: str, storage_dir: str, llm=None, max_pages: int = 3) -> str:
    """扫描件/图片 → 视觉识别项目文号（按 md5 缓存）。"""
    p = Path(str(path or ""))
    if not p.exists():
        return ""
    cached = _read_cache(storage_dir, p)
    if cached is not None:
        return cached
    if llm is None:
        try:
            from app.services.llm_service import LLMService
            llm = LLMService()
        except Exception:
            return ""
    imgs = []
    low = p.suffix.lower()
    try:
        if low == ".pdf":
            from app.services.pdf_key_images import render_all_pages
            imgs = render_all_pages(str(p), max_pages=max_pages,
                                    out_dir=str(Path(storage_dir) / "images" / "appendix"))
        elif low in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            imgs = [str(p)]
    except Exception as e:
        logger.warning(f"[文号] 渲染失败 {p.name}: {e}")
        return ""
    prompt = (
        "请读取图中**本项目的批复/备案/核准文号**（格式如：淮自然资〔2026〕12号、"
        "苏自然资函〔2025〕8号）。只输出文号本身；找不到输出 NONE。\n"
        "⚠️ 不要把《…通知》《…意见》这类**政策文件名里的文号**当项目文号。"
    )
    for img in imgs:
        try:
            data = base64.b64encode(Path(img).read_bytes()).decode()
            mt = "image/jpeg" if str(img).lower().endswith((".jpg", ".jpeg")) else "image/png"
            resp = await llm.chat_with_image(prompt, data, media_type=mt, max_tokens=80)
            val = from_text(str(resp or ""))
            if val:
                _write_cache(storage_dir, p, val)
                logger.info(f"[文号] 视觉识别到项目文号 {val}（{p.name}）")
                return val
        except Exception as e:
            logger.debug(f"[文号] 视觉识别失败 {p.name}: {e}")
    _write_cache(storage_dir, p, "")
    return ""


async def ensure_doc_reference(filled: dict, materials: Iterable[dict],
                               storage_dir: str, llm=None,
                               max_vision_files: int = 6) -> str:
    """材料 → `filled["doc_reference"]`。已有值不动；返回找到的文号（没有为空）。"""
    if not isinstance(filled, dict):
        return ""
    if str(filled.get("doc_reference") or "").strip():
        return str(filled["doc_reference"]).strip()

    items = [m for m in (materials or []) if isinstance(m, dict)]
    # ① 文本层
    for m in items:
        val = from_text(str(m.get("text_content") or ""))
        if val:
            filled["doc_reference"] = val
            try:
                from app.services import field_sources as _fs
                _fs.mark(filled, _fs.MATERIAL, "doc_reference")
            except Exception:
                pass
            logger.info(f"[文号] 文本层识别到项目文号 {val}（{m.get('source_name')}）")
            return val

    # ② 扫描件（只挑名称像批复/合同/协议/函的，最多 N 份）
    cands = []
    for m in items:
        name = str(m.get("source_name") or "")
        src = str(m.get("source_path") or "")
        if not src or not any(k in name for k in _VISION_KW):
            continue
        p = Path(storage_dir) / src
        if p.exists() and p.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            cands.append(p)
    for p in cands[:max_vision_files]:
        val = await from_vision(str(p), storage_dir, llm=llm)
        if val:
            filled["doc_reference"] = val
            try:
                from app.services import field_sources as _fs
                _fs.mark(filled, _fs.MATERIAL, "doc_reference")
            except Exception:
                pass
            return val
    return ""
