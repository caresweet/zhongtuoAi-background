# -*- coding: utf-8 -*-
"""从《稳评数据资料表》Excel 生成一份稳评测试报告（线上/容器内可用）。

用法（容器内）：
    python -m scripts.gen_stability_from_intake \
        --intake storage/templates/测试_稳评数据资料表.xlsx \
        --session test_stab_intake --company-id 1
然后：
    python scripts/gen_full_report.py

本脚本只负责把 Excel 读成 `filled_data` + 公司档案，并写成
`storage/tmp/review_state.pkl`；章节生成与组装由 `gen_full_report.py` 完成。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
os.chdir(str(BACKEND))
sys.path.insert(0, str(BACKEND))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", required=True, help="《稳评数据资料表》xlsx")
    ap.add_argument("--session", default="test_stab_intake")
    ap.add_argument("--company-id", type=int, default=1)
    ap.add_argument("--cache", default="storage/tmp/review_state.pkl")
    args = ap.parse_args()

    from app.services.stability_intake import apply_intake, read_intake_workbook
    from app.services.company_service import load_company
    from app.services.session_io import save_cache

    data = read_intake_workbook(args.intake)
    if data is None:
        print("无法识别《稳评数据资料表》：", args.intake)
        return 1
    filled = {}
    changed = apply_intake(filled, data)
    print(f"[稳评资料] {Path(args.intake).name}：写入 {len(changed)} 项")

    company = await load_company(args.company_id) or {}
    if company.get("implement_unit") and not filled.get("implement_unit"):
        filled["implement_unit"] = company["implement_unit"]
    # 评审表「实施单位负责人/职务」优先取公司档案（法人），别在交付稿里印【待填写】
    if company.get("leader_name") and not filled.get("impl_leader"):
        filled["impl_leader"] = company["leader_name"]
        filled["impl_leader_title"] = company.get("leader_title") or ""
    title = str(filled.get("project_name") or "社会稳定风险评估报告")
    state = {
        "session_id": args.session,
        "_domain": "stability",
        "_conversation_domain": "stability",
        "report_title": title,
        "filled_data": filled,
        "_company_id": args.company_id,
        "_company": company,
        "_project_materials": [],
        "_uploaded_files": [],
        "_report_style": "jinhu",
        "chapters": {},
    }
    save_cache(state, "", BACKEND / args.cache)
    print(f"[状态] 已写 {args.cache}；项目：{title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
