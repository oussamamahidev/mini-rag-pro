"""Tenant UI settings endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..database import get_db
from ..middleware.auth import get_current_tenant
from ..models.project import RetrievalStrategy
from ..models.tenant import Tenant

router = APIRouter()


class SettingsPatch(BaseModel):
    """Mutable tenant-facing settings accepted from the frontend."""

    openai_api_key: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    max_tokens: int | None = Field(default=None, gt=0, le=16000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    default_strategy: RetrievalStrategy | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    reranker_model: str | None = Field(default=None, min_length=1, max_length=255)


class AppSettingsResponse(BaseModel):
    """Settings payload returned to the frontend."""

    model: str
    max_tokens: int
    temperature: float
    default_strategy: RetrievalStrategy
    top_k: int
    reranker_model: str
    updated_at: datetime
    openai_api_key_configured: bool


@router.get("", response_model=AppSettingsResponse)
async def get_app_settings(
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
) -> AppSettingsResponse:
    """Return effective tenant UI settings."""
    return build_settings_response(tenant.metadata.get("ui_settings", {}), settings)


@router.patch("", response_model=AppSettingsResponse)
async def update_app_settings(
    patch: SettingsPatch,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AppSettingsResponse:
    """
    Store tenant UI settings.

    Provider secrets remain environment-managed; an OpenAI key submitted here is
    acknowledged only as configured and is not stored in MongoDB.
    """
    now = datetime.now(UTC)
    current = dict(tenant.metadata.get("ui_settings") or {})
    update = patch.model_dump(exclude_unset=True, mode="python")
    openai_api_key = update.pop("openai_api_key", None)
    if openai_api_key:
        current["openai_api_key_configured"] = True
        current["openai_api_key_updated_at"] = now

    current.update(update)
    current["updated_at"] = now
    await db.tenants.update_one(
        {"id": tenant.id},
        {
            "$set": {
                "metadata.ui_settings": current,
                "updated_at": now,
                "updated_by": tenant.id,
            }
        },
    )
    return build_settings_response(current, settings)


def build_settings_response(stored: dict[str, Any], settings: Settings) -> AppSettingsResponse:
    """Merge stored tenant preferences with server defaults."""
    return AppSettingsResponse(
        model=str(stored.get("model") or settings.openai_model),
        max_tokens=int(stored.get("max_tokens") or 1024),
        temperature=float(stored.get("temperature") if stored.get("temperature") is not None else 0.2),
        default_strategy=stored.get("default_strategy") or settings.default_retrieval_strategy,
        top_k=int(stored.get("top_k") or settings.default_top_k),
        reranker_model=str(stored.get("reranker_model") or settings.reranker_model),
        updated_at=stored.get("updated_at") or datetime.now(UTC),
        openai_api_key_configured=bool(stored.get("openai_api_key_configured") or settings.openai_api_key),
    )
