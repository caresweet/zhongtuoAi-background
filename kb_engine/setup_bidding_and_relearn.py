#!/usr/bin/env python3
"""setup_bidding_and_relearn.py — 补全招标库 + 重学稳评模板

1. 重学稳评模板（修复后能推断缺失的第6/7章）
2. 学习投标文件模板
3. 迁移旧库 company_assets 到招标库
"""

import asyncio
import os
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_engine import DualKB, LLMClient, TemplateLearner, KB_DOMAIN_STABILITY, KB_DOMAIN_BIDDING

BACKEND_DIR = Path(__file__).resolve().parent.parent
STABILITY_TEMPLATE = Path("/Users/mac/WorkBuddy/Claw/zhongtuo-report-dev/assets/金湖稳评报告（空）.docx")
BIDDING_TEMPLATE = BACKEND_DIR / "storage/templates/18f2827d62fe40e48cc71b58547db187.docx"
OLD_KB_DB = BACKEND_DIR / "data/knowledge_base.db"


async def main():
    db = DualKB()
    llm = LLMClient()
    learner = TemplateLearner(db, llm)

    print("=" * 60)
    print("  补全初始化：重学稳评模板 + 招标库")
    print("=" * 60)

    # ── 1. 重学稳评模板 ────────────────────────────────────────
    print("\n【1. 重学稳评模板（含缺失章节推断）】")
    if STABILITY_TEMPLATE.exists():
        result = await learner.learn_from_docx(
            KB_DOMAIN_STABILITY, str(STABILITY_TEMPLATE),
            name="金湖稳评报告模板", doc_role="template",
            category="社会稳定风险评估",
        )
        print(f"  ✅ 完成: {len(result['outline'])} 章")
        for ch in result["outline"]:
            inferred = " [推断]" if ch.get("_inferred") else ""
            print(f"     第{ch['chapter_no']}章 {ch['title'][:30]}{inferred} ({len(ch.get('subsections',[]))} 子节)")
    else:
        print(f"  ⚠️ 模板不存在: {STABILITY_TEMPLATE}")

    # ── 2. 学习投标文件模板 ────────────────────────────────────
    print("\n【2. 学习投标文件模板】")
    if BIDDING_TEMPLATE.exists():
        print(f"  文件: {BIDDING_TEMPLATE.name} ({BIDDING_TEMPLATE.stat().st_size // 1024 // 1024}MB)")
        result = await learner.learn_from_docx(
            KB_DOMAIN_BIDDING, str(BIDDING_TEMPLATE),
            name="投标文件模板（洪泽区经营性地块）", doc_role="example",
            category="招标投标",
        )
        print(f"  ✅ 完成: {len(result['outline'])} 章")
        for ch in result["outline"]:
            print(f"     第{ch['chapter_no']}章 {ch['title'][:40]} ({len(ch.get('subsections',[]))} 子节)")
    else:
        print(f"  ⚠️ 模板不存在: {BIDDING_TEMPLATE}")

    # ── 3. 迁移旧库 company_assets ─────────────────────────────
    print("\n【3. 迁移旧库 company_assets】")
    if OLD_KB_DB.exists():
        old_con = sqlite3.connect(str(OLD_KB_DB))
        old_con.row_factory = sqlite3.Row
        rows = old_con.execute("SELECT * FROM company_assets WHERE is_active=1").fetchall()
        migrated = 0
        for r in rows:
            db.add_fixed_asset(
                KB_DOMAIN_BIDDING, r["asset_type"], r["title"],
                company=r["company"], file_path=r.get("source_file", ""),
                extracted_text=r["content"],
            )
            migrated += 1
        old_con.close()
        print(f"  ✅ 迁移 {migrated} 项固定资料")
    else:
        print(f"  ⚠️ 旧库不存在: {OLD_KB_DB}")

    # ── 汇总 ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  最终汇总")
    print("=" * 60)
    for domain, label in [(KB_DOMAIN_STABILITY, "稳评库"), (KB_DOMAIN_BIDDING, "招标库")]:
        templates = db.get_templates(domain)
        chapters = db.get_learned_chapters(domain)
        assets = db.get_fixed_assets(domain)
        print(f"\n  {label} ({domain}_kb.db):")
        print(f"    模板: {len(templates)} 个")
        print(f"    已学章节: {len(chapters)} 章")
        print(f"    固定资料: {len(assets)} 项")
        if assets:
            types = {}
            for a in assets:
                types[a["asset_type"]] = types.get(a["asset_type"], 0) + 1
            print(f"    资料类型: {types}")

    db.close()
    print("\n✅ 补全初始化完成！")


if __name__ == "__main__":
    asyncio.run(main())
