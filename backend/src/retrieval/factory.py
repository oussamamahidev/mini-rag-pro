"""Retriever singleton factory."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from redis.asyncio import Redis

from ..services.embedding import EmbeddingService
from ..services.llm import LLMService
from ..services.vector_store import VectorStore
from .base import BaseRetriever
from .hybrid import HybridRetriever
from .hyde import HyDERetriever
from .reranker import RerankRetriever
from .vanilla import VanillaRetriever

_retrievers: dict[str, BaseRetriever] = {}


def initialize_retrievers(
    *,
    embedding_svc: EmbeddingService,
    vector_store: VectorStore,
    redis_client: Redis | None,
    db: Any,
    cross_encoder_model: Any | None,
    llm_svc: LLMService,
) -> dict[str, BaseRetriever]:
    """Initialize all retrieval strategy singletons."""
    vanilla = VanillaRetriever(embedding_svc, vector_store)
    hybrid = HybridRetriever(embedding_svc, vector_store, db, redis_client)
    rerank = RerankRetriever(hybrid, cross_encoder_model)
    hyde = HyDERetriever(vanilla, llm_svc)

    _retrievers.clear()
    _retrievers.update(
        {
            "vanilla": vanilla,
            "hybrid": hybrid,
            "rerank": rerank,
            "hyde": hyde,
        }
    )
    return dict(_retrievers)


def get_retriever(strategy: str | None) -> BaseRetriever:
    """Return the retriever for a strategy, falling back to hybrid."""
    if not _retrievers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retrievers are not initialized",
        )
    normalized = (strategy or "hybrid").strip().lower()
    return _retrievers.get(normalized) or _retrievers["hybrid"]


def initialized_strategies() -> list[str]:
    """Return initialized strategy names for diagnostics."""
    return sorted(_retrievers)
