# OpenDB System Architecture Specification

OpenDB is an end-to-end autonomous discovery engine that converts unstructured web pages into structured, verified business entity records.

## Modular Component Layers

1. **Frontend Presentation Layer (`frontend/`)**:
   - Built with React and Vite on port `5173`.
   - Uses Vite HTTP proxying (`vite.config.js`) to delegate `/api/*` requests to the FastAPI backend on port `8000`.
   - Visualizes live stat cards, search strategy controls, Crawled Leads grid, Verified Entities catalog, and real-time crawl activity logs.

2. **Backend API Layer (`backend/app/api/`)**:
   - FastAPI framework on port `8000`.
   - Exposes REST endpoints (`/api/agent/status`, `/api/agent/operations`, `/api/agent/documents`, `/api/agent/entities`, `/api/health/services`).
   - `health.py` uses ultra-fast micro socket probes (<5ms latency) to check infrastructure health.

3. **Autonomous Agent Layer (`backend/app/agent/`)**:
   - `discovery_agent.py`: Continuous 24/7 Haystack 2.x reasoning loop. Evaluates system metrics, invokes LiteLLM / keyword expanders, dispatches search strategies, and triggers continuous verification sweeps.

4. **Task Dispatcher & Background Worker Layer (`backend/app/worker/`)**:
   - `tasks.py`: Implements `_safe_dispatch()` to queue tasks to Celery/Redis if an active Celery worker process is present, automatically falling back to non-blocking background daemon threads if offline.
   - **DB Lock Isolation**: Executes slow network operations (Playwright scraping, LLM calls) with closed database sessions to keep database transactions micro-short (<2ms).

5. **Crawler & Search Layer (`backend/app/crawler/`)**:
   - `searxng_service.py`: Issues live web search queries to SearXNG or DuckDuckGo.
   - `crawler_service.py`: AsyncWebCrawler (Crawl4AI) + Playwright engine rendering web pages, executing JavaScript, and fetching subpages (`/about`, `/contact`, `/team`, etc.).

6. **Extraction & Quality Filtering Layer (`backend/app/extraction/`, `backend/app/classification/`)**:
   - `quality_filter.py`: Multi-stage filter rejecting junk URLs, thin content, adult/illegal domains, and duplicate entities.
   - `css_extractor.py`: Structural HTML parsing (OpenGraph, Meta tags, JSON-LD).
   - `llm_extractor.py`: Hybrid LiteLLM / Qwen 2.5 and heuristic rule engine mapping page content to JSON domain schemas.

7. **Persistence & Storage Layer (`backend/app/persistence/`, `backend/app/storage/`)**:
   - `database.py`: PostgreSQL engine with automated SQLite WAL fallback (`opendb_fallback.db`).
   - `file_storage.py`: Content-addressable storage storing SHA-256 raw HTML pages, Markdown text, and JSON extraction payloads.

