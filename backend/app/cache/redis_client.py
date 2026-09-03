"""Shared Redis client accessor for OpenDB.

A single lazy client is reused across the process. The URL comes from
``settings.REDIS_URL`` so Docker (with password) and local dev (no password)
both work without code changes.
"""
from typing import Optional
import logging
import threading
import redis
from app.config import settings

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()
_redis_available = None
_last_check = 0

def get_redis() -> Optional[redis.Redis]:
    """Return a process-wide Redis client instantly if online, reusing active connection."""
    global _client, _redis_available, _last_check
    import time, socket
    
    if _client is not None and _redis_available is True:
        return _client

    now = time.time()
    if _redis_available is False and (now - _last_check) < 10:
        return None
        
    with _lock:
        if _client is not None and _redis_available is True:
            return _client
        try:
            # Fast 0.02s socket test on initial connect
            with socket.create_connection(("127.0.0.1", 6379), timeout=0.02):
                pass
            from urllib.parse import urlparse
            p = urlparse(settings.REDIS_URL.replace("localhost", "127.0.0.1"))
            _client = redis.Redis(
                host=p.hostname or "127.0.0.1",
                port=p.port or 6379,
                password=p.password,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=True,
            )
            _redis_available = True
            return _client
        except Exception:
            _redis_available = False
            _last_check = now
            return None
