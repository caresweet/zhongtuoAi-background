"""就地填充引擎 —— 直接打开模板 docx，在原文上替换项目数据，**保留全部版式**。

为什么这么做：模板是人工排好版的成品（字体/字号/目录域/页眉页脚/表格框线都在），
重建一份永远会对不上。就地只改文字，版式零风险。

替换粒度是 **run**：命中标注的 `anchor` 后，只改 run 的 `.text`，不重建 run，
因此字体、字号、加粗、颜色全部原样保留。

配套：
- 标注表由 `template_profile.py` 离线产出（anchor → field）
- 表格与图片由 `table_injector.py` 另拼插入
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.template_slot_schema import is_valid_slot_value, value_fits_unit

logger = logging.getLogger(__name__)

# 「第X章」章标题（用于把 docx 切到某一章的区间）
_CHAPTER_HEADING_RE = re.compile(r'^第([一二三四五六七八九十]+)章\s*(.*)$')
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
       "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


@dataclass
class Gap:
    """缺口：模板里没能填上、或需要人工复核的地方。"""
    chapter: int
    anchor: str = ""
    field_key: str = ""
    reason: str = ""        # missing_field | low_conf_ocr | unmatched_slot | no_template | analysis_gap
    severity: str = "warn"  # error | warn | info
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"chapter": self.chapter, "anchor": self.anchor, "field_key": self.field_key,
                "reason": self.reason, "severity": self.severity, "detail": self.detail}


@dataclass
class FillResult:
    """一章的填充结果。"""
    chapter: int
    filled_count: int = 0
    gaps: List[Gap] = field(default_factory=list)
    pending_images: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)  # (anchor, entry)
    pending_tables: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.filled_count > 0


# ═══════════════════════════════════════════════════════════════
# 段落级替换
# ═══════════════════════════════════════════════════════════════

def replace_in_paragraph(p: Paragraph, anchor: str, new_text: str) -> bool:
    """把段落里的 `anchor` 替换为 `new_text`，**保留原 run 格式**。

    做法：定位 anchor 在段落全文中的区间 → 把起点所在 run 改为「前缀+新值+后缀」，
    其余被 anchor 覆盖的 run 清空。这样字体字号完全不变。

    Returns:
        True = 替换成功。
    """
    if not anchor:
        return False
    runs = p.runs
    if not runs:
        return False

    full = "".join(r.text for r in runs)
    idx = full.find(anchor)
    if idx < 0:
        return False

    end = idx + len(anchor)
    pos = 0
    replaced = False
    for r in runs:
        r_start, r_end = pos, pos + len(r.text)
        pos = r_end
        if r_end <= idx or r_start >= end:
            continue                      # 与该区间无交集
        # 与 anchor 有交集
        left = r.text[:max(0, idx - r_start)] if r_start < idx else ""
        right = r.text[max(0, end - r_start):] if r_end > end else ""
        if not replaced:
            r.text = f"{left}{new_text}{right}"
            replaced = True
        else:
            r.text = f"{left}{right}"
    return replaced


def insert_paragraph_after(p: Paragraph, text: str) -> Optional[Paragraph]:
    """在段落 `p` 之后插入一个新段落（复制 p 的段落格式，正文样式）。

    模板里有不少「只有标题、没有正文」的空小节（如第3章「4.实施背景及地块用途」），
    这些小节的内容要用解析出的公告正文补进去，而不是「替换」。
    """
    if not str(text or "").strip():
        return None
    import copy as _copy
    from docx.text.paragraph import Paragraph as _P
    new_p = _copy.deepcopy(p._p)
    # 清空复制来的文字，保留段落属性（样式/缩进）
    for child in list(new_p):
        if child.tag.endswith('}r') or child.tag.endswith('}hyperlink'):
            new_p.remove(child)
    p._p.addnext(new_p)
    para = _P(new_p, p._parent)
    run = para.add_run(str(text).strip())
    # 正文样式：宋体小四（对齐参照件实测：SimSun 12.0pt）
    run.font.name = '宋体'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    run._element.rPr.rFonts.set(qn('w:ascii'), 'Times New Roman')
    from docx.shared import Pt as _Pt
    run.font.size = _Pt(12)
    return para


def replace_paragraph_text(p: Paragraph, new_text: str) -> bool:
    """**整段替换**段落文本，保留首个 run 的字体字号。

    用于「预公告征收目的/工作安排」这类：模板里是别的项目的整段话，
    本项目要用自己的整段话覆盖（不是替换段内某个词）。
    """
    runs = p.runs
    if not runs:
        return False
    runs[0].text = str(new_text or "")
    for r in runs[1:]:
        r.text = ""
    return True


def compose_from_facts(template: str, filled: Dict[str, Any]) -> str:
    """把 `{字段名}` 占位符替换成事实卡取值（取不到则该句去掉，不留空括号）。"""
    filled = filled or {}

    def _sub(m):
        v = filled.get(m.group(1))
        s = str(v).strip() if v is not None else ""
        return "" if s.startswith("【") else s

    out = re.sub(r'\{([a-z_][a-z0-9_]*)\}', _sub, str(template or ""))
    # 清理因缺值留下的空标点/空括号
    out = re.sub(r'[，,、]\s*[。；;]', '。', out)
    out = re.sub(r'（\s*）', '', out)
    out = re.sub(r'\s{2,}', '', out)
    return out.strip()


def format_value_for_anchor(value: str, anchor: str) -> str:
    """按锚点的书写风格调整取值格式。

    模板正文用的是「2026年6月2日」这种中文日期；事实卡里常是 `2026-06-02`。
    直接填进去会让正文出现「2026-06-02，我公司受…」的突兀写法。此处按锚点风格适配。
    """
    v = str(value or "").strip()
    a = str(anchor or "")
    # 中文「年月」锚点 ← ISO 年月值（如 2026-06 → 2026年6月）
    if re.match(r'^\d{4}\s*年\s*\d{1,2}\s*月$', a):
        m = re.match(r'^(\d{4})-(\d{1,2})$', v)
        if m:
            return f"{m.group(1)}年{int(m.group(2))}月"
    # 中文日期锚点 ← ISO 日期值
    if re.match(r'^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$', a):
        m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', v)
        if m:
            return f"{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日"
    # ISO 日期锚点 ← 中文日期值
    if re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', a):
        m = re.match(r'^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日$', v)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 百分比锚点 ← 纯数字值
    if a.endswith('%') and re.fullmatch(r'\d+(?:\.\d+)?', v):
        return f"{v}%"
    return v


def _iter_paragraphs(container) -> List[Paragraph]:
    """取容器（doc 或 cell）内的全部段落（含表格内段落）。"""
    out: List[Paragraph] = []
    for p in getattr(container, "paragraphs", []):
        out.append(p)
    for t in getattr(container, "tables", []):
        for row in t.rows:
            for cell in row.cells:
                out.extend(_iter_paragraphs(cell))
    return out


# ═══════════════════════════════════════════════════════════════
# 章节区间
# ═══════════════════════════════════════════════════════════════

# 🔴 模板 docx 用的是**原始编号**：第一章 = 稳评评审报告表（不编号前置部分）。
#    本系统的正文号 = 模板号 - 1（正文第1章「编制说明」= 模板第二章）。
#    这里显式声明偏移，避免静默错位（把第N章的内容填成第N+1章）。
TEMPLATE_CHAPTER_OFFSET = 1


def chapter_span(doc, chapter_no: int, *, offset: int = TEMPLATE_CHAPTER_OFFSET) -> Tuple[int, int]:
    """返回该**正文号**章节在 `doc.paragraphs` 中的区间 [start, end)。

    以「第X章」标题为界；找不到起始则返回 (-1, -1)。
    """
    target = chapter_no + offset          # 正文号 → 模板号
    paras = doc.paragraphs
    start = -1
    for i, p in enumerate(paras):
        m = _CHAPTER_HEADING_RE.match(p.text.strip())
        if not m:
            continue
        num = _CN.get(m.group(1))
        if num == target:
            start = i
            break
    if start < 0:
        return (-1, -1)
    for j in range(start + 1, len(paras)):
        m = _CHAPTER_HEADING_RE.match(paras[j].text.strip())
        if m and _CN.get(m.group(1)):
            return (start, j)
    return (start, len(paras))


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

class DocxSlotFiller:
    """就地填充器。"""

    def __init__(self, *, ocr_conf_min: float = 0.85):
        self.ocr_conf_min = ocr_conf_min

    @staticmethod
    def _tidy(text: str) -> str:
        """插入正文前的清洗：去首尾空白、去重复标点、把 ISO 日期转中文。"""
        t = str(text or "").strip()
        if not t:
            return ""
        t = re.sub(r'^\s*\d+\s*[.、]\s*', '', t)           # 去掉误带的序号
        t = re.sub(r'(\d{4})-(\d{1,2})-(\d{1,2})',
                   lambda m: f"{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日", t)
        t = re.sub(r'[，,]{2,}', '，', t)
        t = re.sub(r'。{2,}', '。', t)
        t = re.sub(r'\s{2,}', '', t)
        return t.strip()

    def fill_global(
        self,
        doc,
        entries: List[Dict[str, Any]],
        filled: Dict[str, Any],
    ) -> FillResult:
        """**全文**替换（封面 / 公司简介等前置部分）。

        章节区间之外的示例项目数据（最典型的是封面标题里的项目名）不在任何章内，
        必须单独跑一遍全文替换，否则封面会残留模板项目名。

        Args:
            doc: 文档（就地修改）。
            entries: `profile["global"]` 条目。
            filled: 事实卡。

        Returns:
            FillResult（chapter=0 表示前置部分）。
        """
        res = FillResult(chapter=0)
        filled = filled or {}
        for e in (entries or []):
            anchor = str(e.get("anchor", "") or "").strip()
            if not anchor or str(e.get("type", "slot")) != "slot":
                continue
            fk = str(e.get("field", "") or "")
            val = filled.get(fk)
            if not fk or not is_valid_slot_value(val, fk):
                continue
            _newval = format_value_for_anchor(val, anchor)
            if _newval == anchor:
                res.filled_count += 1
                continue
            # 全文替换（段落 + 表格单元格）
            hit = False
            for p in _iter_paragraphs(doc):
                if anchor in p.text and replace_in_paragraph(p, anchor, _newval):
                    hit = True
            if hit:
                res.filled_count += 1
        return res

    def fill_chapter(
        self,
        doc,
        chapter_no: int,
        entries: List[Dict[str, Any]],
        filled: Dict[str, Any],
    ) -> FillResult:
        """按标注表填充某一章（只在本章区间内替换，避免越界改动）。"""
        res = FillResult(chapter=chapter_no)
        filled = filled or {}
        conf_map = (filled.get("_ocr_confidence") or {})

        start, end = chapter_span(doc, chapter_no)
        if start < 0:
            res.gaps.append(Gap(chapter=chapter_no, reason="no_template", severity="error",
                                detail=f"模板中未找到「第{chapter_no}章」标题"))
            return res

        paras = doc.paragraphs[start:end]
        for e in (entries or []):
            anchor = str(e.get("anchor", "") or "").strip()
            etype = str(e.get("type", "slot"))
            if not anchor:
                continue

            if etype == "fixed":
                continue                              # 固定话术：一字不动

            if etype in ("image", "table"):
                # 交给 table_injector 处理，这里只登记锚点
                (res.pending_images if etype == "image" else res.pending_tables).append((anchor, e))
                continue

            # ── type == "insert"：在空小节标题后插入解析出的正文 ──
            if etype == "insert":
                _tpl = str(e.get("template", "") or "")
                fk = str(e.get("field", "") or "")
                text = compose_from_facts(_tpl, filled) if _tpl else str(filled.get(fk) or "").strip()
                text = self._tidy(text)
                if not text or text.startswith("【"):
                    res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                        reason="missing_field", severity="warn",
                                        detail=f"「{anchor}」小节待补内容（{fk or '按模板拼装'}）"))
                    continue
                hit = False
                for p in paras:
                    if anchor and p.text.strip().startswith(anchor):
                        hit = insert_paragraph_after(p, text) is not None
                        break
                if hit:
                    res.filled_count += 1
                else:
                    res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                        reason="unmatched_slot", severity="warn",
                                        detail=f"未定位到小节「{anchor}」"))
                continue

            # ── type == "paragraph"：整段替换（如预公告的征收目的/工作安排整段）──
            if etype == "paragraph":
                fk = str(e.get("field", "") or "")
                val = filled.get(fk)
                if not fk or not is_valid_slot_value(val, fk):
                    res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                        reason="missing_field", severity="error",
                                        detail=f"事实卡缺少「{fk}」（整段替换）"))
                    continue
                hit = False
                for p in paras:
                    if anchor and anchor in p.text:
                        hit = replace_paragraph_text(p, str(val).strip())
                        break
                # 锚点为空时：用「段落序号」定位（id 从 1 起）
                if not hit and not anchor and e.get("paragraph_index"):
                    _idx = int(e["paragraph_index"])
                    if 0 <= _idx < len(paras):
                        hit = replace_paragraph_text(paras[_idx], str(val).strip())
                if hit:
                    res.filled_count += 1
                else:
                    res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                        reason="unmatched_slot", severity="warn",
                                        detail="整段替换未定位到目标段落"))
                continue

            # ── type == "slot" ──
            fk = str(e.get("field", "") or "")
            if not fk:
                res.gaps.append(Gap(chapter=chapter_no, anchor=anchor,
                                    reason="unmatched_slot", severity="warn",
                                    detail="标注未指定字段，需人工确认"))
                continue

            val = filled.get(fk)
            if not is_valid_slot_value(val, fk):
                res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                    reason="missing_field", severity="error",
                                    detail=f"事实卡缺少「{fk}」（当前值：{val!r}）"))
                continue

            # 类型守卫：值必须与槽位单位兼容
            if not value_fits_unit(val, anchor):
                res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                    reason="unmatched_slot", severity="warn",
                                    detail=f"值 {val!r} 与槽位单位不符，未替换"))
                continue

            # 幂等：新旧一致 → 跳过
            _newval = format_value_for_anchor(val, anchor)
            if _newval == anchor:
                res.filled_count += 1
                continue

            hit = False
            hint = str(e.get("context_hint", "") or "")
            if hint:
                # 🔴 同一字面量在模板里可能出现在多处、语义不同
                #    （如「2024年7月3日」= 公示结束日 / 现场勘察日 / 座谈会日）。
                #    优先命中「同时包含锚点与上下文限定词」的段落，避免张冠李戴。
                for p in paras:
                    if anchor in p.text and hint in p.text and \
                            replace_in_paragraph(p, anchor, _newval):
                        hit = True
                        break
            if not hit:
                for p in paras:
                    if anchor in p.text and replace_in_paragraph(p, anchor, _newval):
                        hit = True
                        break
            if hit:
                res.filled_count += 1
                # OCR 低置信 → 值照写，但标记待人工复核
                conf = (conf_map.get(fk) or {}).get("confidence")
                if isinstance(conf, (int, float)) and conf < self.ocr_conf_min:
                    res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                        reason="low_conf_ocr", severity="warn",
                                        detail=f"OCR 置信度 {conf:.2f} < {self.ocr_conf_min}，请人工复核"))
            else:
                res.gaps.append(Gap(chapter=chapter_no, anchor=anchor, field_key=fk,
                                    reason="unmatched_slot", severity="warn",
                                    detail="模板中未定位到该锚点（可能已被其它替换覆盖）"))

        return res


# ═══════════════════════════════════════════════════════════════
# 便捷入口
# ═══════════════════════════════════════════════════════════════

def fill_docx(template_path: str, profile: Dict[str, Any], filled: Dict[str, Any],
              *, ocr_conf_min: float = 0.85) -> Tuple[Any, List[FillResult]]:
    """打开模板 docx，按标注表就地填充全部章节。

    Returns:
        (Document, [FillResult, ...])
    """
    doc = Document(template_path)
    filler = DocxSlotFiller(ocr_conf_min=ocr_conf_min)
    results: List[FillResult] = []
    for ch, entries in (profile.get("chapters") or {}).items():
        try:
            n = int(ch)
        except (ValueError, TypeError):
            continue
        results.append(filler.fill_chapter(doc, n, entries or [], filled))
    return doc, results
