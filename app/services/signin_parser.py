"""座谈会签到表解析 —— 从扫描件里识别参会人员名单。

🔴 为什么需要它
────────────────────────────────────────────────────────────────────────
第8章「（四）风险调查过程」的 spec 里明确声明了依赖：

    "data": ["notice_date", "survey_date", "symposium_date", "symposium_attendees"]

而 `symposium_attendees` 在本项目里**永远是 None**，链路两头都断着：

  ① 签到表图片确实在上传材料里（`_classified_images["survey"] = ["…签到表….jpg"]`），
     但它只被当成插图用，从来没被当作「参会人员名单」解析过；
  ② `questionnaire_parser` 的 vision prompt 里写着「两者都不是（如**签到表**、
     会议记录、公告）= none」—— **显式跳过**签到表页；
  ③ `pdf_data_extractor` 里虽然有提取 `symposium_attendees` 的逻辑，但它读的是
     **文字层**，而签到表是纯扫描件（3173×4485，无文字层），读不到。

后果不是「少一个字段」，而是第8章只能写**遁词**把缺口圆过去：

    「参会人员包括…群众代表及相关部门人员，**实际参加人员以座谈会签到表为准**。」
    「座谈情况形成记录，纳入本次风险评估基础材料。」

业主在成稿里根本看不出这里缺东西（见 `gap_detector.py` 对这类句子的标注）。

🔴 产出格式
    `filled_data["symposium_attendees"] = "张三、李四、王五"`
    —— 顿号连接的姓名列表，与 `pdf_data_extractor` 的既有约定一致。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import pickle
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 文件名线索（找不到分类结果时的兜底）
_SIGNIN_HINTS = ("签到", "signin", "sign_in", "attendance")


# 🔴 签到表**分两种**，绝不能混：
#     · 座谈会 / 走访调查 签到表 → `symposium_attendees`
#       （第8章「（四）风险调查过程 · 4.座谈会」要用的是这一种）
#     · 专家评审会 签到表       → 评审会名单，与座谈会**毫无关系**
#    实测上传的那张是「洪拟征告〔2026〕7号土地征收决策社会稳定风险评估
#    **评审会**签到表」（2026-05-15，淮安市自然资源和规划局洪泽分局会议室，
#    钱晓松/柯峰/…5 人）—— 若只看文件名里的「签到表」就写进
#    `symposium_attendees`，等于**把评审专家当成座谈会群众代表**。
#    这个仓库里「张冠李戴」已经出过好几次（见 field_resolver 的注释），
#    所以这里按**标题**判类型，判不准就两边都不写。
_SYMPOSIUM_KW = ("座谈", "走访", "群众", "村民", "代表", "被征收")
_REVIEW_KW = ("评审", "专家", "论证", "审查", "评估会")


def classify_signin(title: str) -> str:
    """签到表类型：`symposium` / `review` / `unknown`。"""
    t = str(title or "")
    has_symp = any(k in t for k in _SYMPOSIUM_KW)
    has_rev = any(k in t for k in _REVIEW_KW)
    if has_rev and not has_symp:
        return "review"
    if has_symp and not has_rev:
        return "symposium"
    return "unknown"


def _cache_dir() -> Path:
    from app.config import settings
    d = settings.STORAGE_DIR / "signin_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 🔴 识别缓存版本 —— **改了视觉 prompt 或输出结构就必须 +1**：
#    否则旧 pkl 命中后，新加的字段（手写日期/时间）永远拿不到值。
#    v1 → v2：新增手写日期 `meeting_date` 归一 + `meeting_time`。
#    v2 → v3：日期 prompt 加「年份逐个确认」约束 + `_norm_date` 支持两位年份。
_SIGNIN_CACHE_VERSION = 3


def _fingerprint(image_path: str) -> str:
    """按**归一化后的真实路径** + mtime + size 取指纹。

    🔴 必须 `realpath`：材料里同一张图会出现相对路径（`storage/images/x.jpg`）
       与绝对路径两种写法，不归一化就会算出**两个缓存键** —— 实测同一张签到表
       被识别了两遍，而且两次的人名还不一样（`邱晓松` vs `邱烨`），
       等于缓存形同虚设、交付稿里的人名还可能前后不一致。
    """
    h = hashlib.md5()
    h.update(str(_SIGNIN_CACHE_VERSION).encode())
    try:
        h.update(os.path.realpath(str(image_path)).encode())
    except Exception:
        h.update(str(image_path).encode())
    try:
        h.update(str(os.path.getmtime(image_path)).encode())
        h.update(str(os.path.getsize(image_path)).encode())
    except Exception:
        pass
    return h.hexdigest()


def _vision_prompt() -> str:
    return """这是一份**会议签到表**的扫描件（可能是「社会稳定风险评估座谈会签到表」「群众代表签到表」等）。

