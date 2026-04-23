"""Baseline vector-similarity retriever."""

from __future__ import annotations

import time

from ..services.embedding import EmbeddingService
from ..services.vector_store import VectorStore
from .base import BaseRetriever, RetrievedChunk


class VanillaRetriever(BaseRetriever):
    """Pure cosine similarity vector search. Baseline strategy."""

    def __init__(self, embedding_service: EmbeddingService, vector_store: VectorStore) -> None:
        """Create a vanilla retriever from embedding and vector-store services."""
        self.embed = embedding_service
        self.vs = vector_store

    async def retrieve(
        self,
        query: str,
        project_id: str,
        tenant_id: str,
        top_k: int = 5,
    ) -> tuple[list[RetrievedChunk], float]:
        """Embed a query and retrieve nearest chunks from Qdrant."""
        started_at = time.perf_counter()
        query_embedding = await self.embed.embed_text(query)
        collection_name = self.vs.get_collection_name(project_id, tenant_id)
        results = await self.vs.search(
            collection_name,
            query_embedding,
            top_k,
            tenant_id=tenant_id,
            project_id=project_id,
        )
        chunks = [
            RetrievedChunk(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                document_name=result.document_name,
                text=result.text,
                score=result.score,
                page_number=result.page_number,
                chunk_index=result.chunk_index,
                strategy_used=self.get_strategy_name(),
            )
            for result in results
        ]
        return chunks, (time.perf_counter() - started_at) * 1000

    def get_strategy_name(self) -> str:
        """Return the strategy identifier string."""
        return "vanilla"

