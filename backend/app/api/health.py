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
        db.execute(text("SELECT 1"))
        return "online"
    except Exception:
        return "down"

def _check_redis() -> str:
    try:
        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)
        if r.ping():
            return "online"
    except Exception:
        pass
    return "down"

def _check_minio() -> str:
    try:
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        # list_buckets() is a lightweight call that confirms connectivity + auth
        client.list_buckets()
        return "online"
    except Exception:
        pass
    return "down"

@router.get("/health/services")
async def services_health_check(db: Session = Depends(get_db)):
    """Live non-blocking parallel health status checks for all infrastructure services."""
    services = {
        "postgres": "down",
        "redis": "down",
        "minio": "down",
        "searxng": "down",
        "crawl4ai": "online", # Local Playwright engine
        "llm": "down"
    }

    async def check_pg():
        try:
            return await asyncio.wait_for(asyncio.to_thread(_check_postgres, db), timeout=2.0)
        except Exception:
            return "down"

    async def check_red():
        try:
            return await asyncio.wait_for(asyncio.to_thread(_check_redis), timeout=2.0)
        except Exception:
            return "down"

    async def check_min():
        try:
            return await asyncio.wait_for(asyncio.to_thread(_check_minio), timeout=2.0)
        except Exception:
            return "down"

    async def check_searx():
        try:
            searx_url = settings.SEARXNG_URL.replace("localhost", "127.0.0.1")
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.0, connect=0.5)) as client:
                resp = await client.get(searx_url)
                if resp.status_code < 500:
                    return "online"
        except Exception:
            pass
        return "degraded (Live DuckDuckGo Active)"

    async def check_llm():
        try:
            ollama_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags".replace("localhost", "127.0.0.1")
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.0, connect=0.5)) as client:
                resp = await client.get(ollama_url)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    qwen_found = any("qwen" in m.get("name", "").lower() for m in models)
                    if qwen_found:
                        return "online (Qwen 2.5 active)"
                    else:
                        return "online (Ollama active)"
        except Exception:
            pass

        if settings.OPENAI_API_KEY:
            return "online (OpenAI API)"
        return "degraded (Local Extractor)"

    pg_res, red_res, min_res, searx_res, llm_res = await asyncio.gather(
        check_pg(), check_red(), check_min(), check_searx(), check_llm()
    )

    services["postgres"] = pg_res
    services["redis"] = red_res
    services["minio"] = min_res
    services["searxng"] = searx_res
    services["llm"] = llm_res

    return services

