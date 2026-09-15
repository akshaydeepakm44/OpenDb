import os
import asyncio
import httpx
import redis
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
        from urllib.parse import urlparse
        p = urlparse(settings.REDIS_URL.replace("localhost", "127.0.0.1"))
        r = redis.Redis(
            host=p.hostname or "127.0.0.1",
            port=p.port or 6379,
            password=p.password,
            socket_connect_timeout=1.0,
            socket_timeout=1.0
        )
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
    """Sanitized non-blocking infrastructure services health check."""
    from app.persistence.database import get_database_status
    db_status_info = get_database_status()

    redis_state = _check_redis()
    redis_status = "CONNECTED" if redis_state == "online" else "UNAVAILABLE"

    minio_state = _check_minio()
    minio_status = "CONNECTED" if minio_state == "online" else "UNAVAILABLE"

    searxng_online = _quick_port_check(settings.SEARXNG_URL, 8080) or _quick_port_check(settings.SEARXNG_URL, 9090)
    searxng_status = "CONNECTED" if searxng_online else "UNAVAILABLE"

    return {
        "database": {
            "mode": db_status_info["mode"],
            "status": db_status_info["status"],
            "degraded": db_status_info["degraded"]
        },
        "redis": {
            "status": redis_status
        },
        "searxng": {
            "status": searxng_status
        },
        "minio": {
            "status": minio_status
        },
        "crawler": {
            "status": "READY",
            "browser_engine": "Playwright",
            "crawl4ai_status": "READY"
        }
    }


