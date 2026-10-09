#!/usr/bin/env python3
"""逐章生成（评审用）。

素材分析只跑一次（结果缓存），之后可以反复按章号单独生成某一章，
方便「生成一章 → 提意见 → 改 → 再看」的循环。

用法：
    cd backend
    python3 scripts/gen_one_chapter.py --materials ~/Downloads/稳评资料 --chapter 1
    python3 scripts/gen_one_chapter.py --chapter 1              # 复用上次缓存
    python3 scripts/gen_one_chapter.py --materials ... --refresh # 强制重跑素材分析
    python3 scripts/gen_one_chapter.py --chapter 1 --no-docx     # 只出 markdown

产物：
    storage/output/ch{N}.md    单章 markdown
    storage/output/ch{N}.docx  单章 docx（按最终版式渲染，便于看版式）
    storage/tmp/review_state.pkl   素材分析缓存
"""

import argparse
import asyncio
import json
import os
import pickle
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))

STATE_CACHE = BACKEND / "storage" / "tmp" / "review_state.pkl"
OUT_DIR = BACKEND / "storage" / "output"

_IMG_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')


def _collect_materials(root: Path):
    """收集素材目录下的全部文件，带上「文件夹_文件名」的原始名（图片归属靠它）。"""
    items = []
    root = root.expanduser().resolve()
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if f.startswith('.'):
                continue
            full = Path(dp) / f
            rel = os.path.relpath(str(full), str(root)).replace('\\', '/')
            parts = [x for x in rel.split('/') if x not in ('.', '..')]
            orig = "_".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
            items.append({"path": str(full), "original_name": orig,
                          "relative_path": rel, "size": full.stat().st_size})
    return items


async def build_state(materials: Path):
    """跑一次素材分析，返回可复用的 state。"""
    from app.routers.report import _analyze_session_attachments
    from app.services.image_catalog import build_image_catalog

    files = _collect_materials(materials)
    paths = [f["path"] for f in files]
    print(f"📂 素材文件 {len(files)} 个，开始分析（OCR/视觉/表格，首次较慢）...")

    state = {
        "session_id": "cli-review",
        "_domain": "stability",
        "filled_data": {},
        "_uploaded_files": files,
        "chapters": {},
        "logs": [],
        "phase": "validating",
    }
    await _analyze_session_attachments(state, paths)

    # 图片目录（章节归属）
    try:
        state["_img_catalog"] = build_image_catalog(
            files, ai_classifications=state.get("_classified_images"),
            domain=state.get("_domain", "stability"))
        _bc = state["_img_catalog"].get("by_chapter", {})
        print("🖼️ 图片归属：" + (", ".join(f"第{k}章 {len(v)} 张" for k, v in sorted(_bc.items())) or "无"))
    except Exception as e:
        print(f"⚠️ 图片目录构建失败：{e}")

    fd = state.get("filled_data") or {}
    keys = [k for k in fd if not k.startswith("_")]
    print(f"📊 事实卡字段 {len(keys)} 个：" + ", ".join(keys[:20]) + ("..." if len(keys) > 20 else ""))
    print(f"   责任单位 org_name = {fd.get('org_name')!r}")
    print(f"   文号 doc_reference = {fd.get('doc_reference')!r}")
    return state


def _jsonable(o):
    try:
        json.dumps(o, ensure_ascii=False)
        return True
    except Exception:
        return False


def _strip_unserializable(state: dict) -> dict:
    out = {}
    for k, v in state.items():
        if _jsonable(v):
            out[k] = v
        else:
            out[k] = f"<{type(v).__name__}>"
    return out


