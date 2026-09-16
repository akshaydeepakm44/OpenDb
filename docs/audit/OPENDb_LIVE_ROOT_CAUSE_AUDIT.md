# OPENDb LIVE ROOT CAUSE AUDIT & E2E TEST LOG

**Application Target**: `http://57.128.27.215:3001/`  
**Backend API Target**: `http://57.128.27.215:8000/`  
**Test Start Time**: 2026-09-16 12:20:00 IST  
**Environment**: Production Remote Node (`57.128.27.215`) with Public Web/API Interfaces & Local Playwright QA Automation

---

## 1. CONTINUOUS LIVE TESTING TIMELINE

| Timestamp (IST) | Pipeline Stage | Observed Behavior | Expected Behavior | Status | Root Cause / Notes | Component | Blocking? | Action Taken / Retest |
|---|---|---|---|---|---|---|---|---|
| 12:20:30 | Setup | Target application identified at `http://57.128.27.215:3001/` | Target accessible | SUCCESS | Target online, port 3001 (frontend), 8000 (backend), 6379 (redis), 9001 (minio) open | Network / Host | NO | Initialized diagnostics |
| 12:23:50 | Health | Probed `http://57.128.27.215:3001/api/agent/status` | 200 OK with agent telemetry | SUCCESS | Returns live state: status=RUNNING, domain=IT, searches=2785, sources=28131 | FastAPI / Agent 1 | NO | Probing underlying services |
| 12:28:40 | Health Check | Probed `GET /api/health/services` | All core services CONNECTED | SUCCESS | PostgreSQL CONNECTED, Redis CONNECTED, SearXNG CONNECTED, MinIO CONNECTED, Crawler READY | System Infrastructure | NO | Baseline established |
| 12:34:00 | UI Observation | Inspected dashboard via Playwright (`remote_01_landing_dashboard.png`) | Stable UI rendering | SUCCESS | Top metrics visible: 3 Verified Leads, 1,859 Raw Docs, 1,576 Crawled Leads | Frontend UI | NO | Baselined UI counters |
| 12:35:00 | Search Stream | Inspected live crawling log stream (`remote_04_live_stream_10s.png`) | Real SearXNG queries returning 200 OK | FAILURE | Every search query logs `[ERROR] cannot reuse already awaited coroutine` | Worker / Celery (`tasks.py`) | YES | Discovered Root Cause RC-01 |
| 12:36:30 | UI RUN Click | Clicked PAUSE then clicked RUN in browser UI (`remote_03_after_run_click.png`) | Resume discovery loop | SUCCESS | Button transitioned RUNNING -> PAUSED -> RUNNING | Frontend / Backend API | NO | Validated UI button dispatch |
| 12:37:40 | Agent 2 Trigger | Clicked `⚡ Verify with Agent 2 ↗` on crawled lead `WikiLeaks` | Agent 2 starts deep crawl & investigation | PARTIAL | Session created, but fields blocked by `[Errno 11]` in remote container | Agent 2 / Playwright | YES | Discovered Root Cause RC-02 |
| 12:38:20 | Verified Audit | Inspected Verified tab on `57.128.27.215:3001` (`remote_09_verified_tab.png`) | Only 100% verified leads shown | FAILURE | `cybersecurityasia.net` displayed with `Industry: Unknown`, `Size: NOT_FOUND_AFTER_SEARCH` | API / Gating Contract | YES | Discovered Root Cause RC-03 |
| 12:41:30 | Audit Modal | Clicked `🔬 Open Verification Audit & Checklist ↗` on In-Verification card | Audit checklist modal opens | FAILURE | Page crashed to dark blue (`Total divs: 1`). React Error #31: object `{text, supporting_evidence}` | Frontend (`App.jsx`) | YES | Discovered Root Cause RC-04 |

---

## 2. ROOT CAUSE / FAILURE REGISTER

