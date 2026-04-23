"""Tenant data models for organization-level access to the RAG platform."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


def new_uuid() -> str:
    """Return a UUID4 string suitable for application-level MongoDB ids."""
    return str(uuid4())


class TenantPlan(StrEnum):
    """Supported tenant subscription plans."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class PlanLimits(TypedDict):
    """Default resource limits for a tenant plan."""

    rate_limit_per_hour: int
    max_projects: int
    max_documents_per_project: int
    max_file_size_mb: int


PLAN_LIMITS: dict[str, PlanLimits] = {
    TenantPlan.FREE.value: {
        "rate_limit_per_hour": 100,
        "max_projects": 5,
        "max_documents_per_project": 20,
        "max_file_size_mb": 10,
    },
    TenantPlan.PRO.value: {
        "rate_limit_per_hour": 1000,
        "max_projects": -1,
        "max_documents_per_project": -1,
        "max_file_size_mb": 100,
    },
    TenantPlan.ENTERPRISE.value: {
        "rate_limit_per_hour": 5000,
        "max_projects": -1,
        "max_documents_per_project": -1,
        "max_file_size_mb": 500,
    },
}


class TenantCreate(BaseModel):
    """Input payload for creating a tenant before API key generation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """Validate and normalize contact email addresses."""
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("email must be a valid email address")
        return normalized


class TenantPublic(BaseModel):
    """Tenant representation safe to return to API consumers."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_default=True,
    )

    id: str
    name: str
    email: str
    api_key_prefix: str
    previous_api_key_prefix: str | None = None
    key_rotated_at: datetime | None = None
    previous_key_expires_at: datetime | None = None
    plan: TenantPlan = Field(default=TenantPlan.FREE)
    rate_limit_per_hour: int
    max_projects: int
    max_documents_per_project: int
    max_file_size_mb: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime | None = None
    created_by: str | None = None
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Tenant(BaseModel):
    """MongoDB tenant document with API key metadata and plan limits."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_assignment=True,
        validate_default=True,
    )

    _TOUCH_FIELDS: ClassVar[set[str]] = {
        "name",
        "email",
        "api_key_hash",
        "api_key_prefix",
        "previous_api_key_hash",
        "previous_key_hash",
        "previous_api_key_prefix",
        "key_rotated_at",
        "previous_key_expires_at",
        "plan",
        "rate_limit_per_hour",
        "max_projects",
        "max_documents_per_project",
        "max_file_size_mb",
        "is_active",
        "last_active_at",
        "updated_by",
        "metadata",
    }

    id: str = Field(default_factory=new_uuid, frozen=True)
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)
    api_key_hash: str = Field(..., min_length=20)
    api_key_prefix: str = Field(..., min_length=4, max_length=32)
    previous_api_key_hash: str | None = Field(default=None, min_length=20)
    previous_key_hash: str | None = Field(default=None, min_length=20)
    previous_api_key_prefix: str | None = Field(default=None, min_length=4, max_length=32)
    key_rotated_at: datetime | None = None
    previous_key_expires_at: datetime | None = None
    plan: TenantPlan = Field(default=TenantPlan.FREE)
    rate_limit_per_hour: int = Field(default=PLAN_LIMITS[TenantPlan.FREE.value]["rate_limit_per_hour"], ge=-1)
    max_projects: int = Field(default=PLAN_LIMITS[TenantPlan.FREE.value]["max_projects"], ge=-1)
    max_documents_per_project: int = Field(
        default=PLAN_LIMITS[TenantPlan.FREE.value]["max_documents_per_project"],
        ge=-1,
    )
    max_file_size_mb: int = Field(default=PLAN_LIMITS[TenantPlan.FREE.value]["max_file_size_mb"], ge=-1)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now, frozen=True)
    updated_at: datetime = Field(default_factory=utc_now)
    last_active_at: datetime | None = None
    created_by: str | None = Field(default=None, frozen=True)
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def apply_plan_defaults(cls, data: Any) -> Any:
        """Fill missing limit fields from the selected plan."""
        if not isinstance(data, dict):
            return data

        values = dict(data)
        raw_plan = values.get("plan", TenantPlan.FREE.value)
        plan = raw_plan.value if isinstance(raw_plan, TenantPlan) else str(raw_plan)
        if plan not in PLAN_LIMITS:
            raise ValueError(f"plan must be one of: {', '.join(sorted(PLAN_LIMITS))}")

        for field_name, limit in PLAN_LIMITS[plan].items():
            values.setdefault(field_name, limit)
        return values

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """Validate and normalize contact email addresses."""
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("email must be a valid email address")
        return normalized

    @field_validator("api_key_prefix", "previous_api_key_prefix")
    @classmethod
    def validate_api_key_prefix(cls, value: str | None) -> str | None:
        """Ensure the stored key prefix is display-only and never blank."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("api_key_prefix cannot be blank")
        return normalized

    def __setattr__(self, name: str, value: Any) -> None:
        """Refresh updated_at when mutable business fields change."""
        super().__setattr__(name, value)
        if name in self._TOUCH_FIELDS and "updated_at" in self.__class__.model_fields:
            super().__setattr__("updated_at", utc_now())

    def mark_active(self) -> None:
        """Record activity from a successful authenticated request."""
        self.last_active_at = utc_now()

    def touch(self, updated_by: str | None = None) -> None:
        """Manually refresh audit fields for database update operations."""
        if updated_by is not None:
            self.updated_by = updated_by
        self.updated_at = utc_now()

    def to_public(self) -> TenantPublic:
        """Return a public tenant projection without the API key hash."""
        return TenantPublic.model_validate(
            self.model_dump(exclude={"api_key_hash", "previous_api_key_hash", "previous_key_hash"})
        )

    def to_mongo(self) -> dict[str, Any]:
        """Return a JSON-compatible document for MongoDB writes."""
        return self.model_dump(mode="json")
