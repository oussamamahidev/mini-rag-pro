"""Query log data models for RAG analytics, routing, and evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


def new_uuid() -> str:
    """Return a UUID4 string suitable for application-level MongoDB ids."""
    return str(uuid4())


class RoutingDecision(StrEnum):
    """High-level query routing decisions."""

    RAG = "rag"
    WEB_SEARCH = "web_search"
    DIRECT = "direct"
    CLARIFY = "clarify"


class QueryRetrievalStrategy(StrEnum):
    """Retrieval or response strategy used for a query."""

    VANILLA = "vanilla"
    HYBRID = "hybrid"
    RERANK = "rerank"
    HYDE = "hyde"
    WEB_SEARCH = "web_search"
    DIRECT = "direct"
    CLARIFY = "clarify"


class EvaluationStatus(StrEnum):
    """Asynchronous RAGAS evaluation status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RetrievedChunkRef(BaseModel):
    """Snapshot of a retrieved chunk used to answer a query."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chunk_id: str = Field(..., min_length=1)
    document_id: str = Field(..., min_length=1)
    document_name: str = Field(..., min_length=1, max_length=255)
    text: str = Field(..., min_length=1)
    score: float = Field(..., ge=0)
    reranker_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryLogCreate(BaseModel):
    """Input payload for logging a completed query."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_default=True,
    )

    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    session_id: str | None = None
    query: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    routing_decision: RoutingDecision | None = None
    routing_reason: str | None = Field(default=None, max_length=500)
    routing_confidence: float | None = Field(default=None, ge=0, le=1)
    retrieval_strategy: QueryRetrievalStrategy
    retrieved_chunks: list[RetrievedChunkRef] = Field(default_factory=list)
    model: str = Field(..., min_length=1)
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float = Field(..., ge=0)
    total_latency_ms: float = Field(..., ge=0)
    routing_latency_ms: float | None = Field(default=None, ge=0)
    retrieval_latency_ms: float = Field(..., ge=0)
    generation_latency_ms: float = Field(..., ge=0)
    fallback_triggered: bool = False
    no_answer: bool = False
    no_answer_reason: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_total_tokens(cls, data: Any) -> Any:
        """Derive total token count when only prompt and completion are supplied."""
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if values.get("total_tokens") is None:
            prompt_tokens = values.get("prompt_tokens")
            completion_tokens = values.get("completion_tokens")
            if prompt_tokens is not None and completion_tokens is not None:
                values["total_tokens"] = int(prompt_tokens) + int(completion_tokens)
        return values


class QueryLog(BaseModel):
    """MongoDB query log with retrieval snapshots, usage, latency, and RAGAS metrics."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_assignment=True,
        validate_default=True,
    )

    id: str = Field(default_factory=new_uuid, frozen=True)
    tenant_id: str = Field(..., min_length=1, frozen=True)
    project_id: str = Field(..., min_length=1, frozen=True)
    session_id: str | None = None
    query: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    routing_decision: RoutingDecision | None = None
    routing_reason: str | None = Field(default=None, max_length=500)
    routing_confidence: float | None = Field(default=None, ge=0, le=1)
    retrieval_strategy: QueryRetrievalStrategy
    retrieved_chunks: list[RetrievedChunkRef] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieval_scores: list[float] = Field(default_factory=list)
    reranker_scores: list[float | None] = Field(default_factory=list)
    model: str = Field(..., min_length=1)
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(..., ge=0)
    estimated_cost_usd: float = Field(..., ge=0)
    total_latency_ms: float = Field(..., ge=0)
    routing_latency_ms: float | None = Field(default=None, ge=0)
    retrieval_latency_ms: float = Field(..., ge=0)
    generation_latency_ms: float = Field(..., ge=0)
    evaluation_status: EvaluationStatus = Field(default=EvaluationStatus.PENDING)
    evaluation_started_at: datetime | None = None
    evaluation_task_id: str | None = Field(default=None, max_length=255)
    evaluation_runtime_ms: float | None = Field(default=None, ge=0)
    evaluation_cost_usd: float = Field(default=0.0, ge=0)
    evaluation_error: str | None = Field(default=None, max_length=1000)
    evaluation_version: str | None = Field(default=None, max_length=100)
    evaluation_backend: str | None = Field(default=None, max_length=50)
    evaluation_provider: str | None = Field(default=None, max_length=100)
    evaluation_model: str | None = Field(default=None, max_length=255)
    evaluation_model_version: str | None = Field(default=None, max_length=255)
    evaluation_metadata: dict[str, Any] = Field(default_factory=dict)
    faithfulness: float | None = Field(default=None, ge=0, le=1)
    answer_relevancy: float | None = Field(default=None, ge=0, le=1)
    context_precision: float | None = Field(default=None, ge=0, le=1)
    context_recall: float | None = Field(default=None, ge=0, le=1)
    evaluated_at: datetime | None = None
    fallback_triggered: bool = False
    no_answer: bool = False
    no_answer_reason: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utc_now, frozen=True)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_total_tokens(cls, data: Any) -> Any:
        """Derive total token count when only prompt and completion are supplied."""
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if values.get("total_tokens") is None:
            prompt_tokens = values.get("prompt_tokens")
            completion_tokens = values.get("completion_tokens")
            if prompt_tokens is not None and completion_tokens is not None:
                values["total_tokens"] = int(prompt_tokens) + int(completion_tokens)
        return values

    @field_validator("routing_reason", "no_answer_reason")
    @classmethod
    def normalize_optional_reason(cls, value: str | None) -> str | None:
        """Treat blank reason fields as absent."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_and_derive_fields(self) -> "QueryLog":
        """Keep derived retrieval fields and usage totals consistent."""
        expected_total = self.prompt_tokens + self.completion_tokens
        if self.total_tokens != expected_total:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")

        chunk_ids = [chunk.chunk_id for chunk in self.retrieved_chunks]
        scores = [chunk.score for chunk in self.retrieved_chunks]
        reranker_scores = [chunk.reranker_score for chunk in self.retrieved_chunks]
        object.__setattr__(self, "retrieved_chunk_ids", chunk_ids)
        object.__setattr__(self, "retrieval_scores", scores)
        object.__setattr__(self, "reranker_scores", reranker_scores)

        if self.total_latency_ms < max(self.retrieval_latency_ms, self.generation_latency_ms):
            raise ValueError("total_latency_ms cannot be lower than an individual latency component")

        terminal_statuses = {
            EvaluationStatus.COMPLETED.value,
            EvaluationStatus.FAILED.value,
            EvaluationStatus.SKIPPED.value,
        }
        active_statuses = {
            EvaluationStatus.PENDING.value,
            EvaluationStatus.IN_PROGRESS.value,
        }
        if self.evaluation_status in terminal_statuses and self.evaluated_at is None:
            object.__setattr__(self, "evaluated_at", utc_now())
        if self.evaluation_status in active_statuses and self.evaluated_at is not None:
            raise ValueError("evaluated_at must be empty while evaluation is active")

        if self.no_answer:
            object.__setattr__(self, "fallback_triggered", True)
        return self

    def to_mongo(self) -> dict[str, Any]:
        """Return a JSON-compatible document for MongoDB writes."""
        return self.model_dump(mode="json")
