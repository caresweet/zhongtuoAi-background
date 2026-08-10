"""RAG (Retrieval-Augmented Generation) module for report generation.

Provides:
- Chinese legal/technical document chunking
- Embedding model integration
- Chroma vector store management
- Multi-strategy retrieval
"""

from app.rag.chunker import ChineseReportChunker, Chunk, ChunkMetadata
from app.rag.embedder import EmbedderService
from app.rag.vector_store import VectorStoreService
from app.rag.retriever import RetrieverService

# Multi-modal modules — optional, may not be available
try:
    from app.rag.multimodal_embedder import MultiModalEmbedder, MultiModalEmbedding
except ImportError:
    MultiModalEmbedder = None
    MultiModalEmbedding = None

try:
    from app.rag.multimodal_chunker import MultiModalChunker, MultiModalChunk
except ImportError:
    MultiModalChunker = None
    MultiModalChunk = None

try:
    from app.rag.multimodal_vector_store import MultiModalVectorStore
except ImportError:
    MultiModalVectorStore = None

try:
    from app.rag.cross_modal_retriever import CrossModalRetriever, MultiModalRetrievalResult
except ImportError:
    CrossModalRetriever = None
    MultiModalRetrievalResult = None

__all__ = [
    # Text RAG
    "ChineseReportChunker",
    "Chunk",
    "ChunkMetadata",
    "EmbedderService",
    "VectorStoreService",
    "RetrieverService",
    # Multi-modal RAG (optional)
    "MultiModalEmbedder",
    "MultiModalEmbedding",
    "MultiModalChunker",
    "MultiModalChunk",
    "MultiModalVectorStore",
    "CrossModalRetriever",
    "MultiModalRetrievalResult",
]
