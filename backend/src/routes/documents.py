"""Document upload, status, listing, deletion, and reindex routes."""

from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..config import Settings, get_settings
from ..database import get_db
from ..logging_config import get_logger
from ..middleware.auth import get_current_tenant, verify_project_ownership
from ..models.document import Document, DocumentStatus, detect_file_type
from ..models.tenant import Tenant
from ..services.vector_store import VectorStore
from ..tasks.file_tasks import process_uploaded_file

logger = get_logger(__name__)

router = APIRouter()

SUPPORTED_EXTENSIONS = {"pdf", "txt", "docx", "md"}
READ_CHUNK_SIZE = 1024 * 1024


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Accept a document upload, store it on disk, and queue background processing."""
    original_filename = safe_filename(file.filename)
    extension = validate_supported_extension(original_filename)
    await verify_project_ownership(db, project_id, tenant)

    storage_dir = Path(settings.storage_path) / tenant.id / project_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid4()}.{extension}"
    file_path = storage_dir / stored_filename
    document_inserted = False

    file_size_bytes, file_hash = await save_upload_file(file, file_path, tenant.max_file_size_mb)
    try:
        duplicate = await db.documents.find_one(
            {
                "tenant_id": tenant.id,
                "project_id": project_id,
                "metadata.file_hash_sha256": file_hash,
                "is_deleted": {"$ne": True},
            }
        )
        if duplicate is not None:
            file_path.unlink(missing_ok=True)
            logger.info(
                "duplicate upload detected tenant_id=%s project_id=%s existing_document_id=%s",
                tenant.id,
                project_id,
                duplicate["id"],
            )
            return {
                "document_id": duplicate["id"],
                "filename": duplicate["original_filename"],
                "status": duplicate["status"],
                "message": "Duplicate upload detected; existing document reused",
            }

        mime_type = file.content_type or mimetypes.guess_type(original_filename)[0] or "application/octet-stream"
        document = Document(
            tenant_id=tenant.id,
            project_id=project_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(file_path),
            file_size_bytes=file_size_bytes,
            file_type=extension,
            mime_type=mime_type,
            status=DocumentStatus.QUEUED.value,
            indexing_progress=0,
            created_by=tenant.id,
            updated_by=tenant.id,
            metadata={
                "file_hash_sha256": file_hash,
                "upload": {
                    "content_type": file.content_type,
                    "size_bytes": file_size_bytes,
                    "received_at": utc_now(),
                },
                "extraction_warnings": [],
            },
        )

        await db.documents.insert_one(document.model_dump(mode="python"))
        document_inserted = True
        task = process_uploaded_file.delay(document.id)
        await db.documents.update_one(
            {"id": document.id},
            {
                "$set": {
                    "celery_task_id": task.id,
                    "metadata.processing_task_id": task.id,
                    "updated_at": utc_now(),
                }
            },
        )

        logger.info("queued uploaded document document_id=%s project_id=%s", document.id, project_id)
        return {
            "document_id": document.id,
            "filename": original_filename,
            "status": DocumentStatus.QUEUED.value,
            "message": "Document uploaded and queued for processing",
        }
    except Exception:
        if document_inserted:
            await db.documents.update_one(
                {"stored_filename": stored_filename, "tenant_id": tenant.id},
                {
                    "$set": {
                        "status": DocumentStatus.ERROR.value,
                        "error_message": "Failed to queue document processing",
                        "updated_at": utc_now(),
                    }
                },
            )
        else:
            file_path.unlink(missing_ok=True)
        raise


@router.get("/{document_id}/status")
async def get_document_status(
    document_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return the current processing status for one document."""
    document = await load_tenant_document(db, tenant.id, document_id)
    return {
        "id": document["id"],
        "original_filename": document["original_filename"],
        "status": document["status"],
        "indexing_progress": document.get("indexing_progress", 0),
        "error_message": document.get("error_message"),
        "chunk_count": document.get("chunk_count"),
    }


