"""Bidding RAG — thin wrapper over rag_service for bidding-domain queries.

Used by master.py Bidding Question Handler. Delegates to rag_service with
bidding domain scoping to keep the bidding code path self-contained.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional

from app.rag.rag_service import rag_service

logger = logging.getLogger(__name__)


class BiddingRAG:
    """Bidding-domain RAG facade that delegates to the shared rag_service."""

    async def search(
        self,
        query: str,
        n_results: int = 5,
        session_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Search bidding knowledge base."""
        try:
            results = await rag_service.retrieve_with_query(
                query=query,
                session_id=session_id or "__bidding__",
                n_results=n_results,
                domain="bidding",
            )
            return results or []
        except Exception as e:
            logger.warning(f"BiddingRAG search failed: {e}")
            return []

    async def retrieve_for_chapter(
        self,
        chapter_number: int,
        session_id: str = "",
        project_context: str = "",
        n_results: int = 8,
    ) -> Dict[str, Any]:
        """Retrieve bidding knowledge for a specific chapter."""
        try:
            return await rag_service.retrieve_for_chapter(
                chapter_number=chapter_number,
                session_id=session_id or "__bidding__",
                project_context=project_context,
                n_results=n_results,
                domain="bidding",
            )
        except Exception as e:
            logger.warning(f"BiddingRAG chapter retrieval failed: {e}")
            return {
                "chapter_context": "",
                "local_regulation_context": "",
                "example_context": "",
                "project_context": "",
                "sources": [],
            }


# Singleton
bidding_rag = BiddingRAG()
