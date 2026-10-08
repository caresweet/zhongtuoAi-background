"""把「稳评报告模板」按 12 章拆解，切块给 12 个章节 agent 作为写作底稿。

用户要求：**找到数据库中的模板 → 按 12 个章节拆解 → 切块给 12 个 agent 去写。**

数据来源优先级：
1. `data/knowledge_base.db` 的 `templates` 表（domain=stability、is_active=1、category 含「稳评报告」）
   → 取 `template_file_path`，文件在 `backend/storage/<path>`。
2. 兜底：`backend/data/chapter_template_chunks.json`（已切好的缓存）。

产出（缓存到 `backend/data/chapter_template_chunks.json`）：
    {
      "source": "<模板文件名>",
      "chapters": {
         "1": {"title": "稳评评审报告表", "text": "…", "paragraphs": [...]},
         … 到 "12"
      }
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_CACHE_FILE = _BACKEND_DIR / "data" / "chapter_template_chunks.json"

# 「第X章 标题」识别
_CHAPTER_RE = re.compile(r'^第([一二三四五六七八九十]+)章\s*(.*)$')
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def _cn_to_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if s in _CN_NUM:
        return _CN_NUM[s]
    if s.startswith("十") and len(s) > 1 and s[1] in _CN_NUM:
        return 10 + _CN_NUM[s[1]]
    return None


def rank_template_candidates(extra_first: Optional[str] = None) -> List[str]:
    """按「稳评优先」排序返回候选模板 docx 路径（全项目共用，消除各处各写一份）。

    🔴 为什么要排序：`storage/templates/` 里有投标文件（`18f2827d….docx`，36MB）、
    磋商文件、格式规范等**非稳评**文件。裸 `sorted(glob("*.docx"))` 会因字典序把
    投标文件排到第一 —— 评审表渲染曾因此把整份投标文件正文搬进报告第三章（400+ 页）。

    排序依据（依次）：
      1. `extra_first`（调用方显式指定的模板，如会话 state 里的 template_path）
      2. 数据库 `templates` 表：洪泽 → 稳评报告 → 其它，且 `is_active=1`
      3. 磁盘扫描兜底：文件名含「洪泽」或 hash 前缀 `598da27a` 的排前
    """
    import os
    out: List[str] = []
    seen = set()

    def _push(p: str) -> None:
        p = str(p)
        if p and p not in seen and os.path.exists(p) and p.lower().endswith(".docx"):
            seen.add(p); out.append(p)

    if extra_first:
        _push(extra_first)

    # ── 数据库：稳定优先级 ──
    try:
        import sqlite3
        db = _BACKEND_DIR / "data" / "knowledge_base.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            rows = conn.execute(
                "SELECT name, template_file_path FROM templates "
                "WHERE domain='stability' AND is_active=1 AND template_file_path IS NOT NULL "
                "AND template_file_path LIKE '%.docx' "
                "ORDER BY CASE WHEN name LIKE '%洪泽%' THEN 0 "
                "              WHEN name LIKE '%评审表%' THEN 1 "
                "              WHEN name LIKE '%稳评%' THEN 2 ELSE 3 END, id"
            ).fetchall()
            conn.close()
            for _name, rel in rows:
                if rel:
                    _push(str(_BACKEND_DIR / "storage" / rel))
    except Exception as e:
        logger.debug(f"读取模板候选失败: {e}")

    # ── 磁盘扫描兜底：洪泽/598da27a 优先，其余按字典序 ──
    try:
        from app.config import settings
        _tdir = settings.STORAGE_DIR / "templates"
        if _tdir.exists():
            def _rank(p: str) -> int:
                n = os.path.basename(p)
                if "洪泽" in n or n.startswith("598da27a"):
                    return 0
                if "评审表" in n:
                    return 1
                if "金湖" in n or "洞庭" in n:
                    return 2
                return 3
            for p in sorted(_tdir.glob("*.docx"), key=_rank):
                _push(str(p))
    except Exception as e:
        logger.debug(f"扫描模板目录失败: {e}")

    return out


def _find_template_docx() -> Optional[str]:
    """从数据库里找「稳评报告模板」的 docx 路径。"""
    try:
        import sqlite3
        db = _BACKEND_DIR / "data" / "knowledge_base.db"
        if not db.exists():
            return None
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT name, template_file_path FROM templates "
            "WHERE domain='stability' AND is_active=1 AND template_file_path IS NOT NULL "
            "ORDER BY CASE WHEN name LIKE '%洪泽%' THEN 0 "
            "              WHEN name LIKE '%稳评报告%' THEN 1 ELSE 2 END, id"
        ).fetchall()
        for name, rel in rows:
            if not rel:
                continue
            p = _BACKEND_DIR / "storage" / rel
            if p.exists() and p.suffix.lower() == ".docx":
                logger.info(f"章节模板来源: {name} → {p}")
                return str(p)
        logger.warning("数据库中未找到可用的稳评报告模板 docx")
    except Exception as e:
        logger.warning(f"查找模板失败: {e}")
    return None


def _iter_body_texts(docx_path: str) -> List[str]:
    """按文档顺序输出段落文本（跳过空段）。表格内容以 markdown 行形式并入。"""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(docx_path)
    out: List[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith('}p'):
            t = Paragraph(child, doc).text.strip()
            if t:
                out.append(t)
        elif child.tag.endswith('}tbl'):
            tb = Table(child, doc)
            for row in tb.rows:
                cells, prev = [], None
                for c in row.cells:
                    tx = c.text.strip().replace("\n", " ")
                    if tx != prev:
                        cells.append(tx)
                    prev = tx
                line = " | ".join(cells).strip()
                if line:
                    out.append(line)
    return out


def split_template_by_chapter(docx_path: str) -> Dict[str, Any]:
    """把模板 docx 按「第X章」切成 12 块；另附 `front`（首个章标题之前的前置部分）。"""
    lines = _iter_body_texts(docx_path)
    chapters: Dict[int, Dict[str, Any]] = {}
    front: List[str] = []
    cur: Optional[int] = None
    for line in lines:
        m = _CHAPTER_RE.match(line)
        num = _cn_to_int(m.group(1)) if m else None
        if num:
            cur = num
            chapters.setdefault(num, {"title": (m.group(2) or "").strip(), "paragraphs": []})
            continue
        if cur is None:
            front.append(line)          # 封面 / 公司简介等前置部分
            continue
        chapters[cur]["paragraphs"].append(line)
    return {
        "source": os.path.basename(docx_path),
        "front": front,
        "chapters": {
            str(n): {
                "title": d["title"],
                "paragraphs": d["paragraphs"],
                "text": "\n".join(d["paragraphs"]),
            }
            for n, d in sorted(chapters.items())
        },
    }


def build_and_cache(force: bool = False) -> Dict[str, Any]:
    """重建章节模板切块缓存。

    🔴 模板原文件的「第一章 = 稳评评审报告表」是**不编号的前置部分**，
    正文自「编制说明」起。故切块后统一**摘掉评审表、其余前移一位**，
    使块号与 `chapter_definitions` 的正文号一致（1=编制说明 … 11=附件）。
    """
    if _CACHE_FILE.exists() and not force:
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if data.get("chapters") and data.get("shifted") == _SHIFT_VERSION:
                return data
        except Exception:
            pass
    src = _find_template_docx()
    if not src:
        return {"source": "", "chapters": {}, "shifted": _SHIFT_VERSION}
    data = split_template_by_chapter(src)
    data = _drop_review_table_and_shift(data)
    try:
        _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info(f"章节模板切块已缓存: {_CACHE_FILE} ({len(data['chapters'])} 章)")
    except Exception as e:
        logger.warning(f"写入章节模板缓存失败: {e}")
    return data


# 切块口径版本：变更切块规则时递增，使旧缓存自动失效
_SHIFT_VERSION = 3


def _drop_review_table_and_shift(data: Dict[str, Any]) -> Dict[str, Any]:
    """摘掉「评审表」块（模板第一章，不编号），其余块号整体前移一位。"""
    chapters = data.get("chapters") or {}
    shifted: Dict[str, Any] = {}
    for k, v in chapters.items():
        try:
            n = int(k)
        except (ValueError, TypeError):
            continue
        if n <= 1:
            continue          # 1 = 评审表 → 丢弃（不编号前置部分）
        shifted[str(n - 1)] = v
    return {"source": data.get("source", ""), "chapters": shifted,
            "front": data.get("front") or [],
            "shifted": _SHIFT_VERSION}


_CACHE: Optional[Dict[str, Any]] = None

# ═══════════════════════════════════════════════════════════════
# 🔴 DB32/T4013-2021 章号 → 模板块键（模板是**案卷式**，与本系统输出结构不同）
#
#   模板（人工成品《模版（洪泽征地稳评报告）》）原章号：
#     1 稳评评审报告表 / 2 编制说明 / 3 编制依据 / 4 决策事项基本情况 /
#     5 稳评工作方案 / 6 各利益相关者 / 7 四性分析 / 8 分析预测风险源 /
#     9 意见建议 / 10 稳评工作报告 / 11 应急预案 / 12 附件
#   （`_drop_review_table_and_shift` 摘掉评审表后整体前移一位，故键 = 原号 − 1）
#
#   本系统输出（DB32 十章）→ 模板块键：
# ═══════════════════════════════════════════════════════════════
DB32_TO_TEMPLATE_KEY: Dict[int, str] = {
    # 参照件（白马湖）十四章 → 洪泽模板块键
    #   模板原章号：1 评审表 / 2 编制说明 / 3 编制依据 / 4 决策事项基本情况 /
    #              5 稳评工作方案 / 6 各利益相关者 / 7 四性分析 / 8 分析预测风险源 /
    #              9 意见建议 / 10 稳评工作报告 / 11 应急预案 / 12 附件
    #   （摘掉评审表后整体前移一位，故键 = 原号 − 1）
    1: "1",    # 编制说明           ← 模板「编制说明」
    2: "2",    # 编制依据           ← 模板「编制依据」
    3: "",     # 评审表             ← 用专门的评审表模板，非正文章节
    4: "9",    # 社会稳定风险评估工作报告 ← 模板「稳评工作报告」
    5: "10",   # 突发性事件…工作预案   ← 模板「应急预案」
    6: "3",    # 项目基本情况        ← 模板「决策事项基本情况」
    7: "4",    # 稳评工作方案        ← 模板「稳评工作方案」
    8: "5",    # 稳评方法与过程      ← 模板「各利益相关者」
    9: "5",    # 各利益相关者对项目的反映 ← 模板「各利益相关者」
    10: "6",   # 四性分析           ← 模板「四性分析」
    11: "7",   # 分析预测风险源      ← 模板「分析预测风险源」
    12: "7",   # 确定风险等级        ← 模板「分析预测风险源」（含评分体系）
    13: "8",   # 防范和化解风险的工作建议 ← 模板「意见建议」
    14: "11",  # 附件               ← 模板「附件」
}
# 编制说明（不编号前置部分）← 模板「编制说明」
COMPILATION_NOTE_TEMPLATE_KEY = "1"


def _template_key(chapter_number: int) -> Optional[str]:
    """正文章号 → 模板块键（无对应返回 None）。"""
    k = DB32_TO_TEMPLATE_KEY.get(int(chapter_number))
    return k or None


def _load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        _CACHE = build_and_cache()
    return _CACHE


def get_chapter_template_chunk(chapter_number: int) -> str:
    """取该章的模板底稿正文（按 DB32 章号映射到模板块；无则返回空串）。"""
    key = _template_key(chapter_number)
    if key is None:
        return ""
    ch = (_load().get("chapters") or {}).get(key)
    return (ch or {}).get("text", "") or ""


def get_chapter_template_title(chapter_number: int) -> str:
    """取该章在模板中的标题（无则空串）。"""
    key = _template_key(chapter_number)
    if key is None:
        return ""
    ch = (_load().get("chapters") or {}).get(key)
    return (ch or {}).get("title", "") or ""


def get_compilation_note_chunk() -> str:
    """取「编制说明」（不编号前置部分）的模板底稿。"""
    ch = (_load().get("chapters") or {}).get(COMPILATION_NOTE_TEMPLATE_KEY)
    return (ch or {}).get("text", "") or ""


def build_template_block_for_prompt(chapter_number: int, limit: int = 6000) -> str:
    """把该章模板底稿格式化为可注入 prompt 的文本块。"""
    text = get_chapter_template_chunk(chapter_number)
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit] + "\n…（模板后续内容略）"
    return (
        "\n## 📐 本章人工模板底稿（**结构与行文风格以此为准**）\n"
        "🔴 这是从公司标准模板中切出的**本章对应文本**。请：\n"
        "- **沿用它的章节结构、小标题顺序、行文口径与固定表述**；\n"
        "- 把其中的项目专属数据**替换为本项目事实卡的真实数据**；\n"
        "- 模板里的示例数据（别的地块/文号/单位/数字）一律不得照抄。\n"
        f"```\n{text}\n```\n"
    )
