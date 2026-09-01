"""
DLQ (Dead-Letter Queue) API.

Exposes the Celery → Redis dead-letter queue for inspection and management.

Endpoints:
  GET  /api/dlq        — list dead-lettered tasks (newest first)
  GET  /api/dlq/count  — quick count
  POST /api/dlq/clear  — purge all dead letters (admin action)

The queue is populated by the Celery ``task_failure`` signal handler in
``app/worker/celery_app.py`` — a task ends up here when it exhausts its
``max_retries`` (or a ``MaxRetriesExceededError`` propagates).

Each entry is a JSON payload:
  {
    "task_name": "app.worker.tasks.crawl_entity_task",
    "task_id":   "uuid",
    "args":      [...],
    "kwargs":    {...},
    "exc":       "repr of the exception",
    "failed_at": "2025-01-01T00:00:00Z"
  }
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.cache.redis_client import get_redis
from app.worker.celery_app import DLQ_REDIS_KEY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dlq", tags=["DLQ"])


def _decode(raw: Any) -> Optional[dict]:
    """Best-effort JSON decode of a raw Redis list entry."""
    if raw is None:
        return None
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": str(raw)[:500], "parse_error": True}
    if isinstance(raw, dict):
        return raw
    return {"raw": str(raw)[:500], "parse_error": True}


@router.get("")
def list_dlq(
    limit: int = Query(50, ge=1, le=500, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Skip this many newest entries"),
) -> dict:
    """
    Return dead-lettered tasks, newest first.

    ``limit`` defaults to 50, capped at 500. ``offset`` skips the N most
    recent entries (useful for pagination — the Redis list is FIFO, so we
    reverse to show newest on top).
    """
    try:
        r = get_redis()
    except Exception as e:
        logger.error(f"Redis unavailable for DLQ list: {e}")
        raise HTTPException(status_code=503, detail="Redis unavailable")

    try:
        total = r.llen(DLQ_REDIS_KEY)
        # Fetch a window of entries; newest are at the tail (index total-1).
        if total == 0:
            return {"total": 0, "entries": []}
        start = max(total - offset - limit, 0)
        end = total - offset - 1
        if end < start:
            return {"total": total, "entries": []}
        raw_entries = r.lrange(DLQ_REDIS_KEY, start, end)
        entries = [e for e in (_decode(x) for x in raw_entries) if e is not None]
        # Newest first
        entries.reverse()
        return {
            "total": total,
            "returned": len(entries),
            "offset": offset,
            "limit": limit,
            "entries": entries,
        }
    except Exception as e:
        logger.error(f"DLQ list failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/count")
def dlq_count() -> dict:
    """Quick dead-letter count (no payload)."""
    try:
        r = get_redis()
        total = r.llen(DLQ_REDIS_KEY)
        return {"total": total, "key": DLQ_REDIS_KEY}
    except Exception as e:
        logger.error(f"Redis unavailable for DLQ count: {e}")
        raise HTTPException(status_code=503, detail="Redis unavailable")


@router.post("/clear")
def clear_dlq() -> dict:
    """
    Purge ALL dead letters. Destructive — operators should use sparingly.
    Returns the number of entries removed.
    """
    try:
        r = get_redis()
        removed = r.delete(DLQ_REDIS_KEY)
        return {"removed": int(removed or 0), "cleared_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"DLQ clear failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
