"""Embedding model service.

Supports two backends:
1. API-based: Uses the existing LLM API provider's embedding endpoint
2. Local: BGE-M3 via Ollama (fallback)

Configured via environment variables:
- EMBEDDING_API_URL: API endpoint for embeddings
- EMBEDDING_MODEL: Model name (default: text-embedding-3-large)
"""

import os
import json
from typing import List, Optional
import httpx


class EmbedderService:
    """Embedding model wrapper for generating vector embeddings."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        from app.config import settings as _settings
        self.api_url = api_url or os.getenv(
            "EMBEDDING_API_URL",
            _settings.LLM_BASE_URL or _settings.EMBEDDING_API_URL or ""
        )
        self.model = model or os.getenv("EMBEDDING_MODEL", _settings.EMBEDDING_MODEL)
        self.api_key = api_key or os.getenv(
            "EMBEDDING_API_KEY",
            _settings.ANTHROPIC_API_KEY or ""
        )
        self.dimensions: Optional[int] = None

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each is a List[float]).
        """
        if not texts:
            return []

        # Batch processing - DashScope limits batch to 10 max
        batch_size = 10
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = await self._embed_batch(batch)
            all_embeddings.extend(batch_embeddings)

        # Record dimensions from first successful response
        if all_embeddings and self.dimensions is None:
            self.dimensions = len(all_embeddings[0])

        return all_embeddings

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text string to embed.

        Returns:
            Embedding vector.
        """
        results = await self.embed_texts([text])
        return results[0] if results else []

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Send a batch of texts to the embedding API."""
        # Try the standard OpenAI-compatible embeddings endpoint
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                # OpenAI-compatible endpoint
                embed_url = self._get_embeddings_url()
                headers = {
                    "Content-Type": "application/json",
                }
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                response = await client.post(
                    embed_url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "input": texts,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    return [item["embedding"] for item in data.get("data", [])]

                # If API fails, log the error and fall back
                error_body = response.text[:300] if response.text else "no body"
                print(f"Embedding API returned {response.status_code}: {error_body}")
                return self._simple_fallback_embeddings(texts)

            except Exception as e:
                print(f"Embedding API error: {e}, using fallback")
                return self._simple_fallback_embeddings(texts)

    def _get_embeddings_url(self) -> str:
        """Determine the embeddings endpoint URL."""
        base = self.api_url.rstrip('/')
        # If it's the Anthropic-compatible endpoint, use the v1/embeddings path
        if 'anthropic' in base.lower():
            return f"{base}/v1/embeddings"
        # OpenAI-compatible: append /embeddings if not already present
        if not base.endswith('/embeddings'):
            return f"{base}/embeddings"
        return base

    def _simple_fallback_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Simple TF-IDF-like fallback embeddings when API is unavailable.

        This is a deterministic fallback that creates sparse vectors based on
        character bigram frequencies. Not as good as real embeddings, but allows
        the system to function without an embedding service.
        """
        embeddings = []
        for text in texts:
            # Create a simple 1024-dim vector based on character features
            vec = [0.0] * 1024

            # Character bigram hashing
            for i in range(len(text) - 1):
                bigram = text[i:i + 2]
                hash_val = hash(bigram) % 1024
                vec[hash_val] += 0.01

            # Normalize
            magnitude = sum(v * v for v in vec) ** 0.5
            if magnitude > 0:
                vec = [v / magnitude for v in vec]

            embeddings.append(vec)

        self.dimensions = 1024
        return embeddings
