# -*- coding: utf-8 -*-
"""生成投标（响应）文件：招标文件 + 公司档案 + 报价 → docx。

用法：
    python scripts/generate_bid.py \
        --tender "storage/templates/招标文件_丁蜀镇_测试.doc" \
        --company-id 4 --price 37 --out 测试_丁蜀镇_投标文件.docx
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tender", required=True)
    ap.add_argument("--company-id", type=int, default=4)
    ap.add_argument("--price", type=float, required=True, help="投标报价（万元）")
    ap.add_argument("--out", default="投标文件.docx")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true", help="服务方案用内置框架，不调 LLM")
    args = ap.parse_args()

    from app.services.bidding_bid_builder import build_bid_document
    from app.config import settings

    out = Path(settings.STORAGE_DIR) / "generated" / args.out
    p = await build_bid_document(
        tender_path=args.tender, company_id=args.company_id,
        price_wan=args.price, out_path=out, seed=args.seed,
        use_llm=not args.no_llm)
    print("OUTPUT", p)
    try:
        dl = Path.home() / "Downloads" / args.out
        shutil.copyfile(p, dl)
        print("DOWNLOAD", dl)
    except Exception as e:
        print("[下载] 复制失败:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
