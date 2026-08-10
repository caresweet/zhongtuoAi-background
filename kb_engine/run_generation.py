#!/usr/bin/env python3
"""run_generation.py — 驱动顺序化报告生成

用法:
  python3 run_generation.py stability   # 生成稳评报告
  python3 run_generation.py bidding     # 生成投标文件

全程串行输出：think（思考）→ say（对话）→ progress（进度），一句一思。
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_engine import DualKB, LLMClient, SequentialEngine, KB_DOMAIN_STABILITY, KB_DOMAIN_BIDDING

# ── 稳评报告配置 ────────────────────────────────────────────────
STABILITY_CONFIG = {
    "domain": KB_DOMAIN_STABILITY,
    "project_name": "洪泽区2026年度经营性地块土地征收社会稳定风险评估",
    "requirement": (
        "根据洪拟征告〔2026〕7号征收土地预公告，对洪泽区朱坝街道办事处、"
        "三圩社区涉及的拟征收土地事项编制社会稳定风险评估报告。"
        "拟征收地块面积约326342平方米（约489.5亩），土地用途为商业服务业设施用地。"
        "需按照DB32/T4013-2021规范，开展风险调查、风险识别、风险估计、"
        "风险防范化解措施及风险等级评估等工作。"
    ),
    "materials": [
        "/Users/mac/Downloads/稳评资料/洪拟征告〔2026〕7号 征收土地预公告（盖章）.pdf",
        "/Users/mac/Downloads/稳评资料/0-勘测定界报告-.pdf",
    ],
}

# ── 投标文件配置 ────────────────────────────────────────────────
BIDDING_CONFIG = {
    "domain": KB_DOMAIN_BIDDING,
    "project_name": "金湖县2026年度耕地占补平衡技术服务项目",
    "requirement": (
        "根据金湖县自然资源和规划局委托的《金湖县2026年度耕地占补平衡技术服务项目》"
        "竞争性磋商文件（项目编号JSZC-320831-JSYM-C2026-0016，预算100万元），"
        "编制投标响应文件。供应商为江苏众拓测绘有限公司。"
        "响应文件须包含磋商函、资格证明文件、技术响应、商务报价等部分，"
        "严格按照招标文件第六章响应文件组成和格式要求编制。"
    ),
    "materials": [
        "/Users/mac/Downloads/金湖县2026年度耕地占补平衡技术服务项目.doc",
    ],
}


async def run(config: dict):
    """运行生成流程，实时打印事件。"""
    db = DualKB()
    llm = LLMClient()
    engine = SequentialEngine(db, llm)

    print("=" * 70)
    print(f"  报告生成：{config['project_name']}")
    print(f"  领域：{config['domain']}")
    print(f"  资料数：{len(config['materials'])}")
    print("=" * 70)
    print()

    event_count = 0
    async for event_type, data in engine.generate(
        domain=config["domain"],
        requirement=config["requirement"],
        material_paths=config["materials"],
        project_name=config["project_name"],
    ):
        event_count += 1
        if event_count % 20 == 0:
            # 保存检查点
            pass

        if event_type == "think":
            print(f"  💭 [思考] {data}")
        elif event_type == "say":
            print(f"  🤖 {data}")
            print()
        elif event_type == "progress":
            ch = data.get("chapter", "?")
            total = data.get("total", "?")
            phase = data.get("phase", "")
            status = data.get("status", "")
            if phase == "writing":
                print(f"  📊 进度: 第{ch}/{total}章 — 写作中...")
            elif phase == "done":
                print(f"  📊 进度: 第{ch}/{total}章 — {status}")
            elif phase == "complete":
                print(f"\n  📊 最终输出: {data.get('output','')}")
        elif event_type == "error":
            print(f"  ❌ 错误: {data}")
        elif event_type == "complete":
            print(f"\n{'=' * 70}")
            print(f"  ✅ 生成完成！")
            print(f"  运行ID: {data.get('run_id','')}")
            print(f"  章节数: {data.get('chapters',0)}")
            print(f"  Word: {data.get('output','')}")
            print(f"  Markdown: {data.get('md_output','')}")
            print(f"{'=' * 70}")
            db.close()
            return data

    db.close()
    return None


async def main():
    domain = sys.argv[1] if len(sys.argv) > 1 else "stability"
    config = STABILITY_CONFIG if domain == "stability" else BIDDING_CONFIG
    result = await run(config)
    if not result:
        print("\n⚠️ 生成未正常完成")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
