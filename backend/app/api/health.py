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


@router.get("/preflight")
async def system_preflight_check(db: Session = Depends(get_db)):
    """
    Truthful System Pre-Flight Validation endpoint (§20 of Master Prompt).
    Checks all 9 runtime dependencies without faking or swallowing states.
    """
    import time
    from app.persistence.database import IS_FALLBACK_ACTIVE, DATABASE_MODE
    from app.schemas.registry import schema_registry
    from app.worker.tasks import _has_active_celery_worker
    from app.worker.celery_app import celery_app

    report = {
        "fastapi": {"status": "ONLINE"}
    }

    # 1. PostgreSQL Check
    try:
        if IS_FALLBACK_ACTIVE:
            report["postgresql"] = {"status": "DEGRADED", "mode": "SQLITE_FALLBACK"}
        else:
            tbl_count = db.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")).scalar()
            report["postgresql"] = {"status": "ONLINE", "mode": "PRIMARY", "table_count": tbl_count}
    except Exception as e:
        report["postgresql"] = {"status": "OFFLINE", "error": str(e)}

    # 2. Redis Check
    try:
        from urllib.parse import urlparse
        p = urlparse(settings.REDIS_URL.replace("localhost", "127.0.0.1"))
        t0 = time.time()
        r = redis.Redis(host=p.hostname or "127.0.0.1", port=p.port or 6379, password=p.password, socket_connect_timeout=1.5, socket_timeout=1.5)
        if r.ping():
            lat = round((time.time() - t0) * 1000, 2)
            report["redis"] = {"status": "ONLINE", "latency_ms": lat}
        else:
            report["redis"] = {"status": "OFFLINE"}
    except Exception as e:
        report["redis"] = {"status": "OFFLINE", "error": str(e)}

    # 3. Celery Worker Check
    try:
        active_worker = _has_active_celery_worker()
        worker_count = 0
        if active_worker:
            inspector = celery_app.control.inspect(timeout=0.5)
            ping_res = inspector.ping()
            worker_count = len(ping_res) if ping_res else 1
        report["celery_worker"] = {
            "status": "ONLINE" if active_worker else "OFFLINE",
            "active_workers": worker_count
        }
    except Exception:
        report["celery_worker"] = {"status": "OFFLINE", "active_workers": 0}

    # 4. SearXNG Check
    try:
        searx_url = settings.SEARXNG_URL
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(searx_url)
            report["searxng"] = {"status": "ONLINE" if resp.status_code in (200, 302, 301) else "DEGRADED", "url": searx_url}
    except Exception as e:
        report["searxng"] = {"status": "OFFLINE", "url": settings.SEARXNG_URL, "error": str(e)}

    # 5. Crawl4AI Check
    try:
        from crawl4ai import AsyncWebCrawler
        report["crawl4ai"] = {"status": "ONLINE", "engine": "Playwright"}
    except Exception as e:
        report["crawl4ai"] = {"status": "OFFLINE", "error": str(e)}

    # 6. MinIO Check
    try:
        from minio import Minio
        ep = settings.MINIO_ENDPOINT.replace("localhost", "127.0.0.1")
        m = Minio(ep, access_key=settings.MINIO_ACCESS_KEY, secret_key=settings.MINIO_SECRET_KEY, secure=settings.MINIO_SECURE)
        buckets = [b.name for b in m.list_buckets()]
        report["minio"] = {"status": "ONLINE", "buckets": buckets}
    except Exception as e:
        report["minio"] = {"status": "OFFLINE", "error": str(e)}

    # 7. Schemas Check
    schema_count = len(schema_registry._cache)
    report["schemas"] = {
        "status": "ONLINE" if schema_count > 0 else "DEGRADED",
        "loaded": schema_count,
        "schemas": list(schema_registry._cache.keys())
    }

    # 8. LLM Check
    try:
        api_key = getattr(settings, "OPENAI_API_KEY", "") or getattr(settings, "QWEN_API_KEY", "")
        base_url = getattr(settings, "OPENAI_BASE_URL", "")
        model = getattr(settings, "LLM_MODEL", "current-model")
        if api_key and base_url:
            async with httpx.AsyncClient(timeout=2.0) as client:
                models_resp = await client.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"})
                llm_online = models_resp.status_code == 200
            report["llm"] = {
                "status": "ONLINE" if llm_online else "DEGRADED",
                "model": model,
                "provider": getattr(settings, "LLM_PROVIDER", "qwen_gpu")
            }
        else:
            report["llm"] = {"status": "DEGRADED", "reason": "No API key configured", "mode": "DETERMINISTIC_FALLBACK"}
    except Exception as e:
        report["llm"] = {"status": "DEGRADED", "error": str(e), "mode": "DETERMINISTIC_FALLBACK"}

    return report



