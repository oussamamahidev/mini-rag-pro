"""Document data models for uploaded files and processing lifecycle state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


def new_uuid() -> str:
    """Return a UUID4 string suitable for application-level MongoDB ids."""
    return str(uuid4())


class FileType(StrEnum):
    """Supported document file types."""

    PDF = "pdf"
    TXT = "txt"
    DOCX = "docx"
    MD = "md"


class DocumentStatus(StrEnum):
    """Processing status for an uploaded document."""

    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


STATUS_TRANSITIONS: dict[str, list[str]] = {
    DocumentStatus.QUEUED.value: [DocumentStatus.PROCESSING.value, DocumentStatus.ERROR.value],
    DocumentStatus.PROCESSING.value: [DocumentStatus.INDEXING.value, DocumentStatus.ERROR.value],
    DocumentStatus.INDEXING.value: [DocumentStatus.READY.value, DocumentStatus.ERROR.value],
    DocumentStatus.READY.value: [DocumentStatus.INDEXING.value, DocumentStatus.ERROR.value],
    DocumentStatus.ERROR.value: [DocumentStatus.QUEUED.value, DocumentStatus.PROCESSING.value],
}


class DocumentCreate(BaseModel):
    """Input payload for registering an uploaded document."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_default=True,
    )

    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    original_filename: str = Field(..., min_length=1, max_length=255)
    stored_filename: str = Field(..., min_length=1, max_length=255)
    file_path: str = Field(..., min_length=1)
    file_size_bytes: int = Field(..., gt=0)
    file_type: FileType | None = None
    mime_type: str = Field(..., min_length=1, max_length=200)
    created_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_file_type(cls, data: Any) -> Any:
        """Infer file_type from the original filename when omitted."""
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if values.get("file_type") is None and values.get("original_filename"):
            values["file_type"] = detect_file_type(values["original_filename"])
        return values


class Document(BaseModel):
    """MongoDB document record for uploaded source files."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_assignment=True,
        validate_default=True,
    )

    _TOUCH_FIELDS: ClassVar[set[str]] = {
        "original_filename",
        "stored_filename",
        "file_path",
        "file_size_bytes",
        "file_type",
        "mime_type",
        "status",
        "indexing_progress",
        "error_message",
        "celery_task_id",
        "chunk_count",
        "page_count",
        "character_count",
        "processing_started_at",
        "processing_completed_at",
        "is_deleted",
        "deleted_at",
        "updated_by",
        "metadata",
    }

    id: str = Field(default_factory=new_uuid, frozen=True)
    tenant_id: str = Field(..., min_length=1, frozen=True)
    project_id: str = Field(..., min_length=1, frozen=True)
    original_filename: str = Field(..., min_length=1, max_length=255)
    stored_filename: str = Field(..., min_length=1, max_length=255)
    file_path: str = Field(..., min_length=1)
    file_size_bytes: int = Field(..., gt=0)
    file_type: FileType
    mime_type: str = Field(..., min_length=1, max_length=200)
    status: DocumentStatus = Field(default=DocumentStatus.QUEUED)
    indexing_progress: int = Field(default=0, ge=0, le=100)
    error_message: str | None = None
    celery_task_id: str | None = None
    chunk_count: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    character_count: int | None = Field(default=None, ge=0)
    is_deleted: bool = False
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now, frozen=True)
    updated_at: datetime = Field(default_factory=utc_now)
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    created_by: str | None = Field(default=None, frozen=True)
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_file_type(cls, data: Any) -> Any:
        """Infer file_type from the original filename when omitted."""
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if values.get("file_type") is None and values.get("original_filename"):
            values["file_type"] = detect_file_type(values["original_filename"])
        return values

    @field_validator("original_filename", "stored_filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """Reject path-like filenames for upload metadata fields."""
        normalized = value.strip()
        if PurePath(normalized).name != normalized:
            raise ValueError("filename fields must not include path components")
        return normalized

    @field_validator("error_message")
    @classmethod
    def normalize_error_message(cls, value: str | None) -> str | None:
        """Treat blank error messages as absent."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_consistency(self) -> "Document":
        """Validate lifecycle timestamps, error state, and soft-delete consistency."""
        if self.status == DocumentStatus.ERROR.value and not self.error_message:
            raise ValueError("error_message is required when status is error")

        if self.status == DocumentStatus.READY.value and self.indexing_progress < 100:
            object.__setattr__(self, "indexing_progress", 100)

        if self.processing_started_at and self.processing_completed_at:
            if self.processing_completed_at < self.processing_started_at:
                raise ValueError("processing_completed_at cannot be before processing_started_at")

        if self.deleted_at is not None and not self.is_deleted:
            object.__setattr__(self, "is_deleted", True)
        if self.is_deleted and self.deleted_at is None:
            object.__setattr__(self, "deleted_at", utc_now())
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        """Refresh updated_at when mutable document fields change."""
        super().__setattr__(name, value)
        if name in self._TOUCH_FIELDS and "updated_at" in self.__class__.model_fields:
            super().__setattr__("updated_at", utc_now())

    def can_transition_to(self, status: DocumentStatus | str) -> bool:
        """Return whether this document can move to the requested status."""
        next_status = status.value if isinstance(status, DocumentStatus) else str(status)
        return next_status in STATUS_TRANSITIONS.get(str(self.status), [])

    def transition_to(
        self,
        status: DocumentStatus | str,
        *,
        error_message: str | None = None,
        celery_task_id: str | None = None,
    ) -> None:
        """Apply a validated processing status transition."""
        next_status = status.value if isinstance(status, DocumentStatus) else str(status)
        if not self.can_transition_to(next_status):
            raise ValueError(f"invalid document status transition: {self.status} -> {next_status}")

        now = utc_now()
        if next_status == DocumentStatus.ERROR.value:
            self.error_message = error_message or self.error_message or "document processing failed"

        self.status = next_status
        if next_status == DocumentStatus.PROCESSING.value:
            self.processing_started_at = self.processing_started_at or now
        if next_status == DocumentStatus.INDEXING.value:
            self.indexing_progress = max(self.indexing_progress, 1)
        if next_status == DocumentStatus.READY.value:
            self.indexing_progress = 100
            self.processing_completed_at = now
            self.error_message = None
        if celery_task_id is not None:
            self.celery_task_id = celery_task_id

    def soft_delete(self, updated_by: str | None = None) -> None:
        """Mark the document as deleted without removing its MongoDB document."""
        self.is_deleted = True
        self.deleted_at = utc_now()
        if updated_by is not None:
            self.updated_by = updated_by

    def touch(self, updated_by: str | None = None) -> None:
        """Manually refresh audit fields for database update operations."""
        if updated_by is not None:
            self.updated_by = updated_by
        self.updated_at = utc_now()

    def to_mongo(self) -> dict[str, Any]:
        """Return a JSON-compatible document for MongoDB writes."""
        return self.model_dump(mode="json")


def detect_file_type(filename: str) -> str:
    """Detect a supported file type from a filename extension."""
    extension = PurePath(filename).suffix.lower().lstrip(".")
    supported = {file_type.value for file_type in FileType}
    if extension not in supported:
        raise ValueError(f"unsupported file type: {extension or 'missing extension'}")
    return extension
