# -*- coding: utf-8 -*-
"""从《复垦基础资料》Excel（+ 可选材料文本）生成一份复垦测试报告。

模拟真实流程：Excel 是用户填的基础资料，材料文本是用户上传的可研/施工组织设计摘录
（设计参数只写在材料里，Excel 留空，由 `fix_reclamation_fields` 抽取）。

用法：
    python scripts/generate_reclamation_from_intake.py \
        --intake storage/templates/复垦基础资料_城东污水厂_测试.xlsx \
        --material storage/templates/城东污水厂_材料.txt \
        --out 测试_城东污水厂_复垦方案.docx
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", required=True)
    ap.add_argument("--material", default="")
    ap.add_argument("--out", default="测试_复垦方案.docx")
    ap.add_argument("--session", default="test_recl_from_intake")
    ap.add_argument("--land-summary", default="",
                    help="制式《土地分类面积汇总表》.xls/.xlsx（主体用地表）")
    ap.add_argument("--cost-excel", default="",
                    help="第七章投资估算 Excel（管道费用表）；不传则按资料名自动找")
    args = ap.parse_args()

    from app.agent.state import create_initial_state
    from app.agent.agents.chapters import get_chapter_agent
    from app.services.llm_service import LLMService
    from app.services.report_assembler import report_assembler
    from app.services.reclamation_quantities import (
        enrich_filled_data, QUANTITY_ROWS_KEY, BUDGET_KEY)
    from app.services.reclamation_intake import read_intake_workbook, apply_intake
    from app.services.reclamation_figures import ensure_reclamation_figures
    from app.services.field_fixer import fix_reclamation_fields
    from app.services.template_selector import resolve_template_path, build_match_text
    from app.config import settings

    data = read_intake_workbook(args.intake)
    if not data:
        print("基础资料无法识别:", args.intake)
        return 1
    fields = data["fields"]
    project_name = str(fields.get("project_name") or "复垦项目")
    title = f"{project_name}土地复垦方案"

    state = create_initial_state(
        session_id=args.session, report_title=title,
        project_context=title, domain="reclamation")
    state["_domain"] = "reclamation"
    state["_conversation_domain"] = "reclamation"
    state["report_title"] = title
    state["_report_style"] = "jinhu"
    state["phase"] = "generating"
    state["status"] = "generating"
    state["_skip_analysis"] = True

    filled = state["filled_data"] = {}
    filled["_company_id"] = 4
    # 编制单位名称/法人/资质/联系人都来自公司档案（报告表「方案编制单位」栏）
    try:
        from app.services.company_service import load_company
        state["_company"] = await load_company(4) or {}
    except Exception as _ce:
        print("[公司档案] 读取失败（报告表编制单位栏将留空）:", _ce, flush=True)
    changed = apply_intake(filled, data)
    print(f"[基础资料] {Path(args.intake).name}：写入 {len(changed)} 项", flush=True)

    if args.land_summary:
        from app.services.land_summary_excel import read_land_summary
        _sum = read_land_summary(args.land_summary)
        if _sum and _sum.get("rows"):
            _key = ("permanent_land_classification"
                    if _sum.get("kind") == "permanent" else "land_classification")
            if not filled.get(_key):
                filled[_key] = {"rows": _sum["rows"], "合计_m2": _sum["合计_m2"],
                                "source": _sum.get("source")}
                print(f"[地类汇总] {Path(args.land_summary).name} → {_key}："
                      f"{len(_sum['rows'])} 类", flush=True)

    materials = []
    if args.material and Path(args.material).exists():
        materials.append({
            "source_name": Path(args.material).name,
            "source_path": "",
            "text_content": Path(args.material).read_text(encoding="utf-8"),
        })
        # 材料优先补设计参数（Excel 留空的字段由材料填）
        fix_reclamation_fields(filled, materials, title=title)
        print("[设计参数]", {k: filled.get(k) for k in (
            "topsoil_thickness", "hard_surface_type", "hard_area_m2",
            "surface_thickness_cm", "cushion_thickness_cm", "service_years")},
            flush=True)

    state["template_path"] = await resolve_template_path(
        "reclamation",
        build_match_text(filled.get("location"), filled.get("project_name"),
                         state.get("project_context"))) or ""
    print("[模板]", state["template_path"], flush=True)

    figs = ensure_reclamation_figures(
        materials, str(settings.STORAGE_DIR), filled=filled)
    print("[图件]", [f.get("path") for f in figs], flush=True)
    state["_project_materials"] = materials
    state["_uploaded_files"] = list(figs)

    _cost_excel = args.cost_excel
    if not _cost_excel:
        from app.services.reclamation_cost_excel import resolve_cost_excel
        _cost_excel = resolve_cost_excel(filled, materials, str(settings.STORAGE_DIR))
    if _cost_excel:
        print("[投资估算Excel]", _cost_excel, flush=True)
    enrich_filled_data(filled, endpoint=os.getenv("RECLAMATION_BUDGET_ENDPOINT", ""),
                       cost_excel=_cost_excel)
    print("[工程量]", len(filled.get(QUANTITY_ROWS_KEY) or []), "行；",
          "投资估算" if filled.get(BUDGET_KEY) else "无投资估算", flush=True)
    print("[汇总]", filled.get("earthwork_volume"), flush=True)

    llm = LLMService()
    for n in range(1, 11):
        agent = get_chapter_agent(n, llm_service=llm, domain="reclamation")
        if agent is None:
            continue
        agent._stream_queue = None
        t0 = time.time()
        try:
            await agent.run(state)
            md = (state.get("chapters", {}).get(n) or {}).get("markdown", "")
            print(f"Ch{n}: {len(md)} 字 / {time.time() - t0:.0f}s", flush=True)
        except Exception as e:
            print(f"Ch{n} FAILED: {e}", flush=True)
            traceback.print_exc()
        await asyncio.sleep(1)

    out = report_assembler.assemble(state)
    src = settings.STORAGE_DIR / out
    fixed = settings.STORAGE_DIR / "generated" / args.out
    fixed.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != fixed.resolve():
        shutil.copyfile(src, fixed)
        try:
            src.unlink()
        except OSError:
            pass
    print("OUTPUT", fixed, fixed.exists(), flush=True)
    try:
        dl = Path.home() / "Downloads" / args.out
        shutil.copyfile(fixed, dl)
        print("DOWNLOAD", dl, flush=True)
    except Exception as e:
        print("[下载] 复制失败:", e, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
