"""Project CRUD endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..database import get_db
from ..logging_config import get_logger
from ..middleware.auth import get_current_tenant, verify_project_ownership
from ..models.project import Project, ProjectCreate, ProjectPublic, ProjectUpdate
from ..models.tenant import Tenant
from ..services.vector_store import VectorStore

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[ProjectPublic]:
    """Return active projects for the authenticated tenant."""
    cursor = (
        db.projects.find({"tenant_id": tenant.id, "is_deleted": {"$ne": True}}, {"_id": 0})
        .sort("created_at", -1)
        .limit(500)
    )
    projects = await cursor.to_list(length=500)
    return [project_public(project) for project in projects]


@router.post("", response_model=ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProjectPublic:
    """Create a new tenant-scoped project."""
    if tenant.max_projects >= 0:
        current_count = await db.projects.count_documents({"tenant_id": tenant.id, "is_deleted": {"$ne": True}})
        if current_count >= tenant.max_projects:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project limit reached for this plan")

    project_id = str(uuid4())
    project = Project(
        id=project_id,
        tenant_id=tenant.id,
        name=payload.name,
        description=payload.description,
        retrieval_strategy=payload.retrieval_strategy,
        qdrant_collection_name=VectorStore.collection_name(project_id, tenant.id),
        created_by=tenant.id,
        updated_by=tenant.id,
        metadata=payload.metadata,
    )
    await db.projects.insert_one(project.model_dump(mode="python"))
    logger.info("project created tenant_id=%s project_id=%s", tenant.id, project.id)
    return project.to_public()


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProjectPublic:
    """Return one project."""
    project = await verify_project_ownership(db, project_id, tenant)
    return project_public(project)


@router.patch("/{project_id}", response_model=ProjectPublic)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProjectPublic:
    """Update mutable project settings."""
    await verify_project_ownership(db, project_id, tenant)
    update_data = payload.model_dump(exclude_unset=True, mode="python")
    update_data.pop("is_deleted", None)
    update_data.pop("deleted_at", None)
    if not update_data:
        project = await verify_project_ownership(db, project_id, tenant)
        return project_public(project)

    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = tenant.id
    await db.projects.update_one(
        {"id": project_id, "tenant_id": tenant.id},
        {"$set": update_data},
    )
    project = await verify_project_ownership(db, project_id, tenant)
    logger.info("project updated tenant_id=%s project_id=%s", tenant.id, project_id)
    return project_public(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> None:
    """Soft-delete a project and hide its documents from normal listing."""
    await verify_project_ownership(db, project_id, tenant)
    now = datetime.now(UTC)
    await db.projects.update_one(
        {"id": project_id, "tenant_id": tenant.id},
        {
            "$set": {
                "is_deleted": True,
                "deleted_at": now,
                "updated_at": now,
                "updated_by": tenant.id,
            }
        },
    )
    await db.documents.update_many(
        {"project_id": project_id, "tenant_id": tenant.id, "is_deleted": {"$ne": True}},
        {"$set": {"is_deleted": True, "deleted_at": now, "updated_at": now, "updated_by": tenant.id}},
    )
    logger.info("project soft-deleted tenant_id=%s project_id=%s", tenant.id, project_id)
    return None


def project_public(project: dict[str, Any]) -> ProjectPublic:
    """Convert a MongoDB project row into the public model."""
    payload = dict(project)
    payload.pop("_id", None)
    return Project.model_validate(payload).to_public()
