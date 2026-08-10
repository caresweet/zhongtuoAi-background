#!/usr/bin/env python3
"""Rebuild the bidding_knowledge ChromaDB collection at the new 3072-dim
embedding (OpenAI text-embedding-3-large), from the text backup taken before
the embedding switch (data/bidding_knowledge_backup.json).

The old collection holds 1024-dim vectors incompatible with the current model,
so bidding RAG retrieval fails. This re-embeds the same source text so
per-chapter bidding generation can retrieve same-type reference passages.

Usage: cd backend && python -m seed_data.rebuild_bidding_index
"""

import sys, json, asyncio
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService
from app.config import settings


async def rebuild():
    print("=" * 60)
    print("Rebuild bidding_knowledge (OpenAI 3072-dim)")
    print("=" * 60)

    backup = backend_dir / "data" / "bidding_knowledge_backup.json"
    if not backup.exists():
        print(f"✗ backup not found: {backup}")
        return
    data = json.load(open(backup, encoding="utf-8"))
    print(f"Backup chunks: {len(data)}")

    embedder = EmbedderService()
    vs = VectorStoreService()
    print(f"Embedding model: {embedder.model} @ {embedder.api_url}")

    # Drop old 1024-dim collection
    try:
        vs.client.delete_collection("bidding_knowledge")
        print("Dropped old bidding_knowledge")
    except Exception as e:
        print(f"(no old collection: {e})")

    collection = vs.get_bidding_collection()

    # Re-embed in batches of 10
    ids, docs, metas = [], [], []
    for i, item in enumerate(data):
        doc = (item.get("doc") or "").strip()
        if not doc:
            continue
        meta = dict(item.get("meta") or {})
        # ensure domain tag
        meta["domain"] = "bidding"
        ids.append(item.get("id") or f"bidding_chunk_{i}")
        docs.append(doc)
        metas.append(meta)

    print(f"Non-empty chunks to index: {len(docs)}")
    total = 0
    batch = 10
    for i in range(0, len(docs), batch):
        b_docs = docs[i:i+batch]
        b_ids = ids[i:i+batch]
        b_metas = metas[i:i+batch]
        try:
            embs = await embedder.embed_texts(b_docs)
            vs.add_documents(collection, b_ids, b_docs, embs, b_metas)
            total += len(b_docs)
            print(f"  indexed {total}/{len(docs)}")
        except Exception as e:
            print(f"  ✗ batch {i}: {e}")
            await asyncio.sleep(2)

    print(f"\nDone. bidding_knowledge count: {collection.count()}")


if __name__ == "__main__":
    asyncio.run(rebuild())
