"""Celery tasks for upload processing, chunk indexing, and stale task cleanup."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import redis as sync_redis
from pymongo import MongoClient, ReplaceOne
from pymongo.database import Database

from ..config import get_settings
from ..logging_config import get_logger
from ..models.chunk import Chunk
from ..models.document import DocumentStatus
from ..models.project import Project
from ..services.chunking import ChunkingService
from ..services.embedding import EmbeddingService
from ..services.vector_store import VectorStore
from .celery_app import celery_app

logger = get_logger(__name__)

BATCH_SIZE = 20
PROGRESS_UPDATE_INTERVAL = 10


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


def run_async(coro: Any) -> Any:
    """Run an async coroutine from Celery's synchronous worker context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if hasattr(coro, "close"):
        coro.close()
    raise RuntimeError("cannot run Celery indexing task inside an active event loop")


def get_sync_db() -> Database[Any]:
    """Return a synchronous PyMongo database for Celery tasks."""
    settings = get_settings()
    client = MongoClient(settings.mongo_url, uuidRepresentation="standard")
    return client[settings.mongo_db_name]


@celery_app.task(queue="file_processing", bind=True, max_retries=3, default_retry_delay=60)
def process_uploaded_file(self: Any, document_id: str) -> dict[str, Any]:
    """Extract text and create chunk payloads for an uploaded document."""
    settings = get_settings()
    client = MongoClient(settings.mongo_url, uuidRepresentation="standard")
    db = client[settings.mongo_db_name]

    try:
        document = db.documents.find_one({"id": document_id, "is_deleted": {"$ne": True}})
        if document is None:
            logger.warning("document not found for processing document_id=%s", document_id)
            return {"document_id": document_id, "status": "missing"}

        if document.get("status") == DocumentStatus.READY.value:
            logger.info("document already ready; skipping processing document_id=%s", document_id)
            return {"document_id": document_id, "status": "ready"}

        now = utc_now()
        db.documents.update_one(
            {"id": document_id},
            {
                "$set": {
                    "status": DocumentStatus.PROCESSING.value,
                    "processing_started_at": document.get("processing_started_at") or now,
                    "updated_at": now,
                    "celery_task_id": self.request.id,
                    "error_message": None,
                    "metadata.processing_attempts": int(document.get("metadata", {}).get("processing_attempts", 0)) + 1,
                }
            },
        )

        project = db.projects.find_one(
            {
                "id": document["project_id"],
                "tenant_id": document["tenant_id"],
                "is_deleted": {"$ne": True},
            }
        )
        if project is None:
            raise ValueError(f"project not found for document {document_id}")

        service = ChunkingService()
        result = service.process_document(
            document["file_path"],
            document["file_type"],
            int(project.get("chunk_size", settings.default_chunk_size)),
            int(project.get("chunk_overlap", settings.default_chunk_overlap)),
        )

        if result.chunk_count == 0:
            raise ValueError("; ".join(result.warnings) or "no usable text chunks were extracted")

        index_task = index_document_chunks.delay(document_id, result.chunks_as_dicts())
        now = utc_now()
        metadata = result.metadata.to_dict()
        db.documents.update_one(
            {"id": document_id},
            {
                "$set": {
                    "status": DocumentStatus.INDEXING.value,
                    "indexing_progress": 0,
                    "page_count": result.page_count,
                    "character_count": result.character_count,
                    "chunk_count": result.chunk_count,
                    "updated_at": now,
                    "metadata.extraction": metadata,
                    "metadata.extraction_warnings": result.warnings,
                    "metadata.indexing_task_id": index_task.id,
                }
            },
        )
        logger.info("queued indexing document_id=%s chunks=%s", document_id, result.chunk_count)
        return {"document_id": document_id, "status": "indexing", "chunk_count": result.chunk_count}

    except Exception as exc:
        if self.request.retries < self.max_retries and is_retryable_processing_error(exc):
            mark_document_error(db, document_id, str(exc), permanent=False)
            logger.warning(
                "document processing failed; retrying document_id=%s retry=%s error=%s",
                document_id,
                self.request.retries + 1,
                exc,
            )
            raise self.retry(exc=exc)

        mark_document_error(db, document_id, str(exc), permanent=True)
        logger.exception("document processing permanently failed document_id=%s", document_id)
        return {"document_id": document_id, "status": "error", "error": str(exc)}
    finally:
        client.close()