@router.get("")
async def list_documents(
    project_id: str = Query(..., min_length=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """List documents for a tenant project with pagination."""
    await verify_project_ownership(db, project_id, tenant)
    skip = (page - 1) * per_page
    query = {
        "tenant_id": tenant.id,
        "project_id": project_id,
        "is_deleted": {"$ne": True},
    }
    total = await db.documents.count_documents(query)
    cursor = (
        db.documents.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(per_page)
    )
    documents = await cursor.to_list(length=per_page)
    return {
        "items": [document_summary(document) for document in documents],
        "page": page,
        "per_page": per_page,
        "total": total,
    }


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Delete a document, its chunks, its vectors, and its stored file."""
    document = await load_tenant_document(db, tenant.id, document_id)
    await verify_project_ownership(db, document["project_id"], tenant)
    chunk_count = await db.chunks.count_documents({"document_id": document_id})

    vector_store = VectorStore(settings)
    try:
        collection_name = VectorStore.collection_name(document["project_id"], tenant.id)
        await vector_store.delete_document_chunks(collection_name, document_id)
    finally:
        await vector_store.close()

    await db.chunks.delete_many({"document_id": document_id})
    await db.documents.delete_one({"id": document_id, "tenant_id": tenant.id})
    Path(document["file_path"]).unlink(missing_ok=True)

    decrement_document = -1 if document.get("metadata", {}).get("project_document_counted") else 0
    await db.projects.update_one(
        {"id": document["project_id"], "tenant_id": tenant.id},
        {
            "$inc": {
                "document_count": decrement_document,
                "chunk_count": -int(chunk_count),
            },
            "$set": {"updated_at": utc_now()},
        },
    )
    logger.info("deleted document document_id=%s chunks=%s", document_id, chunk_count)
    return {"message": "Document deleted"}


@router.post("/{document_id}/reindex")
async def reindex_document(
    document_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Delete existing chunks and vectors, then restart document processing."""
    document = await load_tenant_document(db, tenant.id, document_id)
    if document["status"] not in {DocumentStatus.READY.value, DocumentStatus.ERROR.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document can only be reindexed from ready or error status",
        )

    await verify_project_ownership(db, document["project_id"], tenant)
    vector_store = VectorStore(settings)
    try:
        collection_name = VectorStore.collection_name(document["project_id"], tenant.id)
        await vector_store.delete_document_chunks(collection_name, document_id)
    finally:
        await vector_store.close()

    existing_chunk_count = await db.chunks.count_documents({"document_id": document_id})
    await db.chunks.delete_many({"document_id": document_id})
    if existing_chunk_count:
        await db.projects.update_one(
            {"id": document["project_id"], "tenant_id": tenant.id},
            {
                "$inc": {"chunk_count": -int(existing_chunk_count)},
                "$set": {"updated_at": utc_now()},
            },
        )
    task = process_uploaded_file.delay(document_id)
    await db.documents.update_one(
        {"id": document_id, "tenant_id": tenant.id},
        {
            "$set": {
                "status": DocumentStatus.QUEUED.value,
                "indexing_progress": 0,
                "chunk_count": 0,
                "error_message": None,
                "celery_task_id": task.id,
                "processing_started_at": None,
                "processing_completed_at": None,
                "updated_at": utc_now(),
                "metadata.processing_task_id": task.id,
                "metadata.reindex_requested_at": utc_now(),
            }
        },
    )
    logger.info("reindex queued document_id=%s", document_id)
    return {"message": "Reindexing started"}


async def save_upload_file(upload: UploadFile, destination: Path, max_file_size_mb: int) -> tuple[int, str]:
    """Stream an UploadFile to disk while computing SHA-256 and enforcing size limits."""
    max_bytes = None if max_file_size_mb < 0 else max_file_size_mb * 1024 * 1024
    sha256 = hashlib.sha256()
    total = 0
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds maximum size of {max_file_size_mb} MB",
                )
            sha256.update(chunk)
            output.write(chunk)

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    return total, sha256.hexdigest()


def validate_supported_extension(filename: str) -> str:
    """Validate and return a supported lowercase file extension."""
    try:
        extension = detect_file_type(filename)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type")
    return extension


def safe_filename(filename: str | None) -> str:
    """Return a path-safe uploaded filename."""
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file must have a filename")
    cleaned = Path(filename).name.strip()
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file must have a filename")
    return cleaned


async def load_tenant_document(db: AsyncIOMotorDatabase, tenant_id: str, document_id: str) -> dict[str, Any]:
    """Load a non-deleted document scoped to a tenant."""
    document = await db.documents.find_one(
        {
            "id": document_id,
            "tenant_id": tenant_id,
            "is_deleted": {"$ne": True},
        }
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def document_summary(document: dict[str, Any]) -> dict[str, Any]:
    """Return the list-view representation of a document."""
    return {
        "id": document["id"],
        "original_filename": document["original_filename"],
        "file_size_bytes": document["file_size_bytes"],
        "file_type": document["file_type"],
        "status": document["status"],
        "indexing_progress": document.get("indexing_progress", 0),
        "error_message": document.get("error_message"),
        "chunk_count": document.get("chunk_count"),
        "page_count": document.get("page_count"),
        "character_count": document.get("character_count"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "metadata": {
            "file_hash_sha256": document.get("metadata", {}).get("file_hash_sha256"),
            "title": document.get("metadata", {}).get("extraction", {}).get("title"),
            "author": document.get("metadata", {}).get("extraction", {}).get("author"),
            "language": document.get("metadata", {}).get("extraction", {}).get("language"),
            "warnings": document.get("metadata", {}).get("extraction_warnings", []),
        },
    }
