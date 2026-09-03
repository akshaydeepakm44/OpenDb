# OpenDB Relational Database Specification

OpenDB features a dual persistence strategy: PostgreSQL for production environments and an automated **SQLite WAL (Write-Ahead Logging)** fallback (`opendb_fallback.db`) for lightweight local development.

## Dual Persistence & Engine Configuration

- **Primary Database**: PostgreSQL (`postgresql://admin:password123@localhost:5432/opendb`)
- **Fallback Database**: SQLite (`sqlite:///./opendb_fallback.db`)
- **SQLite Concurrency & Reliability Pragmas**:
  ```python
  PRAGMA journal_mode = WAL;
  PRAGMA busy_timeout = 30000;
  ```
  WAL mode allows non-blocking read queries (e.g. API endpoint fetches) while background worker threads write new crawl documents and entities.

## Relational Schema & Tables Summary

1. `sources`: Provenance tracking for domains and root search URLs.
2. `domains` & `subdomains`: Structured domain taxonomy hierarchy.
3. `crawl_jobs`: Manual pipeline job execution state and step metrics.
4. `documents`: Crawled web pages, HTTP status, word counts, content hashes, and file paths.
5. `universal_records`: Core canonical entity records (company name, domain, country, entity type, status).
6. `domain_records`: Dynamic domain payload stored in JSON schema structure.
7. `extracted_facts`: Granular key-value facts (`field_name`, `field_value`, `confidence`).
8. `evidence`: Provenance snippets mapping extracted facts back to source URLs.
9. `crawl_activity_log`: Real-time audit trail of every `SEARCH`, `CRAWL`, `EXTRACT`, `VERIFY`, and `FILTER` step.
10. `agent_state`: Current execution state (`RUNNING` / `PAUSED`), current target domain/keyword, and batch state.
11. `search_history`: History of web search queries executed and target URLs found.
12. `batch_results`: Performance tracking per discovery batch (planned vs executed searches, discovered leads).
13. `keyword_performance`: Usage count, success rates, and deprecation flags per discovery keyword.
14. `verification_records`: Verifiable confidence records produced by continuous verification workers.

