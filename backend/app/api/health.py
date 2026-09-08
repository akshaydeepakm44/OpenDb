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
        res = db.execute(text("SELECT 1")).scalar()
        if res == 1:
            return "online"
        return "degraded"
    except Exception as e:
        return f"down ({type(e).__name__})"

def _check_redis() -> str:
    try:
        from app.cache.redis_client import get_redis
        r = get_redis()
        if r is not None and r.ping():
            return "online"
    except Exception:
        pass
    return "down"

def _check_minio() -> str:
    try:
        from app.storage.file_storage import file_storage
        if getattr(file_storage, "minio_client", None) is not None:
            # Real bucket access check
            buckets = file_storage.minio_client.list_buckets()
            return "online"
        return "degraded (local disk)"
    except Exception as e:
        return f"degraded (local disk: {type(e).__name__})"

def _check_searxng() -> str:
    try:
        url = f"{settings.SEARXNG_URL.rstrip('/')}/search"
        with httpx.Client(timeout=0.2) as client:
            resp = client.get(url, params={"q": "test", "format": "json"})
            if resp.status_code == 200 and "results" in resp.json():
                return "online"
    except Exception:
        pass
    return "degraded (Bing search fallback)"

def _check_celery() -> str:
    try:
        from app.worker.celery_app import celery_app
        inspector = celery_app.control.inspect(timeout=0.15)
        pings = inspector.ping()
        if pings:
            return f"online ({len(pings)} worker{'s' if len(pings)>1 else ''})"
    except Exception:
        pass
    return "degraded (thread dispatch mode)"

def _check_llm() -> str:
    try:
        base_url = getattr(settings, "OPENAI_BASE_URL", "http://115.244.46.68:8000/v1")
        with httpx.Client(timeout=0.2) as client:
            resp = client.get(f"{base_url.rstrip('/')}/models")
            if resp.status_code in (200, 401):
                return "online (Qwen GPU API)"
    except Exception:
        pass
    return "degraded (local extractor)"

def _check_playwright() -> str:
    try:
        import importlib.util
        if importlib.util.find_spec("playwright") is not None:
            return "online"
        return "degraded (httpx parser)"
    except Exception:
        return "degraded (httpx parser)"

def _check_sqlite_staging() -> str:
    try:
        from app.persistence.database import staging_engine
        with staging_engine.connect() as conn:
            res = conn.execute(text("SELECT 1")).scalar()
            if res == 1:
                return "online"
    except Exception as e:
        return f"down ({type(e).__name__})"
    return "down"

def _check_postgres_verified() -> str:
    try:
        from app.persistence.database import verified_engine, IS_POSTGRES_AVAILABLE
        if not IS_POSTGRES_AVAILABLE or verified_engine is None:
            return "offline (staging in SQLite)"
        with verified_engine.connect() as conn:
            res = conn.execute(text("SELECT 1")).scalar()
            if res == 1:
                return "online"
    except Exception as e:
        return f"offline ({type(e).__name__})"
    return "offline"

@router.get("/health/services")
async def services_health_check(db: Session = Depends(get_db)):
    """Truthful, ultra-fast runtime service health status check."""
    import time
    now_ts = time.time()
    if hasattr(services_health_check, "_cache") and (now_ts - getattr(services_health_check, "_cache_ts", 0)) < 3:
        return services_health_check._cache

    # Execute all health checks in parallel threads concurrently
    sqlite_task = asyncio.to_thread(_check_sqlite_staging)
    postgres_task = asyncio.to_thread(_check_postgres_verified)
    redis_task = asyncio.to_thread(_check_redis)
    minio_task = asyncio.to_thread(_check_minio)
    searxng_task = asyncio.to_thread(_check_searxng)
    celery_task = asyncio.to_thread(_check_celery)
    pw_task = asyncio.to_thread(_check_playwright)
    llm_task = asyncio.to_thread(_check_llm)

    sqlite_res, pg_res, redis_res, minio_res, searxng_res, celery_res, pw_res, llm_res = await asyncio.gather(
        sqlite_task, postgres_task, redis_task, minio_task, searxng_task, celery_task, pw_task, llm_task
    )

    res = {
        "sqlite_operational_db": sqlite_res,
        "postgres_verified_db": pg_res,
        "redis": redis_res,
        "minio": minio_res,
        "searxng": searxng_res,
        "celery": celery_res,
        "playwright": pw_res,
        "crawl4ai": pw_res,
        "llm": llm_res
    }

    services_health_check._cache = res
    services_health_check._cache_ts = now_ts
    return res

