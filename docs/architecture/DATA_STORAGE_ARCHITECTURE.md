# Data Storage Architecture — Multi-Tier Storage System

OpenDB uses a multi-tier hybrid persistence architecture engineered for scalable web crawling, provenance tracking, fast local lead querying, and async task orchestration.

---

## 1. Storage Architecture Overview

```mermaid
flowchart TD
    Crawler["Crawl4AI / Playwright Crawler"]
    Extractor["Dual Extraction Pipeline"]
    Verifier["Person-Company Association Verifier"]

    subgraph RAW_TIER ["Tier 1: Raw Object Storage (MinIO)"]
        MinIO_DOM["MinIO S3 Bucket (opendb/opendb)\npath: data/processed/markdown/{hash}.md"]
        MinIO_HTML["MinIO S3 Bucket (opendb/opendb)\npath: data/raw/pages/{hash}.html"]
    end

    subgraph RELATIONAL_TIER ["Tier 2: Primary Relational DB (PostgreSQL + pgvector)"]
        PG_Doc["documents Table"]
        PG_Univ["universal_records Table"]
        PG_Domain["domain_records Table (JSONB Payload)"]
        PG_Fact["extracted_facts & evidence Tables"]
        PG_KP["key_person_candidates Table"]
    end

    subgraph VAULT_TIER ["Tier 3: Master Lead Vault (SQLite WAL)"]
        SQLite_Leads["global_leads Table"]
        SQLite_People["global_lead_people Table"]
        SQLite_Pages["global_lead_subpages Table"]
    end

    subgraph CACHE_TIER ["Tier 4: Memory & Task Broker (Redis)"]
        Redis_Broker["Celery Task Queue"]
        Redis_Cache["24h Deduplication Cache (key_people_discovery:{domain})"]
    end

    Crawler -->|Store Cleaned DOM| MinIO_DOM
    Crawler -->|Store Raw HTML| MinIO_HTML

    Extractor -->|Save Document & Universal Record| PG_Doc
    Extractor -->|Save Universal Metadata| PG_Univ
    Extractor -->|Save Dynamic Payload| PG_Domain
    Extractor -->|Save Provenance Facts| PG_Fact

    Verifier -->|Save Key Person Candidates| PG_KP

    PG_Univ -->|Vault Sync (vault_service.py)| SQLite_Leads
    Verifier -->|Vault Sync| SQLite_People

    Crawler <-->|Queue Jobs & Dedup| Redis_Broker
    Verifier <-->|Dedup Cache| Redis_Cache
```

---

## 2. Storage System Specification

| Storage System | Container / Process | Purpose | Stored Artifacts | Primary Access Module | Durability |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PostgreSQL + pgvector** | `opendb_postgres` (Port 5433:5432) | Primary relational engine & vector store | Universal metadata, JSONB payloads, facts, key people, crawl logs | [`backend/app/persistence/repositories.py`](file:///e:/crawl/backend/app/persistence/repositories.py) | Persistent Volume |
| **SQLite WAL Vault** | Embedded (`data/global_leads.db`) | High-speed operational lead vault | Master lead records (`global_leads`), decision makers, subpages | [`backend/app/persistence/vault_service.py`](file:///e:/crawl/backend/app/persistence/vault_service.py) | Persistent File |
| **MinIO S3 Storage** | `opendb_minio` (Port 9001, 9002) | S3-compatible raw evidence storage | HTML source files (`.html`), cleaned Markdown DOM (`.md`), downloaded PDF/CSV files | [`backend/app/storage/file_storage.py`](file:///e:/crawl/backend/app/storage/file_storage.py) | Persistent Volume |
| **Redis** | `opendb_redis` (Port 6379) | Celery broker & deduplication cache | Celery task messages, 24h key-people domain deduplication cache | [`backend/app/cache/redis_client.py`](file:///e:/crawl/backend/app/cache/redis_client.py) | In-memory + AOF persistence |

---

## 3. Data Pipeline Lifecycle

```text
1. CRAWL & RAW CAPTURE
   Crawl4AI fetches URL → HTML content hash computed (SHA-256)
   → HTML saved to MinIO: data/raw/pages/{content_hash}.html
   → Cleaned Markdown DOM saved to MinIO: data/processed/markdown/{content_hash}.md

2. RELATIONAL PERSISTENCE (PostgreSQL)
   → Document record inserted in 'documents' table
   → Universal Metadata record inserted in 'universal_records' table
   → Dynamic domain schema payload stored in 'domain_records' (JSONB)
   → Atomized field facts & exact text selectors stored in 'extracted_facts' & 'evidence'

3. MASTER LEAD VAULT SYNC (SQLite WAL)
   → VaultService extracts lead summary, logo URL, technology stack
   → Inserts or updates 'global_leads' (keyed by MD5 hash of domain)
   → Inserts subpage references in 'global_lead_subpages'

4. KEY PEOPLE DISCOVERY & VERIFICATION
   → Secondary SearXNG search discovers candidates
   → PersonCompanyVerifier calculates confidence score
   → Saves to 'key_person_candidates' (PostgreSQL)
   → Synced to 'global_lead_people' (SQLite Master Vault)
```

---

## 4. File Storage Provider Architecture

The file storage layer (`app/storage/file_storage.py`) uses an abstract storage provider pattern:
- **Local Provider**: Writes directly to local disk paths (`backend/data/raw/`, `backend/data/processed/`).
- **MinIO Provider**: Uses MinIO S3 SDK (`minio.Minio`) to stream objects to the `opendb` bucket.
- **Environment Switch**: Configured via `STORAGE_BACKEND=minio` or `STORAGE_BACKEND=local` in `.env`.
