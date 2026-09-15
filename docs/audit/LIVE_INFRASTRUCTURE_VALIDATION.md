# OpenDB — Live Infrastructure Validation Document

**Date:** 2026-09-15  
**Environment:** Live Host Environment (Windows, Native PostgreSQL 18)

## Live Infrastructure Audit Summary

The following table records the empirical runtime state of all primary OpenDB infrastructure services on the host machine.

| Service | Expected | Actual | Evidence | Status |
|---|---|---|---|---|
| **PostgreSQL** | Running on port 5432/5433, reachable, `opendb` DB active | PostgreSQL 18 running on port 5432. DB `opendb` and role `admin` active. `.env` port 5433 was closed, causing default config fallback. | `psycopg2.connect(host='127.0.0.1', port=5432, dbname='opendb', user='admin')` connected successfully. | **ONLINE** (Port 5432) |
| **Redis** | Running on port 6379, responding to PING | Closed / Unreachable on port 6379. Service process not running on host. | `socket.connect(('127.0.0.1', 6379))` failed with `ConnectionRefusedError`. | **UNAVAILABLE** |
| **MinIO** | Running on port 9000/9002, Object Storage API active | Closed / Unreachable on ports 9000/9002. Service process not running on host. | `socket.connect(('127.0.0.1', 9000))` failed with `ConnectionRefusedError`. | **UNAVAILABLE** |
| **SearXNG** | Running on port 8080/9090, returning JSON search results | Port 8080 occupied by EnterpriseDB web server; port 9090 closed. SearXNG not active. | GET `http://127.0.0.1:8080/search` returned HTML 404 (EnterpriseDB). | **UNAVAILABLE** |
| **Crawl4AI** | Crawl4AI web crawler engine functional | Crawl4AI 0.9.3 installed and operational with Chromium backend. | Executed `AsyncWebCrawler().arun('https://example.com')` successfully in 0.97s. | **READY** |
| **Playwright** | Playwright Chromium browser rendering engine functional | Installed & verified. Chromium Headless Shell v1208 active. | `async_playwright().chromium.launch()` opened `https://example.com` and extracted title. | **READY** |
| **FastAPI** | Uvicorn/FastAPI backend API listening on port 8000 | Port 8000 OPEN and responding. | Socket open on port 8000. | **ONLINE** |
| **React/Vite** | Vite dev server active on port 5173 | Closed / Unreachable on port 5173. Dev server not running. | Socket connection to `127.0.0.1:5173` failed. | **UNAVAILABLE** |
| **Celery worker** | Worker active listening on Redis broker queue | Cannot connect to Redis broker (Redis UNAVAILABLE). | Task dispatch to Redis fails with connection error. | **UNAVAILABLE** |

---

## Configuration Discrepancies & Findings

1. **PostgreSQL Port Mismatch**:
   - `backend/app/config.py` default setting: `POSTGRES_PORT = 5433`.
   - Real PostgreSQL 18 service: Port **5432**.
   - **Diagnosis**: When `.env` or `DATABASE_URL` is updated to specify port `5432`, OpenDB connects directly to PostgreSQL 18 with `database.mode = POSTGRESQL` and `degraded = False`.

2. **SearXNG & Redis Port Conflicts**:
   - Port 8080 is bound by EnterpriseDB HTTP web server instead of SearXNG.
   - Redis and MinIO are not installed as native Windows background services on this host.

3. **Crawl4AI & Playwright Availability**:
   - Playwright Chromium binaries were installed during initial diagnostic execution. Both Crawl4AI and Playwright run natively without external container dependencies.