@celery_app.task(queue="data_indexing", bind=True, max_retries=2, default_retry_delay=60)
def index_document_chunks(self: Any, document_id: str, chunks_data: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate embeddings, persist chunks, and upsert vectors for one document."""
    settings = get_settings()
    mongo_client = MongoClient(settings.mongo_url, uuidRepresentation="standard")
    db = mongo_client[settings.mongo_db_name]

    try:
        return run_async(index_document_chunks_async(document_id, chunks_data, db))

    except Exception as exc:
        if self.request.retries < self.max_retries and is_retryable_indexing_error(exc):
            mark_document_error(db, document_id, str(exc), permanent=False)
            logger.warning(
                "document indexing failed; retrying document_id=%s retry=%s error=%s",
                document_id,
                self.request.retries + 1,
                exc,
            )
            raise self.retry(exc=exc)

        mark_document_error(db, document_id, str(exc), permanent=True)
        logger.exception("document indexing permanently failed document_id=%s", document_id)
        return {"document_id": document_id, "status": "error", "error": str(exc)}
    finally:
        mongo_client.close()


@celery_app.task(queue="default")
def cleanup_stale_documents() -> dict[str, int]:
    """Mark documents stuck in processing/indexing for more than 30 minutes as errors."""
    settings = get_settings()
    client = MongoClient(settings.mongo_url, uuidRepresentation="standard")
    db = client[settings.mongo_db_name]
    try:
        cutoff = utc_now() - timedelta(minutes=30)
        result = db.documents.update_many(
            {
                "status": {"$in": [DocumentStatus.PROCESSING.value, DocumentStatus.INDEXING.value]},
                "processing_started_at": {"$lt": cutoff},
                "is_deleted": {"$ne": True},
            },
            {
                "$set": {
                    "status": DocumentStatus.ERROR.value,
                    "error_message": "Processing timeout",
                    "updated_at": utc_now(),
                    "metadata.processing_timeout": True,
                }
            },
        )
        logger.info("cleaned stale documents count=%s", result.modified_count)
        return {"cleaned": int(result.modified_count)}
    finally:
        client.close()


async def index_document_chunks_async(
    document_id: str,
    chunks_data: list[dict[str, Any]],
    db: Database[Any],
) -> dict[str, Any]:
    """Async implementation of document indexing used by the Celery task."""
    settings = get_settings()
    embedding_service = EmbeddingService(settings)
    vector_store = VectorStore(settings)
    try:
        document = db.documents.find_one({"id": document_id, "is_deleted": {"$ne": True}})
        if document is None:
            logger.warning("document not found for indexing document_id=%s", document_id)
            return {"document_id": document_id, "status": "missing"}

        project_doc = db.projects.find_one(
            {
                "id": document["project_id"],
                "tenant_id": document["tenant_id"],
                "is_deleted": {"$ne": True},
            }
        )
        if project_doc is None:
            raise ValueError(f"project not found for document {document_id}")

        project_payload = dict(project_doc)
        project_payload.pop("_id", None)
        Project.model_validate(project_payload)
        collection_name = await vector_store.ensure_collection(document["project_id"], document["tenant_id"])

        previous_chunk_count = db.chunks.count_documents({"document_id": document_id})
        db.chunks.delete_many({"document_id": document_id})
        await vector_store.delete_document_chunks(collection_name, document_id)

        total_chunks = len(chunks_data)
        if total_chunks == 0:
            raise ValueError("cannot index document without chunks")

        chunks_done = 0
        for batch_start in range(0, total_chunks, BATCH_SIZE):
            batch = chunks_data[batch_start : batch_start + BATCH_SIZE]
            embeddings = await embedding_service.embed_batch([chunk["text"] for chunk in batch])

            operations: list[ReplaceOne] = []
            vector_points: list[tuple[str, list[float], dict[str, Any]]] = []
            for offset, (chunk_payload, embedding) in enumerate(zip(batch, embeddings, strict=True)):
                chunk_index = int(chunk_payload.get("chunk_index", batch_start + offset))
                chunk_id = deterministic_chunk_id(document_id, chunk_index, chunk_payload["text"])
                chunk = Chunk(
                    id=chunk_id,
                    tenant_id=document["tenant_id"],
                    project_id=document["project_id"],
                    document_id=document_id,
                    text=chunk_payload["text"],
                    chunk_index=chunk_index,
                    start_char=int(chunk_payload["start_char"]),
                    end_char=int(chunk_payload["end_char"]),
                    page_number=chunk_payload.get("page_number"),
                    embedding_model=settings.openai_embedding_model,
                    metadata={
                        **dict(chunk_payload.get("metadata") or {}),
                        "section_title": chunk_payload.get("section_title"),
                        "source_file_hash_sha256": document.get("metadata", {}).get("file_hash_sha256"),
                    },
                )
                operations.append(ReplaceOne({"id": chunk.id}, chunk.model_dump(mode="python"), upsert=True))
                vector_points.append(
                    (
                        chunk.qdrant_id,
                        embedding,
                        {
                            "chunk_id": chunk.id,
                            "tenant_id": document["tenant_id"],
                            "project_id": document["project_id"],
                            "document_id": document_id,
                            "document_name": document["original_filename"],
                            "text": chunk.text,
                            "page_number": chunk.page_number,
                            "chunk_index": chunk.chunk_index,
                            "section_title": chunk.metadata.get("section_title"),
                        },
                    )
                )

            if operations:
                db.chunks.bulk_write(operations, ordered=False)
            await vector_store.upsert_batch(collection_name, vector_points)

            chunks_done += len(batch)
            if chunks_done % PROGRESS_UPDATE_INTERVAL == 0 or chunks_done == total_chunks:
                update_indexing_progress(db, document_id, chunks_done, total_chunks)
            await asyncio.sleep(0.1)

        now = utc_now()
        already_counted = bool(document.get("metadata", {}).get("project_document_counted"))
        document_increment = 0 if already_counted else 1
        chunk_increment = total_chunks - previous_chunk_count
        db.documents.update_one(
            {"id": document_id},
            {
                "$set": {
                    "status": DocumentStatus.READY.value,
                    "indexing_progress": 100,
                    "chunk_count": total_chunks,
                    "processing_completed_at": now,
                    "updated_at": now,
                    "error_message": None,
                    "metadata.project_document_counted": True,
                    "metadata.indexed_at": now,
                    "metadata.indexing_attempts": int(document.get("metadata", {}).get("indexing_attempts", 0)) + 1,
                    "metadata.qdrant_collection_name": collection_name,
                }
            },
        )
        db.projects.update_one(
            {"id": document["project_id"], "tenant_id": document["tenant_id"]},
            {
                "$inc": {
                    "document_count": document_increment,
                    "chunk_count": chunk_increment,
                },
                "$set": {"updated_at": now, "metadata.qdrant_collection_name": collection_name},
            },
        )
        invalidate_bm25_cache(settings.redis_url, document["project_id"])
        logger.info("indexed document document_id=%s chunks=%s collection=%s", document_id, total_chunks, collection_name)
        return {"document_id": document_id, "status": "ready", "chunk_count": total_chunks}
    finally:
        await embedding_service.close()
        await vector_store.close()


def update_indexing_progress(db: Database[Any], document_id: str, chunks_done: int, total_chunks: int) -> None:
    """Persist indexing progress as an integer percentage."""
    progress = min(100, int((chunks_done / max(total_chunks, 1)) * 100))
    db.documents.update_one(
        {"id": document_id},
        {
            "$set": {
                "indexing_progress": progress,
                "updated_at": utc_now(),
            }
        },
    )


def deterministic_chunk_id(document_id: str, chunk_index: int, text: str) -> str:
    """Return a stable UUID for a document chunk."""
    text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
    return str(uuid5(NAMESPACE_URL, f"{document_id}:{chunk_index}:{text_hash}"))


def mark_document_error(db: Database[Any], document_id: str, error_message: str, *, permanent: bool) -> None:
    """Persist task failure details on the document record."""
    db.documents.update_one(
        {"id": document_id},
        {
            "$set": {
                "status": DocumentStatus.ERROR.value,
                "error_message": error_message[:2000],
                "updated_at": utc_now(),
                "metadata.processing_permanent_failure": permanent,
            }
        },
    )


def is_retryable_processing_error(exc: Exception) -> bool:
    """Return whether a processing exception should be retried."""
    return not isinstance(exc, (ValueError, FileNotFoundError))


def is_retryable_indexing_error(exc: Exception) -> bool:
    """Return whether an indexing exception should be retried."""
    return not isinstance(exc, ValueError)


def invalidate_bm25_cache(redis_url: str, project_id: str) -> None:
    """Invalidate cached BM25 corpus data after indexing changes."""
    try:
        client = sync_redis.from_url(redis_url)
        client.delete(f"bm25:{project_id}")
        client.close()
        logger.info("BM25 cache invalidated for project %s", project_id)
    except Exception as exc:
        logger.warning("BM25 cache invalidation failed project_id=%s error=%s", project_id, exc)
