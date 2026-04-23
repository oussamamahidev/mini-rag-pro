"""Server-Sent Events query streaming endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from .. import database as database_module
from ..agent.memory import ConversationMemory
from ..agent.router import QueryRouter, RoutingDecision
from ..agent.tools import WebSearchTool
from ..config import Settings, get_settings
from ..database import get_db
from ..logging_config import get_logger
from ..middleware.auth import get_current_tenant
from ..models.tenant import Tenant
from ..retrieval.base import RetrievedChunk
from ..services.llm import LLMService
from ..retrieval.factory import get_retriever
from .query import (
    INSUFFICIENT_CONFIDENCE_THRESHOLD,
    INSUFFICIENT_CONTEXT_MESSAGE,
    NO_DOCUMENTS_MESSAGE,
    calculate_confidence,
    elapsed_ms,
    is_insufficient_answer,
    load_active_project,
    queue_query_evaluation,
    save_query_log,
    schedule_project_query_increment,
    search_results_from_chunks,
    select_requested_strategy,
)

logger = get_logger(__name__)

router = APIRouter()

HEARTBEAT_SECONDS = 10.0
SIMULATED_TOKEN_DELAY_SECONDS = 0.02


class StreamQueryRequest(BaseModel):
    """Request payload for a streaming query."""

    text: str = Field(..., min_length=1, max_length=2000)
    project_id: str = Field(..., min_length=1)
    strategy: str = "hybrid"
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@dataclass(slots=True)
class EventState:
    """Mutable SSE event sequence state."""

    sequence: int = 0


@router.post("/stream")
async def stream_query(
    payload: StreamQueryRequest,
    http_request: Request,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream routed query responses token by token with SSE frames."""
    project = await load_active_project(db, tenant, payload.project_id)
    session_id = payload.session_id or str(uuid4())

    async def generate_events() -> AsyncIterator[str]:
        state = EventState()
        total_started_at = time.perf_counter()
        full_answer = ""
        retrieved_chunks: list[RetrievedChunk] = []
        routing: RoutingDecision | None = None
        routing_latency_ms = 0.0
        retrieval_latency_ms = 0.0
        generation_latency_ms = 0.0
        prompt_tokens = 0
        completion_tokens = 0
        estimated_cost_usd = 0.0
        confidence = 0.0
        insufficient_evidence = False
        no_answer_reason: str | None = None
        web_results: list[dict[str, str]] = []
        predicted_strategy = select_requested_strategy(payload.strategy, project, settings)

        router_agent = getattr(http_request.app.state, "query_router", None)
        web_search = getattr(http_request.app.state, "web_search_tool", None)
        state_llm_service = getattr(http_request.app.state, "llm_service", None)
        llm_service = state_llm_service or LLMService(settings)
        owns_llm_service = state_llm_service is None
        memory = ConversationMemory(database_module.redis_client, tenant.id)

        try:
            yield await emit(
                state,
                http_request,
                {
                    "type": "start",
                    "session_id": session_id,
                    "project_id": payload.project_id,
                },
            )

            history = await memory.get_history(session_id)

            query_router = router_agent or QueryRouter(llm_service.client, model=settings.openai_model)
            routing_started_at = time.perf_counter()
            routing_task = asyncio.create_task(
                query_router.route(
                    payload.text,
                    history,
                    project_default_strategy=predicted_strategy,
                )
            )
            async for frame in heartbeat_while_pending(routing_task, http_request, state, "routing"):
                yield frame
            routing = await routing_task
            routing_latency_ms = elapsed_ms(routing_started_at)
            predicted_strategy = select_requested_strategy(payload.strategy, project, settings)

            logger.info(
                "stream query routing tenant_id=%s project_id=%s decision=%s reason_code=%s confidence=%.2f",
                tenant.id,
                payload.project_id,
                routing.decision,
                routing.reason_code,
                routing.confidence,
            )

            yield await emit(
                state,
                http_request,
                {
                    "type": "routing",
                    "decision": routing.decision,
                    "reason": routing.reason,
                    "reason_code": routing.reason_code,
                    "confidence": routing.confidence,
                    "retrieval_strategy": predicted_strategy if routing.decision == "rag" else None,
                },
            )

            if routing.decision == "rag":
                retriever = get_retriever(predicted_strategy)
                retrieval_started_at = time.perf_counter()
                retrieval_task = asyncio.create_task(
                    retriever.retrieve(
                        payload.text,
                        payload.project_id,
                        tenant.id,
                        top_k=payload.top_k,
                    )
                )
                async for frame in heartbeat_while_pending(retrieval_task, http_request, state, "retrieval"):
                    yield frame
                retrieved_chunks, retrieval_latency_ms = await retrieval_task

                sources_payload = sources_payload_from_chunks(retrieved_chunks)
                yield await emit(state, http_request, {"type": "sources", "data": sources_payload})

                confidence = calculate_confidence(retrieved_chunks)
                if not retrieved_chunks:
                    full_answer = NO_DOCUMENTS_MESSAGE
                    insufficient_evidence = True
                    no_answer_reason = "no_relevant_chunks"
                    yield await emit(state, http_request, {"type": "token", "data": full_answer})
                elif confidence < INSUFFICIENT_CONFIDENCE_THRESHOLD:
                    full_answer = INSUFFICIENT_CONTEXT_MESSAGE
                    insufficient_evidence = True
                    no_answer_reason = "low_retrieval_confidence"
                    yield await emit(state, http_request, {"type": "token", "data": full_answer})
                else:
                    generation_started_at = time.perf_counter()
                    answer_parts: list[str] = []
                    token_iterator = llm_service.generate_answer_stream(
                        payload.text,
                        search_results_from_chunks(retrieved_chunks),
                        conversation_history=history,
                    )
                    async for frame in stream_token_events(token_iterator, http_request, state, answer_parts):
                        yield frame
                    full_answer = "".join(answer_parts).strip()
                    generation_latency_ms = elapsed_ms(generation_started_at)
                    insufficient_evidence = is_insufficient_answer(full_answer)
                    no_answer_reason = "llm_refused_insufficient_context" if insufficient_evidence else None
                    prompt_tokens = estimate_prompt_tokens(payload.text, retrieved_chunks, history)
                    completion_tokens = estimate_tokens(full_answer)
                    estimated_cost_usd = llm_service.estimate_cost(
                        prompt_tokens,
                        completion_tokens,
                        settings.openai_model,
                    )

            elif routing.decision == "web_search":
                yield await emit(state, http_request, {"type": "sources", "data": []})
                generation_started_at = time.perf_counter()
                web_tool = web_search or WebSearchTool(llm_service.client, model=settings.openai_model)
                web_task = asyncio.create_task(web_tool.search_and_answer(payload.text))
                async for frame in heartbeat_while_pending(web_task, http_request, state, "web_search"):
                    yield frame
                full_answer, web_results = await web_task
                generation_latency_ms = elapsed_ms(generation_started_at)
                confidence = round(routing.confidence if web_results else 0.0, 3)
                insufficient_evidence = not web_results or "couldn't find current information" in full_answer.lower()
                no_answer_reason = "web_search_no_results" if insufficient_evidence else None
                prompt_tokens = estimate_tokens(payload.text)
                completion_tokens = estimate_tokens(full_answer)
                estimated_cost_usd = llm_service.estimate_cost(
                    prompt_tokens,
                    completion_tokens,
                    settings.openai_model,
                )

                async for frame in stream_text_as_tokens(full_answer, http_request, state):
                    yield frame

            elif routing.decision == "direct":
                yield await emit(state, http_request, {"type": "sources", "data": []})
                generation_started_at = time.perf_counter()
                answer_parts = []
                direct_iterator = direct_answer_token_stream(
                    llm_service,
                    payload.text,
                    history,
                    settings.openai_model,
                )
                async for frame in stream_token_events(direct_iterator, http_request, state, answer_parts):
                    yield frame
                full_answer = "".join(answer_parts).strip()
                generation_latency_ms = elapsed_ms(generation_started_at)
                confidence = routing.confidence
                prompt_tokens = estimate_tokens(payload.text) + estimate_history_tokens(history[-4:])
                completion_tokens = estimate_tokens(full_answer)
                estimated_cost_usd = llm_service.estimate_cost(
                    prompt_tokens,
                    completion_tokens,
                    settings.openai_model,
                )

            else:
                yield await emit(state, http_request, {"type": "sources", "data": []})
                full_answer = "Could you clarify what you want to know or which document/topic you mean?"
                confidence = routing.confidence
                insufficient_evidence = True
                no_answer_reason = "clarification_needed"
                yield await emit(state, http_request, {"type": "token", "data": full_answer})

            if not full_answer:
                full_answer = "No answer generated."
                insufficient_evidence = True
                no_answer_reason = no_answer_reason or "empty_generation"

            total_latency_ms = elapsed_ms(total_started_at)
            metadata_payload = {
                "type": "metadata",
                "latency_ms": round(total_latency_ms),
                "strategy": predicted_strategy if routing.decision == "rag" else routing.decision,
                "routing_decision": routing.decision,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost_usd": estimated_cost_usd,
                "confidence": confidence,
                "insufficient_evidence": insufficient_evidence,
            }
            yield await emit(state, http_request, metadata_payload)

            yield await emit(
                state,
                http_request,
                {
                    "type": "summary",
                    "routing": {
                        "decision": routing.decision,
                        "reason": routing.reason,
                        "reason_code": routing.reason_code,
                        "confidence": routing.confidence,
                    },
                    "retrieval": {
                        "strategy": predicted_strategy if routing.decision == "rag" else routing.decision,
                        "chunks": len(retrieved_chunks),
                        "sources": len(web_results) if routing.decision == "web_search" else len(retrieved_chunks),
                        "latency_ms": round(retrieval_latency_ms, 2),
                    },
                    "generation": {
                        "model": settings.openai_model,
                        "latency_ms": round(generation_latency_ms, 2),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    },
                    "total_latency_ms": round(total_latency_ms, 2),
                },
            )

            yield await emit(state, http_request, {"type": "done"})

            schedule_background_task(
                memory.add_turn(session_id, payload.text, full_answer),
                "stream memory write",
            )
            schedule_background_task(
                log_query_async(
                    db=db,
                    tenant=tenant,
                    project_id=payload.project_id,
                    session_id=session_id,
                    query=payload.text,
                    answer=full_answer,
                    chunks=retrieved_chunks,
                    strategy=predicted_strategy if routing.decision == "rag" else routing.decision,
                    routing=routing,
                    model=settings.openai_model,
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
                    requested_strategy=payload.strategy,
                    metadata={
                        "streaming": True,
                        "web_sources": web_results,
                        "executed_retriever": get_retriever(predicted_strategy).get_strategy_name()
                        if routing.decision == "rag"
                        else None,
                    },
                ),
                "stream query log",
            )
            schedule_project_query_increment(
                db,
                tenant.id,
                payload.project_id,
                total_tokens=prompt_tokens + completion_tokens,
            )

        except asyncio.CancelledError:
            logger.info("client disconnected during streaming session_id=%s", session_id)
            raise
        except Exception as exc:
            logger.error("streaming query failed session_id=%s error=%s", session_id, exc, exc_info=True)
            yield format_sse(state, {"type": "error", "message": str(exc)})
        finally:
            if owns_llm_service:
                await llm_service.close()

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
            "Access-Control-Allow-Origin": "*",
        },
    )


