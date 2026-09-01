"""Shared Redis client accessor for OpenDB.

A single lazy client is reused across the process. The URL comes from
``settings.REDIS_URL`` so Docker (with password) and local dev (no password)
both work without code changes.
"""
import logging
import threading
import redis
from app.config import settings

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def get_redis() -> redis.Redis:
    """Return a process-wide Redis client, creating it on first use."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = redis.Redis.from_url(
                    settings.REDIS_URL,
                    socket_connect_timeout=1.0,
                    socket_timeout=5.0,
                    decode_responses=True,
                )
    return _client
