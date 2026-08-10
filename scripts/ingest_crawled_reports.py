#!/usr/bin/env python3
"""Ingest crawled stable-risk-assessment reports into the knowledge base.

Downloads from: backend/seed_data/crawled_reports/clean/
Usage: python scripts/ingest_crawled_reports.py
"""

import sys, os, asyncio
from datetime import datetime, timezone
from pathlib import Path

# Load .env BEFORE any other imports
from dotenv import load_dotenv
for p in ['.env', 'backend/.env']:
    if os.path.exists(p):
        load_dotenv(p)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.chunker import ChineseReportChunker
from app.rag.vector_store import VectorStoreService
from app.rag.embedder import EmbedderService
from app.services.cleaning_pipeline import cleaning_pipeline
from app.database.knowledge_db import async_session
from sqlalchemy import text

CLEAN_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "crawled_reports" / "clean"

REPORTS = [
    {
        "file": "nantong_standard_2024.md",
        "title": "南通市重大决策社会稳定风险评估规范 DB3206/T 1091-2024",
        "document_type": "技术标准",
        "domain": "stability",
        "region": "江苏省/南通市",
        "year": "2024",
        "risk_tags": "评估规范/程序标准/第三方管理/评审专家",
        "doc_category": "技术标准",
    },
    {
        "file": "shantou_shuiza_report.md",
        "title": "汕头市潮南区伯公头水闸重建工程社会稳定风险分析报告",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "广东省/汕头市",
        "year": "2017",
        "risk_tags": "水利工程/征地移民/施工影响/生态环境/质量安全",
        "doc_category": "本地案例",
    },
    {
        "file": "cniplan_industry_park.md",
        "title": "某产业园区建设项目社会稳定风险评估报告（完整范文）",
        "document_type": "稳评范文",
        "domain": "stability",
        "region": "全国",
        "year": "2025",
        "risk_tags": "征地补偿/施工影响/安置就业/环境污染/舆情风险",
        "doc_category": "人工范本",
    },
    {
        "file": "xihe_land_acquisition_template.md",
        "title": "征地社会稳定风险评估报告（样式）- 西和县自然资源局",
        "document_type": "工作指南",
        "domain": "stability",
        "region": "甘肃省/陇南市",
        "year": "2023",
        "risk_tags": "土地征收/合法性/合理性/可行性/可控性/应急预案",
        "doc_category": "工作指南",
    },
    {
        "file": "rugao_dc2025_beian.md",
        "title": "如皋市DC2025-99#地块征地项目社会稳定风险评估备案报告",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "江苏省/南通市/如皋市",
        "year": "2026",
        "risk_tags": "征地程序/补偿安置/社会保障/舆情风险/应急预案",
        "doc_category": "本地案例",
    },
    {
        "file": "boxing_penggai_report.md",
        "title": "博兴县博昌街道东伏片区棚改项目土地征收社会稳定风险评估公示",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "山东省/滨州市",
        "year": "2026",
        "risk_tags": "棚改项目/土地征收/城市建设/公众参与",
        "doc_category": "本地案例",
    },
    {
        "file": "fengzhen_construction_land.md",
        "title": "丰镇市2026年第五批次建设用地项目社会稳定风险评估报告公示",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "内蒙古自治区/乌兰察布市",
        "year": "2026",
        "risk_tags": "城镇建设用地/土地征收/补偿安置/公众参与",
        "doc_category": "本地案例",
    },
    {
        "file": "yanping_G205_road.md",
        "title": "国道G205线南平西芹浆甲段公路改建工程社会稳定风险分析公示",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "福建省/南平市",
        "year": "2026",
        "risk_tags": "公路改建/征地拆迁/交通影响/桥梁工程",
        "doc_category": "本地案例",
    },
    {
        "file": "zhongshan_qijiang_road.md",
        "title": "中山市岐江新城产业平台基础设施建设项目社会稳定风险评估公示",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "广东省/中山市",
        "year": "2026",
        "risk_tags": "产业园区/配套道路/征地补偿/留用地安置/养老保障",
        "doc_category": "本地案例",
    },
    {
        "file": "yingde_land_acquisition.md",
        "title": "英德市2026年度第四十三批次城镇建设用地征收土地社会稳定风险评估公示",
        "document_type": "本地案例",
        "domain": "stability",
        "region": "广东省/清远市",
        "year": "2026",
        "risk_tags": "城镇建设用地/土地征收/公众参与/问卷调查",
        "doc_category": "本地案例",
    },
]


