import json
import logging
import os
from celery import Celery
from celery.signals import task_failure
from app.config import settings

logger = logging.getLogger(__name__)

redis_url = settings.REDIS_URL

celery_app = Celery(
    "opendb_worker",
    broker=redis_url,
    backend=redis_url,
    include=["app.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
    # ---- Dead-letter queue hardening ----
    # Acknowledge only after the task has run, so a worker crash re-queues
    # in-flight work instead of silently losing it.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Per-task retry defaults; individual tasks may override via max_retries.
    task_default_retries=2,
    task_retry_countdown=30,
)

# Redis key that backs the dead-letter queue.
DLQ_REDIS_KEY = "opendb:dlq"


def push_to_dlq(task_name: str, task_id: str, args, kwargs, exc) -> None:
    """Serialize a failed task's payload into the Redis dead-letter list.

    Pushed by the task_failure signal when a task has exhausted its retries.
    Failures to push are logged but never mask the original task failure.
    """
    try:
        from app.cache.redis_client import get_redis

        redis_client = get_redis()
        payload = {
            "task_name": task_name,
            "task_id": task_id,
            "args": [str(a) for a in (args or [])],
            "kwargs": {k: str(v) for k, v in (kwargs or {}).items()},
            "error": f"{type(exc).__name__}: {exc}",
            "retries_exhausted": True,
        }
        redis_client.rpush(DLQ_REDIS_KEY, json.dumps(payload, default=str))
        logger.warning(
            "Dead-lettered task %s (id=%s) to %s",
            task_name, task_id, DLQ_REDIS_KEY,
        )
    except Exception as dlq_err:  # noqa: BLE001 - DLQ must never crash the signal
        logger.error("Failed to push task %s to DLQ: %s", task_name, dlq_err)


@task_failure.connect
def on_task_failure(sender, task_id, exception, args, kwargs, traceback, **_):
    """Route tasks that exhaust their retries into the Redis DLQ.

    Only the terminal failure (retries exhausted) is dead-lettered; transient
    retries are handled by Celery itself. We detect exhaustion by checking the
    task's max_retries against the failure state via the task request.
    """
    task = sender
    max_retries = getattr(task, "max_retries", 0)
    # Celery raises a special retry exception when retries are exhausted;
    # a plain MaxRetriesExceededError means the task gave up for good.
    exhausted = False
    try:
        from celery.exceptions import MaxRetriesExceededError
        exhausted = isinstance(exception, MaxRetriesExceededError)
    except Exception:
        exhausted = False

    # As a fallback, treat any task failure whose request has no remaining
    # retries as dead-letterable.
    if not exhausted:
        request = getattr(task, "request", None)
        if request is not None:
            retries = getattr(request, "retries", 0)
            if retries >= max_retries:
                exhausted = True

    if exhausted:
        push_to_dlq(task.name, task_id, args, kwargs, exception)
    else:
        logger.info(
            "Task %s failed (retriable, id=%s): %s",
            task.name, task_id, exception,
        )
