#!/usr/bin/env python3
"""setup_kb.py — 初始化双类型知识库

1. 稳评库：学习金湖稳评报告模板 + 注册固定资料（营业执照/人员证件/资质）
2. 招标库：学习投标文件模板 + 迁移旧库 company_assets
"""

import asyncio
import os
import sys
import sqlite3
from pathlib import Path

# 确保用项目 venv — 需要 backend 目录在 path 中才能 import kb_engine
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_engine import DualKB, LLMClient, TemplateLearner, KB_DOMAIN_STABILITY, KB_DOMAIN_BIDDING

BACKEND_DIR = Path(__file__).resolve().parent.parent
STABILITY_TEMPLATE = Path("/Users/mac/WorkBuddy/Claw/zhongtuo-report-dev/assets/金湖稳评报告（空）.docx")
BIDDING_TEMPLATE = BACKEND_DIR / "storage/templates/18f2827d62fe40e48cc71b58547db187.docx"
OLD_KB_DB = BACKEND_DIR / "data/knowledge_base.db"

# 稳评固定资料
STABILITY_ASSETS = [
    {"asset_type": "营业执照", "title": "江苏众拓项目代理咨询有限公司营业执照",
     "company": "江苏众拓项目代理咨询有限公司",
     "file_path": "/Users/mac/Downloads/资料/营业执照.jpg"},
    {"asset_type": "资质证书", "title": "工程咨询资质证书",
     "company": "江苏众拓项目代理咨询有限公司",
     "file_path": "/Users/mac/Downloads/资料/江苏众拓项目代理咨询有限公司相关记录.jpg"},
]

# 稳评培训证书目录
CERT_DIRS = [
    "/Users/mac/Downloads/资料/2025年11月14日--6人稳评培训证书",
    "/Users/mac/Downloads/资料/2024.12.26稳评培训证书",
]


async def main():
    db = DualKB()
    llm = LLMClient()
    learner = TemplateLearner(db, llm)

    print("=" * 60)
    print("  双类型知识库初始化")
    print("=" * 60)

    # ── 1. 稳评库 ──────────────────────────────────────────────
    print("\n【稳评库 stability_kb.db】")

    # 学习模板
    if STABILITY_TEMPLATE.exists():
        print(f"  学习模板: {STABILITY_TEMPLATE.name} ...")
        result = await learner.learn_from_docx(
            KB_DOMAIN_STABILITY, str(STABILITY_TEMPLATE),
            name="金湖稳评报告模板", doc_role="template",
            category="社会稳定风险评估",
        )
        print(f"  ✅ 模板学习完成: {len(result['outline'])} 章")
        for ch in result["outline"][:5]:
            print(f"     第{ch['chapter_no']}章 {ch['title']} ({len(ch.get('subsections',[]))} 子节)")
        if len(result["outline"]) > 5:
            print(f"     ... 共 {len(result['outline'])} 章")
    else:
        print(f"  ⚠️ 模板文件不存在: {STABILITY_TEMPLATE}")

    # 注册固定资料
    print("  注册固定资料...")
    for asset in STABILITY_ASSETS:
        text = ""
        fp = asset.get("file_path", "")
        if fp and os.path.exists(fp):
            # 图片用 OCR 提取
            if fp.lower().endswith((".jpg", ".jpeg", ".png")):
                import base64
                img_b64 = base64.b64encode(open(fp, "rb").read()).decode()
                try:
                    text = await llm.vision(
                        "请识别图片中的全部文字信息（企业名称、统一社会信用代码、经营范围、有效期等），按原文输出。",
                        img_b64, mime_type="image/jpeg", max_tokens=2000,
                    )
                except Exception as e:
                    text = f"[OCR失败: {e}]"
        db.add_fixed_asset(
            KB_DOMAIN_STABILITY, asset["asset_type"], asset["title"],
            company=asset["company"], file_path=fp, extracted_text=text,
        )
        print(f"  ✅ {asset['asset_type']}: {asset['title']} ({len(text)} 字)")

    # 注册人员证件
    cert_count = 0
    for cert_dir in CERT_DIRS:
        if not os.path.isdir(cert_dir):
            continue
        for fname in os.listdir(cert_dir):
            if fname.lower().endswith((".jpg", ".jpeg", ".png")):
                fp = os.path.join(cert_dir, fname)
                import base64
                img_b64 = base64.b64encode(open(fp, "rb").read()).decode()
                try:
                    text = await llm.vision(
                        "请识别证书中的全部文字（姓名、证书编号、培训机构、有效期等），按原文输出。",
                        img_b64, mime_type="image/jpeg", max_tokens=1500,
                    )
                except Exception:
                    text = ""
                db.add_fixed_asset(
                    KB_DOMAIN_STABILITY, "人员证件", fname,
                    company="江苏众拓项目代理咨询有限公司",
                    file_path=fp, extracted_text=text,
                )
                cert_count += 1
    print(f"  ✅ 人员证件: {cert_count} 份")

    # ── 2. 招标库 ──────────────────────────────────────────────
    print("\n【招标库 bidding_kb.db】")

    # 学习投标文件模板
    if BIDDING_TEMPLATE.exists():
        print(f"  学习模板: 投标文件 (洪泽区2026年经营性地块) ...")
        result = await learner.learn_from_docx(
            KB_DOMAIN_BIDDING, str(BIDDING_TEMPLATE),
            name="投标文件模板（洪泽区经营性地块）", doc_role="example",
            category="招标投标",
        )
        print(f"  ✅ 模板学习完成: {len(result['outline'])} 章")
        for ch in result["outline"][:5]:
            print(f"     第{ch['chapter_no']}章 {ch['title'][:40]}")
        if len(result["outline"]) > 5:
            print(f"     ... 共 {len(result['outline'])} 章")
    else:
        print(f"  ⚠️ 模板文件不存在: {BIDDING_TEMPLATE}")

    # 迁移旧库 company_assets
    if OLD_KB_DB.exists():
        print("  迁移旧库 company_assets ...")
        old_con = sqlite3.connect(str(OLD_KB_DB))
        old_con.row_factory = sqlite3.Row
        rows = old_con.execute("SELECT * FROM company_assets WHERE is_active=1").fetchall()
        migrated = 0
        for r in rows:
            d = dict(r)
            db.add_fixed_asset(
                KB_DOMAIN_BIDDING, d["asset_type"], d["title"],
                company=d.get("company", ""), file_path=d.get("source_file", ""),
                extracted_text=d.get("content", ""),
            )
            migrated += 1
        old_con.close()
        print(f"  ✅ 迁移 {migrated} 项固定资料")
    else:
        print("  ⚠️ 旧库不存在，跳过迁移")

    # ── 汇总 ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  初始化完成汇总")
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
    print("\n✅ 双类型知识库初始化完成！")


if __name__ == "__main__":
    asyncio.run(main())
