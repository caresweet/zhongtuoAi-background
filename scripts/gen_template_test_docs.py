# -*- coding: utf-8 -*-
"""按「白马湖项目」那套流程，从地区模板直接出一份测试成稿。

流程（固定六步，换模板也照做）
------------------------------
1. 从 templates 表取该地区的模板 docx（domain=stability, is_active=1）；
2. analyze_template_file 逐节分析模板，得出哪些小节标注了「重写」；
3. build_from_template：复制模板，把模板里旧项目的事实换成测试项目事实；
4. merge_ai_sections：只把标注「重写」的小节换成已生成报告的 AI 正文，
   正文/一级小标题/二级小标题各按模板角色套格式（不按位置）；
5. make_test_doc：全套数据换成该地区虚构测试值，照片全部换成「图片占位」；
6. 自检：旧值残留、新值出现、章标题数量、页码域、Word 可打开。

测试数据来自 app/services/test_doc_makeup.py 的 TEST_DATA；新地区先在
那里补一条（项目名/单位/位置/文号/面积/户数/问卷数/得分），再跑本脚本。

用法
----
    python scripts/gen_template_test_docs.py                 # 全部已登记地区
    python scripts/gen_template_test_docs.py --region 淮安区 洪泽
    python scripts/gen_template_test_docs.py --region 淮安区 --merge ~/Downloads/xxx.docx
    python scripts/gen_template_test_docs.py --region 淮安区 --no-ai   # 只换数据不出 AI
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.template_case_analysis import analyze_template_file
from app.services.template_fill_report import build_from_template, merge_ai_sections
from app.services.test_doc_makeup import TEST_DATA, make_test_doc

DB = BACKEND / "data" / "knowledge_base.db"
STORAGE = BACKEND / "storage"
TMP = Path("/private/tmp/zhongtuo_template_test")


def _templates(regions):
    rows = sqlite3.connect(DB).execute(
        "select region, template_file_path from templates "
        "where domain='stability' and is_active=1 and region<>''").fetchall()
    out = {r: p for r, p in rows}
    if regions:
        missing = [r for r in regions if r not in out]
        if missing:
            raise SystemExit(f"模板库里没有这些地区：{missing}")
        return {r: out[r] for r in regions}
    return out


def _latest_generated(region: str) -> Path | None:
    found = sorted(Path.home().glob(f"Downloads/测试_{region}_稳评报告_*.docx"))
    return found[-1] if found else None


def build_one(region: str, rel: str, *, merge_doc: Path | None,
              with_ai: bool, ts: str, out_dir: Path) -> dict:
    d = TEST_DATA.get(region)
    if not d:
        raise SystemExit(
            f"{region} 还没有测试数据：先在 "
            f"app/services/test_doc_makeup.py 的 TEST_DATA 里补一条，再运行。")
    tpl = STORAGE / rel
    TMP.mkdir(parents=True, exist_ok=True)

    print(f"[{region}] ① 分析模板…", flush=True)
    analysis = analyze_template_file(tpl, domain="stability",
                                     name=region, region=region)
    filled = {
        "project_name": d["project"],
        "location": d["location"],
        "org_name": d["unit"],
        "decision_unit": d["unit"],
        "doc_reference": d["doc_ref"],
        "report_month": d.get("report_month", "2027-11"),
        "notice_date": d.get("notice_date", "2027-08-06"),
    }
    step = TMP / f"step_{region}.docx"
    print(f"[{region}] ② 复制模板并换事实…", flush=True)
    build_from_template(template_path=tpl, filled=filled, out_path=step)

    merged = step
    gen = merge_doc or _latest_generated(region)
    if with_ai:
        if gen and Path(gen).exists():
            print(f"[{region}] ③ 合并 AI 章节：{Path(gen).name}", flush=True)
            merged = TMP / f"merged_{region}.docx"
            merge_ai_sections(template_path=step, gen_path=gen,
                              analysis_sections=analysis.get("sections") or [],
                              out_path=merged)
        else:
            print(f"[{region}] ③ 跳过 AI 合并：没有已生成报告（可用 --merge 指定）",
                  flush=True)

    out = out_dir / f"测试_{region}_模板成稿_测试数据_{ts}.docx"
    print(f"[{region}] ④ 换测试数据 + 图片占位…", flush=True)
    stats = make_test_doc(region, merged, out)
    print(f"[{region}] OK {out} {stats}", flush=True)
    return {"region": region, "out": str(out), **stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", nargs="*", help="只跑这些地区（默认全部）")
    ap.add_argument("--merge", type=Path, help="指定用于合并的已生成报告 docx")
    ap.add_argument("--no-ai", action="store_true", help="不合并 AI 章节")
    ap.add_argument("--out-dir", type=Path, default=Path.home() / "Downloads")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    results = []
    for region, rel in _templates(args.region).items():
        results.append(build_one(
            region, rel, merge_doc=args.merge, with_ai=not args.no_ai,
            ts=ts, out_dir=args.out_dir))
    print("\n生成清单：")
    for r in results:
        print(f"  {r['region']}: {r['out']} "
              f"（改 {r['paragraphs_changed']} 段，占位图 {r['images_placeholder']} 张）")


if __name__ == "__main__":
    main()
