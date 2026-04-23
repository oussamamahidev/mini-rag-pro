"""Hybrid semantic and BM25 retrieval strategy."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from rank_bm25 import BM25Okapi
from redis.asyncio import Redis

from ..logging_config import get_logger
from ..services.embedding import EmbeddingService
from ..services.vector_store import VectorStore
from .base import BaseRetriever, RetrievedChunk

logger = get_logger(__name__)

BM25_CACHE_TTL_SECONDS = 3600


class HybridRetriever(BaseRetriever):
    """Combine vector search recall with BM25 keyword matching."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        db: Any,
        redis_client: Redis | None = None,
    ) -> None:
        self.embed = embedding_service
        self.vs = vector_store
        self.db = db
        self.redis = redis_client

    async def retrieve(
        self,
        query: str,
        project_id: str,
        tenant_id: str,
        top_k: int = 5,
    ) -> tuple[list[RetrievedChunk], float]:
        """Retrieve chunks using semantic search and BM25, then fuse rankings."""
        started_at = time.perf_counter()
        candidate_k = max(top_k * 3, top_k)

        query_embedding = await self.embed.embed_text(query)
        collection_name = self.vs.get_collection_name(project_id, tenant_id)
        vector_results = await self.vs.search(
            collection_name,
            query_embedding,
            candidate_k,
            tenant_id=tenant_id,
            project_id=project_id,
            score_threshold=0.0,
        )

        bm25_chunks = await self._bm25_search(query, project_id, tenant_id, candidate_k)
        fused = reciprocal_rank_fusion(vector_results, bm25_chunks, top_k, self.get_strategy_name())
        return fused, (time.perf_counter() - started_at) * 1000

    async def _bm25_search(
        self,
        query: str,
        project_id: str,
        tenant_id: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Build a lightweight BM25 index from cached MongoDB chunk text."""
        rows = await self._load_project_chunks(project_id, tenant_id)
        if not rows:
            return []

        corpus = [tokenize(row["text"]) for row in rows]
        query_tokens = tokenize(query)
        if not query_tokens or not any(corpus):
            return []

        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)
        ranked_indices = sorted(range(len(rows)), key=lambda index: scores[index], reverse=True)[:top_k]
        max_score = max((float(scores[index]) for index in ranked_indices), default=0.0)
        normalized_by = max(max_score, 1e-9)

        results = []
        for index in ranked_indices:
            if float(scores[index]) <= 0:
                continue
            row = rows[index]
            results.append(
                RetrievedChunk(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    document_name=row.get("document_name") or "Document",
                    text=row["text"],
                    score=min(1.0, float(scores[index]) / normalized_by),
                    page_number=row.get("page_number"),
                    chunk_index=int(row.get("chunk_index") or 0),
                    strategy_used=self.get_strategy_name(),
                )
            )
        return results

    async def _load_project_chunks(self, project_id: str, tenant_id: str) -> list[dict[str, Any]]:
        """Load chunk rows from Redis cache or MongoDB."""
        cache_key = f"bm25:{project_id}"
        if self.redis is not None:
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    rows = json.loads(cached)
                    if isinstance(rows, list):
                        return rows
            except Exception as exc:
                logger.warning("bm25 cache read failed project_id=%s error=%s", project_id, exc)

        document_rows = await self.db.documents.find(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "is_deleted": {"$ne": True},
            },
            {"_id": 0, "id": 1, "original_filename": 1},
        ).to_list(length=10000)
        document_names = {row["id"]: row.get("original_filename", "Document") for row in document_rows}

        cursor = (
            self.db.chunks.find(
                {"tenant_id": tenant_id, "project_id": project_id},
                {"_id": 0, "id": 1, "document_id": 1, "text": 1, "page_number": 1, "chunk_index": 1},
            )
            .sort([("document_id", 1), ("chunk_index", 1)])
            .limit(20000)
        )
        rows = []
        async for chunk in cursor:
            rows.append(
                {
                    "chunk_id": chunk["id"],
                    "document_id": chunk["document_id"],
                    "document_name": document_names.get(chunk["document_id"], "Document"),
                    "text": chunk["text"],
                    "page_number": chunk.get("page_number"),
                    "chunk_index": chunk.get("chunk_index", 0),
                }
            )

        if self.redis is not None and rows:
            try:
                await self.redis.setex(cache_key, BM25_CACHE_TTL_SECONDS, json.dumps(rows, separators=(",", ":")))
            except Exception as exc:
                logger.warning("bm25 cache write failed project_id=%s error=%s", project_id, exc)
        return rows

    def get_strategy_name(self) -> str:
        """Return the strategy identifier string."""
        return "hybrid"


def reciprocal_rank_fusion(
    vector_results: list[Any],
    bm25_results: list[RetrievedChunk],
    top_k: int,
    strategy_name: str,
) -> list[RetrievedChunk]:
    """Fuse ranked vector and BM25 candidates using reciprocal rank fusion."""
    scores: dict[str, float] = {}
    chunks: dict[str, RetrievedChunk] = {}
    rank_constant = 60.0

    for rank, result in enumerate(vector_results, start=1):
        chunk = RetrievedChunk(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            document_name=result.document_name,
            text=result.text,
            score=result.score,
            page_number=result.page_number,
            chunk_index=result.chunk_index,
            strategy_used=strategy_name,
        )
        chunks[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (rank_constant + rank)

    for rank, chunk in enumerate(bm25_results, start=1):
        chunks.setdefault(chunk.chunk_id, chunk)
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (rank_constant + rank)

    if not scores:
        return []

    max_score = max(scores.values())
    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]
    fused = []
    for chunk_id in ranked_ids:
        chunk = chunks[chunk_id]
        fused.append(
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_name=chunk.document_name,
                text=chunk.text,
                score=round(scores[chunk_id] / max_score, 4),
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                strategy_used=strategy_name,
            )
        )
    return fused


def tokenize(text: str) -> list[str]:
    """Tokenize text for BM25 matching."""
    return re.findall(r"[a-z0-9]+", text.lower())
