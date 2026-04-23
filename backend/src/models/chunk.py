"""Chunk data models for text segments indexed into Qdrant."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def default_embedding_model() -> str:
    """Read the configured embedding model without instantiating settings."""
    if Settings is None:
        return "text-embedding-ada-002"
    field = Settings.model_fields.get("openai_embedding_model")
    value = getattr(field, "default", "text-embedding-ada-002")
    return value if isinstance(value, str) else "text-embedding-ada-002"


DEFAULT_EMBEDDING_MODEL = default_embedding_model()


class ChunkCreate(BaseModel):
    """Input payload for creating a chunk from processed document text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    document_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    chunk_index: int = Field(..., ge=0)
    start_char: int = Field(..., ge=0)
    end_char: int = Field(..., ge=0)
    page_number: int | None = Field(default=None, ge=1)
    embedding_model: str = Field(default=DEFAULT_EMBEDDING_MODEL, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_offsets(self) -> "ChunkCreate":
        """Ensure chunk offsets describe a non-empty range."""
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class Chunk(BaseModel):
    """MongoDB chunk metadata paired with a Qdrant vector point."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
    )

    id: str = Field(default_factory=new_uuid, frozen=True)
    tenant_id: str = Field(..., min_length=1, frozen=True)
    project_id: str = Field(..., min_length=1, frozen=True)
    document_id: str = Field(..., min_length=1, frozen=True)
    text: str = Field(..., min_length=1)
    text_hash: str = Field(default="", max_length=32)
    chunk_index: int = Field(..., ge=0)
    start_char: int = Field(..., ge=0)
    end_char: int = Field(..., ge=0)
    page_number: int | None = Field(default=None, ge=1)
    qdrant_id: str = Field(default="", frozen=True)
    embedding_model: str = Field(default=DEFAULT_EMBEDDING_MODEL, min_length=1)
    char_count: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now, frozen=True)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_and_validate_derived_fields(self) -> "Chunk":
        """Derive hash, Qdrant id, and size counters from text."""
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")

        expected_hash = hashlib.md5(self.text.encode("utf-8")).hexdigest()
        if not self.text_hash:
            object.__setattr__(self, "text_hash", expected_hash)
        elif self.text_hash != expected_hash:
            raise ValueError("text_hash must match the MD5 hash of text")

        if not self.qdrant_id:
            object.__setattr__(self, "qdrant_id", self.id)
        elif self.qdrant_id != self.id:
            raise ValueError("qdrant_id must match id")

        char_count = len(self.text)
        if self.char_count == 0:
            object.__setattr__(self, "char_count", char_count)
        elif self.char_count != char_count:
            raise ValueError("char_count must match len(text)")

        token_count = char_count // 4
        if self.token_count == 0:
            object.__setattr__(self, "token_count", token_count)
        elif self.token_count != token_count:
            raise ValueError("token_count must match char_count // 4")
        return self

    def to_mongo(self) -> dict[str, Any]:
        """Return a JSON-compatible document for MongoDB writes."""
        return self.model_dump(mode="json")
