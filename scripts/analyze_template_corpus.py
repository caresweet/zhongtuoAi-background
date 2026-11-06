# -*- coding: utf-8 -*-
"""把所有模板/范文（docx + PDF）分析成结构画像。

用法：
    python scripts/analyze_template_corpus.py            # 全部
    python scripts/analyze_template_corpus.py --domain reclamation
    python scripts/analyze_template_corpus.py --id 35
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DB = BACKEND / "data" / "knowledge_base.db"
OUT = BACKEND / "data" / "template_profiles" / "_corpus_index.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--active", action="store_true",
                    help="只分析启用中的模板（默认分析全部，含停用的范文）")
    args = ap.parse_args()

    from app.services.template_corpus_profile import (
        analyze_template_file, save_corpus_profile)

    c = sqlite3.connect(str(DB))
    sql = ("select id,name,domain,region,category,template_file_path "
           "from templates where 1=1")
    if args.active:
        sql += " and is_active=1"
    params: tuple = ()
    if args.domain:
        sql += " and domain=?"
        params = (args.domain,)
    if args.id:
        sql += " and id=?"
        params = (args.id,)
    rows = c.execute(sql + " order by domain,id", params).fetchall()

    index = []
    for tid, name, domain, region, category, rel in rows:
        if not rel:
            continue
        path = BACKEND / "storage" / rel
        if not path.exists():
            print(f"[缺失] {tid} {name} → {rel}")
            continue
        prof = analyze_template_file(path, domain=domain or "stability")
        if not prof:
            print(f"[跳过] {tid} {name}（{path.suffix}）")
            continue
        prof["template_id"] = tid
        prof["name"] = name
        prof["region"] = region or ""
        prof["category"] = category or ""
        out = save_corpus_profile(prof)
        typo = prof.get("typography") or {}
        body = (typo.get("body") or {}).get("size")
        h1 = (typo.get("h1") or {}).get("size")
        cnt = prof.get("counts") or {}
        index.append({
            "id": tid, "name": name, "domain": domain, "region": region or "",
            "format": prof.get("format"), "chapters": prof.get("chapters"),
            "sections": prof.get("sections"), "counts": cnt,
            "body_size": body, "h1_size": h1, "profile": str(out.name),
        })
        print(f"{tid:>3} {prof.get('format'):<4} {name[:26]:<28} "
              f"章{prof.get('chapters'):>2} 节{prof.get('sections'):>3} "
              f"固定{cnt.get('fixed',0):>2}/换数{cnt.get('slot',0):>2}/创作{cnt.get('create',0):>2} "
              f"正文{body or '-'}/一级{h1 or '-'}")
        _oc = prof.get("outline_check") or {}
        if _oc and not _oc.get("ok"):
            print(f"      与标准大纲的差异（仅供参考/兜底）：缺 {_oc.get('missing') or '无'}"
                  f"；多 {_oc.get('extra') or '无'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共 {len(index)} 份；索引 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
