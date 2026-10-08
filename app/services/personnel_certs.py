"""证书扫描件识别 —— 解决「这张证书是谁的」。

背景：
    磁盘上的证书扫描件文件名是 `company_P30_rId8.jpeg` 这种，**看不出是谁的**
    （姓名是手写体），直接按文件名匹配不到人。

做法：
    **一次性视觉识别 + 落盘索引**：把每张候选图扫一遍，抽出「姓名 / 证书类型」，
    存进 `storage/personnel_certs.json`。之后由 `personnel_store.sync_personnel_store()`
    把它绑定到人员库（`company_personnel_images` 表）——运行时零 LLM，纯查表。

    新增素材图后重新跑一次即可；单张按指纹缓存，重跑不重复烧 token。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 视为「证书类素材」的目录与文件名特征
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_NAME_HINTS = ("company", "stability_cert", "cert", "证书", "培训", "人员")
_SKIP_HINTS = ("doc_", "pdf_", "page", "img0")   # 文档整页截图，不是证书素材

_INDEX_FILE = "personnel_certs.json"


def _storage_dir() -> Path:
    from app.config import settings
    return settings.STORAGE_DIR


def index_path() -> Path:
    return _storage_dir() / _INDEX_FILE


def _fingerprint(path: str) -> str:
    h = hashlib.md5()
    h.update(str(path).encode())
    try:
        import os
        h.update(str(os.path.getsize(path)).encode())
    except Exception:
        pass
    return h.hexdigest()


def _vision_prompt() -> str:
    return """这是一张公司人员证书/证件的扫描件或截图。请判断它是不是**人员类证书**，并抽出关键信息。

严格输出 JSON（不要解释、不要代码块围栏）：
{
  "is_person_cert": true | false,
  "person_name": "证书上的人员姓名（手写体请尽量辨认；辨认不出填空字符串）",
  "cert_type": "社会稳定风险评估业务培训证书 | 职称证书 | 执业资格证书 | 学历证书 | 身份证 | 其他",
  "issuer": "发证机关（可辨认时填）",
  "cert_no": "证书编号（可辨认时填）"
}

判断规则：
1. 只有**写明了具体人员姓名**的证书才 is_person_cert=true；
   营业执照、资质证书、平台备案截图、流程图、现场照片等一律 false；
