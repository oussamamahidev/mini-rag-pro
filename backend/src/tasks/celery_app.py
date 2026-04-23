"""Celery application configuration for background RAG processing."""

from __future__ import annotations

import importlib.util
import os

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue


def redis_url_from_environment() -> str:
    """Return Redis URL without requiring all application settings at import time."""
    return os.environ.get("REDIS_URL") or os.environ.get("redis_url") or "redis://localhost:6379"


celery_app = Celery(
    "mini_rag",
    broker=redis_url_from_environment(),
    backend=redis_url_from_environment(),
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    task_queues=(
        Queue("default", Exchange("default"), routing_key="default"),
        Queue("file_processing", Exchange("file_processing"), routing_key="file_processing"),
        Queue("data_indexing", Exchange("data_indexing"), routing_key="data_indexing"),
        Queue("evaluation", Exchange("evaluation"), routing_key="evaluation"),
    ),
    task_routes={
        "src.tasks.file_tasks.process_uploaded_file": {"queue": "file_processing"},
        "src.tasks.file_tasks.index_document_chunks": {"queue": "data_indexing"},
        "src.tasks.file_tasks.cleanup_stale_documents": {"queue": "default"},
        "run_ragas_evaluation": {"queue": "evaluation"},
    },
    beat_schedule={
        "cleanup-stale-tasks": {
            "task": "src.tasks.file_tasks.cleanup_stale_documents",
            "schedule": crontab(minute=0),
        },
    },
)

celery_app.autodiscover_tasks(["src.tasks"], related_name="file_tasks")
if importlib.util.find_spec("src.tasks.eval_tasks") is not None:
    celery_app.autodiscover_tasks(["src.tasks"], related_name="eval_tasks")
