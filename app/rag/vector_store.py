"""Chroma vector store integration for RAG.

Manages:
- Global persistent collection for regulations, standards, and example reports
- Session-scoped temporary collections for user-uploaded project materials
"""

import os
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings


class VectorStoreService:
    """Manages Chroma vector database collections for RAG."""

    GLOBAL_COLLECTION = "knowledge_base"  # Regulations, standards, examples
    SESSION_PREFIX = "session_"           # Per-session project materials

    def __init__(self, persist_dir: Optional[str] = None):
        if persist_dir is None:
            persist_dir = os.getenv(
                "CHROMA_PERSIST_DIR",
                str(Path(__file__).resolve().parent.parent.parent / "data" / "chroma")
            )

        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    @property
    def client(self) -> chromadb.PersistentClient:
        return self._client

    # ---- Collection Management ----

    def get_or_create_collection(self, name: str, description: str = "") -> chromadb.Collection:
        """Get or create any named collection."""
        return self._client.get_or_create_collection(
            name=name,
            metadata={"description": description or name},
        )

    def get_or_create_global_collection(self) -> chromadb.Collection:
        """Get or create the global knowledge base collection."""
        return self._client.get_or_create_collection(
            name=self.GLOBAL_COLLECTION,
            metadata={"description": "Global knowledge base: regulations, standards, example reports"},
        )

    def get_bidding_collection(self) -> chromadb.Collection:
        """Get or create the bidding knowledge collection."""
        return self.get_or_create_collection(
            "bidding_knowledge",
            "Bidding documents: announcements, evaluation reports, award notices, templates"
        )

    def create_session_collection(self, session_id: str) -> chromadb.Collection:
        """Create a session-scoped collection for project materials."""
        collection_name = f"{self.SESSION_PREFIX}{session_id}"
        # Delete if exists (clean restart)
        try:
            self._client.delete_collection(collection_name)
        except Exception:
            pass
        return self._client.create_collection(
            name=collection_name,
            metadata={"session_id": session_id, "type": "project_materials"},
        )

    def get_session_collection(self, session_id: str) -> Optional[chromadb.Collection]:
        """Get an existing session collection."""
        collection_name = f"{self.SESSION_PREFIX}{session_id}"
        try:
            return self._client.get_collection(collection_name)
        except Exception:
            return None

    def delete_session_collection(self, session_id: str) -> bool:
        """Delete a session collection."""
        collection_name = f"{self.SESSION_PREFIX}{session_id}"
        try:
            self._client.delete_collection(collection_name)
            return True
        except Exception:
            return False

    def list_collections(self) -> List[str]:
        """List all collection names."""
        return self._client.list_collections()

    def delete_collection(self, name: str) -> bool:
        """Delete a collection by name."""
        try:
            self._client.delete_collection(name)
            return True
        except Exception:
            return False

    # ---- Document Operations ----

    def add_documents(
        self,
        collection: chromadb.Collection,
        ids: List[str],
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Add documents with embeddings to a collection.

        Args:
            collection: Chroma collection.
            ids: Unique IDs for each document chunk.
            documents: Text content of each chunk.
            embeddings: Embedding vectors.
            metadatas: Optional metadata dicts.
        """
        if not ids:
            return

        # Sanitize metadata — ChromaDB only accepts str, int, float, bool
        sanitized = []
        for meta in (metadatas or [{}] * len(ids)):
            clean = {}
            for k, v in meta.items():
                if v is None:
                    clean[k] = ""  # convert None to empty string
                elif isinstance(v, (str, int, float, bool)):
                    clean[k] = v
                else:
                    clean[k] = str(v)  # convert anything else to string
            sanitized.append(clean)

        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=sanitized,
        )

    def add_document_to_global(
        self,
        chunks: List[Dict[str, Any]],
        doc_id_prefix: str,
    ) -> int:
        """Add document chunks to the global collection.

        Args:
            chunks: List of chunk dicts with 'text' and 'metadata' keys.
            doc_id_prefix: Prefix for chunk IDs (e.g., "reg_1").

        Returns:
            Number of chunks added.
        """
        collection = self.get_or_create_global_collection()

        ids = [f"{doc_id_prefix}_chunk_{i}" for i in range(len(chunks))]
        documents = [c["text"] for c in chunks]
        embeddings = [c["embedding"] for c in chunks]
        metadatas = [c.get("metadata", {}) for c in chunks]

        self.add_documents(collection, ids, documents, embeddings, metadatas)
        return len(ids)

    def query(
        self,
        collection: chromadb.Collection,
        query_embedding: List[float],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Query a collection for similar documents.

        Args:
            collection: Chroma collection.
            query_embedding: Query embedding vector.
            n_results: Number of results to return.
            where: Optional metadata filter.

        Returns:
            Dict with 'ids', 'documents', 'metadatas', 'distances'.
        """
        kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
        }
        if where:
            kwargs["where"] = where

        return collection.query(**kwargs)

    def query_global(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        document_type: Optional[str] = None,
        chapter_number: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query the global collection with optional filters.

        Args:
            query_embedding: Query embedding vector.
            n_results: Number of results.
            document_type: Filter by document_type (regulation/standard/example_report).
            chapter_number: Filter by chapter_number (1-10).
            domain: Filter by report domain (stability, bidding, ...). None = all
                domains (backward-compatible with un-tagged legacy chunks).

        Returns:
            Query results dict.
        """
        collection = self.get_or_create_global_collection()

        where = None
        conditions = []
        if document_type:
            conditions.append({"document_type": document_type})
        if chapter_number is not None:
            conditions.append({"chapter_number": chapter_number})
        if domain:
            conditions.append({"domain": domain})

        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        return self.query(collection, query_embedding, n_results, where)

    def query_multi_collection(
        self,
        query_embedding: List[float],
        collections: List[chromadb.Collection],
        n_results_per_collection: int = 3,
    ) -> List[Dict[str, Any]]:
        """Query multiple collections and combine results.

        Args:
            query_embedding: Query embedding vector.
            collections: List of collections to query.
            n_results_per_collection: Results per collection.

        Returns:
            Combined list of result items sorted by relevance (distance).
        """
        all_results = []
        for col in collections:
            result = self.query(col, query_embedding, n_results_per_collection)
            for i in range(len(result.get("ids", [[]])[0])):
                all_results.append({
                    "id": result["ids"][0][i],
                    "document": result["documents"][0][i],
                    "metadata": result["metadatas"][0][i] if result.get("metadatas") else {},
                    "distance": result["distances"][0][i],
                    "collection": col.name,
                })

        # Sort by distance (lower = more similar)
        all_results.sort(key=lambda x: x["distance"])
        return all_results

    # ---- Document Removal ----

    def remove_by_prefix(self, collection: chromadb.Collection, id_prefix: str) -> int:
        """Remove all documents with IDs starting with a prefix."""
        existing = collection.get()
        if not existing.get("ids"):
            return 0

        to_delete = [id for id in existing["ids"] if id.startswith(id_prefix)]
        if to_delete:
            collection.delete(ids=to_delete)
        return len(to_delete)

    def get_document_count(self, collection: chromadb.Collection) -> int:
        """Get the number of documents in a collection."""
        try:
            return collection.count()
        except Exception:
            return 0
