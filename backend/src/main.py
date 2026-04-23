"""FastAPI application entry point for the mini-rag backend."""

from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Literal, TypedDict

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from . import database as database_module
from .agent.memory import ConversationMemory
from .agent.router import QueryRouter
from .agent.tools import WebSearchTool
from .database import close_connections, create_indexes, initialize_connections, test_connections
from .logging_config import configure_logging, get_logger, request_id_context
from .middleware import RateLimitHeadersMiddleware, RequestIDMiddleware
from .routes.admin import router as admin_router
from .routes.analytics import router as analytics_router
from .routes.auth import router as auth_router
from .routes.documents import router as documents_router
from .routes.projects import router as projects_router
from .routes.query import router as query_router
from .routes.settings import router as settings_router
from .routes.streaming import router as streaming_router
from .services.embedding import initialize_embedding_service
from .services.llm import initialize_llm_service
from .services.vector_store import initialize_vector_store

settings = get_settings()
configure_logging(settings)
logger = get_logger(__name__)

ReadinessState = Literal["up", "down", "unknown", "ready", "not_ready"]


class ReadinessPayload(TypedDict, total=False):
    """Readiness payload for a runtime dependency."""

    status: ReadinessState
    latency_ms: int
    model: str
    error: str


def utc_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def create_initial_readiness() -> dict[str, Any]:
    """Create the default readiness state stored on the FastAPI app."""
    return {
        "services": {
            "mongodb": {"status": "unknown"},
            "redis": {"status": "unknown"},
            "qdrant": {"status": "unknown"},
            "reranker_model": {
                "status": "not_ready",
                "model": settings.reranker_model,
            },
        },
        "startup_errors": [],
    }


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Run startup checks with timeout protection and close clients on shutdown."""
    application.state.readiness = create_initial_readiness()
    application.state.cross_encoder = None
    application.state.reranker_model = None
    application.state.embedding_service = None
    application.state.vector_store = None
    application.state.llm_service = None
    application.state.query_router = None
    application.state.conversation_memory = None
    application.state.web_search_tool = None

    try:
        await asyncio.wait_for(
            run_startup(application, settings),
            timeout=settings.startup_timeout_seconds,
        )
    except asyncio.TimeoutError:
        message = f"startup exceeded {settings.startup_timeout_seconds:.1f}s timeout"
        application.state.readiness["startup_errors"].append(message)
        logger.error("%s; continuing in degraded mode", message)
    except Exception as exc:
        message = f"startup failed unexpectedly: {exc}"
        application.state.readiness["startup_errors"].append(message)
        logger.exception("%s; continuing in degraded mode", message)

    try:
        yield
    finally:
        await close_runtime_services(application)
        await close_connections()
        logger.info("application shutdown complete")


async def run_startup(application: FastAPI, app_settings: Settings) -> None:
    """Initialize runtime clients, readiness checks, indexes, and local models."""
    logger.info(
        "starting application name=%s version=%s environment=%s",
        app_settings.app_name,
        app_settings.app_version,
        app_settings.environment,
    )

    await initialize_connections(app_settings)

    application.state.embedding_service = initialize_embedding_service(
        app_settings,
        database_module.redis_client,
    )
    application.state.vector_store = initialize_vector_store(app_settings)
    application.state.llm_service = initialize_llm_service(app_settings)

    dependency_status = await test_connections(
        app_settings,
        timeout_seconds=app_settings.service_check_timeout_seconds,
    )
    qdrant_status = await check_qdrant(app_settings)

    readiness = application.state.readiness
    readiness["services"]["mongodb"] = dependency_status["mongodb"]
    readiness["services"]["redis"] = dependency_status["redis"]
    readiness["services"]["qdrant"] = qdrant_status

    log_dependency_status("mongodb", dependency_status["mongodb"])
    log_dependency_status("redis", dependency_status["redis"])
    log_dependency_status("qdrant", qdrant_status)

    if dependency_status["mongodb"]["status"] == "up":
        await create_mongodb_indexes(readiness)
    else:
        logger.warning("skipping MongoDB index creation because MongoDB is down")

    model_status, model = await load_reranker_model(app_settings)
    readiness["services"]["reranker_model"] = model_status
    application.state.cross_encoder = model
    application.state.reranker_model = model

    initialize_runtime_components(application, model)

    if is_application_ready(readiness["services"]):
        logger.info("application startup complete and ready")
    else:
        logger.warning("application startup complete in degraded mode")


def initialize_runtime_components(application: FastAPI, cross_encoder_model: Any | None) -> None:
    """Wire retrievers and agent helpers to initialized singleton services."""
    if database_module.mongo_database is None:
        logger.warning("skipping retriever initialization because MongoDB is unavailable")
        return

    from .retrieval.factory import initialize_retrievers

    initialize_retrievers(
        embedding_svc=application.state.embedding_service,
        vector_store=application.state.vector_store,
        redis_client=database_module.redis_client,
        db=database_module.mongo_database,
        cross_encoder_model=cross_encoder_model,
        llm_svc=application.state.llm_service,
    )
    application.state.query_router = QueryRouter(application.state.llm_service.client, model=settings.openai_model)
    application.state.conversation_memory = ConversationMemory(database_module.redis_client)
    application.state.web_search_tool = WebSearchTool(application.state.llm_service.client, model=settings.openai_model)
    logger.info("all runtime services initialized")


async def create_mongodb_indexes(readiness: dict[str, Any]) -> None:
    """Create MongoDB indexes and record degraded startup errors on failure."""
    try:
        await create_indexes()
        logger.info("mongodb indexes created successfully")
    except Exception as exc:
        message = f"mongodb index creation failed: {exc}"
        readiness["startup_errors"].append(message)
        logger.warning("%s; continuing in degraded mode", message)


async def load_reranker_model(app_settings: Settings) -> tuple[ReadinessPayload, Any | None]:
    """Load the reranker model with timeout protection."""
    started_at = time.perf_counter()
    try:
        model = await asyncio.wait_for(
            asyncio.to_thread(_build_cross_encoder, app_settings.reranker_model),
            timeout=app_settings.startup_timeout_seconds,
        )
        logger.info("reranker model loaded model=%s", app_settings.reranker_model)
        return (
            {
                "status": "ready",
                "model": app_settings.reranker_model,
                "latency_ms": elapsed_ms(started_at),
            },
            model,
        )
    except asyncio.TimeoutError:
        message = f"reranker model loading timed out after {app_settings.startup_timeout_seconds:.1f}s"
        logger.warning("%s; continuing in degraded mode", message)
        return (
            {
                "status": "not_ready",
                "model": app_settings.reranker_model,
                "latency_ms": elapsed_ms(started_at),
                "error": message,
            },
            None,
        )
    except Exception as exc:
        logger.warning("reranker model loading failed: %s; continuing in degraded mode", exc)
        return (
            {
                "status": "not_ready",
                "model": app_settings.reranker_model,
                "latency_ms": elapsed_ms(started_at),
                "error": str(exc),
            },
            None,
        )


def _build_cross_encoder(model_name: str) -> Any:
    """Create the sentence-transformers CrossEncoder instance."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