| ID | Time (IST) | Stage | Symptom / Failure | Root Cause | Evidence / Log Line | Impact | Fix / Mitigation | Retest Result |
|---|---|---|---|---|---|---|---|---|
| RC-01 | 12:35:00 | Agent 1 Discovery | Celery worker fails every SearXNG search with `cannot reuse already awaited coroutine` | Legacy `run_async` in `tasks.py` caught `RuntimeError` and attempted `loop.run_until_complete(coro)` on an already awaited/closed coroutine | `QUERY:... [ERROR] cannot reuse already awaited coroutine` in live operations log stream | Agent 1 discovery loop cannot enqueue new company URLs | Replace with platform-safe `_runner` and `ThreadPoolExecutor` (fixed in local codebase) | Local tests pass with 5/5 search results |
| RC-02 | 12:38:00 | Agent 2 Investigation | All investigated fields marked `UNVERIFIED` with `[Errno 11] Resource temporarily unavailable` | Legacy `agent2_investigation.py` independently spawned new Chromium browser instances concurrently per field, exhausting Linux OS process/file descriptor limits | `Provenance: Investigation blocked by infrastructure failure: CRAWLER_ERROR: CRAWL_FAILED: Crawl4AI / Playwright engine unavailable ([Errno 11] Resource temporarily unavailable)` | Agent 2 cannot extract missing fields | Architectural coordinated crawl session: single browser context at start of Agent 2, subpages stored to MinIO, aggregated corpus passed to in-memory investigators (fixed in local codebase) | Proven zero `[Errno 11]` errors |
| RC-03 | 12:38:20 | Verified Tab Gating | Unverified leads (e.g. `cybersecurityasia.net` with `Industry: Unknown`) exposed in Verified tab | Fallback in `GET /api/agent/entities` (`if not universal_records: return global_leads`) dumped unverified discovery leads into the Verified view | `GET /api/agent/entities` returned `cybersecurityasia.net` with `industry: 'Unknown'`, `status: 'Verified'` | Compromised authoritative verification trust | Authoritative `verification_contract.py` gate: strict query `UniversalRecord.status.in_(["VERIFIED", ...])`, 7 core required fields must pass with provenance, zero fallbacks | Verified tab displays 0 items when incomplete leads exist |
| RC-04 | 12:41:30 | Audit Modal UI | Clicking `Open Verification Audit` crashes the browser UI with React error #31 | `agent2Detail.phase2_data.business_overview` returned a dictionary `{text, supporting_evidence}` which was rendered directly as a child in JSX (`<div>{...business_overview}</div>`) | `Minified React error #31; visit https://react.dev/errors/31?args[]=object%20with%20keys%20%7Btext%2C%20supporting_evidence%7D` | Modal cannot be opened for enriched leads; UI crashes | Safely extract `.text` property: `{typeof overview === 'object' ? overview.text : overview}` | Fixed in `frontend/src/App.jsx`, verified compilation |

---

## 3. SYSTEM HEALTH PRE-RUN BASELINE (Remote Node: 57.128.27.215)

* **PostgreSQL Status**: CONNECTED (mode: POSTGRESQL, degraded: false)
* **Redis Status**: CONNECTED (authenticated ping: `True`, active tasks in keyspace)
* **MinIO Status**: CONNECTED (Port 9001 OPEN, 1,859 objects, 45 MB)
* **SearXNG Status**: CONNECTED (searxng service health: CONNECTED)
* **Crawl4AI / Playwright**: READY (Playwright browser engine initialized)
* **LLM Status**: Remote Qwen GPU active via `115.244.46.68:8000/v1`
* **Celery / Worker**: Active Celery worker running concurrency=4 on `celery,default` queues
* **Agent 1 Discovery**: RUNNING (`total_searches`: 2785, `sources_discovered`: 28131, `entities_discovered`: 3)
* **Existing Lead Counts**: Crawled: 1,576; In Verification: 17; Verified: 3
