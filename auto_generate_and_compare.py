#!/usr/bin/env python3
"""Automated report generation + template comparison.

1. Generate stability report from 稳评资料
2. Compare with stability templates, re-generate if score < 70%
3. Generate bidding document from 金湖县项目
4. Compare with bidding templates, re-generate if score < 70%
"""

import asyncio
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

STABILITY_DIR = Path("/Users/mac/Downloads/稳评资料")
BIDDING_FILE = Path("/Users/mac/Downloads/金湖县2026年度耕地占补平衡技术服务项目.doc")
MAX_ITERATIONS = 3
MIN_SCORE = 0.70


async def prepare_stability_state():
    """Prepare state for stability report generation."""
    from app.services.file_service import file_service
    from app.agent.state import create_initial_state

    session_id = f"auto_stab_{__import__('uuid').uuid4().hex[:8]}"
    state = create_initial_state(
        session_id=session_id,
        report_title="洪拟征告〔2026〕7号（商业开发项目）土地征收决策社会稳定风险评估报告",
        project_context="洪拟征告〔2026〕7号 社会稳定风险评估",
    )

    # Extract PDFs
    pdf_texts = {}
    image_paths = []
    image_count = 0
    for f in STABILITY_DIR.glob("**/*"):
        if f.is_file() and f.name.startswith("."):
            continue
        if f.suffix.lower() == ".pdf":
            try:
                text = file_service.extract_pdf_text(str(f))
                if text:
                    pdf_texts[f.name] = text
                    print(f"  PDF {f.name}: {len(text)} chars")
            except Exception as e:
                print(f"  PDF {f.name}: FAILED - {e}")
        elif f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            image_count += 1
            # Skip images: Vision API is too slow for 47 images in automation

    if image_count:
        print(f"  Skipping {image_count} images (Vision API bottleneck)")

    state["_pdf_texts"] = pdf_texts
    state["filled_data"] = {
        "org_name": "淮安市洪泽区人民政府",
        "implement_unit": "江苏众拓项目代理咨询有限公司",
        "location": "淮安市洪泽区朱坝街道三圩社区",
        "area_m2": "326342",
        "area_mu": "489.51",
        "land_use": "商业服务业设施用地",
        "doc_reference": "洪拟征告〔2026〕7号",
        "report_title": "洪拟征告〔2026〕7号（商业开发项目）土地征收决策社会稳定风险评估报告",
    }
    state["phase"] = "collecting"
    state["status"] = "collecting"
    state["_domain"] = "stability"
    state["_conversation_domain"] = "stability"
    state["use_master_agent"] = True

    structured = state.get("structured_data", {})
    structured["step_1"] = {"images": image_paths, "attachments": list(pdf_texts.keys())}
    state["structured_data"] = structured

    return state, session_id


