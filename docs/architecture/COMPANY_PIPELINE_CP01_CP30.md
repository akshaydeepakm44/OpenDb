# Company Ingestion Pipeline — CP-01 through CP-30 Breakdown

This document provides a code-level breakdown of the **30 Company Pipeline Checkpoints (CP-01 through CP-30)** in OpenDB, tracing company discovery, candidate qualification, official domain resolution, web crawling, content extraction, and database persistence.

---

## High-Level Sequence Flow Diagram

![OpenDB System Sequence Flow Diagram](C:/Users/aksha/.gemini/antigravity-ide/brain/906a7fea-1c95-4e9e-8da7-343c2fb869d7/pipeline_sequence_diagram_1789453916804.jpg)

![OpenDB Company Pipeline Flow Diagram](C:/Users/aksha/.gemini/antigravity-ide/brain/906a7fea-1c95-4e9e-8da7-343c2fb869d7/company_pipeline_flow_diagram_1789453870391.jpg)
sequenceDiagram
    autonumber
    actor Dashboard as React Dashboard
    participant API as FastAPI Router (app/api/crawl.py)
    participant Agent as Discovery Agent (app/agent/discovery_agent.py)
    participant SearXNG as SearXNG Service (app/crawler/searxng_service.py)
    participant Filter as Quality Filter & Detector (app/crawler/quality_filter.py)
    participant Norm as Normalizer (app/normalization/normalizer.py)
    participant Crawler as Crawl4AI Engine (app/crawler/crawler_service.py)
    participant Extractor as Extraction Engine (app/extraction/extractor.py)
    participant DB as PostgreSQL / MinIO Storage

    Dashboard->>API: POST /api/crawl (URL, Query, Domain)
    API->>Agent: Spawn/Trigger Crawl Job (CP-01)
    Agent->>SearXNG: Execute Meta-Search (CP-06)
    SearXNG-->>Agent: Raw Results
    Agent->>Filter: Candidate Validation & Domain Safety (CP-07..CP-09)
    Filter-->>Agent: Valid Company Candidate
    Agent->>Norm: CP-11 Official Domain Resolution
    Norm-->>Agent: Canonical Domain URL
    Agent->>DB: Check Deduplication (CP-12)
    Agent->>Crawler: Enqueue Crawl Task (CP-13)
    Crawler->>Crawler: Crawl Homepage + Subpages (/about, /team)
    Crawler->>Extractor: Extract Firmographics & Metadata (CP-14..CP-29)
    Extractor->>DB: Save MinIO DOM, Facts & Universal Record
    DB-->>Dashboard: Complete Company Dossier (CP-30)
```

---

## Detailed Checkpoint Breakdown (CP-01 to CP-30)

### CP-01 — Agent Startup
- **File**: [`backend/app/agent/discovery_agent.py`](file:///e:/crawl/backend/app/agent/discovery_agent.py#L90-L150)
- **Class / Method**: `AutonomousDiscoveryAgent.set_status()` / `_get_or_create_state()`
- **Input**: Agent trigger signal or server startup call.
- **Processing**: Initializes thread loop, reads state from PostgreSQL `agent_state` table, sets status to `RUNNING`.
- **Output**: Agent state object.
- **Persistence**: Saved in `agent_state` table.
- **Failure Behavior**: Falls back to default state (`PAUSED`) and logs error.

### CP-02 — Discovery Strategy Selection
- **File**: [`backend/app/agent/discovery_agent.py`](file:///e:/crawl/backend/app/agent/discovery_agent.py#L200-L250)
- **Input**: Active domain list (`Technology`, `Healthcare`, `Education`, `Business`).
- **Processing**: Selects target domain/subdomain taxonomy for search expansion.
- **Output**: Selected domain string.
- **Persistence**: Logged in `agent_state.current_domain`.

### CP-03 — Keyword Generation
- **File**: [`backend/app/agent/keyword_expander.py`](file:///e:/crawl/backend/app/agent/keyword_expander.py#L40-L100)
- **Class / Method**: `KeywordExpander.generate_keywords()`
- **Input**: Domain & subdomain string.
- **Processing**: Expands base terms into specific search queries (e.g., `"SaaS development companies official website"`).
- **Output**: List of expanded query strings.

### CP-04 — Keyword Rotation & Exhaustion Check
- **File**: [`backend/app/persistence/repositories.py`](file:///e:/crawl/backend/app/persistence/repositories.py#L120-L160)
- **Processing**: Queries `keyword_performance` table to check usage count and success rate. Deprecates exhausted terms.

### CP-05 — Query Safety Guard
- **File**: [`backend/app/safety/guardrails.py`](file:///e:/crawl/backend/app/safety/guardrails.py#L20-L60)
- **Processing**: Filters queries against moderation rules (NSFW, illegal content, spam patterns).

### CP-06 — Primary SearXNG Search
- **File**: [`backend/app/crawler/searxng_service.py`](file:///e:/crawl/backend/app/crawler/searxng_service.py#L40-L90)
- **Class / Method**: `SearXNGService.search()`
- **Input**: Safe search query.
- **Output**: List of search result dictionaries (`url`, `title`, `snippet`).
- **Persistence**: Saved in `search_history` table.

### CP-07 — Candidate Validation
- **File**: [`backend/app/crawler/quality_filter.py`](file:///e:/crawl/backend/app/crawler/quality_filter.py#L50-L110)
- **Processing**: Evaluates search result relevance, title quality, and snippet signals.

### CP-08 — Candidate Creation & Context Split
- **File**: [`backend/app/api/crawl.py`](file:///e:/crawl/backend/app/api/crawl.py#L170-L199)
- **Processing**: Creates `CompanyDiscoveryContext` object. Launches parallel Key People discovery branch (`KP-01`).

### CP-09 — Domain Safety Filter
- **File**: [`backend/app/safety/reputation.py`](file:///e:/crawl/backend/app/safety/reputation.py#L30-L70)
- **Processing**: Checks domain against `blocked_domains` table and TLD blocklists.

### CP-10 — Domain Classification
- **File**: [`backend/app/classification/domain_classifier.py`](file:///e:/crawl/backend/app/classification/domain_classifier.py#L30-L80)
- **Class / Method**: `DomainClassifier.classify()`
- **Processing**: Uses keyword signals and metadata to categorize company into Technology, Healthcare, Education, or Business.

---

## Deep Focus: CP-11 — Official Domain Resolution

`CP-11` converts a raw, arbitrary search result URL into an absolute, verified canonical company domain.

```text
               Raw Search URL
                     ↓
