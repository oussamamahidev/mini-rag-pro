"""HyDE retrieval strategy."""

from __future__ import annotations

import time

from ..services.llm import LLMService
from .base import BaseRetriever, RetrievedChunk


class HyDERetriever(BaseRetriever):
    """Generate a hypothetical answer and retrieve against that expanded text."""

    def __init__(self, base_retriever: BaseRetriever, llm_service: LLMService) -> None:
        self.base = base_retriever
        self.llm = llm_service

    async def retrieve(
        self,
        query: str,
        project_id: str,
        tenant_id: str,
        top_k: int = 5,
    ) -> tuple[list[RetrievedChunk], float]:
        """Run HyDE query expansion before semantic retrieval."""
        started_at = time.perf_counter()
        hypothesis = await self.llm.generate_hypothesis(query)
        retrieval_query = f"{query}\n\nHypothetical answer:\n{hypothesis}" if hypothesis else query
        chunks, _ = await self.base.retrieve(retrieval_query, project_id, tenant_id, top_k=top_k)
        return [with_hyde_strategy(chunk) for chunk in chunks], (time.perf_counter() - started_at) * 1000

    def get_strategy_name(self) -> str:
        """Return the strategy identifier string."""
        return "hyde"


def with_hyde_strategy(chunk: RetrievedChunk) -> RetrievedChunk:
    """Return a chunk copy labeled as HyDE output."""
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        text=chunk.text,
        score=chunk.score,
        page_number=chunk.page_number,
        chunk_index=chunk.chunk_index,
        strategy_used="hyde",
    )
