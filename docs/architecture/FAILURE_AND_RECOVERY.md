# Failure Handling & System Recovery Architecture

OpenDB is designed with defensive fault isolation and automatic fallback mechanisms to ensure that failure in one dependency (e.g., SearXNG or Redis) does not crash the entire ingestion engine.

---

## 1. Resilience Matrix

| Dependency / Stage | Failure Mode | Impact | Fallback Strategy / Recovery Mechanism | Code Location |
| :--- | :--- | :--- | :--- | :--- |
| **SearXNG Meta-Search** | Connection timeout or 500 error | Search query returns 0 results | Falls back to pre-configured domain seed URLs (`DOMAIN_DEFAULT_SEEDS`) | [`backend/app/api/crawl.py`](file:///e:/crawl/backend/app/api/crawl.py#L50-L60) |
| **Redis Queue / Broker** | Container down or socket refusal | Celery `apply_async` fails | `_safe_dispatch()` catches exception and launches task in background daemon thread | [`backend/app/worker/tasks.py`](file:///e:/crawl/backend/app/worker/tasks.py#L94-L116) |
| **Crawl4AI / Playwright** | Page timeout, 404, or JavaScript crash | Page HTML cannot be fetched | Logs error in `crawl_errors` table, skips subpage, and proceeds with homepage content | [`backend/app/crawler/crawler_service.py`](file:///e:/crawl/backend/app/crawler/crawler_service.py) |
| **MinIO Object Storage** | MinIO unreachable or storage full | Markdown/HTML storage fails | Falls back to local file storage provider (`data/processed/markdown/`) | [`backend/app/storage/file_storage.py`](file:///e:/crawl/backend/app/storage/file_storage.py) |
| **LiteLLM / OpenAI API** | API rate limit or missing API key | LLM extraction unavailable | Falls back to Mode 1 CSS/heuristic extractor (`css_extractor.py`) | [`backend/app/extraction/llm_extractor.py`](file:///e:/crawl/backend/app/extraction/llm_extractor.py) |
| **PostgreSQL Database** | Brief pool exhaustion | Database query error | Retries transaction with exponential backoff; logs error in audit stream | [`backend/app/persistence/database.py`](file:///e:/crawl/backend/app/persistence/database.py) |

---

## 2. Crawl Error Persistence (`crawl_errors`)

When an unrecoverable exception occurs during crawling or extraction, OpenDB persists the full traceback to the `crawl_errors` table:

```python
def _log_crawl_error(db, url: str, stage: str, error: Exception):
    err_record = CrawlError(
        url=url,
        stage=stage,
        error_type=type(error).__name__,
        error_message=str(error),
        stack_trace=traceback.format_exc(),
        timestamp=utc_now()
    )
    db.add(err_record)
    db.commit()
```

The error stream is surfaced on the dashboard under **Operations Metrics**, enabling technical teams to diagnose site-specific crawling issues without parsing log files manually.