2. person_name 只填姓名两个字/三个字本身，不要带「同志」「姓名：」等前后缀；
3. 辨认不清就填空字符串，**不要猜、不要编造**。
"""


def _list_candidate_images() -> List[str]:
    """收集所有可能是证书素材的图片路径（项目素材目录 + DB asset_images）。"""
    out: List[str] = []
    seen = set()

    # ① storage/extracted_imgs 下的公司素材
    for base in (_storage_dir() / "extracted_imgs", _storage_dir() / "images"):
        if not base.exists():
            continue
        for f in sorted(base.iterdir()):
            if f.suffix.lower() not in _IMG_EXTS:
                continue
            nm = f.name
            if not any(h in nm for h in _NAME_HINTS):
                continue
            if any(s in nm for s in _SKIP_HINTS):
                continue
            if str(f) not in seen:
                seen.add(str(f)); out.append(str(f))

    # ② knowledge_base.db 的 asset_images（按名字含「证书/人员/培训」筛）
    try:
        import sqlite3
        from app.config import settings
        db = settings.DATA_DIR / "knowledge_base.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id, image_name, image_data, mime_type FROM asset_images"
                ).fetchall()
            except Exception:
                rows = []
            conn.close()
            for r in rows:
                nm = str(r["image_name"] or "")
                # 🔴 只认「像人名证书文件名」的素材：短名 + 明确的证书/负责人关键词。
                #    asset_images 里大量条目是项目文档的 OCR 摘句（长文本），
                #    按「证书/人员」二字粗筛会把它们全捞进来，白烧十几轮视觉调用。
                if len(nm) > 24:
                    continue
                if not any(h in nm for h in ("培训证书", "职称证书", "资格证书", "人员证书", "负责人")):
                    continue
                data = r["image_data"]
                if not data:
                    continue
                ext = "png" if "png" in (r["mime_type"] or "") else "jpg"
                fp = _storage_dir() / "images" / f"person_cert_{r['id']}.{ext}"
                try:
                    if not fp.exists():
                        if isinstance(data, str):
                            fp.write_bytes(base64.b64decode(data))
                        else:
                            fp.write_bytes(bytes(data))
                    if str(fp) not in seen:
                        seen.add(str(fp)); out.append(str(fp))
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"读取 asset_images 失败: {e}")

    return out


def load_index() -> Dict[str, dict]:
    try:
        p = index_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_index(idx: Dict[str, dict]) -> None:
    try:
        index_path().write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"人员证书索引落盘失败: {e}")


def _clean_name(v: str) -> str:
    t = re.sub(r"[（(【\[].*?[)）】\]]", "", str(v or ""))
    t = re.sub(r"(同志|先生|女士|姓名|名字)", "", t)
    return re.sub(r"[\s:：、.。]+", "", t).strip()


async def _recognize_one(path: str, llm, attempts: int = 3) -> dict:
    """识别单张证书；视觉模型 TPM 低，必须带退避重试。

    🔴 全部重试失败返回 {} —— 调用方**不得**把空结果写进索引（否则一张图
    的一次限流失败会被永久缓存成「不是人员证书」）。
    """
    import asyncio
    try:
        import base64 as _b64
        data = Path(path).read_bytes()
        b64 = _b64.b64encode(data).decode()
    except Exception as e:
        logger.warning(f"读取证书图失败 {path}: {str(e)[:100]}")
        return {}

    ext = Path(path).suffix.lower().lstrip(".")
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/png")

    for att in range(attempts):
        try:
            text = await llm.chat_with_image(
                _vision_prompt(), b64, media_type=media,
                system="你是稳定的证件识别助手，只输出严格 JSON。", max_tokens=500)
            m = re.search(r"\{[\s\S]*\}", str(text or ""))
            if m:
                return json.loads(m.group(0))
            return {}
        except Exception as e:
            msg = str(e)
            is_tpm = "429" in msg or "TPM" in msg or "circuit" in msg.lower()
            wait = (12, 30, 60)[min(att, 2)] if is_tpm else (3, 8, 15)[min(att, 2)]
            if att == attempts - 1:
                logger.warning(f"证书识别失败 {path}: {msg[:120]}")
                return {}
            await asyncio.sleep(wait)
    return {}


async def build_cert_index(llm_service=None, force: bool = False,
                           delay_seconds: float = 3.0) -> Dict[str, dict]:
    """构建/增量更新「人名 → 证书图」索引。已识别过的图（按指纹）不重复识别。"""
    import asyncio
    idx = load_index()
    if force:
        idx = {}
    if not llm_service:
        from app.services.llm_service import LLMService
        llm_service = LLMService()

    imgs = _list_candidate_images()
    changed = False
    for p in imgs:
        fp = _fingerprint(p)
        if fp in idx:
            continue
        info = await _recognize_one(p, llm_service)
        # 🔴 识别失败（熔断/限流/超时）返回空 dict —— **不要写进索引**，
        #    否则一条失败会被永久缓存成「不是人员证书」，后面再也补不回来。
        if not info:
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            continue
        idx[fp] = {
            "path": p,
            "is_person_cert": bool(info.get("is_person_cert")),
            "person_name": _clean_name(info.get("person_name", "")),
            "cert_type": str(info.get("cert_type") or "").strip(),
            "issuer": str(info.get("issuer") or "").strip(),
            "cert_no": str(info.get("cert_no") or "").strip(),
        }
        changed = True
        if delay_seconds:
            await asyncio.sleep(delay_seconds)   # 🔴 视觉模型 TPM 低，必须节流
    if changed:
        _save_index(idx)
    return idx
