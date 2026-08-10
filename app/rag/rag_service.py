"""Shared RAG service — singleton to avoid per-chapter client creation.

Previously, each ChapterAgent + KnowledgeAgent created new EmbedderService,
VectorStoreService, and RetrieverService instances (~20 per report). This
singleton shares one set of clients across all agents.
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class RAGService:
    """Singleton RAG service with lazy initialization and caching."""

    _instance: Optional["RAGService"] = None

    def __init__(self):
        self._embedder = None
        self._vector_store = None
        self._retriever = None
        self._cache = None

    @classmethod
    def get_instance(cls) -> "RAGService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def embedder(self):
        if self._embedder is None:
            from app.rag.embedder import EmbedderService
            self._embedder = EmbedderService()
        return self._embedder

    @property
    def vector_store(self):
        if self._vector_store is None:
            from app.rag.vector_store import VectorStoreService
            self._vector_store = VectorStoreService()
        return self._vector_store

    @property
    def retriever(self):
        if self._retriever is None:
            from app.rag.retriever import RetrieverService
            self._retriever = RetrieverService(self.embedder, self.vector_store)
        return self._retriever

    @property
    def cache(self):
        if self._cache is None:
            from app.rag.reusable_cache import ReusableCache
            from app.config import settings
            db_path = str(settings.DATA_DIR / "knowledge_base.db")
            self._cache = ReusableCache(db_path=db_path)
        return self._cache

    @property
    def hybrid(self):
        """Lazy-load hybrid retriever and build index from knowledge base."""
        from app.rag.hybrid_retriever import hybrid_retriever
        if not hybrid_retriever._indexed:
            try:
                from app.database.knowledge_db import get_knowledge_db
                import asyncio
                # Get all active knowledge chunks
                db_path = None
                try:
                    from app.config import settings
                    db_path = str(settings.DATA_DIR / "knowledge_base.db")
                except:
                    pass
                if db_path:
                    import sqlite3
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    query = (
                        "SELECT id, cleaned_text, retrieval_text, title FROM knowledge_documents "
                        "WHERE is_active=1 AND (cleaned_text IS NOT NULL AND cleaned_text != '' "
                        "OR retrieval_text IS NOT NULL AND retrieval_text != '')"
                    )
                    rows = conn.execute(query).fetchall()
                    conn.close()
                    docs = []
                    for row in rows:
                        text = (row["cleaned_text"] or row["retrieval_text"] or "").strip()
                        if text:
                            chunks = [text[i:i+500] for i in range(0, len(text), 500)]
                            for ci, chunk in enumerate(chunks):
                                docs.append({
                                    "id": f"doc_{row['id']}_chunk{ci}",
                                    "text": chunk,
                                    "document_id": row["id"],
                                    "metadata": f'{{"title": "{row["title"] or ""}"}}',
                                })
                    if docs:
                        hybrid_retriever.build_index(docs)
                        logger.info(f"Hybrid BM25 index built: {len(docs)} chunks from {len(rows)} docs")
            except Exception as e:
                logger.warning(f"Hybrid index build skipped: {e}")
        return hybrid_retriever

    async def retrieve_hybrid(
        self, query: str, session_id: str, n_results: int = 20, domain: str = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid retrieval: BM25 keyword + vector + RRF fusion."""
        # Get vector results
        vector_results = await self.retrieve_with_query(
            query=query, session_id=session_id, n_results=n_results, domain=domain,
        )
        # Fuse with BM25 keyword search
        return self.hybrid.search_hybrid(query, vector_results, top_k=min(n_results, 20))

    async def retrieve_for_chapter(
        self, chapter_number: int, session_id: str,
        project_context: str = "", n_results: int = 15,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve RAG context for a chapter, scoped to a report domain.

        Token-saving split:
          - domain-general items (regulation/standard/example) are cached by
            (domain, chapter) — reusable across every project in that domain;
          - project-specific session items are always fetched live.
        The two are merged, then formatted. This memoizes the reusable half so
        repeated reports in the same domain skip the embedding + vector search
        for boilerplate context.
        """
        cache = self.cache
        # 🔴 Cache key now includes n_results so different result counts get different cache entries
        key = cache.make_key(domain or "any", chapter_number, f"chapter::{chapter_number}::n{n_results}")

        general_items = cache.get(key)
        if general_items is None:
            general_items = await self.retriever.retrieve_general_for_chapter(
                chapter_number=chapter_number,
                project_context=project_context,
                n_results=n_results,
                domain=domain,
            )
            cache.put(key, general_items, domain=domain or "any", chapter=chapter_number)

        # Session (project) material is never cached — fetch fresh every time.
        session_items = await self.retriever.retrieve_session_items(
            session_id=session_id,
            chapter_number=chapter_number,
            project_context=project_context,
        )

        merged = self.retriever._merge_results(general_items, session_items)
        return self.retriever.format_items(merged, chapter_number)

    async def retrieve_with_query(
        self, query: str, session_id: str, n_results: int = 5,
        domain: Optional[str] = None,
    ):
        """General-purpose retrieval, optionally scoped to a report domain."""
        return await self.retriever.retrieve_with_query(
            query=query, session_id=session_id, n_results=n_results,
            domain=domain,
        )

    @property
    def cache_stats(self) -> Dict[str, Any]:
        """Expose reusable-cache hit/miss stats for observability."""
        return self.cache.stats


# Module-level singleton
rag_service = RAGService.get_instance()
