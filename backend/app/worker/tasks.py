"""
Celery Worker Tasks — §11, §12, §13, §16, §17, §18, §19 of Master Prompt

Worker A — search_and_discover_task:
  SearXNG search → URL list → quality filter → classify listing vs entity
  → enqueue crawl for each valid entity URL

Worker B — crawl_entity_task:
  Crawl entity page + subpages (/about, /contact, /team) → MinIO raw storage
  → extract all 20+ fields → quality filter entity → normalize → deduplicate
  → Postgres save

Worker C — enrich_and_verify_task:
  Normalize → domain-level dedup → multi-signal confidence verification
"""
import sys
import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from app.config import settings
from app.worker.celery_app import celery_app
from app.persistence.database import SessionLocal
from app.persistence.repositories import repo
from app.persistence.models import (
    SearchHistory, Document, Company, Domain,
    BatchResult, CrawlError, CrawlActivityLog,
    KeyPerson, VerificationSession, CanonicalEvidence,
    ArtifactOutbox, utc_now,
)

from app.crawler.searxng_service import searxng_service
from app.crawler.crawler_service import crawler_service
from app.crawler.listing_detector import listing_detector
from app.crawler.quality_filter import quality_filter
from app.crawler.url_discovery import url_discovery
from app.crawler.distributed_slot_manager import slot_manager
from app.safety.resource_governor import governor
from app.storage.file_storage import file_storage
from app.extraction.extractor import extraction_pipeline
from app.normalization.normalizer import normalizer
from app.cache.redis_client import get_redis

logger = logging.getLogger(__name__)


def _runner(coro):
    if sys.platform == 'win32':
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass
    return asyncio.run(coro)


def run_async(coro):
    """
    Run async coroutine safely across platforms.
    Guarantees:
    - coroutine executed exactly once
    - never reuses an already awaited coroutine
    - works with or without existing event loop, worker threads, and FastAPI dev mode
    """
    import concurrent.futures
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_runner, coro).result()
        else:
            return _runner(coro)
    except Exception as e:
        logger.error(f"[run_async] Coroutine execution failed: {e}")
        raise


_worker_check_cache = {"active": False, "last_check": 0}

def _has_active_celery_worker() -> bool:
    """Non-blocking, truthful detection of active Celery worker on system."""
    import time
    import os
    now = time.time()
    if now - _worker_check_cache["last_check"] < 5:
        return _worker_check_cache["active"]

    is_active = False
    try:
        import psutil
        curr_pid = os.getpid()
        parent_pid = os.getppid()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = p.info['pid']
                if pid in (curr_pid, parent_pid):
                    continue
                cmd = ' '.join(p.info.get('cmdline') or []).lower()
                if ('celery' in cmd and 'worker' in cmd) and ('app.worker.celery_app' in cmd or 'celery.exe' in cmd):
                    is_active = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        is_active = False

    _worker_check_cache["active"] = is_active
    _worker_check_cache["last_check"] = now
    return is_active


def _dispatch_task(task_func, **kwargs):
    """
    Enqueues Celery task into Redis task queue with serialized trace context and telemetry.
    """
    from app.audit.tracer import tracer, Checkpoint
    task_name = getattr(task_func, 'name', str(task_func))
    
    if "trace_ctx" not in kwargs:
        kwargs["trace_ctx"] = tracer.get_context_dict()
    trace_ctx = kwargs["trace_ctx"] or {}
    run_id = trace_ctx.get("run_id") or tracer.get_run_id() or "RUN-CELERY"
    agent_id = trace_ctx.get("agent_id") or "AGENT-01"

    try:
        # Route dynamically via celery_app.conf.task_routes (discovery, crawl, verification)
        task_res = task_func.apply_async(kwargs=kwargs)
        task_id = getattr(task_res, "id", str(uuid.uuid4()))
        
        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
            event="TASK_ENQUEUED",
            message=f"Enqueued Celery task '{task_name}' into Redis queue (execution_mode=CELERY, task_id={task_id})",
            task_id=task_id,
            status="QUEUED",
            extra={
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "execution_mode": "CELERY",
                "status": "QUEUED",
                "task": task_name,
                "celery_task_id": task_id
            }
        )
        return True
    except (TypeError, ValueError) as sig_err:
        task_id = str(uuid.uuid4())
        tracer.log_event(
            level="ERROR",
            checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
            event="QUEUE_DISPATCH_FAILED",
            message=f"TASK_SIGNATURE_MISMATCH: Invalid arguments for task '{task_name}': {sig_err}",
            task_id=task_id,
            status="DISPATCH_FAILED",
            extra={
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "execution_mode": "DISPATCH_FAILED",
                "status": "FAILED",
                "exception": str(sig_err),
                "failure_class": "TASK_SIGNATURE_MISMATCH",
                "task": task_name
            },
            exc_info=True
        )
        raise RuntimeError(f"QUEUE_FAILED (TASK_SIGNATURE_MISMATCH): Celery signature rejected for '{task_name}' ({sig_err})")
    except Exception as e:
        task_id = str(uuid.uuid4())
        err_type = type(e).__name__
        is_conn = any(x in str(e).lower() or x in err_type.lower() for x in ["connection", "timeout", "socket", "refused", "auth"])
        failure_class = "REDIS_CONNECTION_FAILED" if is_conn else "TASK_DISPATCH_FAILED"
        tracer.log_event(
            level="ERROR",
            checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
            event="QUEUE_DISPATCH_FAILED",
            message=f"{failure_class}: Redis task queue unreachable for task '{task_name}': {e}",
            task_id=task_id,
            status="FAILED",
            extra={
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "execution_mode": "DISPATCH_FAILED",
                "status": "FAILED",
                "exception": str(e),
                "failure_class": failure_class,
                "task": task_name
            },
            exc_info=True
        )
        raise RuntimeError(f"QUEUE_FAILED ({failure_class}): Redis task queue unreachable ({e})")