async def close_runtime_services(application: FastAPI) -> None:
    """Close application-owned service clients before dependency shutdown."""
    for service_name in ("embedding_service", "vector_store", "llm_service"):
        service = getattr(application.state, service_name, None)
        close_method = getattr(service, "close", None)
        if close_method is None:
            continue
        close_result = close_method()
        if inspect.isawaitable(close_result):
            await close_result


async def check_qdrant(app_settings: Settings) -> ReadinessPayload:
    """Check Qdrant readiness with a bounded timeout."""
    started_at = time.perf_counter()
    client = AsyncQdrantClient(
        url=app_settings.qdrant_url,
        timeout=app_settings.service_check_timeout_seconds,
    )

    try:
        await asyncio.wait_for(
            client.get_collections(),
            timeout=app_settings.service_check_timeout_seconds,
        )
        return {"status": "up", "latency_ms": elapsed_ms(started_at)}
    except Exception as exc:
        logger.warning("qdrant readiness check failed: %s", exc)
        return {
            "status": "down",
            "latency_ms": elapsed_ms(started_at),
            "error": str(exc),
        }
    finally:
        close_result = client.close()
        if inspect.isawaitable(close_result):
            await close_result


def log_dependency_status(name: str, payload: dict[str, Any]) -> None:
    """Log a startup dependency status in normal or degraded mode."""
    if payload.get("status") == "up":
        logger.info("%s connection ready latency_ms=%s", name, payload.get("latency_ms"))
    else:
        logger.warning(
            "%s unavailable during startup error=%s; continuing in degraded mode",
            name,
            payload.get("error", "unknown"),
        )