def render_chapter_docx(state: dict, n: int, out_path: Path) -> bool:
    """只把第 n 章渲染成一份独立 docx（含本章图片），供逐章评审。

    不走 `assemble()`——那会带上封面/评审表/目录；这里只要这一章的版式效果。
    """
    from docx import Document
    from app.services.report_assembler import ReportAssembler

    ch = (state.get("chapters") or {}).get(n) or {}
    md = ch.get("markdown", "") if isinstance(ch, dict) else str(ch)
    if not md:
        return False

    a = ReportAssembler()
    # 组装器内部状态（正常由 assemble() 初始化）
    a._inserted_images = set()
    a._chapter_image_counter = {}
    a._session_state = state
    a._system_rendered_tables = set()
    a._rendered_extracted_tbls = set()
    a._image_placements = {}
    a._current_chapter = n
    a._unmatched_image_markers = []
    a._chapter_image_map = (state.get("_img_catalog") or {}).get("by_chapter", {}) or {}
    a._img_catalog_data = state.get("_img_catalog") or {}

    doc = Document()
    a._setup_page(doc)
    try:
        survey_stats = a._get_survey_stats(state)
    except Exception:
        survey_stats = {}
    try:
        extracted = (a._get_extracted_tables(state) or {}).get(n, [])
    except Exception:
        extracted = []
    image_files = {}
    for img in (state.get("_img_catalog") or {}).get("catalog", []):
        image_files.setdefault(img.get("category", "other"), []).append(img.get("path"))

    a._add_chapter(doc, n, md, image_files, survey_stats, extracted,
                   state.get("filled_data") or {})
    # 🔴 全篇表格补实线边框（杜绝 Word 的虚线网格）
    a.ensure_all_tables_bordered(doc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return True


async def gen_chapter(state: dict, n: int, want_docx: bool = True):
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.llm_service import LLMService

    # 🔴 必须传 llm_service：不传的话 agent 内部 `self._llm` 为空，
    #    会直接走 `_fallback_content()` 输出「（本节待补充…）」骨架，根本不调 LLM。
    agent = get_chapter_agent(n, llm_service=LLMService())
    if agent is None:
        print(f"❌ 没有第 {n} 章的 agent（章号应为 1-11）")
        return

    # 🔴 走**生产同一条**提示词链路（build_chapter_prompt + _custom_prompt），
    #    否则单章产物与正式生成的写法不一致，评审就白评了。
    from app.services.report_workflow import _standard_outline
    from app.services.master_orchestrator import build_chapter_prompt
    outline = state.get("outline_list") or _standard_outline()
    state["outline_list"] = outline
    ch = next((c for c in outline if int(c.get("chapter_no", 0)) == n), None)
    if ch is None:
        print(f"❌ 大纲里没有第 {n} 章")
        return
    filled = state.get("filled_data") or {}
    deps = list(ch.get("depend_on_data") or [])
    ch_data = {k: filled.get(k, "") for k in deps if filled.get(k)}
    ch_def = {"num": n, "title": ch.get("title", f"第{n}章"),
              "key_points": ch.get("need_spec_tags", []),
              "data_needed": deps, "depends_on": ch.get("depends_on", []),
              "argument_note": ch.get("argument_note", "")}
    prompt = build_chapter_prompt(ch_def, filled,
                                  previous_chapters=state.get("chapters", {}),
                                  outline_context=state.get("_outline", {}),
                                      state=state)
    agent_state = {
        "session_id": state.get("session_id", ""),
        "report_title": filled.get("project_name", ""),
        # 🔴 事实卡给**完整**的（与生产一致）；ch_data 只作「本章可用数据」
        "filled_data": filled, "_chapter_data": ch_data,
        "_domain": "stability", "_report_style": "jinhu",
        "current_chapter": n, "chapters": {},
        "_custom_prompt": prompt, "_use_custom_prompt": True,
        # 供 agent 取模板/图片/表格用
        "_uploaded_files": state.get("_uploaded_files", []),
        "_img_catalog": state.get("_img_catalog", {}),
        "_extracted_tables": filled.get("_extracted_tables", []),
    }
    # 🔴 生成前先报出「本章缺哪些必填数据」——生产流程会弹窗问用户，CLI 这里打印出来
    try:
        from app.services.report_workflow import _missing_for, CHAPTER_REQUIRED_FIELDS
        _need = [(f, n) for f in CHAPTER_REQUIRED_FIELDS.get(n, [])]
        _miss = _missing_for(filled, _need) if _need else []
        if _miss:
            print("\n⚠️  本章缺少以下必填数据（生产流程会弹窗问用户，这里建议先补进事实卡）：")
            for m in _miss:
                print(f"     · {m['label']}（key={m['key']}，用于 {m.get('used_in','')}）")
    except Exception as _e:
        pass

    print(f"\n✍️  正在生成第 {n} 章（提示词 {len(prompt)} 字，本章依赖数据 {len(ch_data)} 项）…")
    queue: asyncio.Queue = asyncio.Queue()

    async def _drain():
        while True:
            ev = await queue.get()
            if ev is None:
                break
            try:
                d = ev.get("data") or {}
                msg = d.get("content") or d.get("message") or ""
                if msg:
                    print(f"   {msg[:160]}")
            except Exception:
                pass

    t = asyncio.create_task(_drain())
    try:
        await agent.run(agent_state, queue)
    finally:
        await queue.put(None)
        await t

    cd = (agent_state.get("chapters") or {}).get(n) or {}
    md = cd.get("markdown", "") if isinstance(cd, dict) else str(cd)
    if not md or len(md.strip()) < 50:
        print("❌ 本章没有产出有效内容")
        return
    # 回写主 state，供 docx 渲染复用（图片目录等）
    state.setdefault("chapters", {})[n] = cd
    ch = cd

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_md = OUT_DIR / f"ch{n}.md"
    out_md.write_text(md, encoding="utf-8")
    # 回写缓存，便于 --docx-only 直接重出 docx（不再烧一次 LLM）
    try:
        state["chapters"] = state.get("chapters") or {}
        state["chapters"][n] = ch
        STATE_CACHE.write_bytes(pickle.dumps(state))
    except Exception:
        pass
    print(f"\n✅ 第 {n} 章完成，{len(md)} 字 → {out_md}")

    if want_docx:
        try:
            docx_path = OUT_DIR / f"ch{n}.docx"
            if render_chapter_docx(state, n, docx_path):
                print(f"   docx → {docx_path}")
            else:
                print("   ⚠️ docx 渲染跳过（本章无内容）")
        except Exception as e:
            print(f"⚠️ docx 渲染失败（markdown 已产出）：{e}")

    print("\n" + "=" * 70)
    print(md)
    print("=" * 70)


async def main():
    ap = argparse.ArgumentParser(description="逐章生成（评审用）")
    ap.add_argument("--materials", help="素材目录（首次必填）")
    ap.add_argument("--chapter", type=int, required=True, help="章号 1-11")
    ap.add_argument("--session", default="", help="🔴 界面会话 id（界面上填的值才进得了报告）")
    ap.add_argument("--api", default="", help="后端地址")
    ap.add_argument("--refresh", action="store_true", help="强制重跑素材分析")
    ap.add_argument("--no-docx", action="store_true", help="只出 markdown")
    ap.add_argument("--docx-only", action="store_true",
                    help="不重新生成，直接把 storage/output/chN.md 渲染成 docx（保证与已评审的文本一致）")
    args = ap.parse_args()

    # 🔴 会话状态统一入口（见 `app/services/session_io.py`）：
    #    带 --session 以**界面会话**为准（界面上填的值才进得了报告），
    #    不带才回退离线 pkl 并大声警告。
    from app.services.session_io import load_state
    state = load_state(getattr(args, "session", ""), getattr(args, "api", ""), STATE_CACHE)

    if args.docx_only:
        md_file = OUT_DIR / f"ch{args.chapter}.md"
        if not md_file.exists():
            print(f"❌ 找不到 {md_file}，请先正常生成一次")
            return
        state.setdefault("chapters", {})[args.chapter] = {
            "markdown": md_file.read_text(encoding="utf-8"), "title": "", "status": "review"}
        out = OUT_DIR / f"ch{args.chapter}.docx"
        render_chapter_docx(state, args.chapter, out)
        print(f"✅ 已渲染 → {out}")
        return

    state = None
    if (args.session.strip() or STATE_CACHE.exists()) and not args.refresh:
        try:
            state = load_state(args.session, args.api, STATE_CACHE)
        except Exception as e:
            print(f"⚠️ 状态读取失败，将重跑：{e}")
    if state is None:
        if not args.materials:
            print("❌ 没有缓存，必须提供 --materials")
            return
        state = await build_state(Path(args.materials))
        STATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        STATE_CACHE.write_bytes(pickle.dumps(state))
        print(f"💾 素材分析已缓存 → {STATE_CACHE}")

    await gen_chapter(state, args.chapter, want_docx=not args.no_docx)


if __name__ == "__main__":
    asyncio.run(main())
