"""Celery tasks for asynchronous RAG response evaluation."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument

from ..config import get_settings
from ..evaluation.metrics_store import MetricsStore
from ..evaluation.ragas_eval import EVALUATION_VERSION, run_ragas_evaluation
from ..logging_config import get_logger
from .celery_app import celery_app

logger = get_logger(__name__)

SKIPPED_EVALUATION_ROUTES = {"direct", "web_search", "clarify"}


def get_sync_db() -> Any:
    """Return a synchronous PyMongo database for Celery tasks."""
    settings = get_settings()
    client = MongoClient(settings.mongo_url, uuidRepresentation="standard")
    return client[settings.mongo_db_name]


@celery_app.task(
    queue="evaluation",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    name="run_ragas_evaluation",
)
def run_evaluation_task(self: Any, query_log_id: str) -> dict[str, Any]:
    """
    Run RAGAS evaluation for a completed query log.

    The task claims pending logs atomically, skips non-RAG routes, evaluates RAG
    answers, stores scores on query_logs, and retries unexpected task failures.
    """
    settings = get_settings()
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    mongo_client = MongoClient(settings.mongo_url, uuidRepresentation="standard")
    db = mongo_client[settings.mongo_db_name]
    identity_filter = _query_log_filter(query_log_id)

    try:
        query_log = db.query_logs.find_one_and_update(
            {"$and": [identity_filter, {"evaluation_status": "pending"}]},
            {
                "$set": {
                    "evaluation_status": "in_progress",
                    "evaluation_started_at": utc_now(),
                    "evaluation_task_id": self.request.id,
                    "evaluation_version": EVALUATION_VERSION,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if query_log is None:
            existing = db.query_logs.find_one(identity_filter, {"evaluation_status": 1})
            status = existing.get("evaluation_status") if existing else "missing"
            logger.info(
                "evaluation task skipped query_log_id=%s status=%s",
                query_log_id,
                status,
            )
            return {"query_log_id": query_log_id, "status": status}

        if should_skip_evaluation(query_log):
            reason = _skip_reason(query_log)
            db.query_logs.update_one(
                identity_filter,
                {
                    "$set": {
                        "evaluation_status": "skipped",
                        "evaluated_at": utc_now(),
                        "evaluation_runtime_ms": 0.0,
                        "evaluation_cost_usd": 0.0,
                        "evaluation_error": reason,
                        "evaluation_version": EVALUATION_VERSION,
                        "evaluation_backend": "skipped",
                        "evaluation_provider": "none",
                        "evaluation_model": "none",
                        "evaluation_metadata": {"skip_reason": reason},
                    }
                },
            )
            logger.info("evaluation skipped query_log_id=%s reason=%s", query_log_id, reason)
            return {"query_log_id": query_log_id, "status": "skipped", "reason": reason}

        contexts = _extract_contexts(query_log)
        result = run_async(
            run_ragas_evaluation(
                question=str(query_log.get("query") or ""),
                answer=str(query_log.get("answer") or ""),
                contexts=contexts,
                ground_truth=_extract_ground_truth(query_log),
            )
        )
        run_async(MetricsStore(db).save_evaluation_result(query_log_id, result))

        final_status = "completed" if result.has_scores else "failed"
        logger.info(
            "evaluation finished query_log_id=%s status=%s backend=%s faithfulness=%s relevancy=%s precision=%s error=%s",
            query_log_id,
            final_status,
            result.evaluation_backend,
            result.faithfulness,
            result.answer_relevancy,
            result.context_precision,
            result.error,
        )
        return {
            "query_log_id": query_log_id,
            "status": final_status,
            "backend": result.evaluation_backend,
            "faithfulness": result.faithfulness,
            "answer_relevancy": result.answer_relevancy,
            "context_precision": result.context_precision,
            "context_recall": result.context_recall,
            "error": result.error,
        }

    except Exception as exc:
        logger.exception("evaluation task failed query_log_id=%s retry=%s", query_log_id, self.request.retries)
        if self.request.retries < self.max_retries:
            db.query_logs.update_one(
                identity_filter,
                {
                    "$set": {
                        "evaluation_status": "pending",
                        "evaluation_error": f"evaluation task retry: {str(exc)[:200]}",
                    },
                    "$unset": {
                        "evaluation_started_at": "",
                        "evaluation_task_id": "",
                    },
                },
            )
            raise self.retry(exc=exc)

        db.query_logs.update_one(
            identity_filter,
            {
                "$set": {
                    "evaluation_status": "failed",
                    "evaluated_at": utc_now(),
                    "evaluation_error": f"evaluation task failed: {str(exc)[:500]}",
                    "evaluation_version": EVALUATION_VERSION,
                    "evaluation_backend": "task",
                    "evaluation_provider": "none",
                    "evaluation_model": "none",
                }
            },
        )
        return {"query_log_id": query_log_id, "status": "failed", "error": str(exc)}
    finally:
        mongo_client.close()


def run_async(coro: Any) -> Any:
    """Run an async coroutine from Celery's synchronous worker context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if hasattr(coro, "close"):
        coro.close()
    raise RuntimeError("cannot run evaluation task inside an active event loop")


def should_skip_evaluation(query_log: dict[str, Any]) -> bool:
    """Return whether the log represents a route where RAGAS is not applicable."""
    decision = str(query_log.get("routing_decision") or "").lower()
    strategy = str(query_log.get("retrieval_strategy") or "").lower()
    return decision in SKIPPED_EVALUATION_ROUTES or strategy in SKIPPED_EVALUATION_ROUTES


def _skip_reason(query_log: dict[str, Any]) -> str:
    """Build a compact skip reason for MongoDB."""
    decision = str(query_log.get("routing_decision") or "unknown")
    strategy = str(query_log.get("retrieval_strategy") or "unknown")
    return f"skipped: evaluation is not applicable for decision={decision}, strategy={strategy}"


def _extract_contexts(query_log: dict[str, Any]) -> list[str]:
    """Extract retrieved chunk texts from a query log."""
    contexts = []
    for chunk in query_log.get("retrieved_chunks") or []:
        text = str(chunk.get("text") or "").strip()
        if text:
            contexts.append(text)
    return contexts


def _extract_ground_truth(query_log: dict[str, Any]) -> str | None:
    """Load optional ground truth from top-level or metadata fields."""
    ground_truth = query_log.get("ground_truth")
    if ground_truth:
        return str(ground_truth)
    metadata = query_log.get("metadata") or {}
    ground_truth = metadata.get("ground_truth")
    return str(ground_truth) if ground_truth else None


def _query_log_filter(query_log_id: str) -> dict[str, Any]:
    """Match query logs by app id and Mongo _id when possible."""
    options: list[dict[str, Any]] = [{"id": query_log_id}, {"_id": query_log_id}]
    if ObjectId.is_valid(query_log_id):
        options.append({"_id": ObjectId(query_log_id)})
    return {"$or": options}


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)