def is_application_ready(services: dict[str, dict[str, Any]]) -> bool:
    """Return whether all required runtime dependencies are ready."""
    return (
        services.get("mongodb", {}).get("status") == "up"
        and services.get("redis", {}).get("status") == "up"
        and services.get("qdrant", {}).get("status") == "up"
        and services.get("reranker_model", {}).get("status") == "ready"
    )


def elapsed_ms(started_at: float) -> int:
    """Return elapsed milliseconds since the supplied perf_counter value."""
    return max(0, round((time.perf_counter() - started_at) * 1000))


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)
app.state.readiness = create_initial_readiness()
app.state.cross_encoder = None
app.state.reranker_model = None
app.state.embedding_service = None
app.state.vector_store = None
app.state.llm_service = None
app.state.query_router = None
app.state.conversation_memory = None
app.state.web_search_tool = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted({"http://localhost:3000", "http://frontend:3000", *settings.allowed_origins})
    if settings.environment != "development"
    else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimitHeadersMiddleware)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
app.include_router(documents_router, prefix="/api/documents", tags=["documents"])
app.include_router(query_router, prefix="/api/query", tags=["query"])
app.include_router(streaming_router, prefix="/api/query", tags=["streaming"])
app.include_router(analytics_router, prefix="/api/analytics", tags=["analytics"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])


@app.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    """Return lightweight liveness without checking external dependencies."""
    readiness = getattr(app.state, "readiness", create_initial_readiness())
    return {
        "status": "healthy",
        "timestamp": utc_now(),
        "version": settings.app_version,
        "environment": settings.environment,
        "services": readiness.get("services", {}),
    }


@app.get("/ready", tags=["health"])
async def ready() -> JSONResponse:
    """Return readiness status for MongoDB, Redis, Qdrant, and model loading."""
    dependency_status = await test_connections(
        settings,
        timeout_seconds=settings.service_check_timeout_seconds,
    )
    qdrant_status = await check_qdrant(settings)

    readiness = app.state.readiness
    services = {
        "mongodb": dependency_status["mongodb"],
        "redis": dependency_status["redis"],
        "qdrant": qdrant_status,
        "reranker_model": readiness["services"]["reranker_model"],
    }
    readiness["services"].update(services)

    is_ready = is_application_ready(services)
    payload: dict[str, Any] = {
        "status": "ready" if is_ready else "not_ready",
        "timestamp": utc_now(),
        "version": settings.app_version,
        "environment": settings.environment,
        "services": services,
    }
    if readiness.get("startup_errors"):
        payload["startup_errors"] = readiness["startup_errors"]

    return JSONResponse(
        status_code=(
            status.HTTP_200_OK
            if is_ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content=payload,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return clean JSON for expected HTTP exceptions."""
    request_id = request_id_context.get()
    logger.warning(
        "http exception path=%s status_code=%s detail=%s request_id=%s",
        request.url.path,
        exc.status_code,
        exc.detail,
        request_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
        content={"detail": exc.detail, "request_id": request_id},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected errors and return a clean JSON error response."""
    request_id = request_id_context.get()
    logger.exception(
        "unhandled exception path=%s request_id=%s",
        request.url.path,
        request_id,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error. Check logs for details.", "request_id": request_id},
    )
