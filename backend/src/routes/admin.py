"""Benchmarking and administrative inspection endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..database import get_db
from ..middleware.auth import get_current_tenant, verify_project_ownership
from ..models.tenant import Tenant

router = APIRouter()


@router.get("/chunks")
async def list_project_chunks(
    project_id: str = Query(..., min_length=1),
    document_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=5000, ge=1, le=20000),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return chunk text for authenticated benchmark dataset generation."""
    await verify_project_ownership(db, project_id, tenant)

    filter_query: dict[str, Any] = {
        "tenant_id": tenant.id,
        "project_id": project_id,
    }
    if document_id is not None:
        filter_query["document_id"] = document_id

    document_rows = await db.documents.find(
        {
            "tenant_id": tenant.id,
            "project_id": project_id,
            "is_deleted": {"$ne": True},
        },
        {"_id": 0, "id": 1, "original_filename": 1},
    ).to_list(length=10000)
    document_names = {row["id"]: row.get("original_filename", "") for row in document_rows}

    total = await db.chunks.count_documents(filter_query)
    cursor = (
        db.chunks.find(filter_query, {"_id": 0})
        .sort([("document_id", 1), ("chunk_index", 1)])
        .limit(limit)
    )
    chunks = []
    async for chunk in cursor:
        chunks.append(
            {
                "id": chunk["id"],
                "chunk_id": chunk["id"],
                "document_id": chunk["document_id"],
                "document_name": document_names.get(chunk["document_id"], ""),
                "text": chunk["text"],
                "chunk_index": chunk.get("chunk_index"),
                "page_number": chunk.get("page_number"),
                "char_count": chunk.get("char_count", len(chunk["text"])),
                "token_count": chunk.get("token_count"),
                "metadata": chunk.get("metadata", {}),
            }
        )

    return {
        "items": chunks,
        "total": total,
        "returned": len(chunks),
        "document_count": len(document_rows),
    }