请逐行识别表格里**手写的**参会人员信息，严格输出 JSON：

{
  "is_signin": true | false,
  "meeting_title": "签到表标题（如：社会稳定风险评估座谈会签到表）",
  "meeting_date": "会议日期，归一成 2026-04-28 这种格式；看不清就空串",
  "meeting_time": "手写的具体时间（如 上午9:00 / 14:30）；没有就空串",
  "meeting_location": "会议地点；看不清就空串",
  "attendees": [
    {"name": "姓名", "unit": "单位/村组", "title": "职务", "phone": "电话"}
  ]
}

铁律：
1. **只认手写笔迹**，印刷体的表头、空白行、示例行一律不要；
2. 姓名必须是被扫描件上真实写出来的字，**看不清的宁可不填**，绝不猜、绝不补全；
3. 单位/职务/电话看不清就填空串，不要编；
4. 如果这张根本不是签到表（是问卷/公告/照片），`is_signin` 填 false，`attendees` 填空数组；
5. 行数就是实际签到人数，不要增删；
6. 🔴 `meeting_date` **只抄手写**的日期 —— 表头印的模板日期、空白栏不要。
   常见手写写法「2026.4.28」「2026年4月28日」「2026/4/28」「2026 4 28」
   一律归一成 2026-04-28；只有年月就补 01；真的没有就空串。
7. 🔴🔴 手写年份极易看错（「26」看成「16」「76」等）。抄写前请**逐个数字确认**：
   本批资料都是**近几年**的稳评项目（2024~2027 年之间）；得出的年份若不在这个范围，
   说明读错了，请重新看图确认。年份只写了两位（如「26.4.28」）时补成 **2026**，
   不要补成 2016。**拿不准就填空串**，绝对不要猜一个年份出来。
