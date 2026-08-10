"""DataAnalysisAgent — 分析用户提供的资料，提取每章所需数据。

Replaces OutlineGenerator as the first phase of report generation.
1. Scan all PDFs, images, conversation text
2. Extract structured data per chapter
3. Identify missing data per chapter
4. Present analysis report to user for confirmation
5. Package chapter-specific data for ChapterAgents
"""

import re
import logging
from typing import Dict, List, Any

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class DataAnalysisAgent(BaseAgent):
    """分析用户上传的所有资料，逐章提取数据并分发给章节Agent。"""

    name = "DataAnalysisAgent"
    description = "解析用户提供的PDF/图片/文字资料，提取每章所需数据，检查数据完整性"
    covered_steps = [0]

    # Per-chapter data requirements with chapter-specific field patterns
    CHAPTER_REQUIREMENTS = {
        1: {
            "title": "拟征收决策基本概况",
            "required": ["report_title", "location", "area_m2"],
            "field_patterns": {
                "report_title": [
                    r'([^\s]{2,8}[告发字]\s*〔?\s*\d{4}\s*〕?\s*\d+\s*号)',
                    r'(?:项目名称|决策名称|报告标题)[：:]\s*(.+?)(?:\n|$)',
                ],
                "doc_reference": [
                    r'([^\s]{2,8}[告发字]\s*〔?\s*\d{4}\s*〕?\s*\d+\s*号)',
                ],
                "location": [
                    r'(?:位于|坐落|位置)[：:]?\s*(.{5,100}?)(?:。|\n)',
                ],
                "area_m2": [
                    r'(\d{5,7})\s*(?:平方米|㎡)',
                    r'(?:总面积|面积|合计)\s*[：:]*\s*(\d{5,7})',
                    r'(?:地块|用地).{0,5}面积[^\d]*(\d{5,7})',
                ],
                "area_mu": [r'(\d+\.?\d*)\s*亩'],
                "household_count": [r'(?:涉及|共涉及|征收涉及)[^\d]*(\d+)\s*(?:户|农户)'],
                "population_count": [r'(?:涉及|被征地|安置)[^\d]{0,5}(\d+)\s*(?:人|人口)'],
                "org_name": [
                    r'(?:责任单位|决策单位|征收主体|征收单位|实施单位)[：:]\s*(\S{2,30})',
                    r'(淮安\S{0,10}(?:人民政府|管理委员会|街道办事处))',
                ],
                "implement_unit": [r'江苏众拓项目代理咨询有限公司'],
                "land_use": [r'(?:用途|地类)[：:]?\s*(\S{2,20})'],
                "compensation_standard": [r'(?:补偿标准|综合补偿)[^\d]*(\d[\d,.]*)\s*(?:元|万元)'],
                "total_compensation": [r'(?:征地补偿|补偿总额|总补偿|征地总费用)[^\d]*(\d[\d,.]*)\s*(?:元|万元)'],
                "compensation_doc_id": [r'([^\s]{2,8}[发字]\s*〔?\d{4}〕?\s*\d+\s*号)'],
                "red_line_map": [r'(?:红线图|征收范围图|勘测定界图|用地红线|征地红线|附图)'],
                "funding": [r'(?:资金|投资)[：:]?\s*(\d[\d,.]*)\s*(?:万元|亿元)'],
                "earliest_date": [r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'],
            },
        },
        2: {
            "title": "评估过程、方法和依据",
            "required": [],
            "rag_dependent": True,
            "field_patterns": {
                "legal_basis_docs": [
                    r'DB32/T4013',
                    r'(?:土地管理法|征收条例|补偿条例|突发事件应对法|社会稳定风险评估)',
                    r'(?:依据|根据|按照)[^。]{0,30}(?:法律|法规|条例|办法|标准|规范)',
                ],
            },
        },
        3: {
            "title": "社会稳定风险因素调查",
            "required": [],
            "field_patterns": {
                "total_samples": [
                    r'(?:发放问卷|回收问卷|调查|走访)[^。]{0,15}(\d+)\s*(?:份|人|户)',
                    r'(?:共|共计|总计)\s*(\d+)\s*(?:份|人|户)',
                ],
                "support_count": [r'(\d+)\s*(?:人|份)\s*(?:支持|赞成|同意)'],
                "oppose_count": [r'(\d+)\s*(?:人|份)\s*(?:反对|不同意)'],
                "support_rate": [r'(\d{2,3}\.?\d*)\s*%\s*(?:支持|赞成|同意)'],
                "stakeholder_demands": [r'(?:主要|群众).{0,10}(?:诉求|意见|要求)[：:]?\s*(.+?)(?:\n|。|$)'],
                "sample_coverage_rate": [r'(\d+\.?\d*)\s*%\s*(?:样本|抽样|覆盖)'],
                "survey_method": [
                    r'(?:入户走访|实地走访|现场访谈|座谈会|听证会)[^。]{0,30}',
                    r'(?:参会|参加|出席).{0,10}(\d+)\s*(?:人|名)',
                ],
            },
        },
        4: {
            "title": "决策综合分析",
            "required": [],
            "rag_dependent": True,
        },
        5: {
            "title": "风险因素识别与初始等级表",
            "required": [],
            "rag_dependent": True,
        },
        6: {
            "title": "措施前风险等级研判",
            "required": [],
            "rag_dependent": True,
        },
        7: {
            "title": "风险防范与化解措施",
            "required": [],
            "rag_dependent": True,
        },
        8: {
            "title": "措施后风险等级评估",
            "required": [],
            "rag_dependent": True,
        },
        9: {
            "title": "评估结论与建议",
            "required": [],
            "rag_dependent": True,
        },
        10: {
            "title": "应急预案",
            "required": [],
            "rag_dependent": True,
        },
    }

    async def think(self, state: dict) -> Dict[str, Any]:
        pdf_count = len(state.get("_pdf_texts", {}))
        filled_count = len(state.get("filled_data", {}))
        image_count = self._count_images(state)

        return {
            "summary": f"分析用户资料：{pdf_count}PDF + {image_count}图片 + {filled_count}字段",
            "steps": [
                "📂 扫描所有PDF文档，提取文本内容...",
                "📷 分析图片（公告/问卷/照片）...",
                "📊 逐章提取关键数据...",
                "📋 生成数据完整性报告...",
                "📦 打包每章专属数据...",
            ],
            "actions": [
                {"type": "extract_pdf_data"},
                {"type": "analyze_images"},
                {"type": "extract_chapter_data"},
                {"type": "build_report"},
                {"type": "package_data"},
            ],
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Extract data from all sources and build per-chapter packages."""
        # Step 1: PDF→text (already done by run_master_agent), images→vision API
        # 🔴 Image classification has a 90s timeout; OCR has its own per-page timeouts
        import asyncio as _asyncio_act
        await self._emit_thinking("正在分析图片资料...")
        try:
            await _asyncio_act.wait_for(
                self._analyze_media_files(state),
                timeout=120.0,  # 2 minutes — image classification only (OCR skipped)
            )
        except _asyncio_act.TimeoutError:
            await self._emit_thinking("⏰ 图片分析超时（5分钟），跳过剩余部分，使用已有数据继续...")
        except Exception as e:
            await self._emit_thinking(f"⚠️ 图片分析异常，跳过: {e}")

        # Step 2: Collect all raw data
        await self._emit_thinking("正在收集整理原始数据...")
        raw_data = self._collect_raw_data(state)

        # Step 3: Extract chapter-specific data — regex first (fast),
        # then LLM enhancement in parallel for chapters with missing data.
        await self._emit_thinking("正在按章节提取数据...")
        chapter_data = {}
        chapter_missing = {}
        llm_tasks = []  # (ch_num, task) for chapters needing LLM enhancement

        for ch_num in range(1, 11):
            req = self.CHAPTER_REQUIREMENTS.get(ch_num, {})
            ch_data, missing = self._extract_chapter_data(ch_num, req, raw_data, state)
            chapter_data[ch_num] = ch_data
            chapter_missing[ch_num] = missing
            if missing and self._llm and raw_data.get("_full_pdf_text"):
                llm_tasks.append((ch_num, ch_data, missing))

        if llm_tasks:
            await self._emit_thinking(
                f"正在并行补充 {len(llm_tasks)} 个章节的数据（LLM提取）...")
            import asyncio as _asyncio
            results = await _asyncio.gather(*[
                self._llm_extract_chapter1(ch_data, missing, raw_data)
                for _, ch_data, missing in llm_tasks
            ], return_exceptions=True)
            for (ch_num, _, _), result in zip(llm_tasks, results):
                if isinstance(result, Exception):
                    logger.warning(f"Ch{ch_num} LLM extract failed: {result}")
                else:
                    chapter_data[ch_num], chapter_missing[ch_num] = result

        # Step 4: Build analysis report
        await self._emit_thinking("正在生成分析报告...")
        report = self._build_analysis_report(chapter_data, chapter_missing, raw_data)

        # Step 5: Emit report and wait for confirmation
        await self._emit_analysis_report(report, chapter_data, chapter_missing, state)

        return {
            "chapter_data": chapter_data,
            "chapter_missing": chapter_missing,
            "raw_data": raw_data,
            "report": report,
        }

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        state["_chapter_data_packages"] = result.get("chapter_data", {})
        state["_chapter_missing"] = result.get("chapter_missing", {})
        state["_raw_analysis"] = result.get("raw_data", {})
        return state

    # ═══════════════════════════════════════════════════════
    # Data Collection
    # ═══════════════════════════════════════════════════════

    def _count_images(self, state: dict) -> int:
        count = 0
        structured = state.get("structured_data") or {}
        for sd in structured.values():
            if isinstance(sd, dict):
                imgs = sd.get("images") or sd.get("attachments") or []
                count += len([i for i in imgs if i and not str(i).startswith("[")])
        count += len(state.get("_pdf_images") or [])
        return count

    async def _analyze_media_files(self, state: dict) -> None:
        """Smart media analysis with category-based processing.

        Strategy:
        1. PDFs → text already extracted at upload. Prioritize and report status.
        2. Text-heavy images (问卷/专家意见/签字/座谈) → Full OCR via vision API
        3. Scene photos (现场图/公示图/地图) → Quick classify only, decide placement in report
        4. Skip duplicates and limit quantities to avoid blocking
        """
        structured = state.get("structured_data") or {}
        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

        pdf_count = 0
        image_files = []
        pdf_paths = []

        for sd in structured.values():
            if not isinstance(sd, dict):
                continue
            all_files = (sd.get("images") or []) + (sd.get("attachments") or [])
            for f in all_files:
                if not f or str(f).startswith("["):
                    continue
                fname = str(f).lower()
                if fname.endswith(".pdf"):
                    pdf_count += 1
                    if f not in pdf_paths:
                        pdf_paths.append(f)
                elif any(fname.endswith(ext) for ext in image_exts):
                    if f not in image_files:
                        image_files.append(f)

        # ── Step 1: PDF status (text extracted at upload, report what we have) ──
        pdf_texts = state.get("_pdf_texts") or {}
        if pdf_count > 0:
            total_chars = sum(len(t) for t in pdf_texts.values())
            await self._emit_thinking(
                f"📄 {pdf_count} 个PDF已解析：{len(pdf_texts)} 个成功，共 {total_chars} 字符"
            )

        # ── Step 2: Classify images into processing categories ──
        # TEXT images: need full OCR — surveys, expert opinions, signed docs, meeting records
        TEXT_KW = [
            "问卷", "调查", "测评", "民意", "走访", "入户",
            "专家", "评审", "论证", "意见", "签字", "盖章",
            "登记", "确认", "座谈", "会议记录", "笔录", "签到",
            "扫描", "扫描件",
        ]
        # NOTICE images: public notices, approvals — OCR for key info
        NOTICE_KW = [
            "公告", "公示", "通知", "批复", "决定", "批文",
            "征收", "红头", "文件", "决定书",
        ]
        # SCENE images: site photos, building photos — just classify, don't OCR
        SCENE_KW = [
            "现场", "照片", "现状", "施工", "地块", "道路",
            "建筑", "房屋", "厂房", "农田", "地上", "附着",
            "img_", "dsc", "pict", "photo", "微信", "wechat",
            "mmexport", "image", "picture",
        ]

        text_images = []
        notice_images = []
        scene_images = []
        unknown_images = []

        for img in image_files:
            fname = img.lower()
            if any(kw in fname for kw in TEXT_KW):
                text_images.append(img)
            elif any(kw in fname for kw in NOTICE_KW):
                notice_images.append(img)
            elif any(kw in fname for kw in SCENE_KW):
                scene_images.append(img)
            else:
                # Default: treat as scene photo (most common for unnamed images)
                unknown_images.append(img)

        # Scene + unknown → all scene photos
        all_scene = scene_images + unknown_images

        await self._emit_thinking(
            f"📷 图片分类：{len(text_images)} 文本类 + {len(notice_images)} 公告类 "
            f"+ {len(all_scene)} 现场/照片类"
        )

        # ── Step 3: OCR text images (surveys, expert opinions) ──
        ocr_images = text_images + notice_images
        # Limit: max 12 text images, max 6 notices
        ocr_images = ocr_images[:18]

        if ocr_images:
            # Add to structured_data for ImageAnalysisAgent
            for sk in list(structured.keys()):
                if sk.startswith("step_"):
                    sd = structured[sk]
                    if isinstance(sd, dict):
                        existing = sd.get("images") or []
                        for img in ocr_images:
                            if img not in existing:
                                existing.append(img)
                        sd["images"] = existing
                        break
            else:
                structured["step_1"] = {"images": ocr_images}
            state["structured_data"] = structured

            try:
                from .image_analyzer_agent import ImageAnalysisAgent
                agent = ImageAnalysisAgent(llm_service=self._llm)
                await self._emit_thinking(f"🔍 正在OCR识别 {len(ocr_images)} 张文本图片...")
                await agent.run(state, self._stream_queue)
            except Exception as e:
                logger.warning(f"OCR image analysis failed: {e}")
                await self._emit_thinking(f"⚠️ 文本图片OCR跳过：{e}")

        # ── Step 4: Quick-classify scene photos (placement only, no deep OCR) ──
        scene_to_analyze = all_scene[:20]  # Max 20 scene photos

        if scene_to_analyze:
            await self._emit_thinking(
                f"📸 正在快速分类 {len(scene_to_analyze)} 张场景照片（确定报告位置）..."
            )
            scene_placements = await self._quick_classify_scenes(state, scene_to_analyze)
            state["_scene_photo_placements"] = scene_placements
            # Report summary
            ch_counts = {}
            for p in scene_placements:
                ch = p.get("chapter", "other")
                ch_counts[ch] = ch_counts.get(ch, 0) + 1
            summary_parts = []
            # Store classified photos in state keyed by chapter for agents & assembler
            chapter_images = state.setdefault("_chapter_images", {})
            for p in scene_placements:
                ch = str(p.get("chapter", "other"))
                chapter_images.setdefault(ch, []).append(p)
            state["_chapter_images"] = chapter_images

            for ch, count in sorted(ch_counts.items()):
                ch_label = {1: "第1章 项目概况", 2: "第2章 评估依据", 3: "第3章 风险调查",
                           5: "第5章 风险识别", 8: "第8章 风险防范", 9: "第9章 风险等级",
                           "other": "其他/附录"}.get(ch, f"第{ch}章" if isinstance(ch, int) else str(ch))
                summary_parts.append(f"{ch_label}: {count}张")
            await self._emit_thinking(f"📸 场景照片分布 → {' | '.join(summary_parts)}")

        # ── Step 5: Check for scanned PDFs (skip OCR — too slow, text extracted at upload) ──
        ocr_list = state.get("_pdf_need_ocr", [])
        if ocr_list:
            ocr_names = [p.rsplit('/', 1)[-1][:30] for p in ocr_list[:5]]
            await self._emit_thinking(
                f"⚠️ {len(ocr_list)} 个PDF无可提取文本（扫描版），跳过OCR。"
                f"建议上传文字版PDF或手动输入关键数据。"
                f"（{', '.join(ocr_names)}...）"
            )

    # ═══════════════════════════════════════════════════════
    # Scanned PDF OCR
    # ═══════════════════════════════════════════════════════

    async def _quick_classify_scenes(self, state: dict, scene_images: list) -> list:
        """Quick-classify scene photos to determine report placement.

        Uses a lightweight vision API call — just asks "what is this and where
        should it go in the report?" — NOT full OCR. Much faster (~3s per image).

        Returns: [{path, category, chapter, description}, ...]
        """
        import asyncio as _asyncio, base64
        from app.services.file_service import file_service

        if not self._llm or not scene_images:
            return []

        sem = _asyncio.Semaphore(5)  # 5 concurrent — fast enough for classification
        results = []
        completed = 0
        total = len(scene_images)

        classify_prompt = (
            "你是一个工程报告图片分类助手。请用一句话描述这张图片的内容，"
            "并判断它适合放在社会稳定风险评估报告的哪个章节。\n"
            "可选章节：1-项目概况(现场照片/地块现状), 3-风险调查(走访/座谈/问卷), "
            "5-风险识别, 8-风险防范措施, 9-风险等级, other-附录/其他\n"
            "请用JSON格式回复：{\"desc\":\"简短描述\",\"chapter\":数字或\"other\"}\n"
            "只输出JSON，不要其他内容。"
        )

        async def _classify_one(img_path: str) -> dict:
            nonlocal completed
            async with sem:
                try:
                    abs_path = file_service.get_absolute_path(img_path)
                    if not abs_path.exists():
                        return {"path": img_path, "error": "文件不存在", "chapter": "other", "desc": ""}

                    img_bytes = abs_path.read_bytes()
                    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

                    # Determine media type
                    ext = img_path.rsplit(".", 1)[-1].lower() if "." in img_path else "png"
                    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                            "png": "image/png", "gif": "image/gif",
                            "webp": "image/webp", "bmp": "image/bmp"}.get(ext, "image/png")

                    result_text = await _asyncio.wait_for(
                        self._llm.chat_with_image(
                            text=classify_prompt,
                            image_base64=img_b64,
                            media_type=mime,
                            max_tokens=200,  # Short response
                        ),
                        timeout=15.0,
                    )

                    completed += 1
                    # Parse JSON from response
                    import json as _json, re as _re
                    try:
                        # Try direct JSON parse
                        data = _json.loads(result_text.strip())
                    except Exception:
                        # Try to extract JSON from markdown code blocks
                        m = _re.search(r'\{[^}]+\}', result_text)
                        if m:
                            try:
                                data = _json.loads(m.group(0))
                            except Exception:
                                data = {"desc": result_text[:80], "chapter": "other"}
                        else:
                            data = {"desc": result_text[:80], "chapter": "other"}

                    chapter = data.get("chapter", "other")
                    if isinstance(chapter, str) and chapter.isdigit():
                        chapter = int(chapter)

                    # Emit progress
                    if self._stream_queue:
                        await self._stream_queue.put({
                            "event": "thinking",
                            "data": {"content": f"📸 [{completed}/{total}] {img_path.rsplit('/',1)[-1][:25]}: {data.get('desc','')[:30]}"},
                        })

                    return {
                        "path": img_path,
                        "category": data.get("desc", ""),
                        "chapter": chapter,
                        "description": data.get("desc", ""),
                    }

                except _asyncio.TimeoutError:
                    completed += 1
                    return {"path": img_path, "chapter": "other", "desc": "分类超时"}
                except Exception as e:
                    completed += 1
                    return {"path": img_path, "chapter": "other", "desc": str(e)[:50]}

        tasks = [_classify_one(img) for img in scene_images]
        outcomes = await _asyncio.gather(*tasks, return_exceptions=True)

        for outcome in outcomes:
            if isinstance(outcome, dict):
                results.append(outcome)

        return results

    async def _ocr_scanned_pdfs(self, state: dict, ocr_list: list, pdf_paths: list) -> None:
        """OCR scanned PDFs in PARALLEL with progress feedback.

        Optimizations: asyncio.gather + semaphore(5) + 20s timeout per page.
        """
        import asyncio as _asyncio
        import base64

        ocr_results = {}
        sem = _asyncio.Semaphore(5)

        async def _ocr_one_page(pdf_path: str, pdf_name: str, page_num: int, total_pages: int) -> dict:
            """OCR one page with timeout and semaphore."""
            async with sem:
                try:
                    import fitz
                    doc = fitz.open(pdf_path)
                    if page_num >= len(doc):
                        doc.close()
                        return {"page": page_num, "text": ""}
                    page = doc[page_num]
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    doc.close()

                    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                    ocr_prompt = (
                        "你是一个OCR工具。请逐字识别这张扫描文档中的所有文字，"
                        "按原文格式输出。不要总结、不要添加解释、不要遗漏任何文字。"
                        "只输出识别到的原文内容。"
                    )

                    if not self._llm:
                        return {"page": page_num, "text": ""}

                    ocr_text = await _asyncio.wait_for(
                        self._llm.chat_with_image(
                            text=ocr_prompt,
                            image_base64=img_b64,
                            media_type="image/png",
                            max_tokens=3000,
                        ),
                        timeout=30.0,
                    )

                    if self._stream_queue:
                        await self._stream_queue.put({
                            "event": "thinking",
                            "data": {"content": f"📷 OCR [{page_num+1}/{total_pages}] {pdf_name}"},
                        })

                    return {"page": page_num, "text": ocr_text.strip() if ocr_text else ""}
                except _asyncio.TimeoutError:
                    logger.warning(f"OCR timeout: {pdf_name} page {page_num+1}")
                    return {"page": page_num, "text": "", "timeout": True}
                except Exception as e:
                    logger.warning(f"OCR failed: {pdf_name} page {page_num+1}: {e}")
                    return {"page": page_num, "text": ""}

        for pdf_rel_path in ocr_list:
            try:
                from app.services.file_service import file_service
                pdf_abs = file_service.get_absolute_path(pdf_rel_path)
                if not pdf_abs.exists():
                    from pathlib import Path
                    pdf_abs = Path(pdf_rel_path)
                    if not pdf_abs.exists():
                        continue

                import fitz
                doc = fitz.open(str(pdf_abs))
                total_pages = min(len(doc), 10)
                doc.close()

                await self._emit_thinking(
                    f"🚀 OCR并行识别 {pdf_abs.name}（{total_pages}页，并发3路）..."
                )

                tasks = [
                    _ocr_one_page(str(pdf_abs), pdf_abs.name, pn, total_pages)
                    for pn in range(total_pages)
                ]
                outcomes = await _asyncio.gather(*tasks, return_exceptions=True)

                page_texts = []
                for outcome in outcomes:
                    if isinstance(outcome, Exception):
                        continue
                    if isinstance(outcome, dict) and outcome.get("text"):
                        page_texts.append((outcome["page"], outcome["text"]))
                page_texts.sort(key=lambda x: x[0])

                if page_texts:
                    full_ocr = "\n\n--- PAGE BREAK ---\n\n".join([t[1] for t in page_texts])
                    ocr_results[pdf_rel_path] = full_ocr
                    logger.info(
                        f"OCR {pdf_abs.name}: {len(page_texts)}/{total_pages} pages, "
                        f"{len(full_ocr)} chars"
                    )
                    await self._emit_thinking(
                        f"✅ OCR完成 {pdf_abs.name}：{len(full_ocr)} 字符"
                    )
                else:
                    await self._emit_thinking(f"⚠️ OCR失败 {pdf_abs.name}：无法识别文字")

            except Exception as e:
                logger.warning(f"OCR PDF {pdf_rel_path} failed: {e}")
                await self._emit_thinking(f"⚠️ OCR异常 {pdf_rel_path}：{e}")

        if ocr_results:
            pdf_texts = state.setdefault("_pdf_texts", {})
            pdf_texts.update(ocr_results)
            state["_pdf_texts"] = pdf_texts


    def _collect_raw_data(self, state: dict) -> Dict[str, Any]:
        data = {}

        # PDF texts (most important)
        pdf_texts = state.get("_pdf_texts") or {}
        all_pdf = ""
        for name, text in pdf_texts.items():
            all_pdf += f"\n=== {name} ===\n{text[:10000]}\n"
        for pc in state.get("_pdf_contents") or []:
            all_pdf += f"\n=== {pc.get('name', 'PDF')} ===\n{pc.get('text', '')[:10000]}\n"
        data["_full_pdf_text"] = all_pdf

        # Filled data
        data.update(state.get("filled_data") or {})

        # Conversation
        msgs = state.get("messages") or []
        user_texts = [m.get("content", "") for m in msgs if m.get("role") == "user"]
        data["_conversation"] = "\n".join(user_texts[-10:])

        # Structured data
        for sk, sd in (state.get("structured_data") or {}).items():
            if isinstance(sd, dict):
                for k, v in sd.items():
                    if k not in ("images", "attachments", "_files_processed"):
                        data[f"{sk}.{k}"] = str(v)[:500]

        data["report_title"] = state.get("report_title", "")
        data["project_context"] = state.get("project_context", "")[:3000]

        return data

    # ═══════════════════════════════════════════════════════
    # Per-Chapter Extraction
    # ═══════════════════════════════════════════════════════

    def _extract_chapter_data(
        self, ch_num: int, req: dict, raw: dict, state: dict
    ) -> tuple:
        """Extract chapter-specific data using per-chapter field_patterns."""
        ch_data = {}
        all_keys = req.get("required", []) + list(req.get("field_patterns", {}).keys())

        # 1. Direct key matches from raw data
        for key in all_keys:
            for rk, rv in raw.items():
                if key in rk.lower() or rk.lower().endswith(key):
                    ch_data[key] = str(rv)[:2000] if rv else ""
                    break

        # 2. Per-chapter PDF field patterns
        pdf_text = raw.get("_full_pdf_text", "")
        field_patterns = req.get("field_patterns", {})

        for field, patterns in field_patterns.items():
            if field in ch_data and ch_data[field]:
                continue
            for pattern in patterns:
                match = re.search(pattern, pdf_text)
                if match:
                    try:
                        val = match.group(1) if match.lastindex else match.group(0)
                    except (IndexError, AttributeError):
                        val = match.group(0)
                    if val and len(val.strip()) > 0:
                        ch_data[field] = val.strip()[:500]
                        break

        # 3. From filled_data directly
        filled = state.get("filled_data") or {}
        for key in all_keys:
            if key not in ch_data and key in filled and filled[key]:
                ch_data[key] = str(filled[key])[:2000]

        # 4. RAG-dependent chapters
        if req.get("rag_dependent"):
            ch_data["_source"] = "RAG知识库"

        # Identify missing required fields
        missing = [k for k in req.get("required", []) if not ch_data.get(k)]

        return ch_data, missing

    async def _llm_extract_chapter1(
        self, ch_data: dict, missing: list, raw: dict
    ) -> tuple:
        """Use LLM to extract chapter 1 fields that regex missed.

        Only called when regex extraction leaves required fields missing.
        Sends the full PDF text to LLM with a structured extraction prompt.
        """
        pdf_text = raw.get("_full_pdf_text", "")[:20000]
        user_input = raw.get("_user_input", "")[:5000]
        all_text = f"{pdf_text}\n\n---用户输入---\n{user_input}"

        if len(all_text.strip()) < 100:
            return ch_data, missing

        prompt = (
            "你是文档数据提取专家。请从以下文档中提取所有可用的项目数据。\n\n"
            "需要提取的字段：\n"
            "- report_title: 报告标题\n"
            "- project_name: 项目名称\n"
            "- doc_reference: 文号/公告号\n"
            "- org_name: 责任单位/征收主体名称\n"
            "- implement_unit: 实施单位\n"
            "- location: 位置/坐落地址\n"
            "- area_m2: 面积（平方米，纯数字）\n"
            "- area_mu: 面积（亩，纯数字）\n"
            "- land_use: 土地用途/地类\n"
            "- household_count: 涉及户数\n"
            "- population_count: 涉及人口数\n"
            "- compensation_standard: 补偿标准\n"
            "- total_compensation: 补偿总额\n"
            "- funding: 资金/投资额\n"
            "- total_samples: 调查样本数/问卷数\n"
            "- support_rate: 群众支持率\n"
            "- support_count: 支持人数\n"
            "- oppose_count: 反对人数\n"
            "- announcement_period: 公告期限\n"
            "- announcement_date: 公告日期\n"
            "- decision_purpose: 征收目的\n"
            "- earliest_date: 最早日期\n\n"
            f"文档内容：\n{all_text}\n\n"
            "请以JSON格式输出提取结果，只输出能找到的字段，找不到不要编造。"
            "格式：{\"field_name\": \"value\"}"
        )

        try:
            import asyncio as _asyncio
            response = await _asyncio.wait_for(
                self._llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=3000,
                ),
                timeout=30.0,
            )

            if not response:
                return ch_data, missing

            # Parse JSON from response
            import json
            # Try to extract JSON block
            json_match = None
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    json_match = line
                    break
            if not json_match and "{" in response:
                start = response.index("{")
                end = response.rindex("}") + 1
                json_match = response[start:end]

            if json_match:
                extracted = json.loads(json_match)
                for field in missing[:]:
                    val = extracted.get(field, "")
                    if val and str(val).strip():
                        ch_data[field] = str(val).strip()[:500]
                        missing.remove(field)
                        logger.info(f"LLM extracted ch1.{field}: {str(val)[:50]}")

        except Exception as e:
            logger.warning(f"LLM chapter 1 extraction failed: {e}")

        return ch_data, missing

    # ═══════════════════════════════════════════════════════
    # Report Building
    # ═══════════════════════════════════════════════════════

    def _build_analysis_report(
        self, chapter_data: dict, chapter_missing: dict, raw: dict
    ) -> str:
        lines = [
            "# 📊 资料分析报告\n",
            f"**数据来源**：{len(raw)} 个数据字段\n",
            "---\n",
            "## 各章数据状态\n",
            "| 章节 | 状态 | 已有数据 | 缺失项 |",
            "|------|------|----------|--------|",
        ]

        for ch_num in range(1, 11):
            req = self.CHAPTER_REQUIREMENTS.get(ch_num, {})
            missing = chapter_missing.get(ch_num, [])
            data_count = len(chapter_data.get(ch_num, {}))

            if req.get("rag_dependent") and not req.get("required"):
                status = "✅ RAG"
                miss_str = "—"
            elif not missing:
                status = "✅ 充足"
                miss_str = "—"
            elif len(missing) <= 1:
                status = "⚠️ 基本"
                miss_str = ", ".join(missing)
            else:
                status = "🔴 不足"
                miss_str = ", ".join(missing[:3])

            title = req.get("title", f"第{ch_num}章")
            lines.append(f"| {status} {title} | {data_count}项 | {miss_str} |")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 逐章详情\n")
        for ch_num in range(1, 11):
            req = self.CHAPTER_REQUIREMENTS.get(ch_num, {})
            title = req.get("title", f"第{ch_num}章")
            ch_data = chapter_data.get(ch_num, {})
            missing = chapter_missing.get(ch_num, [])

            lines.append(f"### 第{ch_num}章：{title}")
            if ch_data:
                lines.append("**已提取数据**：")
                for k, v in list(ch_data.items())[:5]:
                    if not k.startswith("_"):
                        lines.append(f"  - {k}: {str(v)[:80]}")
            if missing:
                lines.append(f"**缺失**：{'、'.join(missing)}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("请**确认**以上分析结果，系统将按此数据逐章生成报告。")
        lines.append("如需补充数据请直接提供，回复「**确认**」开始逐章生成。")

        return "\n".join(lines)

    async def _emit_analysis_report(
        self, report: str, chapter_data: dict, chapter_missing: dict, state: dict
    ) -> None:
        if not self._stream_queue:
            return

        await self._stream_queue.put({
            "event": "message",
            "data": {"role": "agent", "content": report, "message_type": "analysis_report"},
        })

        await self._stream_queue.put({
            "event": "analysis_complete",
            "data": {
                "chapters": [
                    {
                        "number": ch_num,
                        "title": self.CHAPTER_REQUIREMENTS.get(ch_num, {}).get("title", ""),
                        "data_count": len(chapter_data.get(ch_num, {})),
                        "missing": chapter_missing.get(ch_num, []),
                    }
                    for ch_num in range(1, 11)
                ],
            },
        })

        state["phase"] = "analysis_review"
        await self._stream_queue.put({
            "event": "phase_change",
            "data": {"phase": "analysis_review", "message": "请确认资料分析结果"},
        })
