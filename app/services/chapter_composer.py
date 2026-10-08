"""每章装配总入口 —— 「模板填空 → 表格另拼 → 缺口补写 → 规则校验」一条线走完。

两条产物：
1. **markdown 预览**（`compose_chapter`）：把模板块按标注表填好，产出与现有
   `state["chapters"][n]["markdown"]` 兼容的文本 —— 便于与旧管线做 A/B 对比。
2. **就地填充的 docx**（`build_document`）：直接打开模板 docx 改文字，版式零损失，
   这是 template_fill 管线的最终形态。

缺口（Gap）统一汇总到 `state["_chapter_gaps"]`，供前端弹窗与 Word 批注使用。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.template_slot_schema import (
    DETECTORS, is_fixed_phrase, is_valid_slot_value, pick_field, value_fits_unit,
)
from app.services.docx_slot_filler import format_value_for_anchor

logger = logging.getLogger(__name__)


@dataclass
class ComposeResult:
    """一章的装配结果。"""
    chapter: int
    markdown: str = ""
    filled_count: int = 0
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)

    def to_chapter_dict(self, title: str = "") -> Dict[str, Any]:
        return {"markdown": self.markdown, "title": title, "status": "template_filled",
                "_filled_count": self.filled_count, "_gaps": self.gaps}


class ChapterComposer:
    """章节装配器。"""

    def __init__(self, llm_service=None):
        self._llm = llm_service

    # ═══════════════════════════════════════════════════════════
    # 1) markdown 预览（填模板块文本）
    # ═══════════════════════════════════════════════════════════

    def compose_chapter(self, chapter_no: int, state: Dict[str, Any]) -> ComposeResult:
        """按标注表填空模板块，产出 markdown。"""
        filled = (state or {}).get("filled_data") or {}
        res = ComposeResult(chapter=chapter_no)

        block = self._template_block(chapter_no)
        if not block:
            res.gaps.append({"chapter": chapter_no, "reason": "no_template", "severity": "error",
                             "detail": f"模板中未找到第{chapter_no}章（已回退骨架）"})
            res.markdown = self._fallback_skeleton(chapter_no)
            return res

        entries = self._profile_entries(chapter_no)
        text = block

        # ① 标注表条目（确定性替换）
        for e in entries:
            anchor = str(e.get("anchor", "") or "")
            etype = str(e.get("type", "slot"))
            if not anchor or etype == "fixed":
                continue
            if etype in ("image", "table"):
                continue                       # 由组装阶段处理

            # ── type == "insert"：空小节标题后补一段（markdown 路径）──
            if etype == "insert":
                from app.services.docx_slot_filler import compose_from_facts, DocxSlotFiller
                _tpl = str(e.get("template", "") or "")
                fk = str(e.get("field", "") or "")
                _val = compose_from_facts(_tpl, filled) if _tpl else str(filled.get(fk) or "").strip()
                _val = DocxSlotFiller._tidy(_val)
                if not _val or _val.startswith("【"):
                    res.gaps.append({"chapter": chapter_no, "anchor": anchor, "field_key": fk,
                                     "reason": "missing_field", "severity": "warn",
                                     "detail": f"「{anchor}」小节待补内容"})
                    continue
                _lines = text.split("\n")
                _done = False
                for _i, _l in enumerate(_lines):
                    if _l.strip().startswith(anchor):
                        _lines.insert(_i + 1, _val)
                        _done = True
                        break
                if _done:
                    text = "\n".join(_lines)
                    res.filled_count += 1
                else:
                    res.gaps.append({"chapter": chapter_no, "anchor": anchor,
                                     "reason": "unmatched_slot", "severity": "warn",
                                     "detail": f"未定位到小节「{anchor}」"})
                continue

            fk = str(e.get("field", "") or "")
            if not fk:
                res.gaps.append({"chapter": chapter_no, "anchor": anchor,
                                 "reason": "unmatched_slot", "severity": "warn",
                                 "detail": "标注未指定字段"})
                continue
            val = filled.get(fk)
            if not is_valid_slot_value(val, fk):
                res.gaps.append({"chapter": chapter_no, "anchor": anchor, "field_key": fk,
                                 "reason": "missing_field", "severity": "error",
                                 "detail": f"事实卡缺少「{fk}」"})
                continue
            if not value_fits_unit(val, anchor):
                continue
            _newval = format_value_for_anchor(val, anchor)
            if _newval == anchor:
                res.filled_count += 1
                continue
            hint = str(e.get("context_hint", "") or "")
            if hint:
                # 同一字面量多处出现、语义不同 → 只替换「同句含限定词」的那一处
                _lines = text.split("\n")
                done = False
                for _i, _l in enumerate(_lines):
                    if anchor in _l and hint in _l:
                        _lines[_i] = _l.replace(anchor, _newval, 1)
                        done = True
                        break
                if done:
                    text = "\n".join(_lines)
                    res.filled_count += 1
                    continue
            if anchor in text:
                text = text.replace(anchor, _newval, 1)
                res.filled_count += 1

        # ② 正则兜底（标注表未覆盖的形状类槽位）
        for rule in DETECTORS:
            if rule.slot_type in ("table_no", "figure_no"):
                continue
            for m in list(rule.pattern.finditer(text)):
                span = m.group(0)
                if is_fixed_phrase(span, text[max(0, m.start() - 30):m.end() + 30]):
                    continue
                fk = pick_field(rule, span, text[max(0, m.start() - 40):m.end() + 40], filled)
                if not fk:
                    continue
                val = filled.get(fk)
                if not is_valid_slot_value(val, fk) or not value_fits_unit(val, span):
                    continue
                if str(val).strip() == span:
                    continue
                text = text.replace(span, str(val).strip(), 1)
                res.filled_count += 1

        res.markdown = self._to_markdown(text, chapter_no)
        res.gaps.extend(self._ocr_gaps(chapter_no, filled))
        return res

    # ═══════════════════════════════════════════════════════════
    # 2) 就地填充 docx（最终形态）
    # ═══════════════════════════════════════════════════════════

    def build_document(self, state: Dict[str, Any], template_path: str,
                       ai_sections: Optional[Dict[int, Dict[str, str]]] = None):
        """打开模板 docx 就地填充，返回 (Document, [Gap,...])。

        包含：槽位替换 + 表格另拼 + 附件挂载 + **AI 自主创建小节的整段替换**。

        Args:
            state: 工作流状态。
            template_path: 模板 docx 路径。
            ai_sections: `{章号: {小节关键词: 正文}}`，由 `compose_ai_sections()` 预先异步生成。
        """
        from docx import Document
        from app.services.docx_slot_filler import DocxSlotFiller
        from app.services.table_injector import (
            inject_land_table, inject_survey_tables, inject_appendix_images,
        )
        from app.services.template_profile import load_profile

        filled = (state or {}).get("filled_data") or {}
        doc = Document(template_path)
        filler = DocxSlotFiller()
        gaps: List[Dict[str, Any]] = []

        profile = load_profile()

        # ① 前置部分全文替换（封面标题里的项目名等，不在任何章节区间内）
        if profile.get("global"):
            try:
                gr = filler.fill_global(doc, profile["global"], filled)
                gaps.extend(g.to_dict() for g in gr.gaps)
                if gr.filled_count:
                    logger.info(f"前置部分全文替换 {gr.filled_count} 处")
            except Exception as e:
                logger.warning(f"前置部分替换失败: {e}")

        # ② 逐章就地填充
        for ch, entries in (profile.get("chapters") or {}).items():
            try:
                n = int(ch)
            except (ValueError, TypeError):
                continue
            r = filler.fill_chapter(doc, n, entries or [], filled)
            gaps.extend(g.to_dict() for g in r.gaps)

        # 表格另拼（模板里没有真实数据的表）
        # 勘测定界报告的表（汇总表 → 分类面积表，按报告原顺序）注入第1章
        _tables = (filled.get("_extracted_tables")
                   or (state or {}).get("_extracted_tables") or [])
        try:
            inject_land_table(doc, 6, _tables)   # 表6-1 土地分类面积表 → 第6章
        except Exception as e:
            logger.warning(f"勘测定界表注入失败: {e}")
        try:
            inject_survey_tables(doc, 9, state)  # 表9-1/9-2 问卷统计 → 第9章
        except Exception as e:
            logger.warning(f"问卷统计表注入失败: {e}")

        # 🔴 AI 自主创建的小节：整段替换模板里的示例文字
        if ai_sections:
            try:
                gaps.extend(self._apply_ai_sections(doc, ai_sections))
            except Exception as e:
                logger.warning(f"AI 自主创建小节替换失败: {e}")

        # 附件图片
        try:
            catalog = state.get("_img_catalog") or {}
            if catalog:
                inject_appendix_images(doc, catalog)
        except Exception as e:
            logger.warning(f"附件图片挂载失败: {e}")

        return doc, gaps

    # ═══════════════════════════════════════════════════════════
    # AI 自主创建小节
    # ═══════════════════════════════════════════════════════════

    async def compose_ai_sections(self, state: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
        """为「AI 自主创建」的小节生成正文（如第6章决策合理性分析）。

        Returns:
            `{章号: {小节关键词: 正文}}`；未生成成功的章不出现在结果里。
        """
        from app.services.ai_gap_writer import AIGapWriter, AI_AUTHORED_SECTIONS
        writer = AIGapWriter(llm_service=self._llm)
        out: Dict[int, Dict[str, str]] = {}

        for ch, kws in AI_AUTHORED_SECTIONS.items():
            block = self._template_block(ch)
            for kw in kws:
                excerpt = self._section_excerpt(block, kw)
                try:
                    text = await writer.write_section(ch, kw, state, template_excerpt=excerpt)
                except Exception as e:
                    logger.warning(f"AI 自主创建失败 ch{ch}「{kw}」: {e}")
                    text = None
                if text:
                    out.setdefault(ch, {})[kw] = text
                    logger.info(f"AI 自主创建：第{ch}章「{kw}」（{len(text)} 字）")
        return out

    def _apply_ai_sections(self, doc, ai_sections: Dict[int, Dict[str, str]]) -> List[Dict[str, Any]]:
        """把 AI 生成的正文替换进 docx（整节段落替换）。"""
        from app.services.docx_slot_filler import (
            chapter_span, replace_paragraph_text, _CHAPTER_HEADING_RE, _CN,
        )
        gaps: List[Dict[str, Any]] = []

        for ch, sections in (ai_sections or {}).items():
            s, e = chapter_span(doc, int(ch))
            if s < 0:
                continue
            paras = doc.paragraphs[s:e]
            for kw, text in sections.items():
                # 定位该小节标题段落
                hi = next((i for i, p in enumerate(paras)
                           if _CHAPTER_HEADING_RE.match(p.text.strip()) is None and kw in p.text.strip()
                           and len(p.text.strip()) <= 20), None)
                if hi is None:
                    gaps.append({"chapter": ch, "reason": "unmatched_slot", "severity": "warn",
                                 "detail": f"未定位到「{kw}」小节标题，AI 正文未写入"})
                    continue
                # 小节内容段 = 标题之后到下一个标题之前
                body = []
                for j in range(hi + 1, len(paras)):
                    t = paras[j].text.strip()
                    if not t:
                        continue
                    if len(t) <= 20 and (t.startswith("（") or
                                         _CHAPTER_HEADING_RE.match(t) or
                                         re.match(r'^\d+[.、]', t)):
                        break
                    body.append(paras[j])
                if not body:
                    gaps.append({"chapter": ch, "reason": "unmatched_slot", "severity": "warn",
                                 "detail": f"「{kw}」小节无正文段落可替换"})
                    continue
                replace_paragraph_text(body[0], text)
                for p in body[1:]:
                    replace_paragraph_text(p, "")
                logger.info(f"AI 正文已写入：第{ch}章「{kw}」（替换 {len(body)} 段）")
        return gaps

    @staticmethod
    def _section_excerpt(block_text: str, keyword: str) -> str:
        """取模板里某小节的示例文字（仅作风格参照）。"""
        lines = str(block_text or "").split("\n")
        out, on = [], False
        for l in lines:
            s = l.strip()
            if not s:
                continue
            if len(s) <= 20 and (s.startswith("（") or s.startswith("（")):
                on = keyword in s
                continue
            if on:
                out.append(s)
        return "\n".join(out)[:600]

    # ═══════════════════════════════════════════════════════════
    # 内部工具
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _template_block(chapter_no: int) -> str:
        try:
            from app.services.template_chapter_chunker import get_chapter_template_chunk
            return get_chapter_template_chunk(chapter_no)
        except Exception:
            return ""

    @staticmethod
    def _profile_entries(chapter_no: int) -> List[Dict[str, Any]]:
        try:
            from app.services.template_profile import load_profile
            return (load_profile().get("chapters") or {}).get(str(chapter_no)) or []
        except Exception:
            return []

    @staticmethod
    def _ocr_gaps(chapter_no: int, filled: Dict[str, Any]) -> List[Dict[str, Any]]:
        """OCR 低置信字段 → 缺口（值照写，标记待人工复核）。"""
        out = []
        conf = (filled.get("_ocr_confidence") or {})
        if not isinstance(conf, dict):
            return out
        try:
            from app.config import settings
            thr = float(getattr(settings, "OCR_FIELD_CONFIDENCE_MIN", 0.85))
        except Exception:
            thr = 0.85
        for fk, info in conf.items():
            c = (info or {}).get("confidence") if isinstance(info, dict) else None
            if isinstance(c, (int, float)) and c < thr:
                out.append({"chapter": chapter_no, "field_key": fk,
                            "reason": "low_conf_ocr", "severity": "warn",
                            "detail": f"OCR 置信度 {c:.2f} < {thr}，请人工复核"})
        return out

    @staticmethod
    def _to_markdown(block_text: str, chapter_no: int) -> str:
        """把「（一）小节名」提升为二级标题，接回报告标题体系。"""
        from app.agent.chapter_templates import sectionize
        return sectionize(str(block_text or ""), chapter_no)

    @staticmethod
    def _fallback_skeleton(chapter_no: int) -> str:
        """模板缺失时的兜底骨架。"""
        try:
            from app.agent.chapter_templates import get_fallback_skeleton
            return get_fallback_skeleton(chapter_no)
        except Exception:
            return ""
