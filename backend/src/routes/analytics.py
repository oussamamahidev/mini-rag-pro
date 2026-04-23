"""Tenant-scoped analytics endpoints for the RAG dashboard."""

from __future__ import annotations

import csv
import io
import json
import math
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.exceptions import RedisError

from .. import database as database_module
from ..database import get_db
from ..evaluation.metrics_store import MetricsStore
from ..logging_config import get_logger
from ..middleware.auth import get_current_tenant
from ..models.tenant import Tenant
from ..services.embedding import EMBEDDING_COST_PER_1K_TOKENS

logger = get_logger(__name__)
router = APIRouter()

OVERVIEW_CACHE_SECONDS = 300
DEFAULT_FAILED_THRESHOLD = 0.5
INSUFFICIENT_EVIDENCE_REASONS = {
    "no_relevant_chunks",
    "low_retrieval_confidence",
    "llm_refused_insufficient_context",
    "web_search_no_results",
    "empty_generation",
}

STRATEGY_INFO: dict[str, tuple[str, str]] = {
    "vanilla": ("Vector search", "Pure semantic similarity. Fast, good baseline."),
    "hybrid": ("Hybrid search", "Combines semantic and keyword retrieval for broader recall."),
    "rerank": ("Reranked retrieval", "Ranks retrieved chunks again for better precision."),
    "hyde": ("HyDE", "Generates a hypothetical answer to improve difficult retrieval."),
    "web_search": ("Web search", "Uses web results for current external information."),
    "direct": ("Direct answer", "Answers simple non-document questions without retrieval."),
    "clarify": ("Clarification", "Asks for more detail when the question is ambiguous."),
}