async def log_query_async(
    *,
    db: AsyncIOMotorDatabase,
    tenant: Tenant,
    project_id: str,
    session_id: str | None,
    query: str,
    answer: str,
    chunks: list[RetrievedChunk],
    strategy: str,
    routing: RoutingDecision,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float,
    total_latency_ms: float,
    routing_latency_ms: float,
    retrieval_latency_ms: float,
    generation_latency_ms: float,
    confidence: float,
    insufficient_evidence: bool,
    no_answer_reason: str | None,
    requested_strategy: str,
    metadata: dict[str, Any],
) -> None:
    """Persist a streaming query log without blocking the SSE response."""
    try:
        query_log = await save_query_log(
            db=db,
            tenant_id=tenant.id,
            project_id=project_id,
            session_id=session_id,
            query=query,
            answer=answer,
            chunks=chunks,
            retrieval_strategy=strategy,
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
            requested_strategy=requested_strategy,
            routing=routing,
            metadata=metadata,
        )
        queue_query_evaluation(query_log)
    except Exception as exc:
        logger.warning("stream query log write failed tenant_id=%s project_id=%s error=%s", tenant.id, project_id, exc)


async def heartbeat_while_pending(
    task: asyncio.Task[Any],
    http_request: Request,
    state: EventState,
    phase: str,
) -> AsyncIterator[str]:
    """Yield heartbeat events while an awaited task is still running."""
    try:
        while not task.done():
            await ensure_client_connected(http_request)
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield await emit(state, http_request, {"type": "heartbeat", "phase": phase})
    except asyncio.CancelledError:
        task.cancel()
        raise


