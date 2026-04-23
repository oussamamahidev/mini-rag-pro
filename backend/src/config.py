"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the mini-rag backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "mini-rag"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production", "test"] = "development"
    secret_key: str = Field(..., min_length=1)
    admin_api_key: str = Field(..., min_length=1)
    log_level: str = "INFO"
    startup_timeout_seconds: float = Field(default=30.0, gt=0)
    service_check_timeout_seconds: float = Field(default=3.0, gt=0)
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    storage_path: str = "storage"

    # OpenAI
    openai_api_key: str = Field(..., min_length=1)
    openai_model: str = "gpt-3.5-turbo"
    openai_embedding_model: str = "text-embedding-ada-002"
    openai_embedding_dimensions: int = 1536

    # MongoDB
    mongo_url: str = Field(..., min_length=1)
    mongo_db_name: str = "minirag"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    vector_score_threshold: float = Field(default=0.6, ge=0, le=1)
    vector_payload_max_bytes: int = Field(default=65536, gt=0)

    # RAG settings
    default_chunk_size: int = 800
    default_chunk_overlap: int = 150
    default_top_k: int = 5
    default_retrieval_strategy: str = "hybrid"

    # Rate limiting
    default_rate_limit_per_hour: int = 100

    # Reranker
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    @field_validator("secret_key", "admin_api_key", "openai_api_key", "mongo_url")
    @classmethod
    def validate_required_strings(cls, value: str, info: ValidationInfo) -> str:
        """Reject missing or blank required environment values."""
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be set and cannot be blank")
        return normalized

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Validate and normalize the configured log level."""
        normalized = value.strip().upper()
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if normalized not in valid_levels:
            raise ValueError(f"log_level must be one of: {', '.join(sorted(valid_levels))}")
        return normalized


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
