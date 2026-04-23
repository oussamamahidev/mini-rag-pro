"""Cross-encoder reranking retrieval strategy."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .base import BaseRetriever, RetrievedChunk


class RerankRetriever(BaseRetriever):
    """Retrieve a broad semantic candidate set and rerank with a cross-encoder."""

    def __init__(self, base_retriever: BaseRetriever, cross_encoder_model: Any | None) -> None:
        self.base = base_retriever
        self.cross_encoder = cross_encoder_model

    async def retrieve(
        self,
        query: str,
        project_id: str,
        tenant_id: str,
        top_k: int = 5,
    ) -> tuple[list[RetrievedChunk], float]:
        """Retrieve candidates and rerank them by query/chunk relevance."""
        started_at = time.perf_counter()
        candidates, _ = await self.base.retrieve(query, project_id, tenant_id, top_k=max(top_k * 4, top_k))
        if not candidates or self.cross_encoder is None:
            return [with_strategy(chunk, self.get_strategy_name()) for chunk in candidates[:top_k]], elapsed(started_at)

        pairs = [(query, chunk.text) for chunk in candidates]
        scores = await asyncio.to_thread(self.cross_encoder.predict, pairs)
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda item: float(item[1]), reverse=True)[:top_k]
        max_score = max((float(score) for _, score in ranked), default=1.0)
        min_score = min((float(score) for _, score in ranked), default=0.0)
        span = max(max_score - min_score, 1e-9)

        chunks = []
        for chunk, score in ranked:
            normalized = (float(score) - min_score) / span if len(ranked) > 1 else 1.0
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_name=chunk.document_name,
                    text=chunk.text,
                    score=round(max(0.0, min(1.0, normalized)), 4),
                    page_number=chunk.page_number,
                    chunk_index=chunk.chunk_index,
                    strategy_used=self.get_strategy_name(),
                )
            )
        return chunks, elapsed(started_at)

    def get_strategy_name(self) -> str:
        """Return the strategy identifier string."""
        return "rerank"


def with_strategy(chunk: RetrievedChunk, strategy: str) -> RetrievedChunk:
    """Return a chunk copy with the requested strategy label."""
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        text=chunk.text,
        score=chunk.score,
        page_number=chunk.page_number,
        chunk_index=chunk.chunk_index,
        strategy_used=strategy,
    )


def elapsed(started_at: float) -> float:
    """Return elapsed milliseconds."""
    return (time.perf_counter() - started_at) * 1000