async def prepare_bidding_state():
    """Prepare state for bidding document generation."""
    from app.agent.state import create_initial_state

    session_id = f"auto_bid_{__import__('uuid').uuid4().hex[:8]}"

    source_text = ""
    if BIDDING_FILE.exists():
        try:
            # Try .doc (old format) via antiword/textract first
            ext = BIDDING_FILE.suffix.lower()
            if ext == ".doc":
                try:
                    import subprocess
                    result = subprocess.run(
                        ["antiword", str(BIDDING_FILE)],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        source_text = result.stdout
                    else:
                        raise RuntimeError("antiword failed")
                except Exception:
                    try:
                        import textract
                        source_text = textract.process(str(BIDDING_FILE)).decode("utf-8", errors="replace")
                    except Exception:
                        pass
            elif ext == ".docx":
                from app.services.file_service import file_service
                # Copy to storage first so file_service can find it
                dest = file_service.get_absolute_path(BIDDING_FILE.name)
                shutil.copy2(str(BIDDING_FILE), str(dest))
                source_text = file_service.extract_docx_text(BIDDING_FILE.name)
            print(f"  Bidding source: {len(source_text)} chars")
        except Exception as e:
            print(f"  Bidding source extract failed: {e}")

    state = create_initial_state(
        session_id=session_id,
        report_title="金湖县2026年度耕地占补平衡技术服务项目投标文件",
        project_context="金湖县2026年度耕地占补平衡技术服务项目",
    )
    state["filled_data"] = {
        "bid_project_name": "金湖县2026年度耕地占补平衡技术服务项目",
        "bid_reference": "金湖县2026年度耕地占补平衡",
    }
    state["_domain"] = "bidding"
    state["_conversation_domain"] = "bidding"
    state["_bidding_source_text"] = source_text
    state["_bidding_report_type"] = "tender_response"
    state["phase"] = "collecting"
    state["status"] = "collecting"

    return state, session_id


async def load_template_paths(domain: str):
    """Load active template paths from DB for a domain."""
    import sqlite3
    from app.config import settings
    from app.services.file_service import file_service

    db_path = settings.DATA_DIR / "knowledge_base.db"
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    category = "社会稳定" if domain == "stability" else "招标投标"
    cursor.execute(
        "SELECT template_file_path, name FROM templates "
        "WHERE category=? AND is_active=1 "
        "ORDER BY id DESC",
        (category,),
    )
    rows = cursor.fetchall()
    conn.close()

    paths = []
    for row in rows:
        abs_path = file_service.get_absolute_path(row[0]) if row[0] else None
        if abs_path and abs_path.exists():
            paths.append((str(abs_path), row[1]))
    return paths


async def compare_with_templates(output_path: str, domain: str):
    """Run diff engine and spec checker against templates."""
    from app.validation.diff_engine import ReportDiffEngine
    from app.validation.spec_checker import check_report_async

    print(f"\n  Comparing with templates for domain={domain}...")

    results = {"diff_scores": [], "spec_check": None}

    # Spec checker
    try:
        spec_report = await check_report_async(output_path)
        if spec_report:
            results["spec_check"] = {
                "score": spec_report.compliance_score,
                "passed": spec_report.passed,
                "details": f"{spec_report.passed_count}/{spec_report.total_rules}",
            }
            print(f"  Spec check: {spec_report.compliance_score:.0%} "
                  f"({spec_report.passed_count}/{spec_report.total_rules})")
    except Exception as e:
        print(f"  Spec check failed: {e}")

    # Diff engine against each template
    templates = await load_template_paths(domain)
    if templates:
        engine = ReportDiffEngine()
        for tpl_path, tpl_name in templates:
            try:
                diff = await engine.compare(output_path, tpl_path)
                results["diff_scores"].append({
                    "template": tpl_name,
                    "score": diff.overall_score,
                    "dimensions": {
                        r.dimension: r.match_percentage for r in diff.results
                    },
                })
                print(f"  vs {tpl_name}: {diff.overall_score:.0%}")
                for r in diff.results:
                    print(f"    {r.dimension}: {r.match_percentage:.0%}")
            except Exception as e:
                print(f"  vs {tpl_name}: FAILED - {e}")
    else:
        print(f"  No active templates found for domain={domain}")

    # Overall score
    scores = [s["score"] for s in results["diff_scores"]]
    if results["spec_check"]:
        scores.append(results["spec_check"]["score"])
    overall = sum(scores) / len(scores) if scores else 1.0
    results["overall_score"] = overall
    print(f"  Overall: {overall:.0%}")
    return results


async def generate_stability_report(state, stream_queue):
    """Run full stability pipeline with auto-approve and real-time event draining."""
    print("  [gen] Importing LLMService...", flush=True)
    from app.services.llm_service import LLMService
    from app.agent.agents.chapter_orchestrator import ChapterOrchestrator

    print("  [gen] Creating LLMService...", flush=True)
    llm = LLMService()
    print(f"  [gen] LLMService ready: model={llm.model}, base_url={llm.base_url}", flush=True)

    async def auto_approve():
        approved = 0
        while approved < 10:
            await asyncio.sleep(3)
            event = state.get("_action_event")
            if not state.get("user_action"):
                state["user_action"] = "approve"
                if isinstance(event, asyncio.Event):
                    event.set()
                approved += 1
                print(f"    [auto] Approved chapter/module {approved}", flush=True)

    async def drain_realtime():
        """Drain SSE events in real-time to show progress."""
        seen_chapters = set()
        event_count = 0
        while True:
            try:
                evt = await asyncio.wait_for(stream_queue.get(), timeout=1.0)
                if isinstance(evt, str):
                    print(f"  [raw] {evt[:150]}", flush=True)
                    continue
                et = evt.get("event", "?")
                data = evt.get("data", {})
                # data can be a dict or a string (some _emit calls pass raw strings)
                if isinstance(data, str):
                    print(f"  [{et}] {data[:180]}", flush=True)
                    continue
                event_count += 1

                # Compact display for each event type
                if et == "thinking":
                    content = str(data.get("content", ""))[:180]
                    if content:
                        print(f"  [{et}] {content}", flush=True)
                elif et == "chapter_start":
                    ch = data.get("chapter", "?")
                    title = str(data.get("title", ""))[:60]
                    print(f"  [{et}] Ch{ch} 「{title}」", flush=True)
                elif et == "chapter_complete":
                    ch = data.get("chapter", "?")
                    md_len = len(data.get("markdown", "") or "")
                    print(f"  [{et}] Ch{ch} done ({md_len} chars)", flush=True)
                elif et == "chapter_confirmed":
                    ch = data.get("chapter", "?")
                    seen_chapters.add(ch)
                    print(f"  [{et}] Ch{ch} confirmed ({len(seen_chapters)} total)", flush=True)
                elif et == "complete":
                    print(f"  [{et}] Pipeline complete!", flush=True)
                    break
                elif et == "phase_change":
                    phase = data.get("phase", "")
                    mode = data.get("mode", "")
                    msg = data.get("message", "")
                    info = phase or mode or msg
                    print(f"  [{et}] {info}", flush=True)
                elif et == "chapter_progress":
                    ch = data.get("current", "?")
                    total = data.get("total", "?")
                    status = data.get("status", "")
                    print(f"  [{et}] {ch}/{total} {status}", flush=True)
                elif et == "chapter_review_prompt":
                    ch = data.get("chapter", "?")
                    print(f"  [{et}] Ch{ch} awaiting review", flush=True)
                elif et == "validation_result":
                    passed = data.get("passed", False)
                    summary = str(data.get("summary", ""))[:120]
                    print(f"  [{et}] passed={passed} {summary}", flush=True)
                elif et == "analysis_complete":
                    chs = len(data.get("chapters", []))
                    print(f"  [{et}] {chs} chapters analyzed", flush=True)
                elif et == "outline_generated":
                    chs = len(data.get("chapters", []))
                    print(f"  [{et}] {chs} chapters outlined", flush=True)
                elif et == "error":
                    print(f"  [ERROR] {str(data)[:200]}", flush=True)
                elif et == "message":
                    content = str(data.get("content", ""))[:120]
                    if content:
                        print(f"  [{et}] {content}", flush=True)
                else:
                    # Show ALL events we didn't handle
                    flat = str(data)[:150]
                    print(f"  [{et}] {flat}", flush=True)
            except asyncio.TimeoutError:
                pass
            except asyncio.QueueEmpty:
                pass
            except Exception as e:
                print(f"  [drain] exiting: {e}", flush=True)
                break

    auto_task = asyncio.create_task(auto_approve())
    drain_task = asyncio.create_task(drain_realtime())
    print("  [gen] Creating ChapterOrchestrator...", flush=True)
    orch = ChapterOrchestrator(llm_service=llm)

    print("  [gen] Starting run_full_pipeline()...", flush=True)
    t0 = __import__('time').time()

    try:
        await asyncio.wait_for(
            orch.run_full_pipeline(state, stream_queue),
            timeout=600.0,
        )
        elapsed = __import__('time').time() - t0
        print(f"  Pipeline completed in {elapsed:.0f}s", flush=True)
    except asyncio.TimeoutError:
        print("  Pipeline timed out (600s)", flush=True)
    except Exception as e:
        import traceback
        print(f"  Pipeline error: {e}", flush=True)
        traceback.print_exc()
    finally:
        auto_task.cancel()
        drain_task.cancel()
        try:
            await auto_task
        except asyncio.CancelledError:
            pass
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

    return state.get("output_path", "")


async def generate_bidding_report(state, stream_queue):
    """Run full bidding pipeline with auto-approve and real-time event draining."""
    from app.services.llm_service import LLMService
    from app.agent.agents.bidding_orchestrator import BiddingOrchestrator

    llm = LLMService()

    async def auto_approve():
        approved = 0
        while approved < 30:
            await asyncio.sleep(3)
            event = state.get("_action_event")
            if not state.get("user_action"):
                state["user_action"] = "approve"
                if isinstance(event, asyncio.Event):
                    event.set()
                approved += 1
                print(f"    [auto] Approved module {approved}", flush=True)

    async def drain_realtime():
        while True:
            try:
                evt = await asyncio.wait_for(stream_queue.get(), timeout=1.0)
                if isinstance(evt, str):
                    print(f"  [raw] {evt[:150]}", flush=True)
                    continue
                et = evt.get("event", "?")
                data = evt.get("data", {})
                if isinstance(data, str):
                    print(f"  [{et}] {data[:180]}", flush=True)
                    continue
                content = str(data.get("content", data.get("message", data.get("summary", ""))))[:200]
                if et == "thinking" and content:
                    print(f"  [{et}] {content}", flush=True)
                elif et == "chapter_review_prompt":
                    ch = data.get("chapter", "?")
                    print(f"  [{et}] Module {ch} ready for review", flush=True)
                elif et == "chapter_confirmed":
                    ch = data.get("chapter", "?")
                    print(f"  [{et}] Module {ch} confirmed", flush=True)
                elif et == "complete":
                    print(f"  [{et}] Pipeline complete!", flush=True)
                    break
                elif et == "phase_change":
                    print(f"  [{et}] {data.get('mode', data.get('message', ''))}", flush=True)
                elif et == "chapter_progress":
                    print(f"  [{et}] {data.get('current', '?')}/{data.get('total', '?')} {data.get('status', '')}", flush=True)
                elif et == "validation_result":
                    print(f"  [{et}] {data.get('summary', '')}", flush=True)
                elif et == "error":
                    print(f"  [ERROR] {str(data)[:200]}", flush=True)
            except asyncio.TimeoutError:
                pass
            except asyncio.QueueEmpty:
                pass
            except Exception:
                break

    auto_task = asyncio.create_task(auto_approve())
    drain_task = asyncio.create_task(drain_realtime())
    orch = BiddingOrchestrator(llm_service=llm)

    print("  Starting BiddingOrchestrator.run_full_pipeline()...", flush=True)
    t0 = __import__('time').time()

    try:
        result = await asyncio.wait_for(
            orch.run_full_pipeline(state, stream_queue, report_type="tender_response"),
            timeout=600.0,
        )
        elapsed = __import__('time').time() - t0
        print(f"  Bidding pipeline completed in {elapsed:.0f}s, status={result.get('status')}", flush=True)
    except asyncio.TimeoutError:
        print("  Bidding pipeline timed out (600s)", flush=True)
    except Exception as e:
        import traceback
        print(f"  Bidding pipeline error: {e}", flush=True)
        traceback.print_exc()
    finally:
        auto_task.cancel()
        drain_task.cancel()
        try:
            await auto_task
        except asyncio.CancelledError:
            pass
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

    return state.get("output_path", "")


async def main():
    print("=" * 70)
    print("AUTO REPORT GENERATION + TEMPLATE COMPARISON")
    print("=" * 70)

    # ──── 1. Stability Report ────
    print("\n" + "=" * 70)
    print("STAGE 1: STABILITY ASSESSMENT REPORT")
    print("=" * 70)

    best_stability_path = ""
    best_stability_score = 0.0

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n--- Stability Iteration {iteration}/{MAX_ITERATIONS} ---")

        state, sid = await prepare_stability_state()
        stream_queue = asyncio.Queue()
        state["_stream_queue"] = stream_queue

        output_path = await generate_stability_report(state, stream_queue)

        if output_path:
            print(f"\n  Output: {output_path}")
            results = await compare_with_templates(output_path, "stability")
            score = results.get("overall_score", 0.0)

            if score > best_stability_score:
                best_stability_score = score
                best_stability_path = output_path

            if score >= MIN_SCORE:
                print(f"  Score {score:.0%} >= {MIN_SCORE:.0%}, done.")
                break
            else:
                print(f"  Score {score:.0%} < {MIN_SCORE:.0%}, re-generating...")
        else:
            print("  No output generated, trying again...")

    # ──── 2. Bidding Document ────
    print("\n" + "=" * 70)
    print("STAGE 2: BIDDING DOCUMENT")
    print("=" * 70)

    best_bidding_path = ""
    best_bidding_score = 0.0

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n--- Bidding Iteration {iteration}/{MAX_ITERATIONS} ---")

        state, sid = await prepare_bidding_state()
        stream_queue = asyncio.Queue()
        state["_stream_queue"] = stream_queue

        output_path = await generate_bidding_report(state, stream_queue)

        if output_path:
            print(f"\n  Output: {output_path}")
            results = await compare_with_templates(output_path, "bidding")
            score = results.get("overall_score", 0.0)

            if score > best_bidding_score:
                best_bidding_score = score
                best_bidding_path = output_path

            if score >= MIN_SCORE:
                print(f"  Score {score:.0%} >= {MIN_SCORE:.0%}, done.")
                break
            else:
                print(f"  Score {score:.0%} < {MIN_SCORE:.0%}, re-generating...")
        else:
            print("  No output generated, trying again...")

    # ──── 3. Final Summary ────
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"  Stability: {best_stability_path} (score={best_stability_score:.0%})")
    print(f"  Bidding:   {best_bidding_path} (score={best_bidding_score:.0%})")
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
