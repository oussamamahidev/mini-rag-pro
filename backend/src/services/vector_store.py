"""Qdrant vector store manager for semantic chunk search."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, FilterSelector, MatchValue, PointStruct, VectorParams

from ..config import Settings
from ..logging_config import get_logger

logger = get_logger(__name__)

VECTOR_SIZE = 1536
BATCH_SIZE = 100
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5
REQUIRED_PAYLOAD_FIELDS = {"chunk_id", "document_id", "document_name", "text", "page_number", "chunk_index"}

vector_store: "VectorStore | None" = None


@dataclass(slots=True)
class SearchResult:
    """Semantic search result returned by Qdrant."""

    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float
    page_number: int | None
    chunk_index: int
    strategy_used: str = "vanilla"


class VectorStore:
    """Manage Qdrant collections, vector upserts, search, and deletion."""

    def __init__(self, settings: Settings) -> None:
        """Create a Qdrant client using application settings."""
        self.settings = settings
        self.client = AsyncQdrantClient(url=settings.qdrant_url, timeout=settings.service_check_timeout_seconds)
        self.vector_size = settings.openai_embedding_dimensions or VECTOR_SIZE
        self.score_threshold = settings.vector_score_threshold
        self.max_payload_bytes = settings.vector_payload_max_bytes

    @staticmethod
    def collection_name(project_id: str, tenant_id: str) -> str:
        """Return the shortened project-scoped Qdrant collection name."""
        tenant_part = compact_identifier(tenant_id)[:8]
        project_part = compact_identifier(project_id)[:8]
        return f"t{tenant_part}_p{project_part}"

    def get_collection_name(self, project_id: str, tenant_id: str) -> str:
        """Return the Qdrant collection name for a tenant project."""
        return self.collection_name(project_id, tenant_id)

    async def ensure_collection(self, project_id: str, tenant_id: str) -> str:
        """Create the project collection if needed and validate its schema."""
        collection_name = self.collection_name(project_id, tenant_id)
        exists = await self.collection_exists(collection_name)
        if not exists:
            await self._with_retries(
                self.client.create_collection,
                collection_name=collection_name,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )
            logger.info("created qdrant collection collection=%s vector_size=%s", collection_name, self.vector_size)

        await self.validate_collection_schema(collection_name)
        return collection_name

    async def upsert_chunk(
        self,
        collection_name: str,
        chunk_id: str,
        embedding: list[float],
        payload: dict[str, Any],
    ) -> None:
        """Upsert a single vector point into Qdrant."""
        await self.upsert_batch(collection_name, [(chunk_id, embedding, payload)])

    async def upsert_batch(
        self,
        collection_name: str,
        points: list[tuple[str, list[float], dict[str, Any]]],
    ) -> None:
        """Upsert vector points into Qdrant in safe batches."""
        if not points:
            return

        for batch in batched(points, BATCH_SIZE):
            point_structs = []
            for chunk_id, embedding, payload in batch:
                self.validate_embedding(embedding)
                self.validate_payload(payload)
                point_structs.append(PointStruct(id=chunk_id, vector=embedding, payload=payload))

            await self._with_retries(
                self.client.upload_points,
                collection_name=collection_name,
                points=point_structs,
                batch_size=BATCH_SIZE,
                wait=True,
                max_retries=MAX_RETRIES,
            )

    async def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 10,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        document_id: str | None = None,
        score_threshold: float | None = None,
    ) -> list[SearchResult]:
        """Run cosine similarity search with optional metadata filters."""
        self.validate_embedding(query_embedding)
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")

        search_filter = build_metadata_filter(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=document_id,
        )
        results = await self._with_retries(
            self.client.search,
            collection_name=collection_name,
            query_vector=query_embedding,
            query_filter=search_filter,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
            score_threshold=self.score_threshold if score_threshold is None else score_threshold,
        )
        return [scored_point_to_result(point) for point in results]

    async def delete_document_chunks(self, collection_name: str, document_id: str) -> int:
        """Delete all points for one document and return the count deleted."""
        if not await self.collection_exists(collection_name):
            return 0

        qdrant_filter = build_metadata_filter(document_id=document_id)
        count = await self._with_retries(
            self.client.count,
            collection_name=collection_name,
            count_filter=qdrant_filter,
            exact=True,
        )
        await self._with_retries(
            self.client.delete,
            collection_name=collection_name,
            points_selector=FilterSelector(filter=qdrant_filter),
            wait=True,
        )
        return int(getattr(count, "count", 0))

    async def delete_collection(self, collection_name: str) -> None:
        """Delete an entire Qdrant collection if it exists."""
        if not await self.collection_exists(collection_name):
            return
        await self._with_retries(self.client.delete_collection, collection_name=collection_name)
        logger.info("deleted qdrant collection collection=%s", collection_name)

    async def get_collection_info(self, collection_name: str) -> dict[str, Any] | None:
        """Return basic collection metadata, or None when absent."""
        if not await self.collection_exists(collection_name):
            return None
        info = await self._with_retries(self.client.get_collection, collection_name=collection_name)
        return {
            "collection_name": collection_name,
            "vectors_count": getattr(info, "vectors_count", None) or getattr(info, "points_count", 0),
            "status": str(getattr(info, "status", "unknown")),
        }

    async def collection_exists(self, collection_name: str) -> bool:
        """Check collection existence without leaking Qdrant errors to callers."""
        try:
            return bool(await self._with_retries(self.client.collection_exists, collection_name=collection_name))
        except Exception as exc:
            logger.warning("qdrant collection existence check failed collection=%s error=%s", collection_name, exc)
            return False

    async def validate_collection_schema(self, collection_name: str) -> None:
        """Validate vector size and distance for an existing collection."""
        info = await self._with_retries(self.client.get_collection, collection_name=collection_name)
        vectors_config = getattr(getattr(info, "config", None), "params", None)
        vectors = getattr(vectors_config, "vectors", None)
        vector_params = first_vector_params(vectors)
        size = getattr(vector_params, "size", None)
        distance = getattr(vector_params, "distance", None)

        if size != self.vector_size:
            raise ValueError(
                f"Qdrant collection {collection_name} vector size mismatch: expected {self.vector_size}, got {size}"
            )
        if str(distance).lower().split(".")[-1] != Distance.COSINE.value.lower():
            raise ValueError(f"Qdrant collection {collection_name} must use cosine distance")

    def validate_embedding(self, embedding: list[float]) -> None:
        """Validate vector dimensionality and value type before insertion/search."""
        if len(embedding) != self.vector_size:
            raise ValueError(f"embedding dimension mismatch: expected {self.vector_size}, got {len(embedding)}")
        if not all(isinstance(value, int | float) for value in embedding):
            raise ValueError("embedding must contain only numeric values")

    def validate_payload(self, payload: dict[str, Any]) -> None:
        """Validate required payload fields and enforce payload size safeguards."""
        missing = REQUIRED_PAYLOAD_FIELDS.difference(payload)
        if missing:
            raise ValueError(f"Qdrant payload missing required fields: {', '.join(sorted(missing))}")
        encoded = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
        if len(encoded) > self.max_payload_bytes:
            raise ValueError(
                f"Qdrant payload exceeds maximum size of {self.max_payload_bytes} bytes"
            )

    async def close(self) -> None:
        """Close the Qdrant client."""
        close_result = self.client.close()
        if hasattr(close_result, "__await__"):
            await close_result

    async def _with_retries(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a Qdrant operation with exponential backoff."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except ValueError:
                raise
            except Exception as exc:
                if attempt >= MAX_RETRIES:
                    logger.exception("qdrant operation failed after retries operation=%s", getattr(func, "__name__", func))
                    raise
                delay = BASE_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "qdrant operation failed; retrying operation=%s attempt=%s delay=%.1fs error=%s",
                    getattr(func, "__name__", func),
                    attempt + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("qdrant retry loop exited unexpectedly")


def build_metadata_filter(
    *,
    tenant_id: str | None = None,
    project_id: str | None = None,
    document_id: str | None = None,
) -> Filter | None:
    """Build a Qdrant metadata filter from supported scope fields."""
    conditions = []
    for key, value in {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "document_id": document_id,
    }.items():
        if value is not None:
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=conditions) if conditions else None


def scored_point_to_result(point: Any) -> SearchResult:
    """Convert a Qdrant scored point into the public SearchResult dataclass."""
    payload = dict(point.payload or {})
    return SearchResult(
        chunk_id=str(payload.get("chunk_id", point.id)),
        document_id=str(payload["document_id"]),
        document_name=str(payload["document_name"]),
        text=str(payload["text"]),
        score=float(point.score),
        page_number=payload.get("page_number"),
        chunk_index=int(payload["chunk_index"]),
    )


def compact_identifier(value: str) -> str:
    """Return a Qdrant-safe compact identifier segment."""
    compact = re.sub(r"[^A-Za-z0-9]", "", value)
    if not compact:
        raise ValueError("identifier must contain at least one alphanumeric character")
    return compact.lower()


def first_vector_params(vectors: Any) -> Any:
    """Return unnamed or first named vector params from Qdrant collection info."""
    if isinstance(vectors, dict):
        if not vectors:
            return None
        return next(iter(vectors.values()))
    return vectors


def batched(items: list[tuple[str, list[float], dict[str, Any]]], batch_size: int) -> Iterable[list[tuple[str, list[float], dict[str, Any]]]]:
    """Yield fixed-size batches from a list."""
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def initialize_vector_store(settings: Settings) -> VectorStore:
    """Create and store the module-level vector store singleton."""
    global vector_store
    vector_store = VectorStore(settings)
    return vector_store


def get_vector_store() -> VectorStore:
    """Return the initialized vector store singleton."""
    if vector_store is None:
        raise RuntimeError("VectorStore has not been initialized")
    return vector_store
