#!/usr/bin/env python3
"""relearn_stability_template.py — 重新学习稳评模板（修复缺失章节）

修复后的大纲提取器能从 "7.1..." 等 Heading 2 推断缺失的 H1 章节。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_engine import DualKB, LLMClient, TemplateLearner, KB_DOMAIN_STABILITY

TEMPLATE = Path("/Users/mac/WorkBuddy/Claw/zhongtuo-report-dev/assets/金湖稳评报告（空）.docx")


async def main():
    db = DualKB()
    llm = LLMClient()
    learner = TemplateLearner(db, llm)

    print("重新学习稳评模板（含缺失章节推断）...")
    result = await learner.learn_from_docx(
        KB_DOMAIN_STABILITY, str(TEMPLATE),
        name="金湖稳评报告模板", doc_role="template",
        category="社会稳定风险评估",
    )
    print(f"✅ 完成: {len(result['outline'])} 章")
    for ch in result["outline"]:
        inferred = " [推断]" if ch.get("_inferred") else ""
        print(f"  第{ch['chapter_no']}章 {ch['title']}{inferred} ({len(ch.get('subsections',[]))} 子节)")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
