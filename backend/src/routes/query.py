"""RAG query endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from .. import database as database_module
from ..agent.memory import ConversationMemory
from ..agent.router import QueryRouter, RoutingDecision
from ..agent.tools import WebSearchTool
from ..config import Settings, get_settings
from ..database import get_db
from ..evaluation.ragas_eval import EVALUATION_VERSION
from ..logging_config import get_logger
from ..middleware.auth import get_current_tenant, verify_project_ownership
from ..models.query_log import EvaluationStatus, QueryLog, RetrievedChunkRef
from ..models.tenant import Tenant
from ..retrieval.base import RetrievedChunk
from ..services.llm import LLMService
from ..services.vector_store import SearchResult
from ..retrieval.factory import get_retriever

logger = get_logger(__name__)

router = APIRouter()

NO_DOCUMENTS_MESSAGE = "No relevant documents found. Please upload documents first."
INSUFFICIENT_CONTEXT_MESSAGE = "I don't have enough information in the provided documents to answer this."
INSUFFICIENT_CONFIDENCE_THRESHOLD = 0.6
SKIPPED_EVALUATION_ROUTES = {"direct", "web_search", "clarify"}


class QueryRequest(BaseModel):
    """Request payload for a RAG query."""

    text: str = Field(..., min_length=1, max_length=2000, description="The user's question")
    project_id: str = Field(..., min_length=1)
    strategy: str = "hybrid"
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class SourceRef(BaseModel):
    """Document chunk used as answer evidence."""

    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float
    page_number: int | None


class AnswerSegment(BaseModel):
    """Answer text segment and its supporting chunk ids."""

    text: str
    supporting_chunk_ids: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """RAG query response returned to API consumers."""

    answer: str
    sources: list[SourceRef]
    retrieval_strategy: str
    routing_decision: str | None
    routing_reason: str | None = None
    routing_reason_code: str | None = None
    routing_confidence: float | None = None
    total_latency_ms: float
    routing_latency_ms: float | None = None
    retrieval_latency_ms: float
    generation_latency_ms: float
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    query_log_id: str
    answer_segments: list[AnswerSegment]
    confidence: float
    insufficient_evidence: bool


class QueryHistoryItem(BaseModel):
    """Compact query log item for history views."""

    query: str
    answer: str
    strategy: str
    latency_ms: float
    faithfulness: float | None = None
    created_at: datetime | None = None


class QueryHistoryResponse(BaseModel):
    """Paginated query history response."""

    items: list[QueryHistoryItem]
    page: int
    per_page: int
    total: int


class SessionHistoryMessage(BaseModel):
    """One chat message stored in conversation memory."""

    role: str
    content: str


@router.post("", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    http_request: Request,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> QueryResponse:
    """Route a query through RAG, web search, direct answer, or clarification."""
    started_at = time.perf_counter()
    project = await load_active_project(db, tenant, request.project_id)

    llm_service = getattr(http_request.app.state, "llm_service", None) or LLMService(settings)
    owns_llm_service = getattr(http_request.app.state, "llm_service", None) is None
    model = settings.openai_model
    memory = ConversationMemory(database_module.redis_client, tenant.id)
    requested_retrieval_strategy = select_requested_strategy(request.strategy, project, settings)

    try:
        history = await memory.get_history(request.session_id) if request.session_id else []
        routing_started_at = time.perf_counter()
        query_router = getattr(http_request.app.state, "query_router", None) or QueryRouter(llm_service.client, model=model)
        routing = await query_router.route(
            request.text,
            history,
            project_default_strategy=requested_retrieval_strategy,
        )
        routing_latency_ms = elapsed_ms(routing_started_at)
        logger.info(
            "query routing tenant_id=%s project_id=%s decision=%s reason_code=%s confidence=%.2f strategy=%s metadata=%s",
            tenant.id,
            request.project_id,
            routing.decision,
            routing.reason_code,
            routing.confidence,
            routing.retrieval_strategy,
            routing.metadata,
        )

        if routing.decision == "web_search":
            web_started_at = time.perf_counter()
            web_tool = WebSearchTool(llm_service.client, model=model)
            answer, web_results = await web_tool.search_and_answer(request.text)
            web_latency_ms = elapsed_ms(web_started_at)
            sources = web_sources_from_results(web_results)
            confidence = round(routing.confidence if web_results else 0.0, 3)
            insufficient_evidence = not web_results or "couldn't find current information" in answer.lower()
            no_answer_reason = "web_search_no_results" if insufficient_evidence else None
            total_latency_ms = elapsed_ms(started_at)
            query_log = await save_query_log(
                db=db,
                tenant_id=tenant.id,
                project_id=request.project_id,
                session_id=request.session_id,
                query=request.text,
                answer=answer,
                chunks=[],
                retrieval_strategy="web_search",
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=web_latency_ms,
                generation_latency_ms=0.0,
                confidence=confidence,
                insufficient_evidence=insufficient_evidence,
                no_answer_reason=no_answer_reason,
                requested_strategy=request.strategy,
                routing=routing,
                metadata={"web_sources": web_results},
            )
            queue_query_evaluation(query_log)
            await save_memory_turn(memory, request.session_id, request.text, answer)
            schedule_project_query_increment(db, tenant.id, request.project_id, total_tokens=0)
            return QueryResponse(
                answer=answer,
                sources=sources,
                retrieval_strategy="web_search",
                routing_decision=routing.decision,
                routing_reason=routing.reason,
                routing_reason_code=routing.reason_code,
                routing_confidence=routing.confidence,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=web_latency_ms,
                generation_latency_ms=0.0,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                query_log_id=query_log.id,
                answer_segments=build_answer_segments(answer, sources, insufficient_evidence),
                confidence=confidence,
                insufficient_evidence=insufficient_evidence,
            )

        if routing.decision == "direct":
            generation_started_at = time.perf_counter()
            try:
                answer, prompt_tokens, completion_tokens = await llm_service.generate_direct_answer(
                    request.text,
                    conversation_history=history,
                    model=model,
                )
            except Exception as exc:
                logger.exception("direct answer generation failed project_id=%s tenant_id=%s", request.project_id, tenant.id)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Answer generation failed: {exc}",
                ) from exc

            generation_latency_ms = elapsed_ms(generation_started_at)
            estimated_cost_usd = llm_service.estimate_cost(prompt_tokens, completion_tokens, model)
            total_latency_ms = elapsed_ms(started_at)
            query_log = await save_query_log(
                db=db,
                tenant_id=tenant.id,
                project_id=request.project_id,
                session_id=request.session_id,
                query=request.text,
                answer=answer,
                chunks=[],
                retrieval_strategy="direct",
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=estimated_cost_usd,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=0.0,
                generation_latency_ms=generation_latency_ms,
                confidence=routing.confidence,
                insufficient_evidence=False,
                no_answer_reason=None,
                requested_strategy=request.strategy,
                routing=routing,
                metadata={},
            )
            queue_query_evaluation(query_log)
            await save_memory_turn(memory, request.session_id, request.text, answer)
            schedule_project_query_increment(
                db,
                tenant.id,
                request.project_id,
                total_tokens=prompt_tokens + completion_tokens,
            )
            return QueryResponse(
                answer=answer,
                sources=[],
                retrieval_strategy="direct",
                routing_decision=routing.decision,
                routing_reason=routing.reason,
                routing_reason_code=routing.reason_code,
                routing_confidence=routing.confidence,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=0.0,
                generation_latency_ms=generation_latency_ms,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=estimated_cost_usd,
                query_log_id=query_log.id,
                answer_segments=[AnswerSegment(text=answer, supporting_chunk_ids=[])],
                confidence=routing.confidence,
                insufficient_evidence=False,
            )

        if routing.decision == "clarify":
            answer = "Could you clarify what you want to know or which document/topic you mean?"
            total_latency_ms = elapsed_ms(started_at)
            query_log = await save_query_log(
                db=db,
                tenant_id=tenant.id,
                project_id=request.project_id,
                session_id=request.session_id,
                query=request.text,
                answer=answer,
                chunks=[],
                retrieval_strategy="clarify",
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=0.0,
                generation_latency_ms=0.0,
                confidence=routing.confidence,
                insufficient_evidence=True,
                no_answer_reason="clarification_needed",
                requested_strategy=request.strategy,
                routing=routing,
                metadata={},
            )
            queue_query_evaluation(query_log)
            await save_memory_turn(memory, request.session_id, request.text, answer)
            schedule_project_query_increment(db, tenant.id, request.project_id, total_tokens=0)
            return QueryResponse(
                answer=answer,
                sources=[],
                retrieval_strategy="clarify",
                routing_decision=routing.decision,
                routing_reason=routing.reason,
                routing_reason_code=routing.reason_code,
                routing_confidence=routing.confidence,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=0.0,
                generation_latency_ms=0.0,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                query_log_id=query_log.id,
                answer_segments=[AnswerSegment(text=answer, supporting_chunk_ids=[])],
                confidence=routing.confidence,
                insufficient_evidence=True,
            )

        predicted_retrieval_strategy = requested_retrieval_strategy
        retriever = get_retriever(predicted_retrieval_strategy)
        try:
            chunks, retrieval_latency_ms = await retriever.retrieve(
                request.text,
                request.project_id,
                tenant.id,
                top_k=request.top_k,
            )
        except Exception as exc:
            logger.exception("query retrieval failed project_id=%s tenant_id=%s", request.project_id, tenant.id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Retrieval failed: {exc}",
            ) from exc

        confidence = calculate_confidence(chunks)
        sources = [source_from_chunk(chunk) for chunk in chunks]

        if not chunks:
            total_latency_ms = elapsed_ms(started_at)
            query_log = await save_query_log(
                db=db,
                tenant_id=tenant.id,
                project_id=request.project_id,
                session_id=request.session_id,
                query=request.text,
                answer=NO_DOCUMENTS_MESSAGE,
                chunks=chunks,
                retrieval_strategy=predicted_retrieval_strategy,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=retrieval_latency_ms,
                generation_latency_ms=0.0,
                confidence=confidence,
                insufficient_evidence=True,
                no_answer_reason="no_relevant_chunks",
                requested_strategy=request.strategy,
                routing=routing,
                metadata={"executed_retriever": retriever.get_strategy_name()},
            )
            queue_query_evaluation(query_log)
            await save_memory_turn(memory, request.session_id, request.text, NO_DOCUMENTS_MESSAGE)
            schedule_project_query_increment(db, tenant.id, request.project_id, total_tokens=0)
            return QueryResponse(
                answer=NO_DOCUMENTS_MESSAGE,
                sources=[],
                retrieval_strategy=predicted_retrieval_strategy,
                routing_decision=routing.decision,
                routing_reason=routing.reason,
                routing_reason_code=routing.reason_code,
                routing_confidence=routing.confidence,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=retrieval_latency_ms,
                generation_latency_ms=0.0,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                query_log_id=query_log.id,
                answer_segments=[AnswerSegment(text=NO_DOCUMENTS_MESSAGE, supporting_chunk_ids=[])],
                confidence=confidence,
                insufficient_evidence=True,
            )

        if confidence < INSUFFICIENT_CONFIDENCE_THRESHOLD:
            total_latency_ms = elapsed_ms(started_at)
            query_log = await save_query_log(
                db=db,
                tenant_id=tenant.id,
                project_id=request.project_id,
                session_id=request.session_id,
                query=request.text,
                answer=INSUFFICIENT_CONTEXT_MESSAGE,
                chunks=chunks,
                retrieval_strategy=predicted_retrieval_strategy,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=retrieval_latency_ms,
                generation_latency_ms=0.0,
                confidence=confidence,
                insufficient_evidence=True,
                no_answer_reason="low_retrieval_confidence",
                requested_strategy=request.strategy,
                routing=routing,
                metadata={"executed_retriever": retriever.get_strategy_name()},
            )
            queue_query_evaluation(query_log)
            await save_memory_turn(memory, request.session_id, request.text, INSUFFICIENT_CONTEXT_MESSAGE)
            schedule_project_query_increment(db, tenant.id, request.project_id, total_tokens=0)
            return QueryResponse(
                answer=INSUFFICIENT_CONTEXT_MESSAGE,
                sources=sources,
                retrieval_strategy=predicted_retrieval_strategy,
                routing_decision=routing.decision,
                routing_reason=routing.reason,
                routing_reason_code=routing.reason_code,
                routing_confidence=routing.confidence,
                total_latency_ms=total_latency_ms,
                routing_latency_ms=routing_latency_ms,
                retrieval_latency_ms=retrieval_latency_ms,
                generation_latency_ms=0.0,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                query_log_id=query_log.id,
                answer_segments=[AnswerSegment(text=INSUFFICIENT_CONTEXT_MESSAGE, supporting_chunk_ids=[])],
                confidence=confidence,
                insufficient_evidence=True,
            )

        generation_started_at = time.perf_counter()
        try:
            answer, prompt_tokens, completion_tokens = await llm_service.generate_rag_answer(
                request.text,
                search_results_from_chunks(chunks),
                conversation_history=history,
                model=model,
            )
        except Exception as exc:
            logger.exception("query generation failed project_id=%s tenant_id=%s", request.project_id, tenant.id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Answer generation failed: {exc}",
            ) from exc

        generation_latency_ms = (time.perf_counter() - generation_started_at) * 1000
        estimated_cost_usd = llm_service.estimate_cost(prompt_tokens, completion_tokens, model)
        insufficient_evidence = is_insufficient_answer(answer)
        no_answer_reason = "llm_refused_insufficient_context" if insufficient_evidence else None
        total_latency_ms = elapsed_ms(started_at)

        query_log = await save_query_log(
            db=db,
            tenant_id=tenant.id,
            project_id=request.project_id,
            session_id=request.session_id,
            query=request.text,
            answer=answer,
            chunks=chunks,
            retrieval_strategy=predicted_retrieval_strategy,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            total_latency_ms=total_latency_ms,
            routing_latency_ms=routing_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            confidence=confidence,
            insufficient_evidence=insufficient_evidence,
            no_answer_reason=no_answer_reason,
            requested_strategy=request.strategy,
            routing=routing,
            metadata={"executed_retriever": retriever.get_strategy_name()},
        )
        queue_query_evaluation(query_log)
        await save_memory_turn(memory, request.session_id, request.text, answer)
        schedule_project_query_increment(
            db,
            tenant.id,
            request.project_id,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return QueryResponse(
            answer=answer,
            sources=sources,
            retrieval_strategy=predicted_retrieval_strategy,
            routing_decision=routing.decision,
            routing_reason=routing.reason,
            routing_reason_code=routing.reason_code,
            routing_confidence=routing.confidence,
            total_latency_ms=total_latency_ms,
            routing_latency_ms=routing_latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            query_log_id=query_log.id,
            answer_segments=build_answer_segments(answer, sources, insufficient_evidence),
            confidence=confidence,
            insufficient_evidence=insufficient_evidence,
        )
    finally:
        if owns_llm_service:
            await llm_service.close()


@router.get("/history", response_model=QueryHistoryResponse)
async def query_history(
    project_id: str = Query(..., min_length=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> QueryHistoryResponse:
    """Return paginated query history for a project."""
    await load_project(db, tenant, project_id)
    filter_query = {"tenant_id": tenant.id, "project_id": project_id}
    total = await db.query_logs.count_documents(filter_query)
    cursor = (
        db.query_logs.find(filter_query, {"_id": 0})
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    items = []
    async for item in cursor:
        answer = str(item.get("answer", ""))
        items.append(
            QueryHistoryItem(
                query=str(item.get("query", "")),
                answer=answer[:200],
                strategy=str(item.get("retrieval_strategy", "")),
                latency_ms=float(item.get("total_latency_ms", 0)),
                faithfulness=item.get("faithfulness"),
                created_at=item.get("created_at"),
            )
        )
    return QueryHistoryResponse(items=items, page=page, per_page=per_page, total=total)


@router.delete("/sessions/{session_id}")
async def clear_query_session(
    session_id: str,
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, str]:
    """Clear a tenant-scoped conversation memory session."""
    memory = ConversationMemory(database_module.redis_client, tenant.id)
    await memory.clear_session(session_id)
    return {"message": "Session cleared"}


@router.get("/sessions/{session_id}/history", response_model=list[SessionHistoryMessage])
async def query_session_history(
    session_id: str,
    tenant: Tenant = Depends(get_current_tenant),
) -> list[SessionHistoryMessage]:
    """Return Redis-backed conversation history for one tenant-scoped session."""
    memory = ConversationMemory(database_module.redis_client, tenant.id)
    history = await memory.get_history(session_id)
    return [SessionHistoryMessage(**message) for message in history]


async def load_active_project(
    db: AsyncIOMotorDatabase,
    tenant: Tenant,
    project_id: str,
) -> dict[str, Any]:
    """Load and validate an active project for querying."""
    project = await load_project(db, tenant, project_id)
    if project.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project is not active and cannot be queried",
        )
    return project


def select_requested_strategy(requested_strategy: str | None, project: dict[str, Any], settings: Settings) -> str:
    """Resolve a request strategy with project and settings fallbacks."""
    normalized = (requested_strategy or "").strip().lower()
    if normalized in {"vanilla", "hybrid", "rerank", "hyde"}:
        return normalized
    project_strategy = str(project.get("retrieval_strategy") or "").strip().lower()
    if project_strategy in {"vanilla", "hybrid", "rerank", "hyde"}:
        return project_strategy
    return settings.default_retrieval_strategy


async def load_project(db: AsyncIOMotorDatabase, tenant: Tenant, project_id: str) -> dict[str, Any]:
    """Load a project scoped to the current tenant."""
    return await verify_project_ownership(db, project_id, tenant)


async def load_conversation_history(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    project_id: str,
    session_id: str | None,
) -> list[dict[str, str]]:
    """Load the last three query/answer pairs as six chat messages."""
    if session_id is None:
        return []
    cursor = (
        db.query_logs.find(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "session_id": session_id,
            },
            {"query": 1, "answer": 1, "_id": 0},
        )
        .sort("created_at", -1)
        .limit(3)
    )
    rows = await cursor.to_list(length=3)
    history: list[dict[str, str]] = []
    for item in reversed(rows):
        history.append({"role": "user", "content": str(item.get("query", ""))})
        history.append({"role": "assistant", "content": str(item.get("answer", ""))})
    return history[-6:]


async def save_query_log(
    *,
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    project_id: str,
    session_id: str | None,
    query: str,
    answer: str,
    chunks: list[RetrievedChunk],
    retrieval_strategy: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float,
    total_latency_ms: float,
    routing_latency_ms: float | None,
    retrieval_latency_ms: float,
    generation_latency_ms: float,
    confidence: float,
    insufficient_evidence: bool,
    no_answer_reason: str | None,
    requested_strategy: str,
    routing: RoutingDecision | None,
    metadata: dict[str, Any] | None,
) -> QueryLog:
    """Persist a query log for analytics and future evaluation."""
    log_metadata = {
        "requested_strategy": requested_strategy,
        "confidence": confidence,
        "insufficient_evidence": insufficient_evidence,
        "answer_segments": [],
        "routing_reason_code": routing.reason_code if routing is not None else None,
        "routing_metadata": routing.metadata if routing is not None else {},
    }
    if metadata:
        log_metadata.update(metadata)

    evaluation_status, evaluation_error = initial_evaluation_state(retrieval_strategy, routing)
    query_log = QueryLog(
        tenant_id=tenant_id,
        project_id=project_id,
        session_id=session_id,
        query=query,
        answer=answer,
        routing_decision=routing.decision if routing is not None else "rag",
        routing_reason=routing.reason if routing is not None else None,
        routing_confidence=routing.confidence if routing is not None else confidence,
        retrieval_strategy=retrieval_strategy,
        retrieved_chunks=[
            RetrievedChunkRef(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_name=chunk.document_name,
                text=chunk.text,
                score=chunk.score,
                metadata={
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "strategy_used": chunk.strategy_used,
                },
            )
            for chunk in chunks
        ],
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        total_latency_ms=total_latency_ms,
        routing_latency_ms=routing_latency_ms,
        retrieval_latency_ms=retrieval_latency_ms,
        generation_latency_ms=generation_latency_ms,
        evaluation_status=evaluation_status,
        evaluation_error=evaluation_error,
        evaluation_runtime_ms=0.0 if evaluation_status == EvaluationStatus.SKIPPED.value else None,
        evaluation_cost_usd=0.0,
        evaluation_version=EVALUATION_VERSION if evaluation_status == EvaluationStatus.SKIPPED.value else None,
        evaluation_backend="skipped" if evaluation_status == EvaluationStatus.SKIPPED.value else None,
        evaluation_provider="none" if evaluation_status == EvaluationStatus.SKIPPED.value else None,
        evaluation_model="none" if evaluation_status == EvaluationStatus.SKIPPED.value else None,
        evaluation_metadata={"skip_reason": evaluation_error} if evaluation_error else {},
        no_answer=insufficient_evidence,
        no_answer_reason=no_answer_reason,
        fallback_triggered=insufficient_evidence,
        metadata=log_metadata,
    )
    await db.query_logs.insert_one(query_log.model_dump(mode="python"))
    return query_log


def initial_evaluation_state(
    retrieval_strategy: str,
    routing: RoutingDecision | None,
) -> tuple[EvaluationStatus, str | None]:
    """Return initial evaluation status for the completed query."""
    decision = str(routing.decision if routing is not None else "").lower()
    strategy = str(retrieval_strategy or "").lower()
    if decision in SKIPPED_EVALUATION_ROUTES or strategy in SKIPPED_EVALUATION_ROUTES:
        reason = f"skipped: evaluation is not applicable for decision={decision or 'unknown'}, strategy={strategy}"
        return EvaluationStatus.SKIPPED, reason
    return EvaluationStatus.PENDING, None


def queue_query_evaluation(query_log: QueryLog) -> None:
    """Queue the asynchronous evaluation task without blocking query responses."""
    try:
        from ..tasks.eval_tasks import run_evaluation_task

        task = run_evaluation_task.delay(query_log.id)
        logger.info(
            "queued query evaluation query_log_id=%s task_id=%s status=%s",
            query_log.id,
            task.id,
            query_log.evaluation_status,
        )
    except Exception as exc:
        logger.warning("failed to queue query evaluation query_log_id=%s error=%s", query_log.id, exc)


def schedule_project_query_increment(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    project_id: str,
    *,
    total_tokens: int,
) -> None:
    """Increment project query stats in the background."""
    task = asyncio.create_task(increment_project_query_stats(db, tenant_id, project_id, total_tokens))
    task.add_done_callback(log_background_error)


async def increment_project_query_stats(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    project_id: str,
    total_tokens: int,
) -> None:
    """Increment project query counters."""
    await db.projects.update_one(
        {"id": project_id, "tenant_id": tenant_id},
        {
            "$inc": {
                "query_count": 1,
                "total_tokens_used": int(total_tokens),
            },
            "$set": {"last_queried_at": datetime.now(UTC)},
        },
    )


def log_background_error(task: asyncio.Task[Any]) -> None:
    """Log background project counter failures without affecting responses."""
    try:
        task.result()
    except Exception as exc:
        logger.warning("project query counter update failed: %s", exc)


async def save_memory_turn(
    memory: ConversationMemory,
    session_id: str | None,
    user_message: str,
    assistant_message: str,
) -> None:
    """Persist a conversation turn when the client supplied a session id."""
    if session_id is None:
        return
    await memory.add_turn(session_id, user_message, assistant_message)


def calculate_confidence(chunks: list[RetrievedChunk]) -> float:
    """Estimate answer confidence from score distribution and support count."""
    if not chunks:
        return 0.0
    scores = sorted((max(0.0, min(1.0, chunk.score)) for chunk in chunks), reverse=True)
    top_score = scores[0]
    top_scores = scores[:3]
    average_top = sum(top_scores) / len(top_scores)
    support_factor = min(len(chunks) / 3, 1.0)
    confidence = (top_score * 0.55) + (average_top * 0.30) + (support_factor * 0.15)
    return round(max(0.0, min(1.0, confidence)), 3)


def source_from_chunk(chunk: RetrievedChunk) -> SourceRef:
    """Convert a retrieved chunk into a source reference."""
    return SourceRef(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        text=chunk.text,
        score=chunk.score,
        page_number=chunk.page_number,
    )


def web_sources_from_results(results: list[dict[str, str]]) -> list[SourceRef]:
    """Convert web search results into source references for the query response."""
    sources: list[SourceRef] = []
    for result in results:
        url = str(result.get("url") or "")
        title = str(result.get("title") or "Web result")
        snippet = str(result.get("snippet") or "")
        source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        sources.append(
            SourceRef(
                chunk_id=f"web_{source_id}",
                document_id=f"web_{source_id[:12]}",
                document_name=title[:255],
                text=snippet,
                score=1.0,
                page_number=None,
            )
        )
    return sources


def search_results_from_chunks(chunks: list[RetrievedChunk]) -> list[SearchResult]:
    """Convert retriever chunks into LLM-compatible search results."""
    return [
        SearchResult(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_name=chunk.document_name,
            text=chunk.text,
            score=chunk.score,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            strategy_used=chunk.strategy_used,
        )
        for chunk in chunks
    ]


def is_insufficient_answer(answer: str) -> bool:
    """Return whether the answer is the model's insufficient-context refusal."""
    normalized = answer.lower()
    return "don't have enough information" in normalized or "provided documents" in normalized and "answer this" in normalized


def build_answer_segments(
    answer: str,
    sources: list[SourceRef],
    insufficient_evidence: bool,
) -> list[AnswerSegment]:
    """Split answer paragraphs and attach likely supporting chunk ids."""
    paragraphs = [paragraph.strip() for paragraph in answer.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        paragraphs = [answer.strip()]
    segments: list[AnswerSegment] = []
    default_support = [] if insufficient_evidence else [source.chunk_id for source in sources[:3]]
    for paragraph in paragraphs:
        matching_ids = [
            source.chunk_id
            for source in sources
            if source.document_name.lower() in paragraph.lower()
        ]
        segments.append(
            AnswerSegment(
                text=paragraph,
                supporting_chunk_ids=matching_ids or default_support,
            )
        )
    return segments


def elapsed_ms(started_at: float) -> float:
    """Return elapsed milliseconds since a perf_counter timestamp."""
    return (time.perf_counter() - started_at) * 1000
