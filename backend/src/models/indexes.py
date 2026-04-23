"""MongoDB index creation for all model-backed collections."""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel

from ..logging_config import get_logger

logger = get_logger(__name__)


async def create_indexes(db: Any) -> None:
    """
    Create all MongoDB indexes.

    The function is idempotent and safe to call during every application startup.
    """
    index_specs: dict[str, list[IndexModel]] = {
        "tenants": [
            IndexModel([("email", ASCENDING)], unique=True, name="uq_tenants_email", background=True),
            IndexModel([("api_key_prefix", ASCENDING)], name="ix_tenants_api_key_prefix", background=True),
            IndexModel([("previous_api_key_prefix", ASCENDING)], name="ix_tenants_previous_api_key_prefix", background=True),
        ],
        "projects": [
            IndexModel(
                [("tenant_id", ASCENDING), ("id", ASCENDING)],
                name="ix_projects_tenant_id_id",
                background=True,
            ),
            IndexModel([("tenant_id", ASCENDING)], name="ix_projects_tenant_id", background=True),
            IndexModel([("tenant_id", ASCENDING), ("is_deleted", ASCENDING)], name="ix_projects_deleted", background=True),
        ],
        "documents": [
            IndexModel(
                [("tenant_id", ASCENDING), ("project_id", ASCENDING)],
                name="ix_documents_tenant_project",
                background=True,
            ),
            IndexModel([("status", ASCENDING)], name="ix_documents_status", background=True),
            IndexModel([("tenant_id", ASCENDING), ("project_id", ASCENDING), ("is_deleted", ASCENDING)], name="ix_documents_deleted", background=True),
            IndexModel(
                [
                    ("tenant_id", ASCENDING),
                    ("project_id", ASCENDING),
                    ("metadata.file_hash_sha256", ASCENDING),
                    ("is_deleted", ASCENDING),
                ],
                name="ix_documents_file_hash",
                background=True,
            ),
        ],
        "chunks": [
            IndexModel(
                [("tenant_id", ASCENDING), ("project_id", ASCENDING)],
                name="ix_chunks_tenant_project",
                background=True,
            ),
            IndexModel(
                [("project_id", ASCENDING), ("document_id", ASCENDING)],
                name="ix_chunks_project_document",
                background=True,
            ),
            IndexModel([("text_hash", ASCENDING)], name="ix_chunks_text_hash", background=True),
        ],
        "query_logs": [
            IndexModel([("tenant_id", ASCENDING)], name="ix_query_logs_tenant_id", background=True),
            IndexModel([("project_id", ASCENDING)], name="ix_query_logs_project_id", background=True),
            IndexModel([("created_at", DESCENDING)], name="ix_query_logs_created_at_desc", background=True),
            IndexModel([("evaluation_status", ASCENDING)], name="ix_query_logs_evaluation_status", background=True),
            IndexModel(
                [("tenant_id", ASCENDING), ("evaluation_status", ASCENDING), ("created_at", DESCENDING)],
                name="ix_query_logs_tenant_eval_created_at",
                background=True,
            ),
            IndexModel(
                [("tenant_id", ASCENDING), ("project_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_query_logs_tenant_project_created_at",
                background=True,
            ),
        ],
        "auth_events": [
            IndexModel([("tenant_id", ASCENDING), ("created_at", DESCENDING)], name="ix_auth_events_tenant_created_at", background=True),
            IndexModel([("event_type", ASCENDING)], name="ix_auth_events_event_type", background=True),
        ],
    }

    confirmed_count = 0
    for collection_name, indexes in index_specs.items():
        collection = db[collection_name]
        created_names = await collection.create_indexes(indexes)
        confirmed_count += len(created_names)

    logger.info("mongodb indexes created_or_confirmed count=%s", confirmed_count)
