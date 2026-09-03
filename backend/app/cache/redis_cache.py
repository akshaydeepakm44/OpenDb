"""Redis-backed cache for OpenDB.

Provides a thin, safe cache wrapper used by the search path (SearXNG results)
and entity lookups. All operations are best-effort: a cache miss, Redis
outage, or serialization failure falls back to the underlying data source
rather than failing the request.

Keys are namespaced under ``opendb:cache:`` to avoid collisions with the
broker / result backend / DLQ.
"""
import hashlib
import json
import logging
from typing import Any, Optional

from app.cache.redis_client import get_redis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "opendb:cache:"
DEFAULT_TTL_SECONDS = 300  # 5 minutes


def _key(namespace: str, *parts: Any) -> str:
    """Build a deterministic, collision-safe cache key."""
    raw = ":".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{CACHE_PREFIX}{namespace}:{digest}"


def cache_get(namespace: str, *parts: Any) -> Optional[Any]:
    """Return the cached value for (namespace, *parts), or None on miss/error."""
    try:
        client = get_redis()
        if not client:
            return None
        raw = client.get(_key(namespace, *parts))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - cache must never break the request
        logger.warning("cache_get failed for %s: %s", namespace, exc)
        return None


def cache_set(namespace: str, *parts: Any, value: Any, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Store a JSON-serializable value. Silently no-ops on error."""
    try:
        client = get_redis()
        if not client:
            return
        client.setex(_key(namespace, *parts), ttl, json.dumps(value, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_set failed for %s: %s", namespace, exc)


def cache_delete_namespace(namespace: str, pattern_prefix: str = "*") -> None:
    """Best-effort bulk delete for a namespace (used for invalidation)."""
    try:
        client = get_redis()
        if not client:
            return
        keys = client.scan_iter(match=f"{CACHE_PREFIX}{namespace}:{pattern_prefix}")
        if keys:
            client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_delete_namespace failed for %s: %s", namespace, exc)