async def stream_token_events(
    token_iterator: AsyncIterator[str],
    http_request: Request,
    state: EventState,
    answer_parts: list[str],
) -> AsyncIterator[str]:
    """Stream token iterator output with heartbeat events between delayed tokens."""
    queue: asyncio.Queue[tuple[str, str | BaseException | None]] = asyncio.Queue()

    async def produce_tokens() -> None:
        try:
            async for token in token_iterator:
                await queue.put(("token", token))
            await queue.put(("done", None))
        except Exception as exc:
            await queue.put(("error", exc))

    producer = asyncio.create_task(produce_tokens())
    try:
        while True:
            await ensure_client_connected(http_request)
            try:
                kind, value = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield await emit(state, http_request, {"type": "heartbeat", "phase": "generation"})
                continue

            if kind == "done":
                break
            if kind == "error":
                if isinstance(value, BaseException):
                    raise value
                raise RuntimeError("token streaming failed")

            token = str(value or "")
            if not token:
                continue
            answer_parts.append(token)
            yield await emit(state, http_request, {"type": "token", "data": token})
    except asyncio.CancelledError:
        producer.cancel()
        raise
    finally:
        if not producer.done():
            producer.cancel()


async def stream_text_as_tokens(
    text: str,
    http_request: Request,
    state: EventState,
) -> AsyncIterator[str]:
    """Simulate token streaming for a fully generated answer."""
    words = text.split()
    if not words:
        return
    for index, word in enumerate(words):
        await ensure_client_connected(http_request)
        token = word if index == 0 else f" {word}"
        yield await emit(state, http_request, {"type": "token", "data": token})
        await asyncio.sleep(SIMULATED_TOKEN_DELAY_SECONDS)


