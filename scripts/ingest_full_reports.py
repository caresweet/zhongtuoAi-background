#!/usr/bin/env python3
"""Ingest full real stable-risk-assessment reports into the knowledge base."""
import sys, os, asyncio, re
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser

from dotenv import load_dotenv
for p in ['.env', 'backend/.env']:
    if os.path.exists(p): load_dotenv(p)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.chunker import ChineseReportChunker
from app.rag.vector_store import VectorStoreService
from app.rag.embedder import EmbedderService
from app.services.cleaning_pipeline import cleaning_pipeline
from app.database.knowledge_db import async_session
from sqlalchemy import text

REPORTS_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "crawled_reports" / "full_reports"

def extract_pdf(p):
    import fitz
    return '\n'.join(page.get_text() for page in fitz.open(p))

class T(HTMLParser):
    def __init__(self): super().__init__(); self.t=[]; self.s=False
    def handle_data(self,d):
        if not self.s and d.strip(): self.t.append(d.strip())
    def handle_starttag(self,t,a):
        if t in ('script','style'): self.s=True
    def handle_endtag(self,t):
        if t in ('script','style'): self.s=False

def extract_html(p):
    ex = T()
    with open(p) as f: ex.feed(f.read())
    return '\n'.join(ex.t)

REPORTS = [
    {
        "file": "xiaoxian_report.pdf",
        "extractor": extract_pdf,
        "title": "萧县2022年第5批次城镇建设用地土地征收项目社会稳定风险评估报告（完整版69页）",
        "document_type": "本地案例",
        "region": "安徽省/宿州市/萧县",
        "year": "2022",
        "risk_tags": "土地征收/城镇建设用地/风险识别/风险等级评判/防范化解措施/四性分析",
        "doc_category": "本地案例",
    },
    {
        "file": "bowang_report.html",
        "extractor": extract_html,
        "title": "马鞍山市博望区征地片区综合地价调整项目社会稳定风险评估报告",
        "document_type": "本地案例",
        "region": "安徽省/马鞍山市/博望区",
        "year": "2023",
        "risk_tags": "征地区片综合地价/补偿标准调整/风险评估",
        "doc_category": "本地案例",
    },
]


async def main():
    chunker = ChineseReportChunker(chunk_size=1000, chunk_overlap=150, max_chunk_size=4000)
    vs = VectorStoreService(); embedder = EmbedderService()
    col = vs.get_or_create_collection("knowledge_base")
    print(f"Collection before: {col.count()}\n")

    async with async_session() as db:
        total_chunks = 0
        for r in REPORTS:
            fp = REPORTS_DIR / r["file"]
            if not fp.exists():
                print(f"⚠️ Skip: {r['file']}"); continue
            raw = r["extractor"](str(fp))
            if not raw or len(raw) < 50:
                print(f"⚠️ Empty: {r['file']}"); continue
            print(f"📄 {r['title'][:70]}... ({len(raw)} chars)")

            # Clean
            cfg = cleaning_pipeline.get_default_config()
            cleaned = cleaning_pipeline.execute(raw, cfg)
            text_to_chunk = cleaned if len(cleaned.strip()) >= 20 else raw

            # Find or insert
            result = await db.execute(text("SELECT id FROM knowledge_documents WHERE title=:t"), {"t": r["title"]})
            row = result.fetchone()
            doc_id = row[0] if row else None
            if doc_id:
                vs.remove_by_prefix(col, f"doc_{doc_id}")
            else:
                await db.execute(text("""INSERT INTO knowledge_documents
                    (title, document_type, file_path, file_type, domain, indexed_status,
                     chunk_count, collection_name, raw_text, cleaned_text, clean_status,
                     created_at, updated_at, is_active)
                    VALUES (:t,:dt,:fp,'pdf','stability','pending',0,
                     'knowledge_base',:rt,:ct,'cleaned',:now,:now,1)"""),
                    {"t": r["title"], "dt": r["document_type"], "fp": r["file"],
                     "rt": raw[:80000], "ct": text_to_chunk[:80000],
                     "now": datetime.now(timezone.utc).isoformat()})
                await db.commit()
                result = await db.execute(text("SELECT last_insert_rowid()"))
                doc_id = result.scalar()
                print(f"  📝 New doc id={doc_id}")

            # Chunk
            meta = {
                "document_type": r["document_type"], "source_file": r["file"],
                "region": r["region"], "year": r["year"],
                "risk_tags": r["risk_tags"], "doc_category": r["doc_category"],
            }
            chunks = chunker.chunk_text(text_to_chunk, meta)
            chunks = chunker.inject_rag_tags(chunks)
            print(f"  📦 {len(chunks)} chunks")

            if not chunks: continue
            texts = [c.text for c in chunks]
            ids_list = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunks))]
            mds = [{
                "document_type": str(r["document_type"]), "source_file": str(r["title"]),
                "domain": "stability",
                "chapter_number": int(c.metadata.chapter_number or 0),
                "section_title": str(c.metadata.section_title or ""),
                "heading_level": int(c.metadata.heading_level or 0),
                "chunk_index": int(c.metadata.chunk_index or 0),
                "total_chunks": int(c.metadata.total_chunks or 0),
                "source_type": "file", "modality": "file", "image_summary": "",
            } for c in chunks]

            embeddings = await embedder.embed_texts(texts)
            if embeddings and embeddings[0]:
                vs.add_documents(col, ids_list, texts, embeddings, mds)
                total_chunks += len(chunks)
                await db.execute(text("""UPDATE knowledge_documents
                    SET chunk_count=:cc, indexed_status='indexed', updated_at=:now WHERE id=:id"""),
                    {"cc": len(chunks), "now": datetime.now(timezone.utc).isoformat(), "id": doc_id})
                await db.commit()
                print(f"  ✅ Embedded {len(chunks)}")
            else:
                print(f"  ❌ Embed failed")

        print(f"\n{'='*60}")
        print(f"✅ Done! Total chunks: {total_chunks}")
        print(f"   Collection: {col.count()} embeddings")
        print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
