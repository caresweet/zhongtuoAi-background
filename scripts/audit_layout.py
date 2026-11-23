# -*- coding: utf-8 -*-
"""命令行版式审核：python scripts/audit_layout.py 文件 [--json] [--out out.json]

支持 DOCX / PDF / 图片；输出区块结构、章节树、异常清单。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))


async def main() -> int:
    ap = argparse.ArgumentParser(description="文档版式审核")
    ap.add_argument("files", nargs="+", help="DOCX / PDF / 图片路径")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--out", default="", help="JSON 写入文件")
    args = ap.parse_args()

    from app.services.layout_audit import audit_document, render_text_summary
    results = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"❌ 文件不存在：{f}")
            continue
        print(f"\n===== {p.name} =====")
        res = await audit_document(p, original_name=p.name)
        results.append(res)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(render_text_summary(res))

    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nJSON 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
