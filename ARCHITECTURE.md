# OpenDB System Architecture Specification (Direct Qwen GPU Execution)

OpenDB is an end-to-end autonomous discovery engine that converts unstructured web pages into structured, verified business entity records.

## Modular Component Layers

1. **Frontend Presentation Layer (`frontend/`)**:
   - Built with React and Vite on port `5173`.
   - Uses Vite HTTP proxying (`vite.config.js`) to delegate `/api/*` requests to the FastAPI backend on port `8000`/`8005`.
   - Visualizes live stat cards, search strategy controls, Crawled Leads grid, Verified Entities catalog, interactive dossiers, and real-time activity logs.

2. **Backend API & Health Layer (`backend/app/api/`)**:
   - FastAPI framework on port `8000` / `8005`.
   - Exposes REST endpoints (`/api/agent/*`, `/api/crawl/*`, `/api/documents/*`, `/api/dlq/*`, `/api/health/services`, `/api/admin/safety/*`).
   - Micro-socket health probes (<5ms latency) monitor infrastructure connectivity without blocking threads.

3. **Autonomous Agent & Strategy Layer (`backend/app/agent/`, `backend/app/haystack/`)**:
   - `discovery_agent.py`: Continuous 24/7 Haystack 2.x reasoning loop directly targeting Qwen GPU endpoint (`http://115.244.46.68:8000/v1`) with model `current-model`.
   - `keyword_expander.py`: Dynamic seed query and target domain expansion engine.
   - `haystack/pipelines.py`: Native Haystack 2.x pipeline generator configured directly for Qwen GPU.

4. **Task Dispatcher & Worker Layer (`backend/app/worker/`)**:
   - `tasks.py`: Implements `_safe_dispatch()` to queue tasks to Celery/Redis if worker is active, with non-blocking background daemon thread fallback when offline.
   - **DB Lock Isolation**: Executes slow network operations (Playwright scraping, LLM calls) with closed database sessions to keep database transactions micro-short (<2ms).

5. **Crawler & Data Acquisition & Verification Layer (`backend/app/crawler/`)**:
   - `searxng_service.py`: 
     - **Initial Search**: Automatically expands search queries to target company website, public LinkedIn profile/company URLs, contact emails, and headquarters location (`search_initial_with_metadata_targets`).
     - **SearXNG Verification Pipeline**: Executes secondary targeted verification searches (`verify_and_enrich_with_searxng`) to cross-check raw crawled data before marking records as `Verified`.
   - `crawler_service.py`: Crawl4AI + Playwright engine rendering web pages and fetching subpages (`/about`, `/contact`, `/team`, `/leadership`).
   - `listing_detector.py` & `resource_discovery.py`: Aggregator listing page detection and automated deep subpage traversal.
   - `realtime_enricher.py`: Extracts verified emails, headquarters, and key decision makers without guesses.

6. **Safety, Guardrails & Local Moderation (`backend/app/safety/`, `backend/app/classification/`)**:
   - `quality_filter.py`: Rejects junk URLs, thin content (<30 words), and low-scoring domains.
   - `guardrails.py`: Enforces domain blocklists, structural data integrity, and policy compliance.
   - `moderation.py`: Local open-source code-level heuristic safety scanner (external cloud moderation APIs removed).
   - `reputation.py`: Domain spam scoring and rate-limit tracking.

7. **Direct Qwen GPU Extraction & Data Normalization Engine (`backend/app/extraction/`, `backend/app/normalization/`)**:
   - `llm_extractor.py`: Direct open-source LLM inference via **Qwen GPU Server Endpoint (`http://115.244.46.68:8000/v1`)** with model `current-model`. All references to `qwen2.5:7b` or Ollama have been removed.
   - `key_people_extractor.py`: Dedicated extraction for founders, C-level executives, and contact emails.
   - `document_extractor.py`: Unstructured document (PDF/DOCX/text) parsing.
   - `normalizer.py`: Standardizes entity names, phone numbers, addresses, social profiles, and domain schemas.

8. **Persistence, Vault & SQLite Fallback (`backend/app/persistence/`, `backend/app/storage/`)**:
   - `database.py`: PostgreSQL engine with automated **SQLite WAL fallback (`opendb_fallback.db`)** preserved as the sole persistence fallback.
   - `vault_service.py`: Entity Vault storing canonical dossiers, payload revision hashes, source verification audit logs, and Dead Letter Queue (DLQ) state handling.
   - `file_storage.py`: Content-Addressable Storage (CAS) archiving SHA-256 raw HTML pages, Markdown text, and JSON extraction payloads.
