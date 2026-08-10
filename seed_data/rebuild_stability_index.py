#!/usr/bin/env python3
"""One-shot re-index of the stability knowledge base after switching embedding
models (DashScope 1024-dim → OpenAI text-embedding-3-large 3072-dim).

The old `knowledge_base` collection holds 1024-dim vectors that are
incompatible with the new 3072-dim model, so it is dropped and rebuilt from:
  1. seed_data/*.md  (base regulations / standards / example / company info)
  2. knowledge_documents rows in knowledge_base.db (uploaded regs/reports)

Every chunk is tagged with `domain` metadata so domain-scoped retrieval works.
The bidding_knowledge collection is left untouched (backed up separately).

Usage:
    cd backend && python -m seed_data.rebuild_stability_index
"""

import sys
import asyncio
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.rag.chunker import ChineseReportChunker
from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService
from app.services.file_service import file_service
from app.config import settings

# Seed filename → (document_type, domain)
SEED_TYPES = {
    "land_management_law": ("regulation", "stability"),
    "emergency_response_law": ("regulation", "stability"),
    "db32_t4013_2021": ("standard", "stability"),
    "stability_assessment_guideline": ("standard", "stability"),
    "example_report": ("example_report", "stability"),
    "company_info": ("company_info", "stability"),
}


def seed_meta(filename: str):
    for prefix, (dtype, domain) in SEED_TYPES.items():
        if filename.startswith(prefix):
            return dtype, domain
    return "regulation", "stability"


async def _embed_with_retry(embedder, texts):
    for attempt in range(3):
        try:
            return await embedder.embed_texts(texts)
        except Exception as e:
            print(f"    embed attempt {attempt+1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(2)
    return None


async def rebuild():
    print("=" * 60)
    print("Rebuild stability knowledge base (OpenAI 3072-dim)")
    print("=" * 60)

    embedder = EmbedderService()
    vector_store = VectorStoreService()
    chunker = ChineseReportChunker()

    print(f"\nEmbedding model: {embedder.model}  @ {embedder.api_url}")

    # ── Step 0: drop the old 1024-dim collection ──
    try:
        vector_store.client.delete_collection(vector_store.GLOBAL_COLLECTION)
        print(f"Dropped old collection '{vector_store.GLOBAL_COLLECTION}'")
    except Exception as e:
        print(f"(no old collection to drop: {e})")

    collection = vector_store.get_or_create_global_collection()
    total = 0

    # ── Step 1: seed markdown files ──
    seed_dir = Path(__file__).resolve().parent
    md_files = sorted(seed_dir.glob("*.md"))
    print(f"\nStep 1: {len(md_files)} seed markdown files")

    for md_file in md_files:
        dtype, domain = seed_meta(md_file.stem)
        text = md_file.read_text(encoding="utf-8")
        raw = chunker.chunk_markdown(text)
        chunk_texts = [c.text for c in raw] if raw else []
        # Merge tiny chunks
        merged, buf = [], ""
        for ct in chunk_texts:
            if len(buf) + len(ct) < 1000:
                buf += ct + "\n\n"
            else:
                if buf.strip():
                    merged.append(buf.strip())
                buf = ct + "\n\n"
        if buf.strip():
            merged.append(buf.strip())
        chunk_texts = merged or [text]

        embeddings = await _embed_with_retry(embedder, chunk_texts)
        if not embeddings:
            print(f"  ✗ {md_file.name}: embedding failed")
            continue

        ids = [f"seed_{md_file.stem}_chunk_{i}" for i in range(len(chunk_texts))]
        metadatas = [{
            "document_type": dtype,
            "domain": domain,
            "source_file": md_file.name,
            "chunk_index": i,
            "total_chunks": len(chunk_texts),
        } for i in range(len(chunk_texts))]
        collection.add(ids=ids, documents=chunk_texts, embeddings=embeddings, metadatas=metadatas)
        total += len(chunk_texts)
        print(f"  ✓ {md_file.name} [{dtype}/{domain}]: {len(chunk_texts)} chunks")

    # ── Step 2: DB-registered documents ──
    import sqlite3
    db_path = settings.DATA_DIR / "knowledge_base.db"
    rows = []
    if db_path.exists():
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT id, title, document_type, domain, file_path, file_type "
            "FROM knowledge_documents WHERE is_active=1"
        ).fetchall()
        con.close()
    print(f"\nStep 2: {len(rows)} DB-registered documents")

    for doc_id, title, dtype, domain, file_path, file_type in rows:
        domain = domain or "stability"
        ft = (file_type or "").lower()
        try:
            if ft in ("txt", "md"):
                text = file_service.read_text_file(file_path)
            elif ft in ("docx", "doc"):
                text = file_service.extract_docx_text(file_path)
            elif ft == "pdf":
                text = file_service.extract_pdf_text(file_path)
            else:
                text = file_service.read_text_file(file_path)
        except Exception as e:
            print(f"  ✗ doc {doc_id} '{title}': extract failed ({e})")
            continue

        if not text or len(text.strip()) < 50:
            print(f"  ⚠ doc {doc_id} '{title}': text too short, skipped")
            continue

        raw = chunker.chunk_markdown(text) or chunker.chunk_text(text)
        chunk_texts = [c.text for c in raw]
        if not chunk_texts:
            print(f"  ⚠ doc {doc_id} '{title}': no chunks")
            continue

        embeddings = await _embed_with_retry(embedder, chunk_texts)
        if not embeddings:
            print(f"  ✗ doc {doc_id} '{title}': embedding failed")
            continue

        ids = [f"doc_{doc_id}_chunk_{i}" for i in range(len(chunk_texts))]
        metadatas = [{
            "document_type": str(dtype or "regulation"),
            "domain": str(domain),
            "source_file": str(title),
            "chapter_number": int(c.metadata.chapter_number or 0),
            "section_title": str(c.metadata.section_title or ""),
            "chunk_index": i,
            "total_chunks": len(chunk_texts),
        } for i, c in enumerate(raw)]
        collection.add(ids=ids, documents=chunk_texts, embeddings=embeddings, metadatas=metadatas)
        total += len(chunk_texts)
        print(f"  ✓ doc {doc_id} '{title}' [{dtype}/{domain}]: {len(chunk_texts)} chunks")

    print("\n" + "=" * 60)
    print(f"Done. Total chunks: {total}  |  Collection count: {collection.count()}")


if __name__ == "__main__":
    asyncio.run(rebuild())
