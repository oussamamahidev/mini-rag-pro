"""Project data models for tenant-scoped RAG document collections."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from ..config import Settings
except Exception:  # pragma: no cover - defensive for isolated model imports
    Settings = None  # type: ignore[assignment]


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


def new_uuid() -> str:
    """Return a UUID4 string suitable for application-level MongoDB ids."""
    return str(uuid4())


def settings_default(field_name: str, fallback: int) -> int:
    """Read a default from Settings without instantiating environment-backed settings."""
    if Settings is None:
        return fallback
    field = Settings.model_fields.get(field_name)
    value = getattr(field, "default", fallback)
    return value if isinstance(value, int) else fallback


DEFAULT_CHUNK_SIZE = settings_default("default_chunk_size", 800)
DEFAULT_CHUNK_OVERLAP = settings_default("default_chunk_overlap", 150)
DEFAULT_TOP_K = settings_default("default_top_k", 5)


class RetrievalStrategy(StrEnum):
    """Supported project retrieval strategies."""

    VANILLA = "vanilla"
    HYBRID = "hybrid"
    RERANK = "rerank"
    HYDE = "hyde"


class ProjectStatus(StrEnum):
    """Lifecycle status for a project."""

    ACTIVE = "active"
    INDEXING = "indexing"
    ERROR = "error"


class ProjectCreate(BaseModel):
    """Input payload for creating a project within the authenticated tenant."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_default=True,
    )

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    retrieval_strategy: RetrievalStrategy = Field(default=RetrievalStrategy.HYBRID)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    """Patch payload for mutable project settings and display fields."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_default=True,
    )

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    retrieval_strategy: RetrievalStrategy | None = None
    status: ProjectStatus | None = None
    chunk_size: int | None = Field(default=None, gt=0)
    chunk_overlap: int | None = Field(default=None, ge=0)
    top_k: int | None = Field(default=None, gt=0, le=100)
    is_deleted: bool | None = None
    deleted_at: datetime | None = None
    updated_by: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_chunking(self) -> "ProjectUpdate":
        """Ensure chunk overlap remains smaller than chunk size when both are supplied."""
        if self.chunk_size is not None and self.chunk_overlap is not None:
            if self.chunk_overlap >= self.chunk_size:
                raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class ProjectPublic(BaseModel):
    """Project representation safe to return to API consumers."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_default=True,
    )

    id: str
    tenant_id: str
    name: str
    description: str | None = None
    retrieval_strategy: RetrievalStrategy
    status: ProjectStatus
    document_count: int
    chunk_count: int
    query_count: int
    total_tokens_used: int
    chunk_size: int
    chunk_overlap: int
    top_k: int
    qdrant_collection_name: str
    is_deleted: bool
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_queried_at: datetime | None = None
    created_by: str | None = None
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Project(BaseModel):
    """MongoDB project document with retrieval configuration and denormalized stats."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_assignment=True,
        validate_default=True,
    )

    _TOUCH_FIELDS: ClassVar[set[str]] = {
        "name",
        "description",
        "retrieval_strategy",
        "status",
        "document_count",
        "chunk_count",
        "query_count",
        "total_tokens_used",
        "chunk_size",
        "chunk_overlap",
        "top_k",
        "last_queried_at",
        "is_deleted",
        "deleted_at",
        "updated_by",
        "metadata",
    }

    id: str = Field(default_factory=new_uuid, frozen=True)
    tenant_id: str = Field(..., min_length=1, frozen=True)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    retrieval_strategy: RetrievalStrategy = Field(default=RetrievalStrategy.HYBRID)
    status: ProjectStatus = Field(default=ProjectStatus.ACTIVE)
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    query_count: int = Field(default=0, ge=0)
    total_tokens_used: int = Field(default=0, ge=0)
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, gt=0)
    chunk_overlap: int = Field(default=DEFAULT_CHUNK_OVERLAP, ge=0)
    top_k: int = Field(default=DEFAULT_TOP_K, gt=0, le=100)
    qdrant_collection_name: str = Field(default="", frozen=True)
    is_deleted: bool = False
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now, frozen=True)
    updated_at: datetime = Field(default_factory=utc_now)
    last_queried_at: datetime | None = None
    created_by: str | None = Field(default=None, frozen=True)
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """Treat blank descriptions as absent."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_consistency(self) -> "Project":
        """Populate derived Qdrant names and validate chunking and soft-delete fields."""
        if not self.qdrant_collection_name:
            object.__setattr__(
                self,
                "qdrant_collection_name",
                f"tenant_{self.tenant_id}_project_{self.id}",
            )

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

        if self.deleted_at is not None and not self.is_deleted:
            object.__setattr__(self, "is_deleted", True)
        if self.is_deleted and self.deleted_at is None:
            object.__setattr__(self, "deleted_at", utc_now())
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        """Refresh updated_at when mutable project fields change."""
        super().__setattr__(name, value)
        if name in self._TOUCH_FIELDS and "updated_at" in self.__class__.model_fields:
            super().__setattr__("updated_at", utc_now())

    def mark_queried(self) -> None:
        """Update query stats after a successful RAG request."""
        self.last_queried_at = utc_now()
        self.query_count += 1

    def soft_delete(self, updated_by: str | None = None) -> None:
        """Mark the project as deleted without removing its MongoDB document."""
        self.is_deleted = True
        self.deleted_at = utc_now()
        if updated_by is not None:
            self.updated_by = updated_by

    def touch(self, updated_by: str | None = None) -> None:
        """Manually refresh audit fields for database update operations."""
        if updated_by is not None:
            self.updated_by = updated_by
        self.updated_at = utc_now()

    def to_public(self) -> ProjectPublic:
        """Return a public project projection."""
        return ProjectPublic.model_validate(self.model_dump())

    def to_mongo(self) -> dict[str, Any]:
        """Return a JSON-compatible document for MongoDB writes."""
        return self.model_dump(mode="json")

