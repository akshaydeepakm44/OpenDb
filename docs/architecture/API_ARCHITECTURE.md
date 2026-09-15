# API Architecture — REST Endpoints Specification

OpenDB provides a RESTful API powered by **FastAPI**, serving web dashboard requests, agent telemetry streams, manual crawl job submissions, and database inspection.

---

## 1. REST API Route Directory

| Endpoint | Method | Purpose | Implementation File | Key Calls / Dependencies |
| :--- | :--- | :--- | :--- | :--- |
| `/api/crawl` | `POST` | Trigger manual company crawl job | [`backend/app/api/crawl.py`](file:///e:/crawl/backend/app/api/crawl.py) | `execute_crawl_pipeline` |
| `/api/crawl/{job_id}` | `GET` | Get crawl job status & metrics | [`backend/app/api/crawl.py`](file:///e:/crawl/backend/app/api/crawl.py) | `repo.get_crawl_job()` |
| `/api/agent/status` | `GET / POST` | Query or update agent status (`RUNNING`/`PAUSED`)| [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `AutonomousDiscoveryAgent.set_status()` |
| `/api/agent/operations` | `GET` | Return system metrics, batch stats & logs | [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `repo.get_agent_operations()` |
| `/api/agent/entities` | `GET` | Search/filter discovered company records | [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `repo.get_filtered_entities()` |
| `/api/agent/entities/{id}`| `GET` | Get detailed company dossier | [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `repo.get_universal_record_detail()` |
| `/api/agent/entities/{id}/people`| `GET` | Get key decision makers for company | [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `repo.get_key_people_by_company()` |
| `/api/agent/documents` | `GET` | List crawled web page documents | [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `repo.get_documents_paginated()` |
| `/api/agent/reset` | `POST` | Purge stored database records (dev tool) | [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py) | `repo.reset_all_data()` |
| `/api/health/services` | `GET` | Check PostgreSQL, Redis, MinIO health | [`backend/app/api/health.py`](file:///e:/crawl/backend/app/api/health.py) | Service ping checks |
| `/api/agent2/investigate` | `POST` | Trigger deep Agent 2 investigation | [`backend/app/api/agent2.py`](file:///e:/crawl/backend/app/api/agent2.py) | `Agent2Orchestrator.investigate()` |

---

## 2. Request & Response Specifications

### `POST /api/crawl`
**Request Payload**:
```json
{
  "url": "https://datai2i.com",
  "query": "SaaS development company",
  "domain": "Technology"
}
```

**Response**:
```json
{
  "job_id": "c6a2f4e0-1123-4b99-a833-289c89e1a123",
  "status": "pending",
  "message": "Crawl job pipeline initiated in background."
}
```

---

### `GET /api/agent/status`
**Response**:
```json
{
  "status": "RUNNING",
  "current_domain": "Information Technology",
  "current_keyword": "SaaS development companies official website",
  "last_run_at": "2026-09-15T11:00:00Z"
}
```
---

### `GET /api/agent/entities/{id}/people`
**Response**:
```json
[
  {
    "id": "kp-98123-4b",
    "person_name": "Example Executive",
    "role": "Chief Executive Officer",
    "verification_status": "VERIFIED",
    "confidence_score": 0.95,
    "source_url": "https://datai2i.com/team",
    "source_type": "OFFICIAL_WEBSITE"
  }
]
```
