"""MongoDB persistence and analytics helpers for evaluation metrics."""

from __future__ import annotations

import inspect
import itertools
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId

from .ragas_eval import EvaluationResult


class MetricsStore:
    """Methods for reading and writing evaluation metrics in MongoDB."""

    def __init__(self, db: Any):
        self.db = db

    async def save_evaluation_result(
        self,
        query_log_id: str,
        result: EvaluationResult,
    ) -> None:
        """Update a query log with RAGAS or fallback scores after evaluation."""
        has_scores = result.has_scores
        update = {
            "evaluation_status": "completed" if has_scores else "failed",
            "evaluated_at": datetime.now(UTC),
            "evaluation_runtime_ms": result.evaluation_runtime_ms,
            "evaluation_cost_usd": result.evaluation_cost_usd,
            "evaluation_version": result.evaluation_version,
            "evaluation_backend": result.evaluation_backend,
            "evaluation_provider": result.evaluation_provider,
            "evaluation_model": result.evaluation_model,
            "evaluation_model_version": result.evaluation_model_version,
            "evaluation_metadata": result.metadata,
        }
        if has_scores:
            update.update(
                {
                    "faithfulness": result.faithfulness,
                    "answer_relevancy": result.answer_relevancy,
                    "context_precision": result.context_precision,
                    "context_recall": result.context_recall,
                }
            )
        if result.error:
            update["evaluation_error"] = result.error

        payload: dict[str, Any] = {"$set": update}
        if not result.error:
            payload["$unset"] = {"evaluation_error": ""}

        await _maybe_await(
            self.db.query_logs.update_one(
                _query_log_filter(query_log_id),
                payload,
            )
        )

    async def get_average_scores(
        self,
        tenant_id: str,
        project_id: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Compute average RAGAS scores for a tenant, optionally filtered by project.

        Completed evaluations are filtered to the last N days. total_queries uses
        the same tenant/project/time filter without requiring completed evals.
        """
        base_match = _base_match(tenant_id, days, project_id)
        total_queries = await _maybe_await(self.db.query_logs.count_documents(base_match))

        pipeline = [
            {"$match": {**base_match, "evaluation_status": "completed"}},
            {
                "$group": {
                    "_id": None,
                    "faithfulness_avg": {"$avg": "$faithfulness"},
                    "answer_relevancy_avg": {"$avg": "$answer_relevancy"},
                    "context_precision_avg": {"$avg": "$context_precision"},
                    "context_recall_avg": {"$avg": "$context_recall"},
                    "evaluation_cost_usd": {"$sum": "$evaluation_cost_usd"},
                    "total_evaluated": {"$sum": 1},
                }
            },
        ]
        rows = await _cursor_to_list(self.db.query_logs.aggregate(pipeline), length=1)
        row = rows[0] if rows else {}
        return {
            "faithfulness_avg": _round_optional(row.get("faithfulness_avg")),
            "answer_relevancy_avg": _round_optional(row.get("answer_relevancy_avg")),
            "context_precision_avg": _round_optional(row.get("context_precision_avg")),
            "context_recall_avg": _round_optional(row.get("context_recall_avg")),
            "evaluation_cost_usd": _round_money(row.get("evaluation_cost_usd")),
            "total_evaluated": int(row.get("total_evaluated") or 0),
            "total_queries": int(total_queries or 0),
        }

    async def get_scores_over_time(
        self,
        tenant_id: str,
        days: int = 14,
    ) -> list[dict[str, Any]]:
        """
        Return daily average scores for the last N days.

        Each item contains date, faithfulness, answer_relevancy,
        context_precision, context_recall, and count.
        """
        pipeline = [
            {"$match": {**_base_match(tenant_id, days), "evaluation_status": "completed"}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$created_at",
                            "timezone": "UTC",
                        }
                    },
                    "faithfulness": {"$avg": "$faithfulness"},
                    "answer_relevancy": {"$avg": "$answer_relevancy"},
                    "context_precision": {"$avg": "$context_precision"},
                    "context_recall": {"$avg": "$context_recall"},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        rows = await _cursor_to_list(self.db.query_logs.aggregate(pipeline))
        return [
            {
                "date": row.get("_id"),
                "faithfulness": _round_optional(row.get("faithfulness")),
                "answer_relevancy": _round_optional(row.get("answer_relevancy")),
                "context_precision": _round_optional(row.get("context_precision")),
                "context_recall": _round_optional(row.get("context_recall")),
                "count": int(row.get("count") or 0),
            }
            for row in rows
        ]

    async def get_latency_percentiles(
        self,
        tenant_id: str,
        days: int = 7,
    ) -> dict[str, Any]:
        """Compute p50, p95, p99, mean, min, and max query latency."""
        cursor = self.db.query_logs.find(
            {**_base_match(tenant_id, days), "total_latency_ms": {"$type": "number"}},
            {"_id": 0, "total_latency_ms": 1},
        )
        rows = await _cursor_to_list(cursor)
        latencies = sorted(float(row["total_latency_ms"]) for row in rows if row.get("total_latency_ms") is not None)
        return _latency_summary(latencies)

    async def get_latency_by_strategy(
        self,
        tenant_id: str,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return average and percentile latency grouped by retrieval strategy."""
        cursor = self.db.query_logs.find(
            {**_base_match(tenant_id, days), "total_latency_ms": {"$type": "number"}},
            {"_id": 0, "retrieval_strategy": 1, "total_latency_ms": 1},
        )
        rows = await _cursor_to_list(cursor)
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            strategy = str(row.get("retrieval_strategy") or "unknown")
            latency = row.get("total_latency_ms")
            if latency is None:
                continue
            grouped[strategy].append(float(latency))

        result = []
        for strategy, latencies in grouped.items():
            summary = _latency_summary(sorted(latencies))
            result.append(
                {
                    "strategy": strategy,
                    "p50": summary["p50"],
                    "p95": summary["p95"],
                    "mean": summary["mean"],
                    "count": summary["sample_size"],
                }
            )
        return sorted(result, key=lambda item: item["count"], reverse=True)

    async def get_failed_queries(
        self,
        tenant_id: str,
        min_faithfulness: float = 0.5,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return low-faithfulness queries that need retrieval or prompt tuning."""
        cursor = (
            self.db.query_logs.find(
                {
                    "tenant_id": tenant_id,
                    "evaluation_status": "completed",
                    "faithfulness": {"$lt": min_faithfulness},
                },
                {
                    "_id": 0,
                    "query": 1,
                    "answer": 1,
                    "faithfulness": 1,
                    "retrieval_strategy": 1,
                    "created_at": 1,
                },
            )
            .sort([("faithfulness", 1), ("created_at", -1)])
            .limit(limit)
        )
        rows = await _cursor_to_list(cursor, length=limit)
        return [
            {
                "query": str(row.get("query") or ""),
                "answer_truncated": _truncate(str(row.get("answer") or ""), 300),
                "faithfulness": _round_optional(row.get("faithfulness")),
                "strategy": str(row.get("retrieval_strategy") or ""),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]


def _base_match(tenant_id: str, days: int, project_id: str | None = None) -> dict[str, Any]:
    """Build a tenant/project/date MongoDB match expression."""
    match: dict[str, Any] = {
        "tenant_id": tenant_id,
        "created_at": {"$gte": datetime.now(UTC) - timedelta(days=days)},
    }
    if project_id:
        match["project_id"] = project_id
    return match


def _query_log_filter(query_log_id: str) -> dict[str, Any]:
    """Match query logs by app id and by Mongo _id when possible."""
    options: list[dict[str, Any]] = [{"id": query_log_id}, {"_id": query_log_id}]
    if ObjectId.is_valid(query_log_id):
        options.append({"_id": ObjectId(query_log_id)})
    return {"$or": options}


async def _maybe_await(value: Any) -> Any:
    """Await Motor results while also accepting synchronous PyMongo results."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _cursor_to_list(cursor: Any, length: int | None = None) -> list[Any]:
    """Convert a Motor or PyMongo cursor to a list."""
    to_list = getattr(cursor, "to_list", None)
    if callable(to_list):
        return await _maybe_await(to_list(length=length))
    if length is None:
        return list(cursor)
    return list(itertools.islice(cursor, length))


def _latency_summary(values: list[float]) -> dict[str, Any]:
    """Return percentile and descriptive latency statistics."""
    if not values:
        return {
            "p50": None,
            "p95": None,
            "p99": None,
            "mean": None,
            "min": None,
            "max": None,
            "sample_size": 0,
        }
    return {
        "p50": _round_latency(_percentile(values, 0.50)),
        "p95": _round_latency(_percentile(values, 0.95)),
        "p99": _round_latency(_percentile(values, 0.99)),
        "mean": _round_latency(sum(values) / len(values)),
        "min": _round_latency(min(values)),
        "max": _round_latency(max(values)),
        "sample_size": len(values),
    }


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the percentile value using the index rule requested for analytics."""
    index = min(len(sorted_values) - 1, max(0, int(len(sorted_values) * pct)))
    return sorted_values[index]


def _round_optional(value: Any) -> float | None:
    """Round a numeric score or return None."""
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return round(number, 4)
    except (TypeError, ValueError):
        return None


def _round_latency(value: float) -> float:
    """Round latency values for API responses."""
    return round(float(value), 2)


def _round_money(value: Any) -> float:
    """Round USD totals to six decimals."""
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0


def _truncate(value: str, limit: int) -> str:
    """Return a compact answer preview."""
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."
