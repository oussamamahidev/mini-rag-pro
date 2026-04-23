"""Authentication and tenant account endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from ..auth.key_generator import generate_api_key
from ..database import get_db
from ..logging_config import get_logger
from ..middleware.auth import (
    get_current_tenant,
    invalidate_auth_cache,
    require_redis,
    write_auth_audit_event,
)
from ..models.tenant import Tenant, TenantCreate, TenantPublic

logger = get_logger(__name__)

router = APIRouter()


class TenantRegistrationRequest(BaseModel):
    """Public tenant registration payload."""

    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)


class TenantRegistrationResponse(BaseModel):
    """Tenant registration response containing the one-time API key."""

    tenant_id: str
    api_key: str
    api_key_prefix: str
    message: str


class RotateKeyResponse(BaseModel):
    """API key rotation response containing the new one-time key."""

    new_api_key: str
    new_prefix: str
    old_prefix: str
    message: str
    rotated_at: datetime


class AccountStatusResponse(BaseModel):
    """Simple account status response."""

    message: str


@router.post("/register", response_model=TenantRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_tenant(
    request: TenantRegistrationRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> TenantRegistrationResponse:
    """Register a tenant and return its API key exactly once."""
    create_payload = TenantCreate(name=request.name, email=request.email)
    existing = await db.tenants.find_one({"email": create_payload.email})
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")

    full_key, prefix, key_hash = generate_api_key()
    tenant_id = str(uuid4())
    tenant = Tenant(
        id=tenant_id,
        name=create_payload.name,
        email=create_payload.email,
        api_key_hash=key_hash,
        api_key_prefix=prefix,
        created_by=tenant_id,
        updated_by=tenant_id,
        metadata={"registration_source": "api"},
    )

    try:
        await db.tenants.insert_one(tenant.model_dump(mode="python"))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered") from exc

    await write_auth_audit_event(
        db,
        tenant.id,
        "register",
        metadata={"api_key_prefix": prefix},
    )
    logger.info("tenant registered tenant_id=%s api_key_prefix=%s", tenant.id, prefix)
    return TenantRegistrationResponse(
        tenant_id=tenant.id,
        api_key=full_key,
        api_key_prefix=prefix,
        message="Save this key now. It cannot be retrieved again.",
    )


@router.get("/me", response_model=TenantPublic)
async def get_me(tenant: Tenant = Depends(get_current_tenant)) -> TenantPublic:
    """Return the authenticated tenant profile."""
    return tenant.to_public()


@router.post("/rotate-key", response_model=RotateKeyResponse)
async def rotate_api_key(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> RotateKeyResponse:
    """Rotate a tenant API key while keeping the old key valid for one hour."""
    redis_client = require_redis()
    new_key, new_prefix, new_hash = generate_api_key()
    rotated_at = datetime.now(UTC)
    grace_expires_at = rotated_at + timedelta(hours=1)
    old_prefix = tenant.api_key_prefix

    await db.tenants.update_one(
        {"id": tenant.id},
        {
            "$set": {
                "api_key_hash": new_hash,
                "api_key_prefix": new_prefix,
                "previous_api_key_hash": tenant.api_key_hash,
                "previous_key_hash": tenant.api_key_hash,
                "previous_api_key_prefix": tenant.api_key_prefix,
                "key_rotated_at": rotated_at,
                "previous_key_expires_at": grace_expires_at,
                "updated_at": rotated_at,
                "updated_by": tenant.id,
            }
        },
    )
    await invalidate_auth_cache(redis_client, old_prefix, new_prefix, tenant.previous_api_key_prefix)
    await write_auth_audit_event(
        db,
        tenant.id,
        "rotate_key",
        metadata={
            "old_prefix": old_prefix,
            "new_prefix": new_prefix,
            "previous_key_expires_at": grace_expires_at,
        },
    )
    logger.info("tenant api key rotated tenant_id=%s old_prefix=%s new_prefix=%s", tenant.id, old_prefix, new_prefix)
    return RotateKeyResponse(
        new_api_key=new_key,
        new_prefix=new_prefix,
        old_prefix=old_prefix,
        message="Old key valid for 1 hour",
        rotated_at=rotated_at,
    )


@router.delete("/account", response_model=AccountStatusResponse)
async def deactivate_account(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> AccountStatusResponse:
    """Deactivate a tenant account without deleting its data."""
    redis_client = require_redis()
    now = datetime.now(UTC)
    await db.tenants.update_one(
        {"id": tenant.id},
        {
            "$set": {
                "is_active": False,
                "updated_at": now,
                "updated_by": tenant.id,
            }
        },
    )
    await invalidate_auth_cache(redis_client, tenant.api_key_prefix, tenant.previous_api_key_prefix)
    await write_auth_audit_event(db, tenant.id, "deactivate", metadata={})
    logger.info("tenant account deactivated tenant_id=%s", tenant.id)
    return AccountStatusResponse(message="Account deactivated")
