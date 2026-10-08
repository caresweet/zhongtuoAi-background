"""人员签名库 —— 「人名 → 手写签名图片」。

来源：知识库模板《洪泽社会稳定征地稳评报告》(`598da27a….docx`) 的
「参与人员签字」区块里带着**手写签名图**（7 张），随模板一起入库。

业主口径：
    「人员签名在模板中都是有图片的，提取出来使用。」

签名图是逐张目视辨认后登记在此的（笔迹无法自动 OCR 可靠识别），
登记表在 `storage/personnel_signatures/index.json`，本模块只做查表。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parents[2]
_SIG_DIR = _BACKEND / "storage" / "personnel_signatures"
_INDEX = _SIG_DIR / "index.json"

# 模板签名图的 rId 顺序 → 人名（**目视逐张辨认**，勿随意改动）
_TEMPLATE_MEDIA_TO_NAME = {
    "image8.png": "陈春",
    "image9.png": "刘利伟",
    "image10.png": "安如月",
    "image11.jpeg": "张抗洪",
    "image12.png": "罗娜",
    "image13.jpeg": "朱璇",
    "image14.jpeg": "夏杰",
}

_cache: Optional[Dict[str, dict]] = None


def build_index(force: bool = False) -> Dict[str, dict]:
    """从模板 docx 抽取签名图并落盘，返回 {人名: {path, template_media}}。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    idx: Dict[str, dict] = {}
    try:
        import re as _re
        import zipfile
        from app.services.template_chapter_chunker import rank_template_candidates
        src = None
        for p in rank_template_candidates():
            if "598da27a" in Path(p).name:
                src = p
                break
        if not src:
            return idx
        z = zipfile.ZipFile(src)
        _SIG_DIR.mkdir(parents=True, exist_ok=True)
        for media, name in _TEMPLATE_MEDIA_TO_NAME.items():
            try:
                data = z.read("word/media/" + media)
            except KeyError:
                continue
            fp = _SIG_DIR / f"{name}{Path(media).suffix}"
            if force or not fp.exists():
                fp.write_bytes(data)
            idx[name] = {"path": str(fp), "template_media": media, "source": Path(src).name}
        z.close()
        _INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info(f"[签名库] 已登记 {len(idx)} 人签名")
    except Exception as e:
        logger.warning(f"签名库构建失败: {e}")
    _cache = idx
    return idx


def load_index() -> Dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        if _INDEX.exists():
            _cache = json.loads(_INDEX.read_text(encoding="utf-8")) or {}
            return _cache
    except Exception:
        pass
    return build_index()


PLACEHOLDER_NAME = "_placeholder.png"


def placeholder_path() -> Optional[str]:
    """签名占位图（没有该人签名图时代用）。不存在则返回 None。"""
    p = _SIG_DIR / PLACEHOLDER_NAME
    return str(p) if p.exists() else None


def signature_or_placeholder(name: str) -> Optional[str]:
    """取签名图；没有就回退到**占位图**（业主口径：「没有的就填个占位图」）。"""
    return signature_for(name) or placeholder_path()


def signature_for(name: str) -> Optional[str]:
    """取某人的签名图路径；没有则返回 None（留空供手签）。"""
    nm = str(name or "").strip()
    if not nm:
        return None
    e = load_index().get(nm) or {}
    p = str(e.get("path") or "")
    return p if p and Path(p).exists() else None
