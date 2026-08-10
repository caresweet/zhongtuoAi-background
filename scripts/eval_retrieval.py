#!/usr/bin/env python3
"""RAG retrieval quality benchmark.

Runs a set of test queries against the knowledge base and reports
hit quality. Run this after any major knowledge base change to verify
retrieval hasn't degraded.
"""

import asyncio, json, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.vector_store import VectorStoreService
from app.rag.embedder import EmbedderService

# Each test case: (query, expected_keywords, min_expected_in_top3)
TEST_CASES = [
    # Compensation queries
    (
        "洪泽区征地补偿标准每亩多少钱",
        ["淮政规", "45000", "区片综合地价"],
        "应返回淮政规〔2026〕1号中洪泽区地价标准",
    ),
    (
        "清江浦区征地补偿标准",
        ["清江浦", "54000", "52000"],
        "应返回清江浦区两个区片的具体地价",
    ),
    # Social security
    (
        "被征地农民社保怎么办理",
        ["苏政发", "社会保障", "87号"],
        "应返回苏政发〔2021〕87号社保办理流程",
    ),
    (
        "安置补助费可以抵缴社保吗",
        ["抵缴", "安置补助费", "书面确认"],
        "应返回安置补助费抵缴规则",
    ),
    # Procedure
    (
        "征地需要哪些法定程序",
        ["预公告", "土地管理法", "第四十七条"],
        "应返回征地法定程序步骤",
    ),
    # Case queries
    (
        "清江浦区黄码镇征地案例",
        ["黄码", "清江浦", "征收"],
        "应返回黄码镇实际征地案卷",
    ),
    (
        "洪泽区三河镇食品产业园项目",
        ["三河", "八里", "食品"],
        "应返回食品产业园完整案卷",
    ),
    # Emergency
    (
        "群体性事件怎么应急处置",
        ["突发事件应对法", "公安", "应急"],
        "应返回应急处突相关条文和预案",
    ),
    # Risk assessment
    (
        "社会稳定风险等级怎么划分",
        ["低风险", "中风险", "高风险", "DB32"],
        "应返回风险等级判定标准",
    ),
    # Survey
    (
        "村民调查问卷有哪些问题",
        ["了解程度", "是否支持", "补偿"],
        "应返回调查问卷内容或模板",
    ),
]


async def run_eval():
    vs = VectorStoreService()
    embedder = EmbedderService()
    col = vs.get_or_create_collection("knowledge_base")

    print("=" * 70)
    print("  RAG 检索质量评估")
    print(f"  Collection: knowledge_base ({col.count()} embeddings)")
    print("=" * 70)

    results = []
    for query, expected_keywords, description in TEST_CASES:
        print(f"\n🔍 {query}")
        print(f"   期望: {description}")

        q_embedding = await embedder.embed_texts([query])
        hits = col.query(query_embeddings=q_embedding, n_results=5)

        top3_text = ' '.join(hits['documents'][0][:3])
        top3_ids = hits['ids'][0][:3]
        distances = hits['distances'][0][:3]

        matched = [kw for kw in expected_keywords if kw.lower() in top3_text.lower()]
        score = len(matched) / len(expected_keywords) if expected_keywords else 0

        # Show first hit summary
        first_tag = hits['documents'][0][0].split('\n')[0][:70] if hits['documents'][0] else ""
        print(f"   Top1: {first_tag} (d={distances[0]:.3f})")
        print(f"   匹配: {matched}/{expected_keywords} → {score:.0%}")

        results.append({
            "query": query,
            "score": score,
            "matched": matched,
            "expected": expected_keywords,
            "top_ids": top3_ids,
            "top_dist": distances[0],
        })

    # Summary
    avg_score = sum(r["score"] for r in results) / len(results)
    perfect = sum(1 for r in results if r["score"] >= 0.67)
    failed = sum(1 for r in results if r["score"] == 0)

    print(f"\n{'=' * 70}")
    print(f"  📊 评估结果")
    print(f"  平均分: {avg_score:.1%}")
    print(f"  高分 (≥67%): {perfect}/{len(results)}")
    print(f"  零分: {failed}/{len(results)}")
    print(f"{'=' * 70}")

    if avg_score < 0.5:
        print("  ⚠️ 检索质量偏低，建议检查索引或补充素材")
    elif avg_score < 0.8:
        print("  ✅ 检索质量良好")
    else:
        print("  🎉 检索质量优秀")

    return results


if __name__ == "__main__":
    asyncio.run(run_eval())
