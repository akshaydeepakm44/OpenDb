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
        keys = list(client.scan_iter(match=f"{CACHE_PREFIX}{namespace}:{pattern_prefix}"))
        if keys:
            client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_delete_namespace failed for %s: %s", namespace, exc)


# ─────────────────────────────────────────────────────────────────────────────
# ENTERPRISE REDIS L1 CACHE METHODS (Matching Architecture Diagram)
# ─────────────────────────────────────────────────────────────────────────────

def acquire_crawl_lock(domain: str, ttl: int = 300) -> bool:
    """Acquire 300s Mutex Lock for crawl operation: lock:crawl:{domain}."""
    try:
        client = get_redis()
        if not client:
            return True
        key = f"lock:crawl:{domain.lower().strip()}"
        acquired = client.set(key, "locked", nx=True, ex=ttl)
        return bool(acquired)
    except Exception as exc:
        logger.warning("acquire_crawl_lock failed for %s: %s", domain, exc)
        return True

def release_crawl_lock(domain: str) -> None:
    """Release crawl Mutex lock for domain."""
    try:
        client = get_redis()
        if client:
            client.delete(f"lock:crawl:{domain.lower().strip()}")
    except Exception:
        pass

def get_master_lead_cache(domain: str) -> Optional[dict]:
    """Instant Get from Hot Cache: master:lead:{domain} (7d TTL)."""
    try:
        client = get_redis()
        if not client:
            return None
        raw = client.get(f"master:lead:{domain.lower().strip()}")
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("get_master_lead_cache failed for %s: %s", domain, exc)
        return None

def set_master_lead_cache(domain: str, lead_data: dict, ttl: int = 604800) -> None:
    """Push Hot Cache: master:lead:{domain} (7d TTL / 604,800s)."""
    try:
        client = get_redis()
        if client:
            client.setex(f"master:lead:{domain.lower().strip()}", ttl, json.dumps(lead_data, default=str))
    except Exception as exc:
        logger.warning("set_master_lead_cache failed for %s: %s", domain, exc)

def cache_query_hash(query_hash: str, results: Any, ttl: int = 60) -> None:
    """Search API 0.04ms Cache: cache:query:{hash} (60s TTL)."""
    try:
        client = get_redis()
        if client:
            client.setex(f"cache:query:{query_hash}", ttl, json.dumps(results, default=str))
    except Exception:
        pass

def get_cached_query_hash(query_hash: str) -> Optional[Any]:
    """Retrieve 0.04ms cached search API response."""
    try:
        client = get_redis()
        if not client:
            return None
        raw = client.get(f"cache:query:{query_hash}")
        return json.loads(raw) if raw else None
    except Exception:
        return None

def is_verified_domain_set(domain: str) -> bool:
    """Duplicate Check via Redis Set: set:verified:domains."""
    try:
        client = get_redis()
        if not client:
            return False
        return client.sismember("set:verified:domains", domain.lower().strip())
    except Exception:
        return False

def add_verified_domain_set(domain: str) -> None:
    """Add verified domain to Redis Set: set:verified:domains."""
    try:
        client = get_redis()
        if client:
            client.sadd("set:verified:domains", domain.lower().strip())
    except Exception:
        pass

