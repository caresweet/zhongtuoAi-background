"""Hybrid Retriever — BM25 keyword + vector semantic + RRF fusion.

Combines keyword matching (BM25) with vector similarity (ChromaDB) for
better retrieval quality. Uses Reciprocal Rank Fusion (RRF) to merge results.
"""

import math
import re
import logging
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class BM25Retriever:
    """Simple BM25 keyword search over document chunks.

    No external deps — pure Python implementation of BM25 scoring.
    Pre-computes document statistics for fast retrieval.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs: List[str] = []         # Document texts
        self._doc_ids: List[str] = []       # Document IDs
        self._doc_terms: List[Dict[str, int]] = []  # Term frequency per doc
        self._idf: Dict[str, float] = {}    # IDF per term
        self._avg_dl: float = 0             # Average document length
        self._total_docs: int = 0

    def index(self, docs: List[Tuple[str, str]]):
        """Index documents for BM25 search.

        Args:
            docs: List of (doc_id, text) tuples
        """
        self._docs = []
        self._doc_ids = []
        self._doc_terms = []
        df = defaultdict(int)  # Document frequency
        total_length = 0

        for doc_id, text in docs:
            self._doc_ids.append(doc_id)
            self._docs.append(text)
            # Tokenize: Chinese + English
            tokens = self._tokenize(text)
            tf = defaultdict(int)
            for t in tokens:
                tf[t] += 1
            self._doc_terms.append(dict(tf))
            total_length += len(tokens)
            for t in set(tokens):
                df[t] += 1

        self._total_docs = len(docs)
        self._avg_dl = total_length / max(self._total_docs, 1)

        # Compute IDF
        for term, freq in df.items():
            self._idf[term] = math.log(1 + (self._total_docs - freq + 0.5) / (freq + 0.5))

    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenizer: split Chinese characters individually, English words as tokens."""
        tokens = []
        # Chinese: split each character
        for ch in text:
            if '一' <= ch <= '鿿':
                tokens.append(ch)
        # English/numbers: split by non-alphanumeric
        eng_tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
        tokens.extend(eng_tokens)
        # Also add bigrams for Chinese (2-char phrases)
        ch_chars = [ch for ch in text if '一' <= ch <= '鿿']
        for i in range(len(ch_chars) - 1):
            tokens.append(ch_chars[i] + ch_chars[i + 1])
        return tokens

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Search for documents matching the query.

        Returns: List of (doc_id, score) sorted by score descending.
        """
        if not self._docs:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = []
        for i in range(self._total_docs):
            score = 0.0
            dl = sum(self._doc_terms[i].values())
            for token in query_tokens:
                if token not in self._idf:
                    continue
                tf = self._doc_terms[i].get(token, 0)
                idf = self._idf[token]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / max(self._avg_dl, 1))
                score += idf * numerator / max(denominator, 0.001)
            if score > 0:
                scores.append((self._doc_ids[i], score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


def reciprocal_rank_fusion(
    keyword_results: List[Tuple[str, float]],
    vector_results: List[Tuple[str, float]],
    k: int = 60,
    top_k: int = 20,
) -> List[Tuple[str, float]]:
    """Merge keyword and vector results using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) for each result list.
    This naturally harmonizes scores from different distributions.
    """
    scores = defaultdict(float)

    for rank, (doc_id, _) in enumerate(keyword_results):
        scores[doc_id] += 1.0 / (k + rank + 1)

    for rank, (doc_id, _) in enumerate(vector_results):
        scores[doc_id] += 1.0 / (k + rank + 1)

    # Sort by fused score
    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged[:top_k]


class HybridRetrieverService:
    """Combines BM25 keyword search with ChromaDB vector search via RRF."""

    def __init__(self):
        self._bm25 = BM25Retriever()
        self._indexed = False
        self._doc_map: Dict[str, Dict[str, Any]] = {}  # doc_id → document metadata

    def build_index(self, documents: List[Dict[str, Any]]):
        """Build/re-build the BM25 index from document chunks.

        Args:
            documents: List of {id, text, metadata, ...} dicts
        """
        docs = []
        self._doc_map = {}
        for d in documents:
            doc_id = d.get("id", str(hash(d.get("text", ""))))
            text = d.get("text", "")
            docs.append((doc_id, text))
            self._doc_map[doc_id] = d
        self._bm25.index(docs)
        self._indexed = True
        logger.info(f"BM25 index built: {len(docs)} documents")

    def search_keyword(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """BM25 keyword search."""
        if not self._indexed:
            return []
        results = self._bm25.search(query, top_k=top_k)
        return [self._doc_map.get(doc_id, {"id": doc_id, "score": score})
                for doc_id, score in results]

    def search_hybrid(
        self,
        query: str,
        vector_results: List[Dict[str, Any]],
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """Hybrid search: BM25 + vector with RRF fusion.

        Args:
            query: Search query
            vector_results: Results from ChromaDB vector search
            top_k: Number of results to return

        Returns: Merged and re-ranked document list
        """
        # Keyword search
        kw_results = self._bm25.search(query, top_k=top_k * 2) if self._indexed else []

        # Vector results as (id, score) tuples
        vec_results = []
        for r in vector_results:
            rid = r.get("id", str(r.get("metadata", {}).get("chunk_id", hash(r.get("content", "")))))
            score = r.get("score", r.get("distance", 0))
            if isinstance(score, (int, float)):
                vec_results.append((rid, float(score)))

        # RRF fusion
        fused = reciprocal_rank_fusion(kw_results, vec_results, top_k=top_k)

        # Map back to full documents
        results = []
        seen = set()
        for doc_id, score in fused:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            doc = self._doc_map.get(doc_id)
            if doc:
                doc = dict(doc)
                doc["hybrid_score"] = score
                doc["source"] = "hybrid"
                results.append(doc)
            else:
                # From vector results
                for vr in vector_results:
                    vrid = vr.get("id", str(vr.get("metadata", {}).get("chunk_id", "")))
                    if vrid == doc_id:
                        vr = dict(vr)
                        vr["hybrid_score"] = score
                        vr["source"] = "hybrid"
                        results.append(vr)
                        break

        # Add top keyword results that weren't in vector results
        for doc_id, score in kw_results[:5]:
            if doc_id not in seen:
                doc = self._doc_map.get(doc_id)
                if doc:
                    doc = dict(doc)
                    doc["hybrid_score"] = score
                    doc["source"] = "keyword"
                    results.append(doc)

        return results[:top_k]


# Singleton
hybrid_retriever = HybridRetrieverService()
