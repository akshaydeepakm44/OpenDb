# OpenDB REST API Specification

The OpenDB FastAPI backend serves standard JSON REST endpoints on port `8000`.

## Endpoints Summary

### Autonomous Agent Control & Status
- `GET /api/agent/status`: Returns current agent execution state (`RUNNING` / `PAUSED`), current domain/subdomain/keyword target, total search statistics, active batch progress, and top verified entities.
- `POST /api/agent/start`: Triggers or resumes the autonomous 24/7 discovery loop.
- `POST /api/agent/stop`: Gracefully pauses the autonomous discovery loop.
- `GET /api/agent/operations`: Returns high-level metrics for dashboard stat cards (`persisted_companies`, `crawled_documents`, `sources_discovered`, `total_searches`).
- `GET /api/agent/documents`: Returns paginated list (`page`, `limit`, `query`) of raw/ingested document cards for the Crawled Leads view.
- `GET /api/agent/documents/{document_id}`: Detailed inspection modal data for a single document.
- `GET /api/agent/entities`: Returns paginated list of verified company records (`UniversalRecord`).
- `GET /api/agent/entities/{entity_id}`: Detailed entity dossier data.

### System Health
- `GET /api/health`: Basic API liveness check (`{"status": "ok"}`).
- `GET /api/health/services`: Instant micro socket probe (<5ms latency) returning real-time operational status for `postgres`, `redis`, `minio`, `searxng`, `crawl4ai`, and `llm`.

### Asynchronous Crawl Job Pipeline
- `POST /api/crawl`: Trigger manual single-site asynchronous crawl job.
- `GET /api/crawl/{job_id}`: Retrieve job execution status and 10-stage pipeline progress metrics.
- `GET /api/crawl/{job_id}/pages`: List discovered and crawled pages for a specific job.
- `GET /api/crawl/{job_id}/results`: Retrieve structured extraction records and evidence for a specific job.

