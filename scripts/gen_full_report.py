#!/usr/bin/env python3
"""整本生成：按十四章结构逐章生成 → 组装成完整报告 docx。

用法：
    cd backend
    python3 scripts/gen_full_report.py                 # 复用素材分析缓存
    python3 scripts/gen_full_report.py --refresh       # 重跑素材分析
    python3 scripts/gen_full_report.py --chapters 1,2  # 只跑指定章（调试用）

产物：
    storage/output/report_full.docx     完整报告
    storage/output/ch{N}.md             各章 markdown
"""

import argparse
import asyncio
import json
import os
import pickle
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))

STATE_CACHE = BACKEND / "storage" / "tmp" / "review_state.pkl"
OUT_DIR = BACKEND / "storage" / "output"


async def generate_all(state: dict, only: list = None):
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.llm_service import LLMService
    from app.services.report_workflow import _standard_outline
    from app.services.master_orchestrator import build_chapter_prompt
    from app.agent.chapter_definitions import ALL_CHAPTERS, get_chapter_kind, get_chapter_title

    llm = LLMService()
    outline = state.get("outline_list") or _standard_outline()
    state["outline_list"] = outline
    filled = state.get("filled_data") or {}
    state.setdefault("chapters", {})

    targets = [n for n in ALL_CHAPTERS if not only or n in only]
    t0 = time.time()
    for i, n in enumerate(targets, 1):
        kind = get_chapter_kind(n)
        if kind in ("table", "attachment"):
            print(f"\n[{i}/{len(targets)}] 第{n}章「{get_chapter_title(n)}」—— {kind}，由组装器渲染，跳过 LLM")
            continue

        print(f"\n[{i}/{len(targets)}] 第{n}章「{get_chapter_title(n)}」…")
        agent = get_chapter_agent(n, llm_service=llm)
        if agent is None:
            print(f"   ❌ 无 agent"); continue

        ch = next((c for c in outline if int(c.get("chapter_no", 0)) == n), None)
        if ch is None:
            print(f"   ❌ 大纲缺该章"); continue
        deps = list(ch.get("depend_on_data") or [])
        ch_data = {k: filled.get(k, "") for k in deps if filled.get(k)}
        ch_def = {"num": n, "title": ch.get("title", ""),
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
            # 🔴 project_context 供 RAG 检索用（此前缺这个键，检索词只剩章节模板，
            #    召回质量差）。带上项目名/位置/用途，让同类土地征收案卷排得上来。
            "project_context": " ".join(str(filled.get(k) or "") for k in
                                        ("project_name", "location", "land_use", "doc_reference")),
            "filled_data": filled, "_chapter_data": ch_data,
            "_domain": "stability", "_report_style": "jinhu",
            "current_chapter": n, "chapters": {},
            "_custom_prompt": prompt, "_use_custom_prompt": True,
            "_uploaded_files": state.get("_uploaded_files", []),
            "_img_catalog": state.get("_img_catalog", {}),
            "_extracted_tables": filled.get("_extracted_tables", []),
        }
        q: asyncio.Queue = asyncio.Queue()

        async def _drain():
            while True:
                ev = await q.get()
                if ev is None:
                    break
                try:
                    d = ev.get("data") or {}
                    msg = d.get("content") or d.get("message") or ""
                    if msg and ("生成完成" in msg or "失败" in msg or "⚠" in msg):
                        print(f"   {str(msg)[:140]}")
                except Exception:
                    pass

        task = asyncio.create_task(_drain())
        _t = time.time()
        try:
            # 🔴 第12章（评分体系+风险等级）要输出 16k token 的表格正文，
            #    8000 预算触发截断重试后 600s 根本不够 —— 会退化成骨架「本节待补充」，
            #    并把上一轮的好内容覆盖掉。给足时间。
            _timeout = 1800 if n == 12 else 900
            await asyncio.wait_for(agent.run(agent_state, q), timeout=_timeout)
        except asyncio.TimeoutError:
            print(f"   ⚠️ 超时（{_timeout}s），跳过")
        except Exception as e:
            print(f"   ❌ 失败: {e}")
        finally:
            await q.put(None)
            await task

        cd = (agent_state.get("chapters") or {}).get(n) or {}
        md = cd.get("markdown", "") if isinstance(cd, dict) else str(cd)
        _old = (state.get("chapters", {}).get(n) or {})
        _old_md = _old.get("markdown", "") if isinstance(_old, dict) else str(_old or "")

        # 🔴 别让「失败退化的骨架」覆盖上一轮的好内容：
        #    LLM 超时/重试耗尽时会回退成「（本节待补充：…）」占位骨架，
        #    长度也 >50 字，会一路写进报告。骨架 + 已有更长正文 → 保留旧的。
        _looks_skeleton = ("本节待补充" in md) or (md.count("【待补充】") >= 2)
        if _looks_skeleton and len(_old_md.strip()) > len(md.strip()) * 2:
            print(f"   ⚠️ 新产出疑为占位骨架（{len(md)}字），保留上一轮正文（{len(_old_md)}字）")
            continue

        if md and len(md.strip()) > 50:
            state["chapters"][n] = cd
            (OUT_DIR).mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"ch{n}.md").write_text(md, encoding="utf-8")
            print(f"   ✅ {len(md)} 字，{time.time()-_t:.0f}s")
        else:
            print(f"   ❌ 无有效产出")

    print(f"\n⏱ 全部完成，用时 {(time.time()-t0)/60:.1f} 分钟")
    return state