async def direct_answer_token_stream(
    llm_service: LLMService,
    query: str,
    history: list[dict[str, str]],
    model: str,
) -> AsyncIterator[str]:
    """Yield direct-answer tokens from OpenAI without document context."""
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Answer the user's trivial question directly and concisely. "
                "Do not claim to have inspected uploaded documents."
            ),
        }
    ]
    messages.extend(sanitize_history(history)[-4:])
    messages.append({"role": "user", "content": query})

    stream = await llm_service.client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        max_tokens=300,
        temperature=0.1,
    )
    async for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token


async def ensure_client_connected(http_request: Request) -> None:
    """Raise CancelledError when the SSE client has disconnected."""
    if await http_request.is_disconnected():
        raise asyncio.CancelledError


async def emit(state: EventState, http_request: Request, payload: dict[str, Any]) -> str:
    """Check client connection and return one complete SSE frame."""
    await ensure_client_connected(http_request)
    return format_sse(state, payload)


def format_sse(state: EventState, payload: dict[str, Any]) -> str:
    """Serialize one complete SSE frame with event id and JSON payload."""
    state.sequence += 1
    event_payload = {"seq": state.sequence, **payload}
    event_type = str(payload.get("type") or "message")
    data = json.dumps(event_payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {state.sequence}\nevent: {event_type}\ndata: {data}\n\n"


def sources_payload_from_chunks(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """Build compact source payloads sent before generation begins."""
    sources = []
    for chunk in chunks:
        text = chunk.text[:300] + "..." if len(chunk.text) > 300 else chunk.text
        sources.append(
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "text": text,
                "score": round(chunk.score, 4),
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
            }
        )
    return sources


def estimate_prompt_tokens(
    query: str,
    chunks: list[RetrievedChunk],
    history: list[dict[str, str]],
) -> int:
    """Approximate prompt token count for streamed responses."""
    context_chars = sum(len(chunk.text) for chunk in chunks)
    history_chars = sum(len(message.get("content", "")) for message in history[-6:])
    return max(0, (len(query) + context_chars + history_chars) // 4)


def estimate_history_tokens(history: list[dict[str, str]]) -> int:
    """Approximate token count for chat history."""
    return sum(len(message.get("content", "")) for message in history) // 4


def estimate_tokens(text: str) -> int:
    """Approximate token count from character length."""
    return max(0, len(text) // 4)


def sanitize_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only user/assistant role messages for direct streaming."""
    sanitized: list[dict[str, str]] = []
    for message in history:
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not content:
            continue
        sanitized.append({"role": role, "content": str(content)[:4000]})
    return sanitized


def schedule_background_task(coro: Any, label: str) -> None:
    """Schedule a background coroutine and log failures."""
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda completed: log_stream_background_error(completed, label))


def log_stream_background_error(task: asyncio.Task[Any], label: str) -> None:
    """Log background task failures without affecting the completed stream."""
    try:
        task.result()
    except asyncio.CancelledError:
        logger.info("%s was cancelled", label)
    except Exception as exc:
        logger.warning("%s failed: %s", label, exc)
