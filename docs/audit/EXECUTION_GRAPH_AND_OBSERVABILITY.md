# OpenDB System Architecture & Execution Graph

## 1. Concurrency Model: Agent 1 ➔ Celery/Redis Queue ➔ Agent 2

OpenDB operates with **two completely decoupled, asynchronous, concurrent agent lifecycles**:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AGENT 1: LEAD DISCOVERY                            │
│  [CP-01: Run Init] ──> [CP-02: Agent 1 Init] ──> [CP-03: Keyword Gen]       │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-04: SearXNG Search]     │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-05: Raw Results]        │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-06: URL Extraction]     │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-07: Domain Filter]      │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-08: Lead Validation]    │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-09: Deduplication]      │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-10: Lead Creation]      │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-18: Initial Crawl]      │
│                                                          │                  │
│                                                          ▼                  │
│                                                 [CP-26: MinIO Raw Storage]  │
│                                                          │                  │
│                                                          ▼                  │
│                                              Doc: CRAWLED_PENDING_AGENT_2   │
│                                                 [CP-11: Agent 1 Step Done]  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
                       [CP-27: Celery/Redis Task Queue]
                         (tasks.agent2_process_card)
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      AGENT 2: DEEP LEAD OBSERVATION                         │
│  [CP-12: Agent 2 Intake] ──> [CP-13: Priority Ranking (0-100)]              │
│                                         │                                   │
│                                         ▼                                   │
│                             [CP-14: Phase 1 Gate Verification]              │
│                             - Vault Path (CP-26)                            │
│                             - Crawled Text (CP-20)                          │
│                             - Word Count                                    │
│                             - Email Evidence                                │
│                             - Industry Sector                               │
│                             - Location Region                               │
│                             - Size Tier                                     │
│                                         │                                   │
│                                 Need more evidence?                         │
│                                         ├── YES ──> [CP-18: Targeted Crawl] │
│                                         │                     │             │
│                                         ▼                     ▼             │
│                             [CP-15: Phase 2 Deep Research & LLM Synthesis]  │
│                                         │                                   │
│                                         ▼                                   │
│                             [CP-16: LinkedIn Person Discovery Loop]         │
│                                         │                                   │
│                                         ▼                                   │
│                             [CP-22: Evidence Provenance Collection]         │
│                             [CP-23: Validation Gate]                        │
│                             [CP-24: Verification Gate]                      │
│                                         │                                   │
│                                         ▼                                   │
│                             [CP-25: PostgreSQL Persistence / Outbox]         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Checkpoint Mapping (CP-01 through CP-30)

