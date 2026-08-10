#!/usr/bin/env python3
"""Standalone report generation script — called via subprocess from API.

Usage: python3 scripts/generate_report.py <session_id> [--domain stability]
Reads session state from JSON file, generates chapters, assembles DOCX,
writes result back to JSON file.

This runs in its own process to avoid uvicorn event loop issues.
"""
import asyncio, json, sys, time, os
from pathlib import Path

# Ensure we're in the backend directory
backend_dir = Path(__file__).resolve().parent.parent
os.chdir(str(backend_dir))
sys.path.insert(0, str(backend_dir))


async def main(session_id: str):
    """Generate report for a session."""
    from app.services.report_service import report_service
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.llm_service import LLMService
    from app.services.report_assembler import report_assembler
    from app.config import settings

    state_file = Path(f"/tmp/report_state_{session_id}.json")
    result_file = Path(f"/tmp/report_result_{session_id}.json")

    try:
        # Load session
        session = report_service.get_session(session_id)
        state = session.state
        state["_report_session"] = session
        state["_skip_analysis"] = True
        state["progress"] = {"current_chapter": 0, "total_chapters": 10, "message": "开始生成..."}

        # Save initial state
        _save_progress(result_file, {"status": "generating", "current_chapter": 0})

        # Data prep
        pdf_texts = state.get("_pdf_texts", {})
        all_pdf_text = "\n".join(pdf_texts.values()) if pdf_texts else ""
        filled = state.setdefault("filled_data", {})

        # Extract info from user messages
        import re as _re_doc
        user_messages = [m.get("content", "") for m in state.get("messages", []) if m.get("role") == "user"]
        all_user_text = " ".join(user_messages)

        # Extract 文号 pattern
        m = _re_doc.search(r'([一-鿿]+拟征告〔\d{4}〕\d+号)', all_user_text)
        if m and not filled.get("doc_reference"):
            filled["doc_reference"] = m.group(1)

        # Extract location from user text if not in filled_data
        if not filled.get("location"):
            loc_match = _re_doc.search(r'(洪泽区|清江浦区|淮安区|淮阴区|涟水县|盱眙县|金湖县)\s*(\S+?(?:街道|镇|乡))?', all_user_text)
            if loc_match:
                parts = [p for p in loc_match.groups() if p]
                filled["location"] = "".join(parts)

        # Extract org_name: 区/县人民政府
        if not filled.get("org_name"):
            org_match = _re_doc.search(r'(洪泽区|清江浦区|淮安区|淮阴区|涟水县|盱眙县|金湖县)人民政府', all_user_text)
            if org_match:
                filled["org_name"] = org_match.group(0)
            elif filled.get("location"):
                # Infer: if location contains 区/县, it's likely the same government
                loc = filled["location"]
                if "区" in loc:
                    filled["org_name"] = loc[:loc.index("区")+1] + "人民政府"
                elif "县" in loc:
                    filled["org_name"] = loc[:loc.index("县")+1] + "人民政府"

        # Build report title
        if not filled.get("report_title"):
            dr = filled.get("doc_reference", "")
            filled["report_title"] = f"{dr}土地征收社会稳定风险评估报告" if dr else "社会稳定风险评估报告"

        print(f"Extracted: doc_ref={filled.get('doc_reference','?')}, loc={filled.get('location','?')}, org={filled.get('org_name','?')}", file=sys.stderr, flush=True)

        # LLM extraction
        if all_pdf_text:
            try:
                from app.services.llm_service import llm_service
                extract_prompt = (
                    "从以下征收土地公告/文件中提取关键数据，用JSON格式返回。\n"
                    "提取: doc_reference, project_name, location, area_m2, area_mu,\n"
                    "org_name, implement_unit, household_count, land_use,\n"
                    "compensation_standard, funding, total_samples, support_rate,\n"
                    "support_count, oppose_count, dept_survey_count, dept_names\n"
                    "找不到的填null。只返回JSON。\n\n"
                    f"文档内容：\n{all_pdf_text[:15000]}"
                )
                llm_result = await _llm_retry(lambda: llm_service.chat(
                    messages=[{"role": "user", "content": extract_prompt}],
                    max_tokens=1500, temperature=0.1,
                ))
                llm_text = llm_result.get("content", "") if isinstance(llm_result, dict) else str(llm_result)
                start = llm_text.find('{')
                if start >= 0:
                    depth = 0
                    for i in range(start, len(llm_text)):
                        if llm_text[i] == '{': depth += 1
                        elif llm_text[i] == '}':
                            depth -= 1
                            if depth == 0:
                                try:
                                    llm_data = json.loads(llm_text[start:i+1])
                                    for k, v in llm_data.items():
                                        if v and v != "null" and not filled.get(k):
                                            filled[k] = v
                                except: pass
                                break
            except Exception as e:
                print(f"LLM extraction failed: {e}", file=sys.stderr)

        filled.setdefault("_fixed_实施单位", "江苏众拓项目代理咨询有限公司")
        state["report_title"] = filled.get("doc_reference") or filled.get("project_name") or "社会稳定风险评估报告"

        # 🔴 Build chapter image map + analyze images by filename
        try:
            report_assembler._get_session_images(state)
            ch_map = state.get("_chapter_image_map", {})
            total_imgs = sum(len(v) for v in ch_map.values())
            print(f"Image map pre-built: {len(ch_map)} chapters, {total_imgs} images", file=sys.stderr, flush=True)

            # Analyze images: generate meaningful captions from filenames
            import re as _re_img
            for ch, imgs in ch_map.items():
                for img_info in imgs:
                    if not isinstance(img_info, dict):
                        continue
                    fname = img_info.get("caption", img_info.get("path", ""))
                    fname = str(fname).rsplit("/", 1)[-1] if "/" in str(fname) else str(fname)

                    # Generate caption from filename
                    caption = fname
                    if '公示' in fname:
                        caption = '社区公示栏现场公示照片'
                    elif '座谈' in fname or '开会' in fname:
                        caption = '稳评座谈会现场照片'
                    elif '现场' in fname or '勘察' in fname:
                        caption = '项目现场勘察照片'
                    elif '位置' in fname or '地图' in fname:
                        caption = '拟征地位置示意图'
                    elif '问卷' in fname or '调查' in fname:
                        caption = '问卷调查表示例'
                    elif '签到' in fname:
                        caption = '座谈会签到表'
                    elif '意见' in fname or '评审' in fname:
                        caption = '专家评审意见表'
                    elif '微信' in fname:
                        caption = '现场工作照片'
                    elif '图片' in fname:
                        caption = '项目相关图片'

                    img_info["caption"] = caption
            print(f"Image captions generated", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"Image processing failed: {e}", file=sys.stderr, flush=True)

        # ═══════════════════════════════════════════════════════════
        # Phase A: Agents — analyze data + retrieve knowledge
        # ═══════════════════════════════════════════════════════════
        llm = LLMService()
        print(f"Using model: {llm.model} @ {llm.base_url}", file=sys.stderr)

        # Agent 1: DataAnalysis — extract structured data from materials
        print("Agent: DataAnalysis...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.data_analysis_agent import DataAnalysisAgent
            da = DataAnalysisAgent(llm_service=llm)
            da._stream_queue = None
            await da.run(state)
            material_count = len(state.get("_project_materials", []))
            print(f"  DataAnalysis done: {material_count} materials processed", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  DataAnalysis skipped: {e}", file=sys.stderr, flush=True)

        # Agent 2: KnowledgeAgent — retrieve RAG context for all chapters
        print("Agent: Knowledge retrieval...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.knowledge_agent import KnowledgeAgent
            ka = KnowledgeAgent(llm_service=llm)
            ka._stream_queue = None
            await ka.run(state)
            kb_cache = state.get("_knowledge_cache", {})
            print(f"  Knowledge done: {len(kb_cache)} chapters cached", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  Knowledge skipped: {e}", file=sys.stderr, flush=True)

        # Agent 3: ImageAnalysis — analyze uploaded images (OCR, classification)
        print("Agent: ImageAnalysis...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.image_analyzer_agent import ImageAnalysisAgent
            ia = ImageAnalysisAgent(llm_service=llm)
            ia._stream_queue = None
            await ia.run(state)
            img_count = len(state.get("_analyzed_images", []))
            print(f"  ImageAnalysis done: {img_count} images analyzed", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  ImageAnalysis skipped: {e}", file=sys.stderr, flush=True)

        # Agent 4: DataValidator — check data completeness before generation
        print("Agent: DataValidator...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.data_validator_agent import DataValidatorAgent
            dv = DataValidatorAgent(llm_service=llm)
            dv._stream_queue = None
            await dv.run(state)
            missing = state.get("_data_gaps", {})
            print(f"  DataValidator done: {len(missing)} data gaps found", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  DataValidator skipped: {e}", file=sys.stderr, flush=True)

        # ═══════════════════════════════════════════════════════════
        # Phase B: Generate chapters
        # ═══════════════════════════════════════════════════════════
        for ch_num in range(1, 11):
            agent = get_chapter_agent(ch_num, llm_service=llm)
            if agent is None: continue
            agent._stream_queue = None

            start = time.time()
            try:
                await agent.run(state)
                ch_data = state.get("chapters", {}).get(ch_num, {})
                md = ch_data.get("markdown", "") if isinstance(ch_data, dict) else ""
                elapsed = time.time() - start
                print(f"Ch{ch_num}: {len(md)} chars in {elapsed:.0f}s", file=sys.stderr)
                _save_progress(result_file, {
                    "status": "generating",
                    "current_chapter": ch_num,
                    "total_chapters": 10,
                    "message": f"第{ch_num}章完成（{len(md)}字）",
                })
                # Rate limit: brief pause between chapters
                if ch_num < 10:
                    await asyncio.sleep(2)
            except Exception as e:
                print(f"Ch{ch_num} FAILED: {e}", file=sys.stderr)
                state["chapters"][ch_num] = {
                    "markdown": f"## 第{ch_num}章\n\n生成失败：{e}",
                    "status": "approved", "title": agent.chapter_title,
                }

        # ═══════════════════════════════════════════════════════════
        # Phase C: Quality Review — review and retry weak chapters
        # ═══════════════════════════════════════════════════════════
        print("Agent: QualityReview...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.quality_review_agent import QualityReviewAgent
            qr = QualityReviewAgent(llm_service=llm)
            qr._stream_queue = None
            await qr.run(state)
            reviews = state.get("_quality_reviews", {})
            retry_count = 0
            for ch_num, review in reviews.items():
                if isinstance(review, dict) and not review.get("passed", True):
                    print(f"  Ch{ch_num} needs revision: {review.get('summary', '')[:80]}", file=sys.stderr, flush=True)
                    # Retry failed chapter
                    agent = get_chapter_agent(int(ch_num), llm_service=llm)
                    if agent:
                        agent._stream_queue = None
                        state[f"_revision_feedback_ch{ch_num}"] = review.get("summary", "")
                        await agent.run(state)
                        retry_count += 1
            print(f"  QualityReview done: {retry_count} chapters retried", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  QualityReview skipped: {e}", file=sys.stderr, flush=True)

        # Agent: CrossReference — check data consistency across chapters
        print("Agent: CrossReference...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.cross_reference_agent import CrossReferenceAgent
            cr = CrossReferenceAgent(llm_service=llm)
            cr._stream_queue = None
            await cr.run(state)
            issues = state.get("_cross_ref_issues", [])
            print(f"  CrossReference done: {len(issues)} issues found", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  CrossReference skipped: {e}", file=sys.stderr, flush=True)

        # Agent: FormatCompliance — verify format compliance
        print("Agent: FormatCompliance...", file=sys.stderr, flush=True)
        try:
            from app.agent.agents.format_compliance_agent import FormatComplianceAgent
            fc = FormatComplianceAgent(llm_service=llm)
            fc._stream_queue = None
            await fc.run(state)
            violations = state.get("_format_violations", [])
            print(f"  FormatCompliance done: {len(violations)} violations", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  FormatCompliance skipped: {e}", file=sys.stderr, flush=True)

        # Assemble (quality fix runs in assembler's _post_generation_fix)
        output_path = ""
        print("Assembling...", file=sys.stderr, flush=True)
        try:
            output_path = report_assembler.assemble(state)
            print(f"Assembled: {output_path}", file=sys.stderr, flush=True)
            abs_output = settings.STORAGE_DIR / output_path
            if abs_output.exists():
                fixed_path = str(abs_output).replace('.docx', '_fixed.docx')
                if Path(fixed_path).exists():
                    output_path = output_path.replace('.docx', '_fixed.docx')
                    print(f"Fixed version: {output_path}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"Assembly failed: {e}", file=sys.stderr, flush=True)
            import traceback; traceback.print_exc(file=sys.stderr)

        chapters = state.get("chapters", {})
        total_chars = sum(len(ch.get("markdown", "")) if isinstance(ch, dict) else 0 for ch in chapters.values())

        # Save final result (API polls this file)
        _save_progress(result_file, {
            "status": "completed",
            "total_chars": total_chars,
            "download_url": f"/api/v1/files/{output_path}" if output_path else None,
            "message": "报告生成完成",
            "output_path": output_path,
        })
        print(f"Result saved: {total_chars} chars, {output_path}", file=sys.stderr, flush=True)

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        _save_progress(result_file, {"status": "error", "message": str(e)[:200]})


async def _llm_retry(fn, max_retries=3):
    for i in range(max_retries):
        try:
            result = await fn()
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            if content and len(content.strip()) > 10:
                return result
        except Exception:
            pass
        if i < max_retries - 1:
            await asyncio.sleep(1)
    return {"content": ""}


def _save_progress(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: generate_report.py <session_id>", file=sys.stderr)
        sys.exit(1)
    session_id = sys.argv[1]
    asyncio.run(main(session_id))
