#!/usr/bin/env python3
"""Re-index existing knowledge base documents with multi-modal support.

Upgrades the ChromaDB knowledge_base collection to store:
1. Text chunks with modality="text" metadata
2. Image descriptions with modality="image" metadata (for PDFs with embedded images)

Existing text-only chunks are preserved and updated with modality tags.
New PDF documents are re-processed to extract embedded images.

Usage:
    cd backend
    python -m seed_data.reindex_multimodal
"""

import sys
import os
import asyncio
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService
from app.rag.multimodal_embedder import MultiModalEmbedder
from app.rag.multimodal_chunker import MultiModalChunker
from app.rag.multimodal_vector_store import MultiModalVectorStore
from app.services.llm_service import llm_service


async def reindex_all():
    """Re-index all knowledge base documents with multi-modal support."""

    print("=" * 60)
    print("Multi-modal Knowledge Base Re-index")
    print("=" * 60)

    # Initialize services
    embedder = EmbedderService()
    vector_store = VectorStoreService()
    mm_embedder = MultiModalEmbedder(embedder, llm_service)
    mm_store = MultiModalVectorStore()
    mm_chunker = MultiModalChunker()

    # ── Step 0: Check existing collection ──
    try:
        old_collection = vector_store.client.get_collection("knowledge_base")
        old_count = old_collection.count()
        print(f"\n📊 Existing 'knowledge_base' collection: {old_count} chunks")

        # Update existing chunks with modality="text" if missing
        print("  Updating existing chunks with modality metadata...")
        existing = old_collection.get()
        if existing and existing.get("ids"):
            updated_metadatas = []
            for meta in (existing.get("metadatas") or []):
                new_meta = dict(meta) if meta else {}
                if "modality" not in new_meta:
                    new_meta["modality"] = "text"
                # Sanitize for ChromaDB
                clean = {}
                for k, v in new_meta.items():
                    if v is None:
                        clean[k] = ""
                    elif isinstance(v, (str, int, float, bool)):
                        clean[k] = v
                    else:
                        clean[k] = str(v)
                updated_metadatas.append(clean)

            old_collection.update(
                ids=existing["ids"],
                metadatas=updated_metadatas,
            )
            print(f"  ✅ Updated {len(existing['ids'])} chunks with modality tags")
    except Exception as e:
        print(f"  ⚠️ Existing collection check: {e}")
        old_collection = None

    # ── Step 1: Re-index seed markdown files ──
    print("\n📝 Step 1: Re-indexing seed markdown files...")
    seed_dir = Path(__file__).resolve().parent
    md_files = list(seed_dir.glob("*.md"))
    print(f"  Found {len(md_files)} markdown files")

    for md_file in md_files:
        print(f"\n  Processing: {md_file.name}")
        try:
            # Determine document type from filename
            doc_type = "unknown"
            name_lower = md_file.name.lower()
            if "company" in name_lower or "众拓" in name_lower:
                doc_type = "company_info"
            elif "standard" in name_lower or "guideline" in name_lower or "db32" in name_lower or "t4013" in name_lower:
                doc_type = "standard"
            elif "law" in name_lower or "管理法" in name_lower:
                doc_type = "regulation"
            elif "emergency" in name_lower or "应急" in name_lower:
                doc_type = "regulation"
            elif "example" in name_lower or "报告" in name_lower or "jinhu" in name_lower:
                doc_type = "example_report"

            # Chunk the file
            chunks = await mm_chunker.chunk_document(
                str(md_file),
                file_type="md",
                metadata={
                    "document_type": doc_type,
                    "source_file": md_file.name,
                },
            )

            # Embed text chunks only (markdown has no images)
            for chunk in chunks:
                if chunk.modality == "text" and not chunk.embedding:
                    chunk.embedding = await mm_embedder.embed_text(chunk.text)

            # Add to vector store
            doc_prefix = f"seed_{md_file.stem}"
            count = await mm_store.add_multimodal_chunks(
                chunks,
                doc_id_prefix=doc_prefix,
                collection_name="knowledge_base",
            )

            # Verify the chunks were indexed
            try:
                collection = vector_store.client.get_collection("knowledge_base")
                indexed_count = collection.count()
                print(f"  ✅ {md_file.name}: {count} chunks added (collection total: {indexed_count})")
            except Exception:
                print(f"  ✅ {md_file.name}: {count} chunks added")

        except Exception as e:
            print(f"  ❌ Failed to index {md_file.name}: {e}")

    # ── Step 2: Re-index PDF knowledge documents ──
    print("\n📄 Step 2: Re-indexing PDF knowledge documents...")
    knowledge_dir = Path(__file__).resolve().parent.parent / "storage" / "knowledge_docs"
    if knowledge_dir.exists():
        pdf_files = list(knowledge_dir.glob("*.pdf"))
        print(f"  Found {len(pdf_files)} PDF files")

        for pdf_file in pdf_files:
            print(f"\n  Processing: {pdf_file.name}")
            doc_type = "unknown"
            name_lower = pdf_file.name.lower()
            if "规范" in name_lower or "标准" in name_lower:
                doc_type = "standard"
            elif "报告" in name_lower:
                doc_type = "example_report"

            try:
                chunks = await mm_chunker.chunk_document(
                    str(pdf_file),
                    file_type="pdf",
                    metadata={
                        "document_type": doc_type,
                        "source_file": pdf_file.name,
                    },
                )

                text_count = 0
                image_count = 0
                dropped_images = 0
                kept_chunks = []

                for chunk in chunks:
                    if chunk.modality == "text" and not chunk.embedding:
                        chunk.embedding = await mm_embedder.embed_text(chunk.text)
                        text_count += 1
                        kept_chunks.append(chunk)
                    elif chunk.modality == "image" and chunk.image_data and not chunk.embedding:
                        # Save image to temp dir, describe, embed
                        temp_dir = str(Path(__file__).resolve().parent.parent / "storage" / "extracted_imgs")
                        os.makedirs(temp_dir, exist_ok=True)
                        img_path = mm_chunker.save_image_data(
                            chunk.image_data, temp_dir,
                            pdf_file.stem, image_count
                        )
                        # Borderline images (needs_vl) go through the gated
                        # judge+describe call; a DROP verdict returns None.
                        mm_result = await mm_embedder.embed_image(
                            img_path, gated=getattr(chunk, "needs_vl", False)
                        )
                        if mm_result is None:
                            dropped_images += 1
                            continue
                        chunk.embedding = mm_result.embedding
                        chunk.text = mm_result.text  # Image description
                        chunk.image_description = mm_result.text
                        chunk.image_path = img_path
                        image_count += 1
                        kept_chunks.append(chunk)
                    else:
                        kept_chunks.append(chunk)

                doc_prefix = f"pdf_{pdf_file.stem}"
                count = await mm_store.add_multimodal_chunks(
                    kept_chunks,
                    doc_id_prefix=doc_prefix,
                    collection_name="knowledge_base",
                )
                print(f"  ✅ {pdf_file.name}: {count} chunks (text: {text_count}, images: {image_count}, dropped: {dropped_images})")

            except Exception as e:
                print(f"  ❌ Failed to index {pdf_file.name}: {e}")
                import traceback
                traceback.print_exc()
    else:
        print(f"  ⚠️ knowledge_docs directory not found: {knowledge_dir}")

    # ── Step 3: Report final stats ──
    print("\n" + "=" * 60)
    print("✅ Re-index complete!")
    try:
        stats = mm_store.get_collection_stats("knowledge_base")
        print(f"\n📊 Final collection stats:")
        print(f"   Total chunks: {stats.get('total', 0)}")
        print(f"   Text chunks:  {stats.get('text', 0)}")
        print(f"   Image chunks: {stats.get('image', 0)}")
        print(f"   Table chunks: {stats.get('table', 0)}")
    except Exception as e:
        print(f"  ⚠️ Could not get stats: {e}")

    # Cleanup
    mm_embedder.clear_cache()
    print("\nDone. ✨")


if __name__ == "__main__":
    asyncio.run(reindex_all())