| Checkpoint ID | Name | Module & Function | Primary Agent | Description |
|---|---|---|---|---|
| `CP-01` | RUN_INITIALIZATION | `app.main:lifespan` / `api.agent:start_discovery_agent` | SYSTEM | Generation of unique `run_id`, system startup |
| `CP-02` | AGENT_1_INIT | `app.agent.discovery_agent:_discovery_loop` | AGENT-01 | Agent 1 state initialization (`CREATED` ➔ `STARTED`) |
| `CP-03` | KEYWORD_GENERATION | `app.agent.keyword_expander:get_next_query` | AGENT-01 | Generation of search keyword seeds & geographic modifiers |
| `CP-04` | SEARCH_EXECUTION | `app.crawler.searxng_service:search_with_meta` | AGENT-01 | HTTP call to SearXNG with SafeSearch=2 & engine list |
| `CP-05` | SEARCH_RESULTS | `app.crawler.searxng_service:search_with_meta` | AGENT-01 | Parsing JSON candidate list returned from SearXNG |
| `CP-06` | URL_EXTRACTION | `app.worker.tasks:search_and_discover_task` | AGENT-01 | Normalization and extraction of target domains/URLs |
| `CP-07` | DOMAIN_FILTERING | `app.crawler.quality_filter:filter_url` | AGENT-01 | Domain rejection (blacklists, news, social media, non-B2B) |
| `CP-08` | LEAD_VALIDATION | `app.crawler.quality_filter:qualify_company_candidate` | AGENT-01 | Heuristic and candidate pre-crawl qualification |
| `CP-09` | DEDUPLICATION | `app.persistence.vault_service:is_candidate_locked` | AGENT-01 | Redis L1 and database domain duplication check |
| `CP-10` | LEAD_CREATION | `app.worker.tasks:crawl_entity_task` | AGENT-01 | Staging document card created (`CRAWLED_PENDING_AGENT_2`) |
| `CP-11` | AGENT_1_COMPLETION | `app.agent.discovery_agent:_discovery_loop` | AGENT-01 | Agent 1 batch step complete; yields to next iteration |
| `CP-12` | AGENT_2_INIT | `app.agent.agent2_orchestrator:get_or_create_session` | AGENT-02 | Agent 2 card intake and session creation |
| `CP-13` | LEAD_ANALYSIS | `app.agent.agent2_orchestrator:rank_card` | AGENT-02 | Priority score calculation (0-100) with explainable reasons |
| `CP-14` | LEAD_CLASSIFICATION | `app.agent.agent2_orchestrator:verify_phase1` | AGENT-02 | 7-field hard verification gate audit |
| `CP-15` | DEEP_RESEARCH | `app.agent.agent2_orchestrator:synthesize_business` | AGENT-02 | Haystack / LLM synthesis of evidence-grounded overview |
| `CP-16` | DOMAIN_VERIFICATION | `app.agent.agent2_orchestrator:discover_and_verify_linkedin` | AGENT-02 | Multi-round LinkedIn discovery & authentic `/in/` profile evaluation |
| `CP-17` | CRAWL_SCHEDULING | `app.agent.agent2_orchestrator:verify_phase1` | AGENT-02 | Decision to schedule targeted subpage re-crawls |
| `CP-18` | CRAWL_EXECUTION | `app.crawler.crawler_service:crawl_site` | BOTH | Crawl4AI / Playwright browser rendering of target pages |
| `CP-19` | PAGE_DISCOVERY | `app.crawler.crawler_service:crawl_site` | BOTH | Link discovery on corporate pages (`/about`, `/contact`, `/team`) |
| `CP-20` | CONTENT_EXTRACTION | `app.crawler.evidence_validator:extract_raw_page_facts` | BOTH | Extraction of visible text, page title, meta description |
| `CP-21` | COMPANY_DATA_EXTRACTION| `app.agent.agent2_investigation` | AGENT-02 | Field-specific extraction (email, industry, location, size) |
| `CP-22` | EVIDENCE_COLLECTION | `app.agent.agent2_investigation` | AGENT-02 | Source URL provenance snippet and extraction confidence binding |
| `CP-23` | VALIDATION_GATE | `app.agent.agent2_investigation:can_mark_not_found`| AGENT-02 | Exhaustion verification rule; prohibits early NOT_FOUND |
| `CP-24` | VERIFICATION_GATE | `app.agent.agent2_orchestrator:finalize_verification`| AGENT-02 | Final verification status determination (`VERIFIED` vs `BLOCKED`) |
| `CP-25` | DATABASE_PERSISTENCE | `app.persistence.outbox_sync_service` | BOTH | PostgreSQL write or SQLite outbox promotion |
| `CP-26` | OBJECT_STORAGE | `app.storage.file_storage` | BOTH | MinIO S3 object storage of raw HTML, Markdown, and Brand Kit |
| `CP-27` | QUEUE_PROCESSING | `app.worker.tasks` / Celery | SYSTEM | Task enqueue, dispatch, Celery execution, queue depth tracking |
| `CP-28` | FRONTEND_EVENT | `app.api.agent:get_operations_dashboard` | SYSTEM | Live activity stream, log broadcasting |
| `CP-29` | RUN_COMPLETION | `app.agent.discovery_agent:set_status` | SYSTEM | Clean run completion or pausing |
| `CP-30` | FAILURE_RECOVERY | `app.audit.tracer` | SYSTEM | Forensic exception logging, retry, fallback activation, recovery |

---

## 3. Strict Infrastructure Policy Matrix

| Component | Allowed Primary | Allowed Fallback | Forbidden Substitutions (Must FAIL explicitly) |
|---|---|---|---|
| **Database** | PostgreSQL | SQLite (`opendb_fallback.db`) | In-memory mock DB, unpersisted dictionaries |
| **Object Storage** | MinIO S3 (`localhost:9000`) | NONE (Must raise `STORAGE_FAILED`) | Silent write to local disk |
| **Task Queue** | Redis (`localhost:6379`) + Celery | NONE (Must raise `QUEUE_FAILED`) | SQLite Celery broker, background threads, FastAPI BackgroundTasks |
| **Search Engine**| SearXNG (`localhost:8080`) | NONE (Must return `SEARCH_FAILED`) | Mock results, hardcoded domain seeds, fake URLs |
| **Browser Rendering**| Crawl4AI / Playwright | NONE (Must raise `CRAWL_FAILED`) | Plain HTTP client pretending to render JS |
| **Intelligence LLM**| GPU Qwen / OpenAI Compatible | Deterministic Taxonomy Expansion | Fabricated names, fake numbers, hallucinated executives |
