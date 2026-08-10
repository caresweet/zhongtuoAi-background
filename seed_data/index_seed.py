"""Index seed knowledge documents into ChromaDB.

Reads .md files from seed_data/, chunks via ChineseReportChunker,
embeds via EmbedderService, and stores in the global knowledge_base
ChromaDB collection.

Usage:
    cd backend
    python -m seed_data.index_seed
"""

import os
import sys
import asyncio
from pathlib import Path

# Ensure backend is on path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.rag.chunker import ChineseReportChunker
from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService

# Document type mapping based on filename prefix
DOCUMENT_TYPES = {
    "land_management_law": "regulation",
    "emergency_response_law": "regulation",
    "db32_t4013_2021": "standard",
    "stability_assessment_guideline": "standard",
    "example_report": "example_report",
    "company_info": "company_info",  # 江苏众拓固定信息，不可修改
}


def get_document_type(filename: str) -> str:
    """Map filename to document_type for ChromaDB metadata."""
    for prefix, dtype in DOCUMENT_TYPES.items():
        if filename.startswith(prefix):
            return dtype
    return "regulation"


async def index_documents():
    """Main indexing routine."""
    seed_dir = Path(__file__).resolve().parent
    md_files = sorted(seed_dir.glob("*.md"))

    if not md_files:
        print("No .md files found in seed_data/")
        return

    print(f"Found {len(md_files)} seed documents to index\n")

    chunker = ChineseReportChunker()
    embedder = EmbedderService()
    vector_store = VectorStoreService()

    collection = vector_store.get_or_create_global_collection()
    total_chunks = 0

    for md_file in md_files:
        filename = md_file.stem
        doc_type = get_document_type(filename)

        print(f"Processing: {md_file.name} (type: {doc_type})")

        # Read document
        text = md_file.read_text(encoding="utf-8")
        print(f"  Read {len(text)} characters")

        # Chunk via ChineseReportChunker (returns List[Chunk] dataclass objects)
        raw_chunks = chunker.chunk_markdown(text)

        if raw_chunks:
            # Extract text from Chunk dataclass objects
            chunk_texts = [c.text if hasattr(c, 'text') else str(c) for c in raw_chunks]
            print(f"  Chunked into {len(chunk_texts)} pieces")
        else:
            # Fallback: simple paragraph-based chunking
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            if not paragraphs:
                print(f"  Skipping empty file: {md_file.name}")
                continue
            chunk_texts = []
            current = ""
            for p in paragraphs:
                if len(current) + len(p) < 1200:
                    current += p + "\n\n"
                else:
                    if current:
                        chunk_texts.append(current.strip())
                    current = p + "\n\n"
            if current:
                chunk_texts.append(current.strip())
            print(f"  Fallback chunking: {len(chunk_texts)} chunks")

        if not chunk_texts:
            print(f"  Warning: No content extracted from {md_file.name}")
            continue

        # Remove previous chunks for this document
        try:
            vector_store.remove_by_prefix(f"doc_{filename}_")
        except Exception:
            pass  # First run, nothing to remove

        # Merge small chunks to avoid API errors (min 50 chars per chunk)
        merged = []
        buf = ""
        for ct in chunk_texts:
            if len(buf) + len(ct) < 1000:
                buf += ct + "\n\n"
            else:
                if buf.strip():
                    merged.append(buf.strip())
                buf = ct + "\n\n"
        if buf.strip():
            merged.append(buf.strip())
        if merged:
            chunk_texts = merged
            print(f"  Merged into {len(chunk_texts)} chunks")

        # Embed chunk_texts with retry and rate-limit delay
        embeddings = None
        for attempt in range(3):
            try:
                embeddings = await embedder.embed_texts(chunk_texts)
                break
            except Exception as e:
                print(f"  Embedding attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)  # Rate limit backoff

        if embeddings is None:
            print(f"  ✗ All embedding attempts failed, skipping document")
            continue

        # Store in ChromaDB
        ids = [f"doc_{filename}_chunk_{i}" for i in range(len(chunk_texts))]
        metadatas = [
            {
                "document_type": doc_type,
                "source_file": md_file.name,
                "chunk_index": i,
                "total_chunks": len(chunk_texts),
            }
            for i in range(len(chunk_texts))
        ]

        try:
            collection.add(
                ids=ids,
                documents=chunk_texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            total_chunks += len(chunk_texts)
            print(f"  ✓ Indexed {len(chunk_texts)} chunks")
        except Exception as e:
            print(f"  ✗ Failed to store in ChromaDB: {e}")

        # Rate limit delay between documents
        await asyncio.sleep(1)

    # Verify: test retrieval
    print(f"\n{'='*50}")
    print(f"Testing retrieval...")
    results = vector_store.query_global("社会稳定风险评估 合法性分析 征收", top_k=3)
    if results and results.get("documents"):
        for i, (doc, meta) in enumerate(zip(results["documents"], results.get("metadatas", [[]]))):
            dist = results.get("distances", [[0]]*len(results["documents"]))
            print(f"  Result {i+1}: [{meta.get('document_type','?')}] {str(doc)[:80]}... (dist={dist[i]:.3f})")
    else:
        print("  No results — retrieval may be empty")

    print(f"\nDone. Total chunks indexed: {total_chunks}")
    print(f"Collection count: {collection.count()}")


def main():
    asyncio.run(index_documents())


if __name__ == "__main__":
    main()
