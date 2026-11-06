# -*- coding: utf-8 -*-
"""对知识库里的模板逐份做「人工批注式分析」。

产出：docs/template_analysis/<id>_<名称>.md + README.md（汇总表）

用法：
    python scripts/analyze_templates.py                 # 全部启用模板
    python scripts/analyze_templates.py --domain reclamation
    python scripts/analyze_templates.py --id 40
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DB = BACKEND / "data" / "knowledge_base.db"
STORAGE = BACKEND / "storage"
OUT = BACKEND / "docs" / "template_analysis"


def _slug(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(s or "")).strip("_")[:60] or "template"


def _templates(domain: str = "", tid: int = 0, include_inactive: bool = False):
    conn = sqlite3.connect(str(DB))
    q = ("select id,name,domain,region,template_file_path from templates "
         "where 1=1" if include_inactive else
         "select id,name,domain,region,template_file_path from templates where is_active=1")
    args = []
    if tid:
        q += " and id=?"
        args.append(tid)
    if domain:
        q += " and domain=?"
        args.append(domain)
    q += " order by domain, region, id"
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return rows


def _table_plan(path: Path, domain: str) -> dict:
    if path.suffix.lower() != ".docx":
        return {}
    out = {}
    try:
        from app.services.template_table_copy import chapter_table_plan
        for ch in range(1, 15):
            plan = chapter_table_plan(str(path), ch)
            if plan:
                out[ch] = plan
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--include-inactive", action="store_true",
                    help="连未启用的历史模板一起分析")
    args = ap.parse_args()

    from app.services.template_case_analysis import analyze_template_file, render_markdown

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _templates(args.domain, args.id, args.include_inactive)
    if not rows:
        print("没有匹配的启用模板")
        return 1

    index = ["# 模板批注式分析（自动生成）", "",
             "| id | 名称 | 域 | 地区 | 格式 | 章/节 | 固定/换数/重写 | 报告 |",
             "|---|---|---|---|---|---|---|---|"]
    done = 0
    for tid, name, domain, region, rel in rows:
        p = STORAGE / str(rel or "")
        if not p.exists():
            print(f"[跳过] #{tid} {name}：文件不存在 {p}")
            continue
        try:
            a = analyze_template_file(p, domain=domain, name=name, region=region or "")
            md = render_markdown(a, table_plan=_table_plan(p, domain))
        except Exception as e:
            print(f"[失败] #{tid} {name}：{e}")
            continue
        fn = f"{tid}_{_slug(name)}.md"
        (out_dir / fn).write_text(md, encoding="utf-8")
        c = a.get("counts") or {}
        index.append(f"| {tid} | {name} | {domain} | {region or '-'} | {a['format']} | "
                     f"{a.get('chapters', 0)}/{len(a.get('sections') or [])} | "
                     f"{c.get('fixed', 0)}/{c.get('slot', 0)}/{c.get('create', 0)} | "
                     f"[{fn}]({fn}) |")
        print(f"[OK] #{tid} {name} → {out_dir / fn}")
        done += 1

    (out_dir / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"\n完成 {done} 份；汇总：{out_dir / 'README.md'}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