def _safe_dispatch(task_func, **kwargs):
    """
    Controlled Task Dispatch Execution Adapter:
    1. If an active Celery worker exists: Dispatches via Celery Redis queue (execution_mode=CELERY).
    2. If no Celery worker exists and task is NOT Agent 2: Runs the EXACT SAME task function locally in a background daemon thread
       (execution_mode=LOCAL_THREAD) with full telemetry.
    3. If no Celery worker exists and task IS Agent 2: FAILS CLOSED. Agent 2 must run on dedicated verification workers.
    """
    from app.audit.tracer import tracer, Checkpoint
    task_name = getattr(task_func, 'name', str(task_func))
    
    if "trace_ctx" not in kwargs:
        kwargs["trace_ctx"] = tracer.get_context_dict()
    trace_ctx = kwargs["trace_ctx"] or {}
    run_id = trace_ctx.get("run_id") or tracer.get_run_id() or "RUN-LOCAL"
    agent_id = trace_ctx.get("agent_id") or "AGENT-01"
    task_id = str(uuid.uuid4())

    # Attempt Celery dispatch if Redis is reachable
    from app.worker.celery_app import REDIS_AVAILABLE
    if REDIS_AVAILABLE:
        try:
            return _dispatch_task(task_func, **kwargs)
        except Exception as dispatch_err:
            logger.warning(f"[_safe_dispatch] Celery dispatch failed for {task_name}, evaluating fallback: {dispatch_err}")

    # Controlled Fallback: LOCAL_THREAD execution mode
    execution_mode = "LOCAL_THREAD"
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
        event="TASK_ENQUEUED",
        message=f"Task '{task_name}' dispatched to local background thread (execution_mode=LOCAL_THREAD, task_id={task_id})",
        task_id=task_id,
        status="QUEUED",
        extra={
            "run_id": run_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "execution_mode": execution_mode,
            "status": "QUEUED",
            "task": task_name
        }
    )

    def _execute_local_thread():
        started_at = utc_now().isoformat()
        if trace_ctx:
            tracer.restore_context_dict(trace_ctx)
        tracer.set_context(agent_id=agent_id, task_id=task_id, run_id=run_id)

        tracer.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
            event="TASK_STARTED",
            message=f"Starting task '{task_name}' in local thread (execution_mode=LOCAL_THREAD, task_id={task_id})",
            task_id=task_id,
            status="STARTED",
            extra={
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "execution_mode": execution_mode,
                "status": "STARTED",
                "started_at": started_at,
                "task": task_name
            }
        )
        try:
            # Execute the EXACT same Celery task function via apply()
            res = task_func.apply(kwargs=kwargs)
            completed_at = utc_now().isoformat()
            tracer.log_event(
                level="INFO",
                checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
                event="TASK_COMPLETED",
                message=f"Task '{task_name}' completed successfully (execution_mode=LOCAL_THREAD, task_id={task_id})",
                task_id=task_id,
                status="SUCCESS",
                extra={
                    "run_id": run_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "execution_mode": execution_mode,
                    "status": "SUCCESS",
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "exception": None,
                    "task": task_name
                }
            )
            return res
        except Exception as exc:
            completed_at = utc_now().isoformat()
            tracer.log_event(
                level="ERROR",
                checkpoint=Checkpoint.CP27_QUEUE_PROCESSING,
                event="TASK_FAILED",
                message=f"Task '{task_name}' failed in local thread: {exc}",
                task_id=task_id,
                status="FAILED",
                extra={
                    "run_id": run_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "execution_mode": execution_mode,
                    "status": "FAILED",
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "exception": str(exc),
                    "task": task_name
                },
                exc_info=True
            )

    thread = threading.Thread(
        target=_execute_local_thread,
        name=f"local-task-{task_name[:15]}-{task_id[:6]}",
        daemon=True
    )
    thread.start()
    return True



def _log_crawl_error(db, url: str, stage: str, error: Exception):
    """Persist crawl errors to the CrawlError table for the failure stream UI."""
    try:
        err = CrawlError(
            id=str(uuid.uuid4()),
            url=url,
            stage=stage,
            error_type=type(error).__name__,
            error_message=str(error)[:1000],
        )
        db.add(err)
        db.commit()
    except Exception:
        db.rollback()