@router.get("/overview")
async def analytics_overview(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return the cached dashboard overview for the current tenant."""
    cache_key = f"analytics:overview:{tenant.id}"
    cached = await read_cache(cache_key)
    if cached is not None:
        return cached

    try:
        store = MetricsStore(db)
        today_start = start_of_day_utc()
        week_start = start_of_week_utc()
        cutoff_30d = datetime.now(UTC) - timedelta(days=30)

        avg_scores = await store.get_average_scores(tenant.id, days=30)
        latency = await store.get_latency_percentiles(tenant.id, days=30)
        latency_by_strategy = await store.get_latency_by_strategy(tenant.id, days=30)
        score_rows = await store.get_scores_over_time(tenant.id, days=14)

        total_queries = await db.query_logs.count_documents({"tenant_id": tenant.id})
        queries_today = await db.query_logs.count_documents({"tenant_id": tenant.id, "created_at": {"$gte": today_start}})
        queries_this_week = await db.query_logs.count_documents({"tenant_id": tenant.id, "created_at": {"$gte": week_start}})
        completed_evaluations = await db.query_logs.count_documents(
            {"tenant_id": tenant.id, "evaluation_status": "completed"}
        )
        total_documents = await db.documents.count_documents({"tenant_id": tenant.id, "is_deleted": {"$ne": True}})
        total_chunks = await db.chunks.count_documents({"tenant_id": tenant.id})
        active_projects = await db.projects.count_documents(
            {"tenant_id": tenant.id, "status": "active", "is_deleted": {"$ne": True}}
        )
        daily_query_counts = await query_counts_by_day(db, tenant.id, days=14)
        estimated_cost_usd_30d = await total_cost(db, tenant.id, cutoff_30d)
        rates = await quality_rates(db, tenant.id, days=30)

        overview = {
            "total_queries": int(total_queries),
            "queries_today": int(queries_today),
            "queries_this_week": int(queries_this_week),
            "faithfulness_avg": avg_scores["faithfulness_avg"],
            "answer_relevancy_avg": avg_scores["answer_relevancy_avg"],
            "context_precision_avg": avg_scores["context_precision_avg"],
            "latency_p50_ms": latency["p50"] or 0.0,
            "latency_p95_ms": latency["p95"] or 0.0,
            "latency_p99_ms": latency["p99"] or 0.0,
            "latency_mean_ms": latency["mean"] or 0.0,
            "evaluation_coverage_pct": percent(completed_evaluations, total_queries),
            "total_documents": int(total_documents),
            "total_chunks": int(total_chunks),
            "active_projects": int(active_projects),
            "estimated_cost_usd_30d": round_money(estimated_cost_usd_30d),
            "no_answer_rate_pct": rates["no_answer_rate_pct"],
            "insufficient_evidence_rate_pct": rates["insufficient_evidence_rate_pct"],
            "scores_over_time": merge_scores_and_query_counts(score_rows, daily_query_counts, days=14),
            "latency_by_strategy": latency_by_strategy,
        }
        await write_cache(cache_key, overview)
        return overview
    except Exception as exc:
        logger.exception("analytics overview failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load analytics overview") from exc


@router.get("/queries")
async def analytics_queries(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    project_id: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    min_faithfulness: float | None = Query(default=None, ge=0, le=1),
    max_faithfulness: float | None = Query(default=None, ge=0, le=1),
    routing_decision: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    sort_by: Literal["created_at", "faithfulness", "latency_ms"] = Query(default="created_at"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return paginated query history with dashboard filters."""
    try:
        filter_query = build_query_filter(
            tenant.id,
            project_id=project_id,
            strategy=strategy,
            min_faithfulness=min_faithfulness,
            max_faithfulness=max_faithfulness,
            routing_decision=routing_decision,
            date_from=date_from,
            date_to=date_to,
        )
        sort_field = {"created_at": "created_at", "faithfulness": "faithfulness", "latency_ms": "total_latency_ms"}[
            sort_by
        ]
        direction = 1 if sort_order == "asc" else -1
        total = await db.query_logs.count_documents(filter_query)
        cursor = (
            db.query_logs.find(
                filter_query,
                {
                    "_id": 0,
                    "id": 1,
                    "query": 1,
                    "answer": 1,
                    "retrieval_strategy": 1,
                    "routing_decision": 1,
                    "faithfulness": 1,
                    "answer_relevancy": 1,
                    "total_latency_ms": 1,
                    "evaluation_status": 1,
                    "created_at": 1,
                },
            )
            .sort(sort_field, direction)
            .skip((page - 1) * per_page)
            .limit(per_page)
        )
        rows = await cursor.to_list(length=per_page)
        items = [
            {
                "id": row.get("id"),
                "query_truncated": truncate(str(row.get("query") or ""), 180),
                "answer_truncated": truncate(str(row.get("answer") or ""), 280),
                "strategy": row.get("retrieval_strategy"),
                "routing_decision": row.get("routing_decision"),
                "faithfulness": round_optional(row.get("faithfulness")),
                "answer_relevancy": round_optional(row.get("answer_relevancy")),
                "total_latency_ms": round_optional(row.get("total_latency_ms")),
                "evaluation_status": row.get("evaluation_status"),
                "created_at": iso_datetime(row.get("created_at")),
            }
            for row in rows
        ]
        return {
            "items": items,
            "total": int(total),
            "page": page,
            "per_page": per_page,
            "pages": math.ceil(total / per_page) if total else 0,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("analytics query history failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load query history") from exc


@router.get("/queries/{query_id}")
async def analytics_query_detail(
    query_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return full details for one tenant-owned query log."""
    try:
        query_log = await db.query_logs.find_one(query_identity_filter(tenant.id, query_id))
        if query_log is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Query not found")

        session_id = query_log.get("session_id")
        conversation_count = 0
        if session_id:
            conversation_count = await db.query_logs.count_documents(
                {"tenant_id": tenant.id, "session_id": session_id}
            )

        payload = to_jsonable(query_log)
        payload["prompt_preview"] = build_prompt_preview(query_log)
        payload["conversation_context"] = bool(session_id and conversation_count > 1)
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("analytics query detail failed tenant_id=%s query_id=%s", tenant.id, query_id)
        raise internal_error("Failed to load query detail") from exc


@router.get("/strategies")
async def analytics_strategies(
    days: int = Query(default=30, ge=1, le=365),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict[str, Any]]:
    """Compare retrieval strategies by quality and latency."""
    try:
        return await build_strategy_comparison(db, tenant.id, days=days)
    except Exception as exc:
        logger.exception("analytics strategies failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load strategy analytics") from exc


@router.get("/retrieval-strategies")
async def analytics_retrieval_strategies(
    days: int = Query(default=30, ge=1, le=365),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return retrieval-strategy comparison data for dashboards."""
    try:
        return await build_strategy_comparison(db, tenant.id, days=days)
    except Exception as exc:
        logger.exception("analytics retrieval strategies failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load retrieval strategy analytics") from exc


@router.get("/failed")
async def analytics_failed_queries(
    limit: int = Query(default=20, ge=1, le=100),
    project_id: str | None = Query(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return low-faithfulness queries likely to need improvement."""
    try:
        match: dict[str, Any] = {
            "tenant_id": tenant.id,
            "evaluation_status": "completed",
            "faithfulness": {"$lt": DEFAULT_FAILED_THRESHOLD},
        }
        if project_id:
            match["project_id"] = project_id
        cursor = (
            db.query_logs.find(
                match,
                {
                    "_id": 0,
                    "id": 1,
                    "query": 1,
                    "answer": 1,
                    "faithfulness": 1,
                    "answer_relevancy": 1,
                    "context_precision": 1,
                    "retrieval_strategy": 1,
                    "routing_decision": 1,
                    "created_at": 1,
                },
            )
            .sort([("faithfulness", 1), ("created_at", -1)])
            .limit(limit)
        )
        rows = await cursor.to_list(length=limit)
        return [failed_query_payload(row) for row in rows]
    except Exception as exc:
        logger.exception("analytics failed queries failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load failed queries") from exc


@router.get("/worst-performing")
async def analytics_worst_performing_queries(
    limit: int = Query(default=20, ge=1, le=100),
    project_id: str | None = Query(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return queries with the weakest combined evaluation score."""
    try:
        match: dict[str, Any] = {"tenant_id": tenant.id, "evaluation_status": "completed"}
        if project_id:
            match["project_id"] = project_id
        pipeline = [
            {"$match": match},
            {
                "$addFields": {
                    "quality_score": {
                        "$avg": [
                            "$faithfulness",
                            "$answer_relevancy",
                            "$context_precision",
                        ]
                    }
                }
            },
            {"$sort": {"quality_score": 1, "created_at": -1}},
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "id": 1,
                    "query": 1,
                    "answer": 1,
                    "faithfulness": 1,
                    "answer_relevancy": 1,
                    "context_precision": 1,
                    "quality_score": 1,
                    "retrieval_strategy": 1,
                    "routing_decision": 1,
                    "created_at": 1,
                }
            },
        ]
        rows = await db.query_logs.aggregate(pipeline).to_list(length=limit)
        return [
            {
                **failed_query_payload(row),
                "quality_score": round_optional(row.get("quality_score")),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.exception("analytics worst-performing failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load worst-performing queries") from exc


@router.get("/top-failing-documents")
async def analytics_top_failing_documents(
    limit: int = Query(default=20, ge=1, le=100),
    min_faithfulness: float = Query(default=DEFAULT_FAILED_THRESHOLD, ge=0, le=1),
    project_id: str | None = Query(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return documents most often present in low-faithfulness query contexts."""
    try:
        match: dict[str, Any] = {
            "tenant_id": tenant.id,
            "evaluation_status": "completed",
            "faithfulness": {"$lt": min_faithfulness},
        }
        if project_id:
            match["project_id"] = project_id
        pipeline = [
            {"$match": match},
            {"$unwind": "$retrieved_chunks"},
            {
                "$group": {
                    "_id": {
                        "query_id": "$id",
                        "document_id": "$retrieved_chunks.document_id",
                        "document_name": "$retrieved_chunks.document_name",
                    },
                    "faithfulness": {"$first": "$faithfulness"},
                    "context_precision": {"$first": "$context_precision"},
                    "retrieval_strategy": {"$first": "$retrieval_strategy"},
                    "created_at": {"$first": "$created_at"},
                }
            },
            {
                "$group": {
                    "_id": {
                        "document_id": "$_id.document_id",
                        "document_name": "$_id.document_name",
                    },
                    "failed_query_count": {"$sum": 1},
                    "avg_faithfulness": {"$avg": "$faithfulness"},
                    "avg_context_precision": {"$avg": "$context_precision"},
                    "strategies": {"$addToSet": "$retrieval_strategy"},
                    "latest_failure_at": {"$max": "$created_at"},
                }
            },
            {"$sort": {"failed_query_count": -1, "avg_faithfulness": 1}},
            {"$limit": limit},
        ]
        rows = await db.query_logs.aggregate(pipeline).to_list(length=limit)
        return [
            {
                "document_id": row["_id"].get("document_id"),
                "document_name": row["_id"].get("document_name"),
                "failed_query_count": int(row.get("failed_query_count") or 0),
                "avg_faithfulness": round_optional(row.get("avg_faithfulness")),
                "avg_context_precision": round_optional(row.get("avg_context_precision")),
                "strategies": row.get("strategies") or [],
                "latest_failure_at": iso_datetime(row.get("latest_failure_at")),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.exception("analytics top failing documents failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load top failing documents") from exc


@router.get("/confidence-faithfulness-correlation")
async def analytics_confidence_faithfulness_correlation(
    days: int = Query(default=30, ge=1, le=365),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return Pearson correlation between confidence and faithfulness."""
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        cursor = db.query_logs.find(
            {
                "tenant_id": tenant.id,
                "created_at": {"$gte": cutoff},
                "evaluation_status": "completed",
                "faithfulness": {"$type": "number"},
            },
            {"_id": 0, "faithfulness": 1, "routing_confidence": 1, "metadata.confidence": 1},
        )
        rows = await cursor.to_list(length=None)
        pairs = []
        for row in rows:
            confidence = nested_get(row, "metadata", "confidence")
            if confidence is None:
                confidence = row.get("routing_confidence")
            confidence_value = float_or_none(confidence)
            faithfulness_value = float_or_none(row.get("faithfulness"))
            if confidence_value is not None and faithfulness_value is not None:
                pairs.append((confidence_value, faithfulness_value))

        correlation = pearson_correlation(pairs)
        return {
            "correlation": round_optional(correlation),
            "sample_size": len(pairs),
            "days": days,
            "interpretation": correlation_interpretation(correlation),
        }
    except Exception as exc:
        logger.exception("analytics confidence correlation failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load confidence correlation") from exc


@router.get("/no-answer-rate")
async def analytics_no_answer_rate(
    days: int = Query(default=30, ge=1, le=365),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return no-answer rate for the selected window."""
    try:
        return await rate_payload(db, tenant.id, days, no_answer_condition(), "no_answer")
    except Exception as exc:
        logger.exception("analytics no-answer rate failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load no-answer rate") from exc


@router.get("/insufficient-evidence-rate")
async def analytics_insufficient_evidence_rate(
    days: int = Query(default=30, ge=1, le=365),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return insufficient-evidence rate for the selected window."""
    try:
        return await rate_payload(db, tenant.id, days, insufficient_evidence_condition(), "insufficient_evidence")
    except Exception as exc:
        logger.exception("analytics insufficient evidence rate failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load insufficient-evidence rate") from exc


@router.get("/costs")
async def analytics_costs(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Return token usage and estimated cost breakdown for the current month."""
    try:
        month_start = start_of_month_utc()
        query_costs = await query_cost_summary(db, tenant.id, month_start)
        embedding_costs = await embedding_cost_summary(db, tenant.id, month_start)
        daily_cost = await daily_cost_summary(db, tenant.id, month_start)
        cost_by_model = build_cost_by_model(query_costs["by_model"], embedding_costs["by_model"])
        total_cost = query_costs["estimated_cost_usd"] + embedding_costs["estimated_cost_usd"]

        return {
            "this_month": {
                "total_tokens": int(query_costs["total_tokens"]),
                "prompt_tokens": int(query_costs["prompt_tokens"]),
                "completion_tokens": int(query_costs["completion_tokens"]),
                "embedding_tokens": int(embedding_costs["embedding_tokens"]),
                "estimated_cost_usd": round_money(total_cost),
            },
            "daily_cost": daily_cost,
            "cost_by_model": add_cost_percentages(cost_by_model),
        }
    except Exception as exc:
        logger.exception("analytics costs failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to load cost analytics") from exc


@router.get("/export")
async def analytics_export(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    project_id: str | None = Query(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> StreamingResponse:
    """Stream tenant query logs as CSV without loading all rows into memory."""
    try:
        filter_query = build_query_filter(tenant.id, project_id=project_id, date_from=date_from, date_to=date_to)
        filename = f"queries_{datetime.now(UTC).date().isoformat()}.csv"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(
            stream_query_csv(db, filter_query),
            media_type="text/csv",
            headers=headers,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("analytics export failed tenant_id=%s", tenant.id)
        raise internal_error("Failed to export query logs") from exc


async def read_cache(key: str) -> dict[str, Any] | None:
    """Read a JSON analytics payload from Redis."""
    redis_client = database_module.redis_client
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except (RedisError, json.JSONDecodeError) as exc:
        logger.warning("analytics cache read failed key=%s error=%s", key, exc)
        return None


async def write_cache(key: str, payload: dict[str, Any]) -> None:
    """Write a JSON analytics payload to Redis."""
    redis_client = database_module.redis_client
    if redis_client is None:
        return
    try:
        await redis_client.setex(key, OVERVIEW_CACHE_SECONDS, json.dumps(payload, separators=(",", ":")))
    except (RedisError, TypeError) as exc:
        logger.warning("analytics cache write failed key=%s error=%s", key, exc)


def build_query_filter(
    tenant_id: str,
    *,
    project_id: str | None = None,
    strategy: str | None = None,
    min_faithfulness: float | None = None,
    max_faithfulness: float | None = None,
    routing_decision: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Build a tenant-scoped query_logs filter from analytics query params."""
    if date_from and date_to and normalize_datetime(date_from) > normalize_datetime(date_to):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_from must be before date_to")

    filter_query: dict[str, Any] = {"tenant_id": tenant_id}
    if project_id:
        filter_query["project_id"] = project_id
    if strategy:
        filter_query["retrieval_strategy"] = strategy
    if routing_decision:
        filter_query["routing_decision"] = routing_decision
    faithfulness_filter: dict[str, float] = {}
    if min_faithfulness is not None:
        faithfulness_filter["$gte"] = min_faithfulness
    if max_faithfulness is not None:
        faithfulness_filter["$lte"] = max_faithfulness
    if faithfulness_filter:
        filter_query["faithfulness"] = faithfulness_filter
    date_filter: dict[str, datetime] = {}
    if date_from:
        date_filter["$gte"] = normalize_datetime(date_from)
    if date_to:
        date_filter["$lte"] = normalize_datetime(date_to)
    if date_filter:
        filter_query["created_at"] = date_filter
    return filter_query


def query_identity_filter(tenant_id: str, query_id: str) -> dict[str, Any]:
    """Build an id filter that supports app ids and MongoDB ObjectIds."""
    options: list[dict[str, Any]] = [{"id": query_id}]
    if ObjectId.is_valid(query_id):
        options.append({"_id": ObjectId(query_id)})
    return {"tenant_id": tenant_id, "$or": options}


async def query_counts_by_day(db: AsyncIOMotorDatabase, tenant_id: str, days: int) -> dict[str, int]:
    """Return query counts grouped by UTC day."""
    cutoff = datetime.now(UTC) - timedelta(days=days - 1)
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": start_of_day_utc(cutoff)}}},
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$created_at",
                        "timezone": "UTC",
                    }
                },
                "query_count": {"$sum": 1},
            }
        },
    ]
    rows = await db.query_logs.aggregate(pipeline).to_list(length=None)
    return {str(row["_id"]): int(row.get("query_count") or 0) for row in rows}


def merge_scores_and_query_counts(
    score_rows: list[dict[str, Any]],
    query_counts: dict[str, int],
    *,
    days: int,
) -> list[dict[str, Any]]:
    """Merge daily evaluation scores with total query counts."""
    scores_by_date = {str(row.get("date")): row for row in score_rows}
    merged = []
    for day in last_n_dates(days):
        row = scores_by_date.get(day, {})
        merged.append(
            {
                "date": day,
                "faithfulness": row.get("faithfulness"),
                "answer_relevancy": row.get("answer_relevancy"),
                "context_precision": row.get("context_precision"),
                "query_count": int(query_counts.get(day, 0)),
            }
        )
    return merged


async def total_cost(db: AsyncIOMotorDatabase, tenant_id: str, cutoff: datetime) -> float:
    """Return estimated query plus evaluation cost since cutoff."""
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": cutoff}}},
        {
            "$group": {
                "_id": None,
                "estimated_cost_usd": {"$sum": {"$ifNull": ["$estimated_cost_usd", 0]}},
                "evaluation_cost_usd": {"$sum": {"$ifNull": ["$evaluation_cost_usd", 0]}},
            }
        },
    ]
    rows = await db.query_logs.aggregate(pipeline).to_list(length=1)
    if not rows:
        return 0.0
    row = rows[0]
    return float(row.get("estimated_cost_usd") or 0.0) + float(row.get("evaluation_cost_usd") or 0.0)


async def quality_rates(db: AsyncIOMotorDatabase, tenant_id: str, days: int) -> dict[str, float]:
    """Return no-answer and insufficient-evidence rates."""
    no_answer = await rate_payload(db, tenant_id, days, no_answer_condition(), "no_answer")
    insufficient = await rate_payload(db, tenant_id, days, insufficient_evidence_condition(), "insufficient_evidence")
    return {
        "no_answer_rate_pct": no_answer["rate_pct"],
        "insufficient_evidence_rate_pct": insufficient["rate_pct"],
    }


async def build_strategy_comparison(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    *,
    days: int,
) -> list[dict[str, Any]]:
    """Build strategy comparison objects with metrics and latency."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    metric_pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": cutoff}}},
        {
            "$group": {
                "_id": "$retrieval_strategy",
                "total_queries": {"$sum": 1},
                "faithfulness_avg": {"$avg": "$faithfulness"},
                "answer_relevancy_avg": {"$avg": "$answer_relevancy"},
                "context_precision_avg": {"$avg": "$context_precision"},
            }
        },
    ]
    metric_rows = await db.query_logs.aggregate(metric_pipeline).to_list(length=None)
    latency_rows = await MetricsStore(db).get_latency_by_strategy(tenant_id, days=days)
    latency_by_strategy = {row["strategy"]: row for row in latency_rows}

    best_strategy = None
    best_faithfulness = -1.0
    for row in metric_rows:
        faithfulness = float_or_none(row.get("faithfulness_avg"))
        if faithfulness is not None and faithfulness > best_faithfulness:
            best_faithfulness = faithfulness
            best_strategy = str(row.get("_id") or "unknown")

    result = []
    for row in metric_rows:
        strategy = str(row.get("_id") or "unknown")
        display_name, description = STRATEGY_INFO.get(
            strategy,
            (strategy.replace("_", " ").title(), "Custom retrieval strategy."),
        )
        latency = latency_by_strategy.get(strategy, {})
        result.append(
            {
                "strategy": strategy,
                "display_name": display_name,
                "description": description,
                "total_queries": int(row.get("total_queries") or 0),
                "faithfulness_avg": round_optional(row.get("faithfulness_avg")),
                "answer_relevancy_avg": round_optional(row.get("answer_relevancy_avg")),
                "context_precision_avg": round_optional(row.get("context_precision_avg")),
                "latency_p50_ms": latency.get("p50"),
                "latency_p95_ms": latency.get("p95"),
                "is_recommended": strategy == best_strategy,
            }
        )
    return sorted(result, key=lambda item: item["total_queries"], reverse=True)


def failed_query_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize a low-quality query row for analytics tables."""
    return {
        "id": row.get("id"),
        "query": row.get("query"),
        "answer": row.get("answer"),
        "faithfulness": round_optional(row.get("faithfulness")),
        "answer_relevancy": round_optional(row.get("answer_relevancy")),
        "context_precision": round_optional(row.get("context_precision")),
        "strategy": row.get("retrieval_strategy"),
        "routing_decision": row.get("routing_decision"),
        "created_at": iso_datetime(row.get("created_at")),
    }


async def rate_payload(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    days: int,
    condition: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Return count, percentage, and daily trend for a query-rate condition."""
    cutoff = start_of_day_utc(datetime.now(UTC) - timedelta(days=days - 1))
    base_match = {"tenant_id": tenant_id, "created_at": {"$gte": cutoff}}
    total = await db.query_logs.count_documents(base_match)
    matched = await db.query_logs.count_documents({"$and": [base_match, condition]})
    daily_pipeline = [
        {"$match": base_match},
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$created_at",
                        "timezone": "UTC",
                    }
                },
                "total": {"$sum": 1},
                "matched": {"$sum": {"$cond": [condition_to_expression(condition), 1, 0]}},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    rows = await db.query_logs.aggregate(daily_pipeline).to_list(length=None)
    by_day = {str(row["_id"]): row for row in rows}
    return {
        "metric": label,
        "total_queries": int(total),
        "count": int(matched),
        "rate_pct": percent(matched, total),
        "days": days,
        "over_time": [
            {
                "date": day,
                "count": int((by_day.get(day) or {}).get("matched") or 0),
                "total_queries": int((by_day.get(day) or {}).get("total") or 0),
                "rate_pct": percent(
                    int((by_day.get(day) or {}).get("matched") or 0),
                    int((by_day.get(day) or {}).get("total") or 0),
                ),
            }
            for day in last_n_dates(days)
        ],
    }


def no_answer_condition() -> dict[str, Any]:
    """Return Mongo condition for no-answer logs."""
    return {"no_answer": True}


def insufficient_evidence_condition() -> dict[str, Any]:
    """Return Mongo condition for insufficient-evidence logs."""
    return {
        "$or": [
            {"metadata.insufficient_evidence": True},
            {"no_answer_reason": {"$in": sorted(INSUFFICIENT_EVIDENCE_REASONS)}},
        ]
    }


def condition_to_expression(condition: dict[str, Any]) -> dict[str, Any]:
    """Convert supported match conditions to an aggregation boolean expression."""
    if condition == no_answer_condition():
        return {"$eq": ["$no_answer", True]}
    return {
        "$or": [
            {"$eq": ["$metadata.insufficient_evidence", True]},
            {"$in": ["$no_answer_reason", sorted(INSUFFICIENT_EVIDENCE_REASONS)]},
        ]
    }


async def query_cost_summary(db: AsyncIOMotorDatabase, tenant_id: str, month_start: datetime) -> dict[str, Any]:
    """Summarize query token usage and cost for the current month."""
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": month_start}}},
        {
            "$group": {
                "_id": None,
                "total_tokens": {"$sum": {"$ifNull": ["$total_tokens", 0]}},
                "prompt_tokens": {"$sum": {"$ifNull": ["$prompt_tokens", 0]}},
                "completion_tokens": {"$sum": {"$ifNull": ["$completion_tokens", 0]}},
                "estimated_cost_usd": {"$sum": {"$ifNull": ["$estimated_cost_usd", 0]}},
                "evaluation_cost_usd": {"$sum": {"$ifNull": ["$evaluation_cost_usd", 0]}},
            }
        },
    ]
    rows = await db.query_logs.aggregate(pipeline).to_list(length=1)
    base = rows[0] if rows else {}
    by_model_rows = await db.query_logs.aggregate(
        [
            {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": month_start}}},
            {
                "$group": {
                    "_id": "$model",
                    "cost_usd": {"$sum": {"$ifNull": ["$estimated_cost_usd", 0]}},
                }
            },
        ]
    ).to_list(length=None)
    by_model = [
        {"model": str(row.get("_id") or "unknown"), "cost_usd": float(row.get("cost_usd") or 0.0)}
        for row in by_model_rows
    ]
    evaluation_cost = float(base.get("evaluation_cost_usd") or 0.0)
    if evaluation_cost > 0:
        by_model.append({"model": "ragas-evaluation", "cost_usd": evaluation_cost})
    return {
        "total_tokens": int(base.get("total_tokens") or 0),
        "prompt_tokens": int(base.get("prompt_tokens") or 0),
        "completion_tokens": int(base.get("completion_tokens") or 0),
        "estimated_cost_usd": float(base.get("estimated_cost_usd") or 0.0) + evaluation_cost,
        "by_model": by_model,
    }


async def embedding_cost_summary(db: AsyncIOMotorDatabase, tenant_id: str, month_start: datetime) -> dict[str, Any]:
    """Estimate embedding token usage and cost from chunk metadata."""
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": month_start}}},
        {
            "$group": {
                "_id": "$embedding_model",
                "embedding_tokens": {"$sum": {"$ifNull": ["$token_count", 0]}},
            }
        },
    ]
    rows = await db.chunks.aggregate(pipeline).to_list(length=None)
    total_tokens = 0
    by_model = []
    for row in rows:
        model = str(row.get("_id") or "unknown")
        tokens = int(row.get("embedding_tokens") or 0)
        cost = embedding_cost(tokens, model)
        total_tokens += tokens
        by_model.append({"model": model, "cost_usd": cost})
    return {
        "embedding_tokens": total_tokens,
        "estimated_cost_usd": sum(item["cost_usd"] for item in by_model),
        "by_model": by_model,
    }


async def daily_cost_summary(db: AsyncIOMotorDatabase, tenant_id: str, month_start: datetime) -> list[dict[str, Any]]:
    """Return daily query and evaluation costs for the current month."""
    query_rows = await db.query_logs.aggregate(
        [
            {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": month_start}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$created_at",
                            "timezone": "UTC",
                        }
                    },
                    "cost_usd": {
                        "$sum": {
                            "$add": [
                                {"$ifNull": ["$estimated_cost_usd", 0]},
                                {"$ifNull": ["$evaluation_cost_usd", 0]},
                            ]
                        }
                    },
                    "queries": {"$sum": 1},
                }
            },
        ]
    ).to_list(length=None)
    daily: dict[str, dict[str, Any]] = {
        str(row["_id"]): {"date": str(row["_id"]), "cost_usd": float(row.get("cost_usd") or 0.0), "queries": int(row.get("queries") or 0)}
        for row in query_rows
    }
    embedding_rows = await db.chunks.aggregate(
        [
            {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": month_start}}},
            {
                "$group": {
                    "_id": {
                        "date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$created_at",
                                "timezone": "UTC",
                            }
                        },
                        "model": "$embedding_model",
                    },
                    "tokens": {"$sum": {"$ifNull": ["$token_count", 0]}},
                }
            },
        ]
    ).to_list(length=None)
    for row in embedding_rows:
        day = str(row["_id"].get("date"))
        model = str(row["_id"].get("model") or "unknown")
        daily.setdefault(day, {"date": day, "cost_usd": 0.0, "queries": 0})
        daily[day]["cost_usd"] += embedding_cost(int(row.get("tokens") or 0), model)
    return [
        {"date": row["date"], "cost_usd": round_money(row["cost_usd"]), "queries": row["queries"]}
        for row in sorted(daily.values(), key=lambda item: item["date"])
    ]


def build_cost_by_model(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge cost rows by model."""
    totals: dict[str, float] = defaultdict(float)
    for group in groups:
        for item in group:
            totals[str(item["model"])] += float(item.get("cost_usd") or 0.0)
    return [{"model": model, "cost_usd": round_money(cost)} for model, cost in totals.items() if cost > 0]


def add_cost_percentages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add percentage share to cost-by-model rows."""
    total = sum(float(row.get("cost_usd") or 0.0) for row in rows)
    result = []
    for row in sorted(rows, key=lambda item: item["cost_usd"], reverse=True):
        result.append(
            {
                "model": row["model"],
                "cost_usd": row["cost_usd"],
                "pct": round((float(row["cost_usd"]) / total) * 100, 2) if total > 0 else 0.0,
            }
        )
    return result


async def stream_query_csv(db: AsyncIOMotorDatabase, filter_query: dict[str, Any]) -> AsyncIterator[str]:
    """Yield CSV rows for query logs."""
    columns = [
        "id",
        "created_at",
        "query",
        "answer",
        "strategy",
        "routing_decision",
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "total_latency_ms",
        "model",
        "cost_usd",
    ]
    yield csv_line(columns)
    cursor = db.query_logs.find(
        filter_query,
        {
            "_id": 0,
            "id": 1,
            "created_at": 1,
            "query": 1,
            "answer": 1,
            "retrieval_strategy": 1,
            "routing_decision": 1,
            "faithfulness": 1,
            "answer_relevancy": 1,
            "context_precision": 1,
            "total_latency_ms": 1,
            "model": 1,
            "estimated_cost_usd": 1,
        },
    ).sort("created_at", -1)
    async for row in cursor:
        yield csv_line(
            [
                row.get("id"),
                iso_datetime(row.get("created_at")),
                row.get("query"),
                row.get("answer"),
                row.get("retrieval_strategy"),
                row.get("routing_decision"),
                row.get("faithfulness"),
                row.get("answer_relevancy"),
                row.get("context_precision"),
                row.get("total_latency_ms"),
                row.get("model"),
                row.get("estimated_cost_usd"),
            ]
        )


def csv_line(values: list[Any]) -> str:
    """Serialize a CSV line."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["" if value is None else value for value in values])
    return output.getvalue()


def build_prompt_preview(query_log: dict[str, Any]) -> str:
    """Reconstruct the first 500 characters of the likely RAG prompt."""
    contexts = []
    for chunk in query_log.get("retrieved_chunks") or []:
        metadata = chunk.get("metadata") or {}
        contexts.append(
            f"[Source: {chunk.get('document_name')}, chunk {metadata.get('chunk_index')}]\n{chunk.get('text')}"
        )
    joined_contexts = "\n---\n".join(contexts)
    preview = (
        "Retrieved context:\n"
        f"{joined_contexts}\n\n"
        "Use only the retrieved context above. Include source markers when feasible.\n\n"
        f"Question: {query_log.get('query')}"
    )
    return preview[:500]


def pearson_correlation(pairs: list[tuple[float, float]]) -> float | None:
    """Compute Pearson correlation for confidence and faithfulness pairs."""
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_var * y_var)
    if denominator == 0:
        return None
    return numerator / denominator


def correlation_interpretation(value: float | None) -> str:
    """Return a compact human-readable correlation label."""
    if value is None:
        return "insufficient_data"
    magnitude = abs(value)
    if magnitude >= 0.7:
        strength = "strong"
    elif magnitude >= 0.4:
        strength = "moderate"
    elif magnitude >= 0.2:
        strength = "weak"
    else:
        strength = "very_weak"
    direction = "positive" if value >= 0 else "negative"
    return f"{strength}_{direction}"


def embedding_cost(tokens: int, model: str) -> float:
    """Estimate embedding cost for a model and token count."""
    rate = EMBEDDING_COST_PER_1K_TOKENS.get(model, EMBEDDING_COST_PER_1K_TOKENS["text-embedding-3-small"])
    return (tokens / 1000) * rate


def normalize_datetime(value: datetime) -> datetime:
    """Ensure datetime values are timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def start_of_day_utc(value: datetime | None = None) -> datetime:
    """Return the UTC start of day."""
    resolved = normalize_datetime(value or datetime.now(UTC))
    return resolved.replace(hour=0, minute=0, second=0, microsecond=0)


def start_of_week_utc() -> datetime:
    """Return the UTC start of the current ISO week."""
    today = start_of_day_utc()
    return today - timedelta(days=today.weekday())


def start_of_month_utc() -> datetime:
    """Return the UTC start of the current month."""
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def last_n_dates(days: int) -> list[str]:
    """Return the last N UTC dates as ISO strings, including today."""
    today = datetime.now(UTC).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def percent(numerator: int | float, denominator: int | float) -> float:
    """Return a rounded percentage."""
    if not denominator:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100, 2)


def round_optional(value: Any) -> float | None:
    """Round a numeric value or return None."""
    parsed = float_or_none(value)
    return round(parsed, 4) if parsed is not None else None


def round_money(value: Any) -> float:
    """Round a money value."""
    parsed = float_or_none(value)
    return round(parsed or 0.0, 6)


def float_or_none(value: Any) -> float | None:
    """Convert a value to float while preserving missing and NaN values."""
    try:
        if value is None:
            return None
        parsed = float(value)
        if math.isnan(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def truncate(value: str, limit: int) -> str:
    """Return a compact string preview."""
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def iso_datetime(value: Any) -> str | None:
    """Serialize datetimes to ISO strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return normalize_datetime(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def nested_get(payload: dict[str, Any], *keys: str) -> Any:
    """Read a nested dictionary value."""
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def to_jsonable(value: Any) -> Any:
    """Convert MongoDB payloads into JSON-safe values."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return normalize_datetime(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def internal_error(message: str) -> HTTPException:
    """Return a generic internal error without leaking backend details."""
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)