┌───────────────────────────────────────────┐
│ 1. Redirect Resolution                    │
│    Follows 301/302 HTTP redirects          │
└────────────────────┬──────────────────────┘
                     ↓
┌───────────────────────────────────────────┐
│ 2. Tracking Parameter Stripping           │
│    Strips utm_*, gclid, fbclid, ref, etc. │
└────────────────────┬──────────────────────┘
                     ↓
┌───────────────────────────────────────────┐
│ 3. Fragment Removal                       │
│    Strips #section, #about                │
└────────────────────┬──────────────────────┘
                     ↓
┌───────────────────────────────────────────┐
│ 4. TLD Extraction                         │
│    tldextract extracts registered domain  │
└────────────────────┬──────────────────────┘
                     ↓
┌───────────────────────────────────────────┐
│ 5. Canonical Homepage Construction        │
│    Formats clean https://domain.com       │
└───────────────────────────────────────────┘
```

### Code Implementation
- **File**: [`backend/app/normalization/normalizer.py`](file:///e:/crawl/backend/app/normalization/normalizer.py#L18-L45)
- **Method**: `DataNormalizer.normalize_url()`

```python
def normalize_url(self, raw_url: str, base_url: Optional[str] = None) -> Optional[str]:
    if base_url:
        raw_url = urljoin(base_url, raw_url)

    parsed = urlparse(raw_url)
    clean_query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_")])
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), '', clean_query, ''))
```

---

### CP-12 — Deduplication Engine
- **File**: [`backend/app/persistence/repositories.py`](file:///e:/crawl/backend/app/persistence/repositories.py#L180-L220)
- **Processing**: Queries PostgreSQL `documents` and `universal_records` tables for matching canonical domain URLs.

### CP-13 — Crawl Queue Enqueue
- **File**: [`backend/app/worker/tasks.py`](file:///e:/crawl/backend/app/worker/tasks.py#L94-L116)
- **Method**: `_safe_dispatch(crawl_entity_task)`
- **Processing**: Pushes target domain into Redis Celery queue (or daemon thread fallback).

### CP-14 to CP-29 — Deep Crawling & Multi-Mode Extraction
- **File**: [`backend/app/crawler/crawler_service.py`](file:///e:/crawl/backend/app/crawler/crawler_service.py) & [`backend/app/extraction/extractor.py`](file:///e:/crawl/backend/app/extraction/extractor.py)
- **Stages**:
  - **CP-14**: Playwright headless browser startup.
  - **CP-15**: BFS homepage HTML fetch.
  - **CP-16**: Subpage link discovery (`/about`, `/team`, `/contact`).
  - **CP-17**: DOM clean-up & HTML parsing.
  - **CP-18**: Markdown generation & MinIO storage (`data/processed/markdown/{hash}.md`).
  - **CP-19**: OpenGraph & JSON-LD metadata extraction (`css_extractor.py`).
  - **CP-20**: H1 & Header hierarchy extraction.
  - **CP-21**: Domain semantic firmographic extraction (`llm_extractor.py`).
  - **CP-22**: Text normalization & sanitization (`normalizer.py`).
  - **CP-23**: Creation of `universal_records` row.
  - **CP-24**: Creation of `domain_records` dynamic JSONB row.
  - **CP-25**: Atomized facts generation (`extracted_facts`).
  - **CP-26**: Evidence snippet mapping (`evidence` table).
  - **CP-27**: Vector embedding generation (384-dim pgvector).
  - **CP-28**: SQLite Master Vault sync (`global_leads`).
  - **CP-29**: Audit activity log emission (`crawl_activity_log`).

### CP-30 — Dashboard & Completeness Scoring
- **File**: [`backend/app/api/agent.py`](file:///e:/crawl/backend/app/api/agent.py#L150-L210)
- **Processing**: Calculates completeness score (0-100), updates company dossier status, and streams telemetry to React UI.
