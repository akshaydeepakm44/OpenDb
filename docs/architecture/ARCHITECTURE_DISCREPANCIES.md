# Architecture Discrepancies & Code Realities Report

This document reports all discrepancies identified between legacy documentation/comments and the actual source code implementation in OpenDB.

---

## 1. Identified Discrepancies

### Discrepancy 1: Celery Task Dispatch vs Local Fallback Threads
- **Legacy Claim**: OpenDB relies strictly on a standalone Celery worker process for all asynchronous background task execution.
- **Actual Code Behavior**: `tasks.py` contains `_safe_dispatch()`. If Celery or Redis is unavailable or unpingable, tasks are dynamically executed inside background daemon threads (`threading.Thread(daemon=True)`).
- **Evidence**: [`backend/app/worker/tasks.py:L94-L116`](file:///e:/crawl/backend/app/worker/tasks.py#L94-L116).
- **Impact**: OpenDB can execute ingestion pipelines in standalone development environments without launching Celery container workers.

### Discrepancy 2: Primary Relational DB vs Master Lead Vault
- **Legacy Claim**: Ingested company data is stored exclusively in PostgreSQL relational tables (`universal_records`, `domain_records`).
- **Actual Code Behavior**: OpenDB maintains a dual-persistence model. Extracted leads are simultaneously written to an SQLite WAL database (`data/global_leads.db`) via `VaultService` (`global_leads`, `global_lead_people`, `global_lead_subpages`).
- **Evidence**: [`backend/app/persistence/vault_service.py`](file:///e:/crawl/backend/app/persistence/vault_service.py).
- **Impact**: The system provides ultra-fast local lead querying independently of PostgreSQL connection pools.

### Discrepancy 3: Agent 1 Boundary vs Agent 2 Deep Investigation
- **Legacy Claim**: Continuous discovery is fully handled by a single LLM orchestrator.
- **Actual Code Behavior**: OpenDB separates execution into:
  - **Agent 1** ([`backend/app/agent/discovery_agent.py`](file:///e:/crawl/backend/app/agent/discovery_agent.py)): Continuous 24/7 taxonomical discovery, candidate qualification, crawling, extraction, and parallel key people discovery.
  - **Agent 2** ([`backend/app/agent/agent2_orchestrator.py`](file:///e:/crawl/backend/app/agent/agent2_orchestrator.py)): Optional deep investigation tier using Haystack pipelines (`/api/agent2`) triggered on demand for deep verification.
- **Evidence**: [`backend/app/agent/agent2_orchestrator.py`](file:///e:/crawl/backend/app/agent/agent2_orchestrator.py) and [`backend/app/api/agent2.py`](file:///e:/crawl/backend/app/api/agent2.py).

---

## 2. Active vs Legacy Component Inventory

| Component | Status | Code Path | Notes / Evidence |
| :--- | :--- | :--- | :--- |
| **FastAPI Core App** | **ACTIVE** | `backend/app/main.py` | Primary API runtime |
| **Discovery Agent (Agent 1)** | **ACTIVE** | `backend/app/agent/discovery_agent.py` | 24/7 discovery loop |
| **Key People Discovery Agent**| **ACTIVE** | `backend/app/agent/key_people_discovery_agent.py` | Executed via Celery task |
| **Person-Company Verifier** | **ACTIVE** | `backend/app/extraction/person_verifier.py` | Multi-signal verifier |
| **Vault Service (SQLite)** | **ACTIVE** | `backend/app/persistence/vault_service.py` | SQLite WAL synchronization |
| **Agent 2 Orchestrator** | **EXPERIMENTAL** | `backend/app/agent/agent2_orchestrator.py` | Triggered via `/api/agent2` |
| **Haystack Pipelines** | **PARTIALLY USED**| `backend/app/haystack/pipelines.py` | Used for vector search endpoints |
