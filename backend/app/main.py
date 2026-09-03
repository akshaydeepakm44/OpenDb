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
from app.persistence.database import init_db
from app.api import health, crawl, documents, schemas, agent

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing OpenDB FastAPI Application...")
    init_db()

    # §10 — Auto-resume agent if it was RUNNING before container restart
    try:
        from app.agent.discovery_agent import discovery_agent
        discovery_agent.resume_if_was_running()
    except Exception as e:
        logger.warning(f"Agent auto-resume skipped: {e}")

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
app.include_router(crawl.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(schemas.router, prefix="/api")

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
        port=8005,
        reload=True,
        reload_excludes=["*.db*", "*.log", "*.sqlite*", "data/*", "logs/*", "*.png"]
    )
