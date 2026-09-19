import os
import sys
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure Windows event loop policy
if sys.platform == 'win32':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from app.config import settings
from app.audit.tracer import tracer, Checkpoint
from app.persistence.database import init_db
from app.api import health, crawl, documents, schemas, agent, admin_safety, agent2, manual_search

tracer.initialize(log_level=settings.LOG_LEVEL)
logger = logging.getLogger("opendb")

@asynccontextmanager
async def lifespan(app: FastAPI):
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP01_RUN_INIT,
        event="APP_LIFESPAN_START",
        message="Initializing OpenDB FastAPI Application...",
        status="STARTING"
    )
    init_db()

    # Recovery Sync: Synchronize any pending records from SQLite fallback to PostgreSQL
    try:
        from app.persistence.sync_fallback import sync_pending_fallback_records
        res = sync_pending_fallback_records()
        if res.get("synced_count", 0) > 0:
            logger.info(f"[Lifespan] Recovered and synchronized {res['synced_count']} fallback records from SQLite into PostgreSQL.")
    except Exception as sync_err:
        logger.debug(f"[Lifespan] SQLite fallback recovery note: {sync_err}")

    # §10 — Auto-resume agent if it was RUNNING before container restart
    try:
        from app.agent.discovery_agent import discovery_agent
        discovery_agent.resume_if_was_running()
    except Exception as e:
        tracer.log_event(
            level="WARNING",
            checkpoint=Checkpoint.CP02_AGENT_INIT,
            event="AGENT_AUTO_RESUME_SKIPPED",
            message=f"Agent auto-resume skipped: {e}"
        )

    yield
    logger.info("Shutting down OpenDB application.")
    # Signal agent loop to stop cleanly
    try:
        from app.agent.discovery_agent import discovery_agent
        discovery_agent.is_running_loop = False
    except Exception:
        pass

app = FastAPI(
    title=settings.APP_NAME,
    description="OpenDB Universal Domain-Aware Web Crawling & Ingestion API",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for React Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(admin_safety.router, prefix="/api")
app.include_router(crawl.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(schemas.router, prefix="/api")
app.include_router(agent2.router, prefix="/api/agent2", tags=["Agent 2"])
app.include_router(manual_search.router, prefix="/api")

@app.get("/")
def root():
    return {
        "message": "Welcome to OpenDB Ingestion Pipeline API",
        "docs": "/docs",
        "health": "/api/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["*.db*", "*.log", "*.sqlite*", "data/*", "logs/*", "*.png"]
    )