async def main():
    chunker = ChineseReportChunker(chunk_size=1000, chunk_overlap=150, max_chunk_size=4000)
    vs = VectorStoreService()
    embedder = EmbedderService()
    col = vs.get_or_create_collection("knowledge_base")

    print(f"Collection count before: {col.count()}")
    print(f"API URL: {embedder.api_url}")
    print(f"Model: {embedder.model}")
    print(f"API Key: {'SET' if embedder.api_key else 'MISSING'}")
    print()

    async with async_session() as db:
        total_chunks = 0

        for r in REPORTS:
            filepath = CLEAN_DIR / r["file"]
            if not filepath.exists():
                print(f"⚠️  Skipping: {r['file']} (not found)")
                continue

            with open(filepath, 'r') as f:
                content = f.read()

            # Extract body text
            for marker in ["## 全文内容", "## 报告内容", "## 范文正文", "## 模板内容", "## 公示内容"]:
                idx = content.find(marker)
                if idx >= 0:
                    raw_text = content[idx:]
                    break
            else:
                raw_text = content

            print(f"📄 {r['title']} ({len(raw_text)} chars)")

            # Clean
            cfg = cleaning_pipeline.get_default_config()
            cleaned = cleaning_pipeline.execute(raw_text, cfg)
            text_to_chunk = cleaned if len(cleaned.strip()) >= 20 else raw_text

            # Find existing doc
            result = await db.execute(
                text("SELECT id FROM knowledge_documents WHERE title = :title"),
                {"title": r["title"]}
            )
            row = result.fetchone()
            doc_id = row[0] if row else None

            if not doc_id:
                # Insert new
                result = await db.execute(
                    text("""INSERT INTO knowledge_documents
                        (title, document_type, file_path, file_type, domain, indexed_status,
                         chunk_count, collection_name, raw_text, cleaned_text, clean_status,
                         created_at, updated_at, is_active)
                        VALUES (:t, :dt, :fp, 'md', :dom, 'pending', 0,
                         'knowledge_base', :rt, :ct, 'cleaned', :now, :now, 1)"""),
                    {"t": r["title"], "dt": r["document_type"], "fp": r["file"],
                     "dom": r["domain"], "rt": raw_text[:80000], "ct": text_to_chunk[:80000],
                     "now": datetime.now(timezone.utc).isoformat()}
                )
                await db.commit()
                result = await db.execute(text("SELECT last_insert_rowid()"))
                doc_id = result.scalar()
                print(f"  📝 New doc id={doc_id}")

            # Remove old embeddings
            vs.remove_by_prefix(col, f"doc_{doc_id}")

            # Chunk
            meta = {
                "document_type": r["document_type"],
                "source_file": r["file"],
                "region": r["region"],
                "year": r["year"],
                "risk_tags": r["risk_tags"],
                "doc_category": r["doc_category"],
            }
            chunks = chunker.chunk_text(text_to_chunk, meta)
            chunks = chunker.inject_rag_tags(chunks)
            print(f"  📦 {len(chunks)} chunks")

            if not chunks:
                print(f"  ⚠️  No chunks generated")
                continue

            # Embed
            texts = [c.text for c in chunks]
            ids_list = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunks))]
            mds = [{
                "document_type": str(r["document_type"]),
                "source_file": str(r["title"]),
                "domain": "stability",
                "chapter_number": int(c.metadata.chapter_number or 0),
                "section_title": str(c.metadata.section_title or ""),
                "heading_level": int(c.metadata.heading_level or 0),
                "chunk_index": int(c.metadata.chunk_index or 0),
                "total_chunks": int(c.metadata.total_chunks or 0),
                "source_type": "file",
                "modality": "file",
                "image_summary": "",
            } for c in chunks]

            print(f"  🔤 Generating embeddings for {len(texts)} texts...")
            embeddings = await embedder.embed_texts(texts)

            if embeddings and len(embeddings) > 0 and embeddings[0]:
                dim = len(embeddings[0])
                print(f"  ✅ Got {len(embeddings)} embeddings (dim={dim})")
                vs.add_documents(col, ids_list, texts, embeddings, mds)
                total_chunks += len(chunks)

                # Update SQLite
                await db.execute(
                    text("""UPDATE knowledge_documents
                        SET chunk_count=:cc, indexed_status='indexed',
                        updated_at=:now WHERE id=:id"""),
                    {"cc": len(chunks), "now": datetime.now(timezone.utc).isoformat(), "id": doc_id}
                )
                await db.commit()
                print(f"  ✅ Stored {len(chunks)} embeddings for doc_{doc_id}")
            else:
                print(f"  ❌ Embedding failed for doc_{doc_id}")
                await db.execute(
                    text("""UPDATE knowledge_documents
                        SET indexed_status='error', index_error='Embedding API returned empty',
                        updated_at=:now WHERE id=:id"""),
                    {"now": datetime.now(timezone.utc).isoformat(), "id": doc_id}
                )
                await db.commit()

        print(f"\n{'='*60}")
        print(f"✅ Done! Total chunks embedded: {total_chunks}")
        print(f"   Collection count: {col.count()}")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
