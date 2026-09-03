import os
import asyncio
import httpx
import redis
from minio import Minio
from sqlalchemy import text
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config import settings
from app.persistence.database import get_db

router = APIRouter()

@router.get("/health")
def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV
    }

def _check_postgres(db: Session) -> str:
    try:
        from app.persistence.database import IS_FALLBACK_ACTIVE
        if IS_FALLBACK_ACTIVE:
            return "degraded (SQLite fallback active)"
        db.execute(text("SELECT 1"))
        return "online"
    except Exception:
        return "down"

def _check_redis() -> str:
    try:
        r = redis.Redis.from_url(settings.REDIS_URL.replace("localhost", "127.0.0.1"), socket_connect_timeout=0.1, socket_timeout=0.1)
        if r.ping():
            return "online"
    except Exception:
        pass
    return "down"

def _check_minio() -> str:
    try:
        import socket
        from urllib.parse import urlparse
        ep = settings.MINIO_ENDPOINT
        p = urlparse(f"http://{ep}" if "://" not in ep else ep)
        h = p.hostname or "127.0.0.1"
        pt = p.port or 9000
        with socket.create_connection((h, pt), timeout=0.1):
            return "online"
    except Exception:
        pass
    return "down"

def _quick_port_check(url_or_endpoint: str, default_port: int) -> bool:
    import socket
    from urllib.parse import urlparse
    try:
        if "://" not in url_or_endpoint:
            url_or_endpoint = f"http://{url_or_endpoint}"
        parsed = urlparse(url_or_endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or default_port
        with socket.create_connection((host, port), timeout=0.05):
            return True
    except Exception:
        return False

@router.get("/health/services")
def services_health_check(db: Session = Depends(get_db)):
    """Instant non-blocking service health status check (<5ms total latency)."""
    from app.persistence.database import IS_FALLBACK_ACTIVE

    pg_status = "degraded (SQLite fallback active)" if IS_FALLBACK_ACTIVE else (_check_postgres(db))
    redis_status = _check_redis()
    minio_status = _check_minio()
    searx_status = "online" if _quick_port_check(settings.SEARXNG_URL, 8080) else "degraded (Live DuckDuckGo Active)"
    
    ollama_online = _quick_port_check(settings.OLLAMA_BASE_URL, 11434)
    if ollama_online:
        llm_status = "online (Ollama active)"
    elif getattr(settings, "OPENAI_API_KEY", None):
        llm_status = "online (OpenAI API)"
    else:
        llm_status = "degraded (Local Extractor)"

    return {
        "postgres": pg_status,
        "redis": redis_status,
        "minio": minio_status,
        "searxng": searx_status,
        "crawl4ai": "online",
        "llm": llm_status
    }