def assemble_full(state: dict) -> str:
    from app.services.report_assembler import ReportAssembler
    print("\n📚 组装完整报告…")
    path = ReportAssembler().assemble(state)
    # assemble() 返回的是「generated/xxx.docx」这类相对路径，真实落点是 storage/generated/
    _p = Path(path)
    if not _p.is_absolute():
        for _base in (BACKEND, BACKEND / "storage"):
            if (_base / _p).exists():
                _p = _base / _p
                break
        else:
            _p = BACKEND / "storage" / _p
    return str(_p)



# 🔴 会话状态通道统一走 `app.services.session_io`（界面与脚本共用一份事实源）。
#    以前这里内联了一份 HTTP 读写，其他脚本又各写各的 —— 同一个坑会反复踩。
from app.services.session_io import load_state as _load_state, put_state as _put_state, save_cache as _save_cache

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--chapters", default="", help="逗号分隔的章号，只跑这些章")
    ap.add_argument("--personnel", default="", help="逗号分隔的报告人员姓名（决定前置工作组名单与培训证书取图）")
    ap.add_argument("--cert-personnel", default="", help="逗号分隔的证书人员姓名（最多 3 人，决定公司资质证书区贴哪几张人员证书）")
    ap.add_argument("--session", default="", help="🔴 界面会话 id —— 以该会话的状态为准（用户在界面填的值才进得了报告）")
    ap.add_argument("--api", default="", help="后端地址，默认 http://localhost:8000")
    args = ap.parse_args()

    try:
        state = _load_state(args.session, args.api, STATE_CACHE)
    except Exception as e:
        print(f"❌ {e}")
        return
    only = [int(x) for x in args.chapters.split(",") if x.strip()] if args.chapters else None

    # 🔴 用户勾选的报告人员 → 前置「稳评工作组人员及分工」+ 培训证书取图
    if args.personnel.strip():
        _sel = [x.strip() for x in args.personnel.split(",") if x.strip()]
        state["selected_personnel"] = _sel
        state.setdefault("filled_data", {})["_selected_personnel"] = _sel
        print(f"👥 报告人员：{'、'.join(_sel)}")

    # 🔴 「选择证书」独立多选框（最多 3 人）→ 前置「公司资质证书」区的几张人员证书图
    if getattr(args, "cert_personnel", "").strip():
        _csel = [x.strip() for x in args.cert_personnel.split(",") if x.strip()][:3]
        state["selected_cert_personnel"] = _csel
        state.setdefault("filled_data", {})["_selected_cert_personnel"] = _csel
        print(f"📜 证书人员：{'、'.join(_csel)}")

    # 🔴 与 node_field_validate 同款的两步预处理，保证脚本产物与线上一致：
    #    ① 清洗伪值（「未检测到/未提及」类自述被当成字段值）并回填 project_name；
    #    ② 注入问卷数据（座谈会PDF解析缓存 → 人工核验数据集）
    try:
        from app.services.field_sanitizer import sanitize_fields
        _cleared = sanitize_fields(state.setdefault("filled_data", {}), verbose=True, state=state)
        if _cleared:
            print(f"🧹 字段清洗：剔除 {len(_cleared)} 个伪值 → {_cleared[:10]}")
    except Exception as e:
        print(f"⚠️ 字段清洗跳过: {e}")
    # 🔴 与 node_field_validate 一致：由「稳评公示开始日期」推算公示期（一周）
    try:
        from app.services.report_workflow import derive_publicity_period
        derive_publicity_period(state.setdefault("filled_data", {}))
        _pp = state["filled_data"].get("publicity_period_text")
        if _pp:
            print(f"📅 稳评公示期：{_pp}")
    except Exception as e:
        print(f"⚠️ 公示期推算跳过: {e}")

    # 🔴 勘测定界报告 → 提取表（第6章 `[SYS_TABLE:6-1]` / 第3章 表3-1、3-2 的来源）。
    #    界面路径在 `routers/report.py` 的上传分析分支里做同一件事，走的是**同一个函数**；
    #    脚本路径不经过那里，必须在这里补 —— 否则脚本出的报告里
    #    `[SYS_TABLE:6-1]` 渲染不出表，正文却印着「上表所列…」，自相引用一张不存在的表。
    try:
        from app.services.land_survey_parser import collect_land_survey
        _filled = state.setdefault("filled_data", {})
        _sur_tables, _sur = collect_land_survey(state)
        if _sur:
            _xt = list(_filled.get("_extracted_tables") or [])
            _have = {str(t.get("_rendered_caption") or t.get("kind") or "") for t in _xt}
            for _t in _sur_tables:
                if str(_t.get("_rendered_caption") or _t.get("kind") or "") not in _have:
                    _xt.append(_t)
            _filled["_extracted_tables"] = _xt
            # 🔴 面积以勘测定界为准（与 `routers/report.py` 的上传分析分支同一口径：
            #    勘测定界是实测，公告里的是概数）。用户确认过的（`_confirmed_`）不动。
            _m2 = _sur.get("area_m2")
            if _m2 and not _filled.get("_confirmed_area_m2"):
                _filled["area_m2"] = str(_m2)
                try:
                    _filled["area_mu"] = f"{round(float(_m2) / 666.67, 4):g}"
                except (TypeError, ValueError):
                    pass
            for _k in ("location", "land_use", "boundary_points"):
                _v = _sur.get(_k)
                if _v and str(_v).strip() and not str(_filled.get(_k, "")).strip():
                    _filled[_k] = str(_v)
            if _sur.get("owner_units"):
                _filled["_land_owners"] = _sur["owner_units"]
            if _sur.get("by_category"):
                _filled["_land_by_category"] = _sur["by_category"]
            if _sur.get("by_sub_category"):
                _filled["_land_by_sub_category"] = _sur["by_sub_category"]
            print(f"📐 勘测定界：{len(_sur_tables)} 张提取表 / 面积 {_sur.get('area_m2')} m²"
                  f" / 坐落 {_sur.get('location', '')[:30]}")
        else:
            print("⚠️ 未找到可解析的勘测定界报告 → 第6章的表6-1 将渲染不出来")
    except Exception as e:
        print(f"⚠️ 勘测定界解析跳过: {e}")

    try:
        from app.services.survey_data import merge_survey_into_state
        if merge_survey_into_state(state):
            _sa = (state.get("generated_sections") or {}).get("survey_analysis", {}) or {}
            print(f"📊 问卷数据：{_sa.get('total_samples', 0)} 份 / 支持率 {_sa.get('support_rate', 0)}%"
                  f"（来源：{(state.get('generated_sections') or {}).get('survey_data_source', '')}）")
    except Exception as e:
        print(f"⚠️ 问卷数据合并跳过: {e}")

    # 🔴 座谈会签到表 → `symposium_attendees`（第8章 4.座谈会要用）。
    #    与界面路径调**同一个**函数，按指纹缓存，只有首次要 ~15s 视觉识别。
    try:
        from app.services.signin_parser import ensure_symposium_attendees
        _sr = await ensure_symposium_attendees(state, llm_service=None)
        if _sr.get("names"):
            _k = "评审会" if _sr.get("_kind") == "review" else "座谈会"
            print(f"🖊 签到表识别：{_k} {len(_sr['names'])} 人 — {'、'.join(_sr['names'][:6])}")
    except Exception as e:
        print(f"⚠️ 签到表解析跳过: {e}")

    state = await generate_all(state, only)
    # 🔴 这份 pkl 已**降级为「离线兜底」**：只有在没传 `--session` 时才会被读到。
    #    写的时候打上来源标记，下次谁再用它，日志会明说「这份是从哪个会话导出的」，
    #    不会再出现「不知道 pkl 对应哪个界面会话」的情况。
    _save_cache(state, args.session, STATE_CACHE)

    path = assemble_full(state)
    print(f"\n✅ 完成 → {path}")

    # 🔴 写回界面会话 —— 界面与脚本共用一份状态（下次脚本/界面看到的是同一份）
    if args.session.strip():
        try:
            _put_state(args.session.strip(), state, args.api)
            print(f"💾 已写回会话 {args.session.strip()}")
        except Exception as e:
            print(f"⚠️ 写回会话失败（报告已生成，不影响文件）: {e}")
    else:
        # 离线模式：记下这份缓存是从哪来的，下次提示更明确
        state.setdefault("_source_session", "")
        print("⚠️ 离线模式：未写回任何界面会话")

    out = OUT_DIR / "report_full.docx"
    try:
        import shutil
        shutil.copy2(path, out)
        print(f"   副本 → {out}")
    except Exception as e:
        print(f"   副本失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())