"""


def _norm_date(v: Any) -> str:
    """手写日期归一成 `YYYY-MM-DD`；识别不出返回空串（绝不推断）。"""
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.search(r'(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})', s)
    if not m:
        # 「2026 4 28」这种空格分隔（手写常见）
        m = re.search(r'(\d{4})\s+(\d{1,2})\s+(\d{1,2})(?!\d)', s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
    else:
        m = re.search(r'(\d{4})\s*[年./\-]\s*(\d{1,2})\s*月?', s)
        if m:
            y, mo, d = m.group(1), m.group(2), "1"
        else:
            # 🔴 手写「26.4.28」——年份只写两位。按 20XX 补全
            #    （00-79 → 20xx；80-99 → 19xx）。视觉模型有时会把「26」
            #    补成错的四位数（实测把 2026 读成 2016），两位写法更可信。
            m = re.search(r'(?<!\d)(\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})(?!\d)', s)
            if not m:
                return ""
            _yy = int(m.group(1))
            y = str(2000 + _yy) if _yy <= 79 else str(1900 + _yy)
            mo, d = m.group(2), m.group(3)
    try:
        yi, moi, di = int(y), int(mo), int(d)
        if not (1 <= moi <= 12 and 1 <= di <= 31):
            return ""
        return f"{yi:04d}-{moi:02d}-{di:02d}"
    except (TypeError, ValueError):
        return ""


def _cn_date(iso: str) -> str:
    """`2026-04-28` → `2026年4月28日`。"""
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', str(iso or ""))
    if not m:
        return str(iso or "")
    return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日"


def _resolve(path: str) -> str:
    """材料登记里存的是**相对 storage** 的路径。"""
    if not path:
        return path
    if os.path.exists(path):
        return path
    try:
        from app.config import settings
        for base in (settings.STORAGE_DIR, settings.BASE_DIR):
            cand = Path(base) / path
            if cand.exists():
                return str(cand)
    except Exception:
        pass
    return path


def find_signin_image(state: Dict[str, Any]) -> str:
    """在会话材料里找签到表。**图片优先**，其次签到表 PDF
    （`parse_signin_sheet` 已支持 PDF 逐页渲染）。找不到返回空串。"""
    state = state or {}
    cands: List = []
    pdfs: List = []
    imgs = state.get("_classified_images") or {}
    for key in ("signin", "survey"):
        for p in (imgs.get(key) or []):
            if isinstance(p, str) and p.lower().endswith((".jpg", ".jpeg", ".png")):
                cands.append(p)
            elif isinstance(p, str) and p.lower().endswith(".pdf"):
                pdfs.append(p)
    for u in (state.get("_uploaded_files") or []):
        p = str(u.get("path") if isinstance(u, dict) else u)
        n = str(u.get("original_name") if isinstance(u, dict) else "")
        if not p:
            continue
        _low = p.lower()
        if _low.endswith((".jpg", ".jpeg", ".png")):
            cands.append((p, n) if n else p)
        elif _low.endswith(".pdf"):
            pdfs.append((p, n) if n else p)
    for pool in (cands, pdfs):
        for c in pool:
            p, n = (c if isinstance(c, tuple) else (c, ""))
            if any(k in (p + n).lower() for k in _SIGNIN_HINTS):
                real = _resolve(p)
                if os.path.exists(real):
                    return real
    return ""


def _to_images(image_path: str) -> List[tuple]:
    """把待识别文件转成视觉 API 可用的 (b64, media_type) 列表。

    · 图片 → 原样 base64（jpg/jpeg/png）；
    · **PDF → 逐页渲染成 PNG**（视觉 API 不收 PDF，实测直接 base64 喂 PDF
      报 400 `The image format is illegal`）。最多取前 3 页 —— 签到表
      几乎总在第 1 页，多页是为了兼容「首页是封面」的扫描件。
    """
    ext = str(image_path).lower().rsplit(".", 1)[-1]
    try:
        if ext == "pdf":
            out: List[tuple] = []
            import fitz
            doc = fitz.open(image_path)
            for pg in list(doc)[:3]:
                try:
                    data = pg.get_pixmap(dpi=150).tobytes("png")
                except Exception:
                    continue
                if len(data) > 128:
                    out.append((base64.b64encode(data).decode(), "image/png"))
            doc.close()
            if not out:
                logger.warning(f"签到表 PDF 渲染为空: {image_path}")
            return out
        if ext in ("jpg", "jpeg", "png", "webp", "bmp"):
            media = "image/png" if ext == "png" else "image/jpeg"
            return [(base64.b64encode(Path(image_path).read_bytes()).decode(), media)]
        logger.warning(f"签到表不支持的文件类型: {image_path}")
    except Exception as e:
        logger.warning(f"签到表转图失败 {image_path}: {e}")
    return []


async def parse_signin_sheet(image_path: str, llm=None,
                             attempts: int = 3) -> Dict[str, Any]:
    """识别一张签到表。结果按 (路径+mtime+size) 缓存。支持图片与 PDF。"""
    image_path = _resolve(image_path)
    if not image_path or not os.path.exists(image_path):
        return {}
    cache_file = _cache_dir() / f"signin_{_fingerprint(image_path)}.pkl"
    if cache_file.exists():
        try:
            return pickle.loads(cache_file.read_bytes())
        except Exception:
            pass

    if llm is None:
        from app.services.llm_service import LLMService
        llm = LLMService()

    data = None
    for b64, media in _to_images(image_path):
        text = ""
        for att in range(attempts):
            try:
                text = await llm.chat_with_image(
                    _vision_prompt(), b64, media_type=media,
                    system="你是稳定的文书识别助手，只输出严格 JSON，看不清的内容一律留空。",
                    max_tokens=1500,
                )
                break
            except Exception as e:
                _msg = str(e)
                _tpm = "429" in _msg or "TPM" in _msg
                if att == attempts - 1:
                    logger.warning(f"签到表识别失败: {_msg[:120]}")
                    text = ""
                    break
                _wait = (15, 30, 60)[att] if _tpm else (3, 6, 12)[att]
                logger.info(f"签到表第{att+1}次失败（{'限流' if _tpm else '其他'}），{_wait}s 后重试")
                await asyncio.sleep(_wait)

        m = re.search(r'\{[\s\S]*\}', str(text or ""))
        if not m:
            continue
        try:
            cand = json.loads(m.group(0))
        except Exception:
            continue
        if not isinstance(cand, dict):
            continue
        data = cand
        # 签到表通常就在第 1 页；识别出「是签到表」或有人名就不再翻页
        if cand.get("is_signin") or cand.get("attendees"):
            break

    if data is None:
        return {}

    names, seen = [], set()
    for a in (data.get("attendees") or []):
        nm = str((a or {}).get("name") or "").strip()
        # 1 个字的、纯数字的、含标点的都不是人名
        if len(nm) < 2 or nm.isdigit() or nm in seen:
            continue
        if re.search(r'[，。、；：（）()\[\]0-9]', nm):
            continue
        seen.add(nm)
        names.append(nm)
    data["attendees"] = [a for a in (data.get("attendees") or [])
                         if str((a or {}).get("name") or "").strip() in seen]
    data["names"] = names
    data["count"] = len(names)
    try:
        cache_file.write_bytes(pickle.dumps(data))
    except Exception:
        pass
    return data


async def ensure_symposium_attendees(state: Dict[str, Any], llm_service=None) -> Dict[str, Any]:
    """确保 `filled_data["symposium_attendees"]` 有值：命中缓存秒回，否则跑一次识别。

    与 `survey_data.ensure_questionnaire_result` 同一套路：解析一次、按指纹缓存、
    写回 state。识别不到就**什么都不写**（绝不编名字），由 `gap_detector` 去标注缺口。
    """
    state = state or {}
    filled = state.setdefault("filled_data", {})
    cur = str(filled.get("symposium_attendees") or "").strip()
    if cur:
        return {"names": [x for x in re.split(r'[、，,]', cur) if x.strip()],
                "count": len([x for x in re.split(r'[、，,]', cur) if x.strip()]),
                "cached": True}

    img = find_signin_image(state)
    if not img:
        logger.info("未找到签到表图片，跳过参会人员解析")
        return {}
    res = await parse_signin_sheet(img, llm=llm_service)
    if not res or not res.get("names"):
        logger.info(f"签到表未识别出姓名（{img}）")
        return res or {}

    names = res["names"]
    kind = classify_signin(res.get("meeting_title", ""))
    res["_kind"] = kind
    if kind == "review":
        # 🔴 评审会 → 记到 `review_meeting_attendees`，**不碰**座谈会字段
        filled["review_meeting_attendees"] = "、".join(names)
        if res.get("meeting_location") and not str(filled.get("review_meeting_location") or "").strip():
            filled["review_meeting_location"] = str(res["meeting_location"]).strip()
        # 🔴 评审会**手写日期** → 评审时间（报告落款/评审意见栏要用真实日期）
        _rd = _norm_date(res.get("meeting_date"))
        if _rd and not str(filled.get("review_meeting_date") or "").strip():
            filled["review_meeting_date"] = _rd
            filled["review_meeting_date_text"] = _cn_date(_rd)
        logger.info(f"签到表是**评审会**的（{res.get('meeting_title','')[:30]}）→ "
                    f"记入 review_meeting_attendees（{len(names)} 人，"
                    f"日期 {_rd or '未识别'}），不写座谈会参会人员")
        return res
    if kind == "unknown":
        logger.warning(f"签到表类型判不准（标题「{res.get('meeting_title','')}」）"
                       f"→ 两边都不写，避免张冠李戴")
        return res

    filled["symposium_attendees"] = "、".join(names)
    filled["symposium_attendee_count"] = str(len(names))
    # 🔴 签到表上的**手写日期** = 座谈会实际发生日（业主：「每次的座谈会和群众调查
    #    都是有写时间的，应该大多数都是人为书写的，尽量识别出来」）。
    #    已有值（材料/用户填的）不覆盖。
    _sd = _norm_date(res.get("meeting_date"))
    if _sd and not str(filled.get("symposium_date") or "").strip():
        filled["symposium_date"] = _sd
        filled["symposium_date_text"] = _cn_date(_sd)
    if res.get("meeting_time") and not str(filled.get("symposium_time") or "").strip():
        filled["symposium_time"] = str(res["meeting_time"]).strip()
    try:
        from app.services.field_sources import mark
        mark(filled, "material", "symposium_attendees", "symposium_attendee_count",
             "symposium_location", "symposium_date", "symposium_date_text", "symposium_time")
    except Exception:
        pass
    if res.get("meeting_location") and not str(filled.get("symposium_location") or "").strip():
        filled["symposium_location"] = str(res["meeting_location"]).strip()
    logger.info(f"座谈会签到表解析完成：{len(names)} 人 — {'、'.join(names[:8])}"
                + ("…" if len(names) > 8 else ""))
    return res