def _log_activity(db, url: str, stage: str, status: str, message: str = "",
                  entity_name: str = None, domain: str = None, batch_id: str = None):
    """
    Write one live crawl activity event to CrawlActivityLog.
    Called at every stage: SEARCH, CRAWL, EXTRACT, VERIFY, FILTER.
    """
    try:
        entry = CrawlActivityLog(
            id=str(uuid.uuid4()),
            url=url or "",
            domain=domain or "",
            stage=stage,
            status=status,
            message=message[:500] if message else "",
            entity_name=entity_name,
            batch_id=batch_id,
            timestamp=utc_now(),
        )
        db.add(entry)
        db.commit()
    except Exception as e:
        logger.warning(f"[Activity Log] Failed to write log: {e}")
        try:
            db.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# WORKER A — Search & Discover
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.search_and_discover",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def search_and_discover_task(
    self,
    query: str = None,
    keyword: str = None,
    domain: str = None,
    subdomain: str = None,
    batch_id: str = None,
    trace_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    §11 — Worker A: Search SearXNG → classify results → enqueue entity crawls.
    Each SearXNG URL is classified as listing page or entity page.
    Listing pages get their entity links extracted and each entity enqueued separately.
    """
    from app.audit.tracer import tracer, Checkpoint
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-01", task_id=getattr(self.request, "id", None))

    # Backward compat: if called without 'query', build it from keyword + domain
    if not query:
        query = f"{domain or ''} {keyword or ''}".strip()
    if not keyword:
        keyword = query

    logger.info(f"[Worker A] Search: '{query}' | domain='{domain}' batch={batch_id}")

    db = SessionLocal()
    try:
        search_results, is_fallback, log_msg = run_async(
            searxng_service.search_with_meta(query=query, max_results=20)
        )

        # Log search event
        fallback_tag = " [FALLBACK]" if is_fallback else ""
        _log_activity(db, url=f"QUERY: {query}", stage="SEARCH", domain=domain,
                      status="OK" if search_results else "EMPTY",
                      message=f"{log_msg}{fallback_tag} → {len(search_results)} URLs found",
                      batch_id=batch_id)

        # Save Search History
        history = SearchHistory(
            id=str(uuid.uuid4()),
            keyword=keyword,
            domain=domain or "",
            sources_found=len(search_results),
            batch_id=batch_id,
        )
        if hasattr(history, "is_fallback"):
            history.is_fallback = is_fallback
        if hasattr(history, "log_message"):
            history.log_message = log_msg
        db.add(history)
        db.commit()

        if not search_results:
            return {"keyword": keyword, "sources_found": 0, "enqueued_crawls": 0}

        # Process each search result URL with deduplication and backpressure
        enqueued = 0
        max_cycle_results = getattr(settings, "MAX_DISCOVERY_RESULTS_PER_CYCLE", 10)
        high_watermark = getattr(settings, "DISCOVERY_QUEUE_HIGH_WATERMARK", 100)
        r = get_redis()

        for res in search_results:
            if enqueued >= max_cycle_results:
                logger.info(f"[Worker A] Reached max discovery limit ({max_cycle_results}) for cycle.")
                break

            # Queue Backpressure Check
            if r is not None:
                try:
                    current_crawl_q = r.llen("crawl") + r.llen("celery")
                    if current_crawl_q >= high_watermark:
                        logger.warning(f"[Worker A] Crawl queue at capacity ({current_crawl_q}>={high_watermark}). Pausing enqueue.")
                        break
                except Exception:
                    pass

            target_url = res.get("url")
            # Stage 1: Pre-crawl qualification
            qual = quality_filter.qualify_company_candidate(
                title=res.get("title", ""),
                snippet=res.get("snippet", ""),
                url=target_url
            )
            if not qual["qualified"] or qual.get("priority") in ["REJECTED", "DEPRIORITIZED"]:
                _log_activity(db, url=target_url, stage="FILTER", domain=domain,
                              status="FILTERED", message=f"Candidate filtered ({qual.get('priority')}): {qual['reason']}", batch_id=batch_id)
                continue

            # Canonical Domain Deduplication Check
            reg_domain = url_discovery.get_canonical_registrable_domain(target_url)
            if reg_domain:
                if r is not None:
                    try:
                        if r.get(f"opendb:domain:active:{reg_domain}") or r.get(f"opendb:domain:crawled:{reg_domain}"):
                            _log_activity(db, url=target_url, stage="FILTER", domain=domain,
                                          status="DUPLICATE", message=f"Domain '{reg_domain}' already active/crawled (Redis lock)", batch_id=batch_id)
                            continue
                    except Exception:
                        pass

                # Check PostgreSQL authoritative lake
                try:
                    exists_lead = db.query(Company).filter(Company.primary_domain == reg_domain).first()
                    if exists_lead:
                        _log_activity(db, url=target_url, stage="FILTER", domain=domain,
                                      status="DUPLICATE", message=f"Domain '{reg_domain}' already exists in PostgreSQL lake", batch_id=batch_id)
                        continue
                except Exception:
                    pass

                # Mark active in Redis temporary lock (5 minutes)
                if r is not None:
                    try:
                        r.set(f"opendb:domain:active:{reg_domain}", "1", ex=300)
                    except Exception:
                        pass

            # Stage 2: Classify listing vs entity
            classification = listing_detector.classify_url(target_url)

            if classification == "listing":
                _log_activity(db, url=target_url, stage="CRAWL", domain=domain,
                              status="QUEUED", message="Classified as LISTING page — queuing source extraction",
                              batch_id=batch_id)
                _safe_dispatch(crawl_source_task, source_url=target_url, domain=domain, batch_id=batch_id)
                enqueued += 1
            else:
                parsed_u = urlparse(target_url)
                entity_root_url = f"{parsed_u.scheme}://{parsed_u.netloc}/" if parsed_u.netloc else target_url
                _log_activity(db, url=entity_root_url, stage="CRAWL", domain=domain,
                              status="QUEUED", message=f"Qualified ({qual['company_size']}) — queuing root entity crawl ({reg_domain or parsed_u.netloc})",
                              batch_id=batch_id)
                _safe_dispatch(crawl_entity_task, url=entity_root_url, domain=domain, batch_id=batch_id)
                enqueued += 1

        return {
            "query": query,
            "keyword": keyword,
            "sources_found": len(search_results),
            "enqueued_crawls": enqueued,
            "is_fallback": is_fallback,
        }

    except Exception as e:
        logger.error(f"[Worker A] Search task failed for '{query}': {e}")
        _log_activity(db, url=f"QUERY:{query}", stage="SEARCH", domain=domain,
                      status="ERROR", message=str(e)[:300], batch_id=batch_id)
        try:
            raise self.retry(exc=e)
        except Exception:
            return {"error": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# WORKER A.2 — Crawl Source / Listing Page
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.crawl_source",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def crawl_source_task(
    self,
    source_url: str,
    domain: str,
    batch_id: str = None,
    trace_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    §11 — Crawl a listing/directory page and extract individual entity URLs.
    Each discovered entity URL is enqueued as a separate crawl_entity_task.
    """
    from app.audit.tracer import tracer, Checkpoint
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-01", task_id=getattr(self.request, "id", None))

    logger.info(f"[Worker A.2] Crawling source listing: {source_url}")


    try:
        crawled = run_async(
            crawler_service.crawl_site(starting_url=source_url, max_depth=1, max_pages=1)
        )
        if not crawled:
            return {"status": "no_content", "source_url": source_url}

        item = crawled[0]

        # Deep content classification
        page_type, confidence = listing_detector.classify_page(
            url=source_url,
            html_content=item.html_content or "",
            text_content=item.text or "",
        )

        if page_type == "listing" and confidence > 0.5:
            # Extract individual entity links
            entity_links = listing_detector.extract_entity_links(
                html_content=item.html_content or "",
                base_url=source_url,
            )
            enqueued = 0
            for entity_url in entity_links:
                keep, reason = quality_filter.filter_url(entity_url)
                if not keep:
                    continue
                _safe_dispatch(
                    crawl_entity_task,
                    url=entity_url,
                    domain=domain,
                    batch_id=batch_id,
                )
                enqueued += 1
            logger.info(f"[Worker A.2] Extracted {len(entity_links)} links from listing, enqueued {enqueued}")
            return {
                "status": "listing_processed",
                "source_url": source_url,
                "entity_links_found": len(entity_links),
                "enqueued": enqueued,
            }
        else:
            # Treat as entity after all
            _safe_dispatch(
                crawl_entity_task,
                url=source_url,
                domain=domain,
                batch_id=batch_id,
            )
            return {"status": "reclassified_as_entity", "source_url": source_url}

    except Exception as e:
        logger.error(f"[Worker A.2] Source crawl failed for {source_url}: {e}")
        try:
            raise self.retry(exc=e)
        except Exception:
            return {"status": "error", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# WORKER B — Crawl Entity + Extract
# ─────────────────────────────────────────────────────────────────────────────

# Targeted corporate subpages to crawl for company intelligence (§12, §13)
ENTITY_SUBPAGES = [
    "/about", "/about-us", "/company", "/products", "/services",
    "/solutions", "/technology", "/platform", "/product", "/contact",
    "/locations", "/team", "/leadership", "/careers", "/customers",
    "/industries", "/case-studies", "/pricing", "/blog"
]
MAX_ENTITY_PAGES = 8


@celery_app.task(
    name="tasks.crawl_entity",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def crawl_entity_task(
    self,
    url: str,
    domain: str,
    batch_id: str = None,
    trace_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    §12, §13 — Worker B: Deep entity crawl + extraction.
    Crawls homepage + targeted subpages (/about, /company, /team, /leadership, etc.)
    for maximum verified field extraction.
    """
    from app.audit.tracer import tracer, Checkpoint
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-01", task_id=getattr(self.request, "id", None), lead_id=domain)

    logger.info(f"[Worker B] Entity crawl: {url}")

    # ── Stage 0: Look up existing source reference (DO NOT create document yet) ───
    db = SessionLocal()
    existing_doc_id = None
    source_id = None
    base_host = urlparse(url).netloc or url
    try:
        source = repo.get_or_create_source(db, name=base_host, base_url=url)
        source_id = source.id
        doc = db.query(Document).filter(Document.url == url).first()
        if doc:
            existing_doc_id = doc.id
    except Exception as err:
        logger.error(f"[Worker B] Stage 0 error for {url}: {err}")
        return {"status": "error", "error": str(err)}
    finally:
        db.close()

    # ── Stage 1: Intelligent Single-Session Crawl (outside DB transaction) ──
    max_pages = getattr(settings, "MAX_PAGES_PER_DOMAIN_AGENT1", 4)
    try:
        crawled_items = run_async(
            crawler_service.crawl_site(
                starting_url=url,
                max_depth=1,
                max_pages=max_pages,
                slot_type="standard"
            )
        )
    except Exception as crawl_err:
        logger.error(f"[Worker B] Intelligent crawl failed for {url}: {crawl_err}")
        db = SessionLocal()
        try:
            _log_activity(db, url=url, stage="CRAWL", domain=domain,
                          status="ERROR", message=f"Intelligent crawl failed: {crawl_err}",
                          batch_id=batch_id)
        finally:
            db.close()
        return {"status": "error", "error": str(crawl_err)}

    if not crawled_items:
        db = SessionLocal()
        try:
            _log_activity(db, url=url, stage="CRAWL", domain=domain,
                          status="EMPTY", message=f"No crawled content retrieved for {url}",
                          batch_id=batch_id)
        finally:
            db.close()
        return {"status": "empty", "reason": "No crawled content retrieved", "url": url}

    item = crawled_items[0]
    word_count = item.metadata.get("word_count", 0) if item.metadata else 0
    clean_domain = base_host.replace("www.", "").lower()

    # ── Stage 1.5: Nuanced HTTP error / 403 WAF Handling ──
    if item.http_status and item.http_status >= 400:
        if item.http_status in (403, 429):
            # Nuanced 403 / 429 handling: Bot protection or WAF does NOT mean non-existent company!
            logger.warning(f"[Worker B] HTTP {item.http_status} WAF/bot-protection for {url} — preserving for Agent 2 SERP verification.")
            db = SessionLocal()
            try:
                doc = db.query(Document).filter(Document.url == url).first()
                if not doc:
                    derived_waf_title = clean_domain.split(".")[0].replace("-", " ").title()
                    doc = repo.create_document(
                        db=db,
                        crawl_job_id=None,
                        source_id=source_id,
                        url=url,
                        canonical_url=url,
                        title=derived_waf_title,
                        content_type="text/html",
                        http_status=item.http_status,
                        content_hash=f"waf_{item.http_status}_{clean_domain}",
                        raw_path=None,
                        markdown_path=None,
                        text_path=None,
                        word_count=0,
                        links_count=0,
                        images_count=0
                    )
                doc.lifecycle_state = "BLOCKED_NEEDS_ALTERNATIVE_EVIDENCE"
                doc.http_status = item.http_status
                db.commit()
                doc_id_val = str(doc.id)
                _log_activity(db, url=url, stage="CRAWL", domain=domain,
                              status="BLOCKED", message=f"HTTP {item.http_status} bot-protection -> Route to Agent 2 for search evidence verification", batch_id=batch_id)
                _safe_dispatch(agent2_process_card_task, document_id=doc_id_val)
            finally:
                db.close()
            return {
                "status": "blocked",
                "http_status": item.http_status,
                "lifecycle_state": "BLOCKED_NEEDS_ALTERNATIVE_EVIDENCE",
                "url": url,
                "domain": clean_domain
            }
        else:
            # 404 or server 500 permanent error
            logger.warning(f"[Worker B] Permanent HTTP error ({item.http_status}) for {url} — skipping storage.")
            db = SessionLocal()
            try:
                _log_activity(db, url=url, stage="CRAWL", domain=domain,
                              status="FILTERED", message=f"HTTP {item.http_status} error", batch_id=batch_id)
            finally:
                db.close()
            return {"status": "http_error", "http_status": item.http_status, "url": url}

    # ── Stage 2: Content quality filter (MUST pass before persisting document) ──
    keep, reason = quality_filter.filter_content(
        url=url,
        html_content=item.html_content or "",
        text_content=item.text or "",
        title=item.title or "",
        word_count=word_count,
    )
    if not keep:
        logger.info(f"[Worker B] Content filtered ({reason}): {url}")
        db = SessionLocal()
        try:
            _log_activity(db, url=url, stage="FILTER", domain=domain,
                          status="FILTERED", message=f"Content rejected: {reason}", batch_id=batch_id)
        finally:
            db.close()
        return {"status": "filtered", "reason": reason, "url": url}

    # ── Stage 3: Save to MinIO raw storage (with durable outbox fallback) ───────
    raw_artifacts_list = []
    crawl_ts = datetime.now(timezone.utc).isoformat()

    html_hash, raw_rel = file_storage.save_raw_page(item.html_content or "")
    md_rel = file_storage.save_processed_markdown(item.markdown or "", html_hash)
    txt_rel = file_storage.save_processed_text(item.text or "", html_hash)

    # Save homepage company page artifact
    _, hp_md_path = file_storage.save_company_page_artifact(
        domain=clean_domain,
        page_slug="homepage",
        content=item.markdown or item.text or "",
        ext="md",
        metadata={
            "source_url": url,
            "page_type": "homepage",
            "crawl_timestamp": crawl_ts,
            "crawl_job_id": batch_id or ""
        }
    )
    raw_artifacts_list.append(hp_md_path)

    if item.html_content:
        _, hp_html_path = file_storage.save_company_page_artifact(
            domain=clean_domain,
            page_slug="homepage",
            content=item.html_content,
            ext="html",
            metadata={
                "source_url": url,
                "page_type": "homepage",
                "crawl_timestamp": crawl_ts,
                "crawl_job_id": batch_id or ""
            }
        )
        raw_artifacts_list.append(hp_html_path)

    # Derive genuine title from evidence — NEVER append "Official Portal" or fabricate description
    derived_title = (item.title or "").strip()
    if not derived_title or len(derived_title) < 2 or derived_title.lower() in ["home", "index", "welcome", "default", "404", "error"]:
        derived_title = clean_domain.split(".")[0].replace("-", " ").title() if "." in clean_domain else clean_domain
    if not derived_title:
        derived_title = "Unknown"

    # ── Stage 4: Single-Session Subpage Artifacts (NO extra browser launches!) ──
    additional_text = []
    subpages_crawled_urls = []

    for sub_item in crawled_items[1:]:
        if sub_item.text and (sub_item.http_status or 200) < 400:
            additional_text.append(sub_item.text)
            subpages_crawled_urls.append(sub_item.url)
            sub_slug = urlparse(sub_item.url).path.strip("/").replace("/", "_") or "subpage"
            _, sub_md_path = file_storage.save_company_page_artifact(
                domain=clean_domain,
                page_slug=sub_slug,
                content=sub_item.markdown or sub_item.text or "",
                ext="md",
                metadata={
                    "source_url": sub_item.url,
                    "page_type": sub_slug,
                    "crawl_timestamp": crawl_ts,
                    "crawl_job_id": batch_id or ""
                }
            )
            raw_artifacts_list.append(sub_md_path)

    # Combine crawled evidence text for fact extraction
    enriched_text = item.text or ""
    if additional_text:
        enriched_text += "\n\n" + "\n\n".join(additional_text)

    # ── Stage 5: Extract directly observable facts using Anti-Hallucination Validator ────
    from app.crawler.evidence_validator import extract_raw_page_facts, validate_fact
    raw_facts = extract_raw_page_facts(item.html_content or "", enriched_text, url)

    # Validate detected facts against evidence
    validated_emails = []
    for em in raw_facts.get("detected_emails") or []:
        fact_prov = validate_fact("email", em, enriched_text, url, domain=clean_domain)
        if fact_prov:
            validated_emails.append(em)

    raw_metadata_payload = {
        "raw_page_title": raw_facts.get("raw_page_title") or derived_title,
        "meta_description": raw_facts.get("meta_description"),
        "detected_emails": validated_emails,
        "detected_social_links": raw_facts.get("detected_social_links") or [],
        "subpages_crawled": subpages_crawled_urls,
        "pages_crawled_count": 1 + len(subpages_crawled_urls),
        "crawl_timestamp": crawl_ts,
        "crawl_job_id": batch_id or "",
    }

    # ── Stage 6: Persist Staging Document Record & Enqueue to Agent 2 ────────────
    db = SessionLocal()
    try:
        if existing_doc_id:
            doc = db.query(Document).filter(Document.id == existing_doc_id).first()
        else:
            doc = db.query(Document).filter(Document.url == url).first()

        if not doc:
            doc = repo.create_document(
                db=db,
                crawl_job_id=None,
                source_id=source_id,
                url=url,
                canonical_url=url,
                title=derived_title,
                content_type="text/html",
                http_status=item.http_status or 200,
                content_hash=html_hash,
                raw_path=raw_rel,
                markdown_path=md_rel,
                text_path=txt_rel,
                word_count=word_count,
                links_count=item.metadata.get("links_count", 0) if item.metadata else 0,
                images_count=item.metadata.get("images_count", 0) if item.metadata else 0,
            )
            doc.lifecycle_state = "CRAWLED_PENDING_AGENT_2"
            doc.raw_artifacts = raw_artifacts_list
            doc.raw_metadata = raw_metadata_payload
            db.commit()
            db.refresh(doc)
        else:
            doc.title = derived_title
            doc.http_status = item.http_status or 200
            doc.content_hash = html_hash
            doc.raw_path = raw_rel
            doc.markdown_path = md_rel
            doc.text_path = txt_rel
            doc.word_count = word_count
            doc.links_count = item.metadata.get("links_count", 0) if item.metadata else 0
            doc.images_count = item.metadata.get("images_count", 0) if item.metadata else 0
            doc.lifecycle_state = "CRAWLED_PENDING_AGENT_2"
            doc.raw_artifacts = raw_artifacts_list
            doc.raw_metadata = raw_metadata_payload
            db.commit()

        doc_id = str(doc.id)
        _log_activity(
            db, url=url, stage="CRAWL", domain=domain,
            status="OK",
            message=f"Agent 1 Crawled OK — Single-session ({len(crawled_items)} pages) recorded [CRAWLED_PENDING_AGENT_2].",
            batch_id=batch_id
        )

        logger.info(f"[Agent 1] Successfully crawled {clean_domain} in single browser session. Staging record {doc_id} created.")

        # Agent 1 -> Agent 2 handoff (respecting verification queue watermarks)
        r = get_redis()
        verif_q = r.llen("verification") if r is not None else 0
        max_verif_q = getattr(settings, "VERIFICATION_QUEUE_HIGH_WATERMARK", 50)
        if verif_q < max_verif_q:
            _safe_dispatch(agent2_process_card_task, document_id=doc_id)
            logger.info(f"[Worker B] Automatically enqueued card {doc_id} ({clean_domain}) to Agent 2 verification queue.")
        else:
            logger.info(f"[Worker B] Verification queue at high watermark ({verif_q}>={max_verif_q}). Card {doc_id} ready in PostgreSQL for Agent 2.")

        return {
            "status": "success",
            "lifecycle_state": "CRAWLED_PENDING_AGENT_2",
            "document_id": doc_id,
            "domain": clean_domain,
            "url": url,
            "pages_crawled": len(crawled_items),
            "minio_artifacts": raw_artifacts_list,
            "raw_metadata": raw_metadata_payload,
        }
    except Exception as e:
        logger.error(f"[Worker B] Save staging record failed for {url}: {e}")
        _log_crawl_error(db, url, "crawl_entity", e)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# WORKER C — Enrich, Normalize & Verify
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.enrich_and_verify",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def enrich_and_verify_task(self, universal_record_id: str) -> Dict[str, Any]:
    """
    §17, §18, §15 — Worker C: Normalize → deduplicate → verify.
    """
    logger.info(f"[Worker C] Enriching Record '{universal_record_id[:8]}'")
    db = SessionLocal()
    try:
        record = db.query(UniversalRecord).filter(
            UniversalRecord.id == universal_record_id
        ).first()
        if not record:
            return {"status": "error", "reason": "Record not found"}

        # ── 1. Normalize ──────────────────────────────────────────────────────
        raw_name = record.canonical_name or record.title or ""
        canonical_name = normalizer.normalize_string(raw_name)

        normalized_country = None
        if record.country:
            normalized_country = normalizer.normalize_country(record.country)

        normalized_url = None
        if record.url:
            normalized_url = normalizer.normalize_url(record.url)

        record.canonical_name = canonical_name or raw_name
        record.country = normalized_country or record.country
        if normalized_url:
            record.url = normalized_url

        # ── 1b. Entity Quality Filter Check ────────────────────────────────────
        keep_ent, ent_reason = quality_filter.filter_entity(
            canonical_name=record.canonical_name,
            url=record.url or "",
            confidence=float(record.confidence or 0.5)
        )
        if not keep_ent:
            record.status = "Filtered"
            db.commit()
            _log_activity(db, url=record.url or "", stage="FILTER", domain="Technology",
                          status="FILTERED", message=f"Entity verification rejected: {ent_reason}",
                          entity_name=record.canonical_name)
            return {"status": "filtered", "reason": ent_reason, "record_id": record.id}

        # ── 2. Deduplication — multi-signal ──────────────────────────────────
        # Signal 1: Exact name match
        name_dup = None
        if canonical_name:
            name_dup = (
                db.query(UniversalRecord)
                .filter(
                    UniversalRecord.id != record.id,
                    UniversalRecord.canonical_name == canonical_name,
                )
                .first()
            )

        # Signal 2: Domain match (same website, different path)
        domain_dup = None
        if record.url:
            record_domain = urlparse(record.url).netloc.lower()
            all_records = (
                db.query(UniversalRecord)
                .filter(UniversalRecord.id != record.id)
                .limit(500)
                .all()
            )
            for other in all_records:
                if other.url and urlparse(other.url).netloc.lower() == record_domain:
                    domain_dup = other
                    break

        is_duplicate = bool(name_dup or domain_dup)
        if is_duplicate:
            record.status = "Duplicate"
            db.commit()
            return {
                "status": "duplicate",
                "record_id": record.id,
                "duplicate_of": (name_dup or domain_dup).id,
            }

        # ── 2b. Strict Media/Blog Rejection ──────────────────────────────────
        desc_lower = (record.description or "").lower()
        title_lower = (record.canonical_name or "").lower()
        
        media_indicators = [
            "news portal", "latest news", "breaking news", "daily news", "read our blog", 
            "welcome to my blog", "vlog", "magazine", "journal", "publication", 
            "news agency", "recipes", "food guide", "lifestyle blog"
        ]
        is_news_or_blog = any(ind in desc_lower for ind in media_indicators) or any(ind in title_lower for ind in media_indicators)

        if is_news_or_blog:
            record.status = "Filtered"
            db.commit()
            _log_activity(db, url=record.url or "", stage="VERIFIED", domain="Media/Blog",
                          status="FILTERED", message="Entity identified as news/media/blog, rejected from B2B vault",
                          entity_name=record.canonical_name)
            return {"status": "filtered", "reason": "Entity identified as news/media/blog, not a B2B company"}

        # ── 3. Verification — confidence scoring ──────────────────────────────
        confidence = float(record.confidence or 0.5)
        score_reasons = []

        if len(canonical_name) > 3:
            confidence += 0.10
            score_reasons.append("name_ok")
        if record.url:
            confidence += 0.10
            score_reasons.append("has_url")
        if record.description and len(record.description) > 50:
            confidence += 0.10
            score_reasons.append("has_description")
        if record.country:
            confidence += 0.05
            score_reasons.append("has_country")

        # Check domain record for additional fields
        dom_rec = db.query(DomainRecord).filter(
            DomainRecord.universal_record_id == record.id
        ).first()
        if dom_rec and dom_rec.data:
            filled_fields = quality_filter.score_entity_completeness(dom_rec.data)
            confidence += filled_fields * 0.15
            score_reasons.append(f"completeness={filled_fields:.2f}")

        confidence = round(min(1.0, confidence), 4)
        is_verified = confidence >= 0.60

        record.confidence = confidence
        record.status = "Verified" if is_verified else "Discovered"
        record.updated_at = utc_now()
        db.commit()

        # Sync to Master Vault if verified
        if is_verified and record.url:
            try:
                from app.persistence.vault_service import MasterVaultService
                domain_key = urlparse(record.url).netloc.lower().lstrip("www.")
                dom_data = dom_rec.data if dom_rec and isinstance(dom_rec.data, dict) else {}
                rec_industry = (
                    dom_data.get("industry")
                    or (record.domain.name if (record.domain and hasattr(record.domain, "name")) else None)
                    or "Commercial Enterprise"
                )
                rec_size = dom_data.get("company_size") or dom_data.get("company_tier")
                MasterVaultService.persist_master_lead(
                    db=db,
                    domain=domain_key,
                    company_name=record.canonical_name,
                    technology_stack=dom_data.get("technologies") or [],
                    quality_score=confidence * 10.0,
                    headquarters=dom_data.get("headquarters") or record.location,
                    industry=rec_industry,
                    company_size=rec_size,
                    summary=record.description or f"{record.canonical_name} corporate profile.",
                    decision_makers=dom_data.get("key_people") or dom_data.get("leadership")
                )
                if not (dom_data.get("key_people") or dom_data.get("leadership")):
                    _safe_dispatch(
                        search_company_people_task,
                        company_name=record.canonical_name,
                        domain=rec_industry,
                        official_domain=domain_key
                    )
            except Exception as vault_err:
                logger.warning(f"[Worker C] Vault sync warning: {vault_err}")

        # ── 4. Save Verification Record ────────────────────────────────────────
        v_record = VerificationRecord(
            id=str(uuid.uuid4()),
            universal_record_id=record.id,
            is_verified=is_verified,
            confidence=confidence,
            verification_notes=f"Signals: {', '.join(score_reasons)}",
        )
        db.add(v_record)
        db.commit()

        _log_activity(db, url=record.url or "", stage="VERIFIED", domain=record.entity_type or "Technology",
                      status="OK" if is_verified else "UNVERIFIED",
                      message=f"Verification {'PASSED' if is_verified else 'PENDING'} — name='{record.canonical_name}' confidence={confidence:.0%}",
                      entity_name=record.canonical_name)

        return {
            "status": "success",
            "record_id": record.id,
            "canonical_name": record.canonical_name,
            "is_verified": is_verified,
            "confidence": confidence,
        }

    except Exception as e:
        logger.error(f"[Worker C] Enrichment failed for {universal_record_id}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# WORKER P — Search Company People
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.search_company_people",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def search_company_people_task(
    self,
    company_name: str,
    domain: str,
    official_domain: str = None,
    batch_id: str = None,
) -> Dict[str, Any]:
    """
    Parallel Search for key people belonging to the discovered company.
    Executes prioritized queries with early stopping once 3 strong key people are found.
    """
    logger.info(f"[Worker P] People search for: '{company_name}' (Domain: {official_domain})")
    db = SessionLocal()
    try:
        from app.agent.key_people_discovery_agent import key_people_agent
        from app.extraction.key_people_extractor import key_people_extractor
        from app.persistence.models import KeyPersonCandidate
        import re as _li_re
        
        def _is_real_linkedin_profile(url: str) -> bool:
            """Return True only for genuine linkedin.com/in/<slug> URLs."""
            if not url or not isinstance(url, str):
                return False
            m = _li_re.search(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-_]{2,})', url)
            if not m:
                return False
            slug = m.group(1).lower()
            # Reject known non-profile slugs
            bad_slugs = {"search", "jobs", "feed", "login", "signup", "home", "pub", "in", "sharing", "posts"}
            return slug not in bad_slugs
        
        from app.extraction.person_verifier import person_verifier
        ident = person_verifier.canonicalize_company_identity(url=official_domain or domain or "", title=company_name, raw_name=company_name)
        clean_company = ident["company_name"]
        site_domain = ident["canonical_domain"] or (official_domain or "").replace("www.", "").lower().strip()

        if not site_domain or clean_company.lower() in {"home", "index", "welcome", "unknown", "company"}:
            logger.info(f"[Worker P] Aborting people search for invalid/generic identity: '{clean_company}' ({site_domain})")
            return {"status": "skipped", "reason": "Invalid or generic company identity"}

        # Generate prioritized queries (cap 5)
        queries = key_people_agent.generate_queries(
            company_name=clean_company,
            official_domain=site_domain
        )
        
        all_discovered = []
        last_query = queries[0]["query"] if queries else ""
        for q_obj in queries:
            q = q_obj["query"]
            last_query = q
            try:
                results, is_fallback, log_msg = run_async(
                    searxng_service.search_with_meta(query=q, max_results=4)
                )
                if results:
                    relevant_results = key_people_agent.filter_relevant_results(
                        results,
                        official_domain=site_domain,
                        company_name=clean_company
                    )
                    batch_people = key_people_extractor.extract_from_linkedin_search_snippets(
                        relevant_results,
                        clean_company,
                        official_domain=site_domain
                    )
                    for bp in batch_people:
                        if not any(dp["name"].lower() == bp["name"].lower() for dp in all_discovered):
                            all_discovered.append(bp)
                    
                    # Early stopping rule: Stop additional people searches once 3 strong people found!
                    if len(all_discovered) >= key_people_agent.EARLY_STOP_PEOPLE_COUNT:
                        logger.info(f"[Worker P] Reached stopping target ({len(all_discovered)} people) for '{clean_company}' — stopping further queries.")
                        break
            except Exception as e:
                logger.warning(f"[Worker P] SearXNG error on query '{q}': {e}")

        if not all_discovered:
            return {"status": "no_results", "company_name": clean_company}

        saved_count = 0
        for p in all_discovered:
            existing = db.query(KeyPersonCandidate).filter(
                KeyPersonCandidate.source_domain == site_domain,
                KeyPersonCandidate.person_name == p["name"]
            ).first()
            
            p_profile = p.get("linkedin_url")
            if p_profile and not _is_real_linkedin_profile(p_profile):
                p_profile = None
            
            # Genuine profile URL only — never store a search or query URL
            valid_profile_url = p_profile if p_profile else None
            cand_status = p.get("match_status", "VERIFIED" if valid_profile_url else "HIGH_CONFIDENCE")
            cand_score = float(p.get("confidence_score", 0.95))
            
            if not existing:
                cand = KeyPersonCandidate(
                    company_name=clean_company,
                    person_name=p["name"],
                    role=p["title"],
                    source_url=valid_profile_url, # Genuine profile URL or None, NEVER a search query!
                    source_domain=site_domain,
                    source_type=p.get("source_type", "linkedin_profile" if valid_profile_url else "web_search_snippet"),
                    discovery_query=last_query,
                    evidence_text=p.get("evidence", ""),
                    confidence_score=cand_score,
                    verification_status=cand_status
                )
                db.add(cand)
                saved_count += 1
                
                _log_activity(db, url=valid_profile_url or f"PERSON:{p['name']}", stage="SEARCH", domain=domain,
                              status="OK", message=f"Discovered Key Person: {p['name']} ({p['title']}) [Evidence: {p.get('source_type')}]",
                              entity_name=clean_company, batch_id=batch_id)
            else:
                if valid_profile_url and not existing.source_url:
                    existing.source_url = valid_profile_url
                    existing.verification_status = "HIGH_CONFIDENCE"
                    saved_count += 1

        # Backfill discovered key people into DomainRecord and GlobalLead for immediate dashboard display
        if all_discovered and site_domain:
            try:
                univs = db.query(UniversalRecord).filter(UniversalRecord.url.ilike(f"%{site_domain}%")).all()
                for u in univs:
                    dom_rec = db.query(DomainRecord).filter(DomainRecord.universal_record_id == u.id).first()
                    if dom_rec:
                        d_data = dict(dom_rec.data or {})
                        kp_list = list(d_data.get("key_people") or [])
                        existing_k_names = {kp.get("name", "").lower() for kp in kp_list if isinstance(kp, dict)}
                        for p in all_discovered:
                            p_url = p.get("linkedin_url")
                            if p["name"].lower() not in existing_k_names:
                                kp_list.append({
                                    "name": p["name"],
                                    "title": p["title"],
                                    "linkedin_url": p_url,
                                    "linkedin_search_url": p_url
                                })
                                existing_k_names.add(p["name"].lower())
                        d_data["key_people"] = kp_list
                        dom_rec.data = d_data
                
                company = db.query(Company).filter(Company.primary_domain == site_domain).first()
                if company:
                    existing_kp_names = {kp.full_name.lower() for kp in db.query(KeyPerson).filter(KeyPerson.company_id == company.id).all()}
                    for p in all_discovered:
                        if p["name"].lower() not in existing_kp_names:
                            p_url = p.get("linkedin_url")
                            db.add(KeyPerson(
                                company_id=company.id,
                                full_name=p["name"],
                                title=p["title"],
                                linkedin_url=p_url,
                                linkedin_search_url=p_url,
                                verification_status="DISCOVERED"
                            ))
                            existing_kp_names.add(p["name"].lower())
            except Exception as sync_err:
                logger.debug(f"[Worker P] Key people backfill sync notice: {sync_err}")
        
        db.commit()
        return {"status": "success", "company_name": company_name, "people_found": saved_count}
        
    except Exception as e:
        logger.error(f"[Worker P] People search failed for '{company_name}': {e}")
        try:
            raise self.retry(exc=e)
        except Exception:
            return {"error": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 2 CELERY TASKS — Autonomous Verification & Evidence Pipeline
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="tasks.agent2_rank_cards", bind=True)
def agent2_rank_cards_task(self) -> Dict[str, Any]:
    """Ranks all pending CRAWLED_PENDING_AGENT_2 cards."""
    from app.agent.agent2_orchestrator import agent2_orchestrator
    db = SessionLocal()
    try:
        pending_docs = db.query(Document).filter(
            Document.lifecycle_state == "CRAWLED_PENDING_AGENT_2"
        ).all()
        ranked = []
        for doc in pending_docs:
            session = agent2_orchestrator.get_or_create_session(doc.id, db)
            if session:
                score = agent2_orchestrator.rank_card(session, db)
                ranked.append({"document_id": doc.id, "domain": session.domain, "priority_score": score})
        return {"status": "success", "ranked_count": len(ranked), "results": ranked}
    finally:
        db.close()


@celery_app.task(name="tasks.agent2_process_card", bind=True)
def agent2_process_card_task(self, document_id: str, trace_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runs end-to-end Agent 2 verification workflow for a specific card."""
    from app.agent.agent2_orchestrator import agent2_orchestrator
    from app.audit.tracer import tracer, Checkpoint
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-02", task_id=getattr(self.request, "id", None))
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP12_AGENT2_INIT,
        event="AGENT2_TASK_STARTED",
        message=f"Agent 2 worker received verification task for document {document_id}",
        agent_id="AGENT-02",
        status="STARTED"
    )
    return run_async(agent2_orchestrator.execute_full_verification(document_id))


@celery_app.task(name="tasks.agent2_verify_phase1", bind=True)
def agent2_verify_phase1_task(self, session_id: str, trace_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Executes Phase 1 evidence verification for an Agent 2 session."""
    from app.agent.agent2_orchestrator import agent2_orchestrator
    from app.persistence.models import VerificationSession
    from app.audit.tracer import tracer
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-02", task_id=getattr(self.request, "id", None))
    db = SessionLocal()
    try:
        session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
        if not session:
            return {"status": "error", "error": "Session not found"}
        return run_async(agent2_orchestrator.verify_phase1(session, db))
    finally:
        db.close()


@celery_app.task(name="tasks.agent2_synthesize_business", bind=True)
def agent2_synthesize_business_task(self, session_id: str, trace_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runs Phase 2 Haystack business synthesis."""
    from app.agent.agent2_orchestrator import agent2_orchestrator
    from app.persistence.models import VerificationSession
    from app.audit.tracer import tracer
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-02", task_id=getattr(self.request, "id", None))
    db = SessionLocal()
    try:
        session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
        if not session:
            return {"status": "error", "error": "Session not found"}
        return run_async(agent2_orchestrator.synthesize_business(session, db))
    finally:
        db.close()


@celery_app.task(name="tasks.agent2_search_linkedin", bind=True)
def agent2_search_linkedin_task(self, session_id: str, trace_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runs Phase 2 LinkedIn key person discovery."""
    from app.agent.agent2_orchestrator import agent2_orchestrator
    from app.persistence.models import VerificationSession
    from app.audit.tracer import tracer
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-02", task_id=getattr(self.request, "id", None))
    db = SessionLocal()
    try:
        session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
        if not session:
            return {"status": "error", "error": "Session not found"}
        return run_async(agent2_orchestrator.discover_and_verify_linkedin(session, db))
    finally:
        db.close()


@celery_app.task(name="tasks.agent2_finalize_verification", bind=True)
def agent2_finalize_verification_task(self, session_id: str, trace_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Finalizes verification decision and triggers PostgreSQL Outbox."""
    from app.agent.agent2_orchestrator import agent2_orchestrator
    from app.persistence.models import VerificationSession
    from app.audit.tracer import tracer
    if trace_ctx:
        tracer.restore_context_dict(trace_ctx)
    tracer.set_context(agent_id="AGENT-02", task_id=getattr(self.request, "id", None))
    db = SessionLocal()
    try:
        session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
        if not session:
            return {"status": "error", "error": "Session not found"}
        return run_async(agent2_orchestrator.finalize_verification_and_sync(session, db))
    finally:
        db.close()


@celery_app.task(name="tasks.agent2_sync_postgres", bind=True)
def agent2_sync_postgres_task(self, limit: int = 10) -> Dict[str, Any]:
    """Flushes SQLite outbox entries to PostgreSQL Lake."""
    from app.persistence.outbox_sync_service import outbox_sync_service
    db = SessionLocal()
    try:
        return outbox_sync_service.process_outbox_queue(db=db, limit=limit)
    finally:
        db.close()

