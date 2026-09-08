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

from app.worker.celery_app import celery_app
from app.persistence.database import SessionLocal
from app.persistence.repositories import repo
from app.persistence.models import (
    SearchHistory, Document, UniversalRecord, DomainRecord,
    VerificationRecord, ExtractedFact, BatchResult, CrawlError,
    CrawlActivityLog, SearchCandidate, Company, PostgresSyncOutbox, utc_now,
)

from app.crawler.searxng_service import searxng_service
from app.crawler.crawler_service import crawler_service
from app.crawler.listing_detector import listing_detector
from app.crawler.quality_filter import quality_filter
from app.crawler.candidate_classifier import candidate_classifier
from app.storage.file_storage import file_storage
from app.extraction.extractor import extraction_pipeline
from app.normalization.normalizer import normalizer

logger = logging.getLogger(__name__)


def run_async(coro):
    """Run async coroutine safely across platforms without 'Event loop is closed' errors."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()


_worker_check_cache = {"active": False, "last_check": 0}

def _has_active_celery_worker() -> bool:
    import time
    now = time.time()
    if now - _worker_check_cache["last_check"] < 5:
        return _worker_check_cache["active"]

    try:
        from app.cache.redis_client import get_redis
        if get_redis() is None:
            _worker_check_cache["active"] = False
            _worker_check_cache["last_check"] = now
            return False

        inspector = celery_app.control.inspect(timeout=0.25)
        res = inspector.ping()
        is_active = bool(res and len(res) > 0)
        _worker_check_cache["active"] = is_active
        _worker_check_cache["last_check"] = now
        return is_active
    except Exception:
        _worker_check_cache["active"] = False
        _worker_check_cache["last_check"] = now
        return False


def _safe_dispatch(task_func, **kwargs):
    """
    Safely dispatch task.
    Skips dispatch if agent is PAUSED.
    """
    try:
        from app.agent.discovery_agent import discovery_agent
        db = SessionLocal()
        state = discovery_agent._get_or_create_state(db)
        is_paused = (state.status != "RUNNING")
        db.close()
        if is_paused:
            logger.info(f"[Safe Dispatch] Agent is PAUSED — skipping task '{task_func.name}'")
            return
    except Exception:
        pass

    dispatched_to_celery = False
    if _has_active_celery_worker():
        try:
            task_func.apply_async(kwargs=kwargs, queue="celery")
            dispatched_to_celery = True
            logger.info(f"[Safe Dispatch] Enqueued task '{task_func.name}' to Redis Celery queue.")
        except Exception as e:
            logger.debug(f"[Safe Dispatch] Celery queue push skipped: {e}")

    if not dispatched_to_celery:
        def _run_bg():
            try:
                task_func(**kwargs)
            except Exception as err:
                logger.error(f"[Safe Dispatch] Background task execution failed: {err}")

        threading.Thread(target=_run_bg, daemon=True).start()




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
# WORKER A — Search & Discover (Candidate First + Multi-Gate Filter)
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
) -> Dict[str, Any]:
    """
    Worker A: Search SearXNG → Save search candidates → Gate 1 classification + Dedup
    → Stage 1 Homepage Crawl + Gate 2 Qualification.
    Search engine outputs are stored strictly as SearchCandidates first.
    """
    if not query:
        query = f"{domain or ''} {keyword or ''}".strip()
    if not keyword:
        keyword = query

    logger.info(f"[Worker A] Search Discovery: '{query}' | domain='{domain}' batch={batch_id}")

    db = SessionLocal()
    try:
        search_results, is_fallback, log_msg = run_async(
            searxng_service.search_initial_with_metadata_targets(query=query, max_results=20)
        )

        fallback_tag = " [FALLBACK]" if is_fallback else ""
        _log_activity(db, url=f"QUERY: {query}", stage="SEARCH", domain=domain,
                      status="OK" if search_results else "EMPTY",
                      message=f"{log_msg}{fallback_tag} → {len(search_results)} URLs discovered",
                      batch_id=batch_id)

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

        enqueued_candidates = 0
        from app.events.event_bus import publish_pipeline_event, EventType

        for res in search_results:
            target_url = res.get("url")
            if not target_url or not target_url.startswith("http"):
                continue

            title = res.get("title", "")
            snippet = res.get("snippet", "")

            # 1. Save raw search result into SQLite search_candidates (SEARCH_CANDIDATE)
            candidate = SearchCandidate(
                id=str(uuid.uuid4()),
                search_query=query,
                raw_url=target_url,
                title=title,
                snippet=snippet,
                batch_id=batch_id,
                status="SEARCH_CANDIDATE"
            )
            db.add(candidate)
            db.commit()

            # 2. Gate 1 — Source Classification & Domain Blocklist
            cat, cat_reason, is_allowed = candidate_classifier.classify(target_url, title=title, snippet=snippet)

            if not is_allowed:
                candidate.status = "REJECTED"
                candidate.source_category = cat
                candidate.rejection_reason = f"Gate 1 ({cat}): {cat_reason}"
                candidate.gate1_passed = False
                db.commit()

                _log_activity(db, url=target_url, stage="GATE1_FILTER", domain=domain,
                              status="REJECTED", message=f"Gate 1 Rejected ({cat}): {cat_reason}", batch_id=batch_id)
                publish_pipeline_event(EventType.URL_REJECTED, {"category": cat, "reason": cat_reason}, entity_url=target_url, domain=domain)
                continue

            candidate.gate1_passed = True
            candidate.source_category = cat

            # 3. Canonical Domain Normalization
            canonical_domain = normalizer.extract_canonical_root_domain(target_url)
            candidate.canonical_domain = canonical_domain

            # 4. Redis + SQLite Domain Deduplication Check
            existing_company = db.query(Company).filter(Company.canonical_domain == canonical_domain).first()
            if existing_company:
                candidate.status = "DEDUPLICATED"
                candidate.rejection_reason = f"Domain {canonical_domain} already registered in Company Lake"
                db.commit()
                _log_activity(db, url=target_url, stage="FILTER", domain=domain,
                              status="DEDUPLICATED", message=f"Duplicate domain: {canonical_domain}", batch_id=batch_id)
                continue

            existing_candidate = db.query(SearchCandidate).filter(
                SearchCandidate.id != candidate.id,
                SearchCandidate.canonical_domain == canonical_domain,
                SearchCandidate.status.in_(["QUALIFIED", "STAGE1_CRAWLED", "ALLOWED"])
            ).first()

            if existing_candidate:
                candidate.status = "DEDUPLICATED"
                candidate.rejection_reason = f"Domain {canonical_domain} already in candidate pipeline"
                db.commit()
                continue

            candidate.status = "ALLOWED"
            db.commit()

            publish_pipeline_event(EventType.URL_CLASSIFIED, {"category": cat, "title": title}, entity_url=target_url, domain=domain)

            # 5. Dispatch Stage 1 Homepage Crawl & Gate 2 Qualification
            _safe_dispatch(process_candidate_qualification_task, candidate_id=candidate.id, domain=domain, batch_id=batch_id)
            enqueued_candidates += 1

        return {
            "query": query,
            "keyword": keyword,
            "sources_found": len(search_results),
            "enqueued_crawls": enqueued_candidates,
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


@celery_app.task(
    name="tasks.process_candidate_qualification",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_candidate_qualification_task(
    self,
    candidate_id: str,
    domain: str = None,
    batch_id: str = None,
) -> Dict[str, Any]:
    """
    Stage 1 Lightweight Crawl (Homepage only) + Gate 2 Company Qualification Engine.
    Only candidates scoring >= 60 are inserted into SQLite `companies` table and `postgres_sync_outbox`.
    """
    db = SessionLocal()
    try:
        candidate = db.query(SearchCandidate).filter(SearchCandidate.id == candidate_id).first()
        if not candidate:
            return {"status": "error", "message": "Candidate not found"}

        canonical_domain = candidate.canonical_domain or normalizer.extract_canonical_root_domain(candidate.raw_url)
        homepage_url = f"https://{canonical_domain}" if not canonical_domain.startswith("http") else canonical_domain

        # Stage 1: Lightweight Homepage Crawl
        crawled_items = run_async(
            crawler_service.crawl_site(starting_url=homepage_url, max_depth=1, max_pages=1)
        )

        html_text = ""
        page_title = candidate.title or ""
        meta_desc = candidate.snippet or ""

        if crawled_items and len(crawled_items) > 0:
            item = crawled_items[0]
            html_text = item.text or item.html_content or ""
            page_title = item.title or page_title

        # Gate 2: Deterministic Signals & Company Confidence Score
        from app.crawler.company_qualification_engine import qualification_engine
        score, qual_status, meta = qualification_engine.evaluate(
            domain=canonical_domain,
            html_text=html_text,
            title=page_title,
            meta_desc=meta_desc
        )

        candidate.confidence_score = score

        if qual_status == "REJECTED":
            candidate.status = "REJECTED"
            candidate.rejection_reason = f"Gate 2 Qualification Score ({score}/100) below threshold. Reasons: {', '.join(meta.get('reasons', []))}"
            db.commit()
            _log_activity(db, url=homepage_url, stage="GATE2_QUALIFICATION", domain=domain,
                          status="REJECTED", message=f"Gate 2 Rejected ({score}/100) — {canonical_domain}", batch_id=batch_id)
            return {"status": "rejected", "score": score, "domain": canonical_domain}

        elif qual_status == "LLM_REVIEW":
            candidate.status = "LLM_REVIEW"
            candidate.rejection_reason = f"Gate 2 Score ({score}/100) requires review"
            db.commit()
            _log_activity(db, url=homepage_url, stage="GATE2_QUALIFICATION", domain=domain,
                          status="LLM_REVIEW", message=f"Gate 2 LLM Review Needed ({score}/100) — {canonical_domain}", batch_id=batch_id)
            return {"status": "llm_review", "score": score, "domain": canonical_domain}

        # Gate 2 Passed -> QUALIFIED_COMPANY!
        candidate.status = "QUALIFIED"
        db.commit()

        # Save to SQLite `companies` table (Operational Truth)
        company = db.query(Company).filter(Company.canonical_domain == canonical_domain).first()
        if not company:
            company = Company(
                id=str(uuid.uuid4()),
                canonical_domain=canonical_domain,
                company_name=meta["company_name"],
                company_type=meta["company_type"],
                industry=domain or "Technology & Business Services",
                official_url=homepage_url,
                status="QUALIFIED_COMPANY",
                company_confidence_score=score,
                qualification_reasons=meta.get("reasons", []),
                meta_info={"raw_candidate_url": candidate.raw_url}
            )
            db.add(company)
        else:
            company.company_confidence_score = max(company.company_confidence_score, score)
            company.status = "QUALIFIED_COMPANY"
        db.commit()

        # Insert into `postgres_sync_outbox` for asynchronous sync to PostgreSQL
        outbox_entry = PostgresSyncOutbox(
            id=str(uuid.uuid4()),
            entity_type="COMPANY",
            entity_id=company.id,
            action="UPSERT",
            payload={
                "id": company.id,
                "domain": company.canonical_domain,
                "company_name": company.company_name,
                "company_type": company.company_type,
                "industry": company.industry,
                "official_url": company.official_url,
                "quality_score": company.company_confidence_score,
                "qualification_reasons": company.qualification_reasons,
                "status": company.status,
                "updated_at": company.updated_at.isoformat() if company.updated_at else None
            },
            sync_status="PENDING"
        )
        db.add(outbox_entry)
        db.commit()

        _log_activity(db, url=homepage_url, stage="QUALIFIED_COMPANY", domain=domain,
                      status="QUALIFIED", message=f"Company Qualified ({score}/100): {company.company_name} [{canonical_domain}]",
                      entity_name=company.company_name, batch_id=batch_id)

        # Stage 2: Deep Crawl subpages (/about, /contact, /team)
        _safe_dispatch(crawl_entity_task, url=homepage_url, domain=domain, batch_id=batch_id)

        return {"status": "qualified", "company_id": company.id, "domain": canonical_domain, "score": score}

    except Exception as e:
        logger.error(f"[Qualification Engine] Error processing candidate {candidate_id}: {e}")
        return {"status": "error", "error": str(e)}
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
) -> Dict[str, Any]:
    """
    §11 — Crawl a listing/directory page and extract individual entity URLs.
    Each discovered entity URL is enqueued as a separate crawl_entity_task.
    """
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

# Subpages to crawl for maximum field extraction (§12)
ENTITY_SUBPAGES = ["/about", "/about-us", "/contact", "/team", "/products", "/services"]
MAX_ENTITY_PAGES = 6


@celery_app.task(
    name="tasks.crawl_entity",
    bind=True,
    max_retries=3,
    default_retry_delay=45,
)
def crawl_entity_task(
    self,
    url: str,
    domain: str,
    batch_id: str = None,
) -> Dict[str, Any]:
    """
    §12, §13 — Worker B: Deep entity crawl + extraction.
    Crawls homepage + up to 5 subpages (/about, /contact, /team, /products, /services)
    for maximum field extraction.
    """
    logger.info(f"[Worker B] Entity crawl: {url}")

    # ── Stage 0: Immediately persist Document Record ──────────────────────────
    db = SessionLocal()
    try:
        base_host = urlparse(url).netloc or url
        source = repo.get_or_create_source(db, name=base_host, base_url=url)
        doc = db.query(Document).filter(Document.url == url).first()
        if not doc:
            netloc = base_host.replace("www.", "")
            derived_title = netloc.split(".")[0].replace("-", " ").title() if "." in netloc else netloc
            if not derived_title or len(derived_title) < 2:
                derived_title = "Enterprise Lead"
            doc = repo.create_document(
                db=db,
                crawl_job_id=None,
                source_id=source.id,
                url=url,
                canonical_url=url,
                title=f"{derived_title} Official Portal",
                content_type="text/html",
                http_status=200,
                content_hash=str(uuid.uuid4())[:16],
                raw_path="",
                markdown_path="",
                text_path="",
                word_count=0,
                links_count=0,
                images_count=0,
            )
            db.commit()
            db.refresh(doc)
        doc_id = doc.id
    except Exception as err:
        logger.error(f"[Worker B] Stage 0 error for {url}: {err}")
        return {"status": "error", "error": str(err)}
    finally:
        db.close()

    # ── Stage 1: Crawl homepage (outside DB transaction) ─────────────────────
    try:
        crawled_items = run_async(
            crawler_service.crawl_site(
                starting_url=url,
                max_depth=2,
                max_pages=MAX_ENTITY_PAGES,
            )
        )
    except Exception as crawl_err:
        logger.error(f"[Worker B] Homepage crawl failed for {url}: {crawl_err}")
        return {"status": "error", "error": str(crawl_err)}

    if not crawled_items:
        db = SessionLocal()
        try:
            _log_activity(db, url=url, stage="CRAWL", domain=domain,
                          status="OK", message=f"Queued / Persisted Document ID={doc_id[:8]}",
                          batch_id=batch_id)
        finally:
            db.close()
        return {"status": "persisted", "reason": "Basic document created", "url": url, "document_id": doc_id}

    item = crawled_items[0]
    word_count = item.metadata.get("word_count", 0) if item.metadata else 0

    # ── Stage 2: Update Document Record ──────────────────────────────────────
    html_hash, raw_rel = file_storage.save_raw_page(item.html_content or "")
    md_rel = file_storage.save_processed_markdown(item.markdown or "", html_hash)
    txt_rel = file_storage.save_processed_text(item.text or "", html_hash)

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.title = item.title or doc.title
            doc.http_status = item.http_status or 200
            doc.content_hash = html_hash
            doc.raw_path = raw_rel
            doc.markdown_path = md_rel
            doc.text_path = txt_rel
            doc.word_count = word_count
            doc.links_count = item.metadata.get("links_count", 0) if item.metadata else 0
            doc.images_count = item.metadata.get("images_count", 0) if item.metadata else 0
            db.commit()

        _log_activity(db, url=url, stage="CRAWL", domain=domain,
                      status="OK", message=f"Crawled OK — Updated Document ID={doc_id[:8]} ({word_count} words)",
                      batch_id=batch_id)

        # ── Stage 3: Content quality filter ──────────────────────────────────
        keep, reason = quality_filter.filter_content(
            url=url,
            html_content=item.html_content or "",
            text_content=item.text or "",
            title=item.title or "",
            word_count=word_count,
        )
        if not keep:
            logger.info(f"[Worker B] Content filtered ({reason}): {url}")
            _log_activity(db, url=url, stage="FILTER", domain=domain,
                          status="FILTERED", message=f"Content rejected for verification: {reason}", batch_id=batch_id)
            return {"status": "filtered", "reason": reason, "url": url}
    finally:
        db.close()

    # ── Stage 4: Also crawl subpages (outside DB transaction) ────────────────
    base = urlparse(url)
    base_url = f"{base.scheme}://{base.netloc}"
    additional_html = []
    for subpath in ENTITY_SUBPAGES:
        subpage_url = f"{base_url}{subpath}"
        try:
            sub_items = run_async(
                crawler_service.crawl_site(
                    starting_url=subpage_url, max_depth=1, max_pages=1
                )
            )
            if sub_items and sub_items[0].text:
                additional_html.append(sub_items[0].text)
        except Exception:
            pass  # Subpage failures are non-fatal

    enriched_text = item.text or ""
    if additional_html:
        enriched_text += "\n\n" + "\n\n".join(additional_html)
        enriched_text = enriched_text[:50000]

    # ── Stage 5: Extract Information via LLM (outside DB transaction) ───────
    try:
        payload = run_async(
            extraction_pipeline.process_document_extraction(
                document_id=doc_id,
                url=item.url,
                html_content=item.html_content or "",
                text_content=enriched_text,
                user_domain=domain,
            )
        )
    except Exception as ext_err:
        logger.error(f"[Worker B] Extraction failed for {url}: {ext_err}")
        return {"status": "error", "error": str(ext_err)}

    # Save JSON extraction payload to MinIO
    file_storage.save_extracted_json(doc_id, payload)

    # ── Stage 6: Persist Extracted Record to Database ────────────────────────
    db = SessionLocal()
    try:
        _log_activity(db, url=url, stage="EXTRACT", domain=domain,
                      status="OK", message=f"Extracted JSON payload generated for document ID={doc_id[:8]}",
                      entity_name=(payload.get("universal") or {}).get("canonical_name"), batch_id=batch_id)

        entity_name = (
            (payload.get("universal") or {}).get("canonical_name", "")
            or (payload.get("domain_data") or {}).get("company_name", "")
            or item.title
            or ""
        )
        entity_confidence = float(
            (payload.get("universal") or {}).get("confidence", 0.5) or 0.5
        )
        entity_url = item.url

        keep_entity, entity_reason = quality_filter.filter_entity(
            canonical_name=entity_name,
            url=entity_url,
            confidence=entity_confidence,
        )
        if not keep_entity:
            logger.info(f"[Worker B] Entity filtered ({entity_reason}): {entity_name}")
            _log_activity(db, url=url, stage="FILTER", domain=domain,
                          status="FILTERED", message=f"Entity rejected: {entity_reason} | name='{entity_name}'",
                          entity_name=entity_name, batch_id=batch_id)
            return {"status": "entity_filtered", "reason": entity_reason, "url": url}

        domain_key = urlparse(entity_url).netloc.lower()
        existing = db.query(UniversalRecord).filter(
            UniversalRecord.url.ilike(f"%{domain_key}%")
        ).first()
        if existing:
            logger.info(f"[Worker B] Duplicate domain {domain_key} — skipping")
            _log_activity(db, url=url, stage="FILTER", domain=domain,
                          status="DUPLICATE", message=f"Domain already indexed: {domain_key}",
                          entity_name=entity_name, batch_id=batch_id)
            return {"status": "duplicate_domain", "url": url, "existing_id": existing.id}

        repo.save_extraction_results(db, payload)

        # Enterprise Master Vault Integration (Redis L1, SQLite WAL Vault, MinIO L3, Postgres Open Lake)
        try:
            from app.persistence.vault_service import MasterVaultService
            from app.extraction.key_people_extractor import key_people_extractor
            dom_data = payload.get("domain_data") or {}
            univ_data = payload.get("universal") or {}
            subpages_list = [
                {"url": url, "markdown": item.markdown or item.text or ""}
            ]
            d_makers = dom_data.get("key_people") or dom_data.get("leadership")
            if not d_makers:
                d_makers = key_people_extractor.extract_from_text_and_html(
                    enriched_text, item.html_content or "", entity_name, domain_key
                )
            MasterVaultService.persist_master_lead(
                db=db,
                domain=domain_key,
                company_name=entity_name,
                logo_url=dom_data.get("logo_url"),
                technology_stack=dom_data.get("technologies") or dom_data.get("tech_stack"),
                quality_score=entity_confidence * 10.0,
                headquarters=dom_data.get("headquarters") or dom_data.get("location"),
                industry=dom_data.get("industry"),
                company_size=dom_data.get("company_size"),
                revenue_funding=dom_data.get("funding_stage") or dom_data.get("revenue"),
                verified_emails=dom_data.get("contact_emails") or dom_data.get("emails"),
                summary=univ_data.get("description") or dom_data.get("business_overview"),
                decision_makers=d_makers,
                crawled_subpages=subpages_list
            )
        except Exception as vault_err:
            logger.warning(f"[Worker B] MasterVault persistence notice for {domain_key}: {vault_err}")

        univ_rec = db.query(UniversalRecord).filter(
            UniversalRecord.document_id == doc_id
        ).first()

        if univ_rec:
            from app.persistence.models import RecordState
            from app.events.event_bus import publish_pipeline_event, EventType
            univ_rec.status = RecordState.RAW_INGESTED
            db.commit()

            _log_activity(db, url=url, stage="RAW_INGESTED", domain=domain,
                          status="OK",
                          message=f"Raw company record ingested into SQLite Staging DB — name='{entity_name}' ID={univ_rec.id[:8]}",
                          entity_name=entity_name, batch_id=batch_id)
            publish_pipeline_event(EventType.RAW_INGESTED, {"record_id": univ_rec.id, "entity_name": entity_name}, entity_url=url, domain=domain)

        return {
            "status": "success",
            "document_id": doc_id,
            "universal_record_id": univ_rec.id if univ_rec else None,
            "entity_name": entity_name,
            "subpages_crawled": len(additional_html),
        }
    except Exception as e:
        logger.error(f"[Worker B] Save extraction failed for {url}: {e}")
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

        # ── 3. Dedicated Playwright & SearXNG Verification Pipeline ─────────
        domain_key = urlparse(record.url).netloc.lower().replace("www.", "") if record.url else record.canonical_name.lower()
        
        # Playwright Live Browser Verification Check
        from app.verifier.playwright_verifier import playwright_verifier
        pw_result = playwright_verifier.verify_url(record.url)

        verification_data = run_async(
            searxng_service.verify_and_enrich_with_searxng(
                company_name=record.canonical_name,
                domain=domain_key
            )
        )

        confidence = float(record.confidence or 0.5)
        score_reasons = []

        if pw_result.get("is_verified"):
            confidence += 0.25
            score_reasons.append("playwright_live_site_verified")
            if pw_result.get("extracted_emails"):
                verification_data.setdefault("verified_emails", []).extend(pw_result["extracted_emails"])
        elif pw_result.get("reason"):
            score_reasons.append(f"playwright_notice='{pw_result.get('reason')}'")

        if len(canonical_name) > 3:
            confidence += 0.05
            score_reasons.append("name_ok")
        if record.url:
            confidence += 0.05
            score_reasons.append("has_url")

        # SearXNG Verification Cross-Check Signals
        if verification_data.get("verified_hq"):
            record.location = verification_data["verified_hq"]
            confidence += 0.20
            score_reasons.append(f"searxng_hq_verified='{verification_data['verified_hq']}'")

        if verification_data.get("verified_emails"):
            confidence += 0.15
            score_reasons.append(f"searxng_emails_verified={len(verification_data['verified_emails'])}")

        if verification_data.get("verified_people"):
            confidence += 0.20
            score_reasons.append(f"searxng_linkedin_people_verified={len(verification_data['verified_people'])}")

        # Check domain record for additional fields
        dom_rec = db.query(DomainRecord).filter(
            DomainRecord.universal_record_id == record.id
        ).first()
        if dom_rec and dom_rec.data:
            filled_fields = quality_filter.score_entity_completeness(dom_rec.data)
            confidence += filled_fields * 0.15
            score_reasons.append(f"completeness={filled_fields:.2f}")

        confidence = round(min(1.0, confidence), 4)
        is_verified = bool(verification_data.get("is_verified") and confidence >= 0.55)

        from app.persistence.models import RecordState
        from app.events.event_bus import publish_pipeline_event, EventType
        from app.worker.sync_worker import sync_verified_records_to_postgres

        record.confidence = confidence
        if is_verified:
            record.status = RecordState.VERIFIED
            record.postgres_sync_status = "PENDING"
            publish_pipeline_event(EventType.PLAYWRIGHT_VERIFIED, {"confidence": confidence}, entity_url=record.url)
            publish_pipeline_event(EventType.EXTRACTION_COMPLETED, {"company": record.canonical_name}, entity_url=record.url)
            publish_pipeline_event(EventType.VALIDATION_COMPLETED, {"company": record.canonical_name}, entity_url=record.url)
            publish_pipeline_event(EventType.COMPANY_VERIFIED, {"company": record.canonical_name, "confidence": confidence}, entity_url=record.url)
        else:
            record.status = RecordState.INVALID
            publish_pipeline_event(EventType.COMPANY_REJECTED, {"reason": "Verification score below threshold", "confidence": confidence}, entity_url=record.url)
            
        db.commit()

        # Trigger PostgreSQL Atomic Sync Worker
        if is_verified:
            try:
                sync_res = sync_verified_records_to_postgres()
                logger.info(f"[Worker C] PostgreSQL sync result: {sync_res}")
            except Exception as sync_err:
                logger.warning(f"[Worker C] PostgreSQL sync notice: {sync_err}")

        # Sync to Master Vault if verified
        if is_verified and record.url:
            try:
                from app.persistence.vault_service import MasterVaultService
                dom_data = dom_rec.data if dom_rec and isinstance(dom_rec.data, dict) else {}
                d_makers = dom_data.get("key_people") or dom_data.get("leadership") or verification_data.get("verified_people")
                v_emails = dom_data.get("contact_emails") or verification_data.get("verified_emails")
                MasterVaultService.persist_master_lead(
                    db=db,
                    domain=domain_key,
                    company_name=record.canonical_name,
                    technology_stack=dom_data.get("technologies") or [],
                    quality_score=confidence * 10.0,
                    headquarters=verification_data.get("verified_hq") or dom_data.get("headquarters") or record.location,
                    industry=record.domain.name if (record.domain and hasattr(record.domain, "name")) else "Technology",
                    verified_emails=v_emails,
                    summary=record.description or f"{record.canonical_name} verified enterprise record.",
                    decision_makers=d_makers
                )
            except Exception as vault_err:
                logger.warning(f"[Worker C] Vault sync warning: {vault_err}")

        # ── 4. Save Verification Record ────────────────────────────────────────
        v_record = VerificationRecord(
            id=str(uuid.uuid4()),
            universal_record_id=record.id,
            is_verified=is_verified,
            confidence=confidence,
            verification_notes=f"SearXNG Verification Signals: {', '.join(score_reasons)}",
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
        try:
            raise self.retry(exc=e)
        except Exception:
            return {"status": "error", "error": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def adaptive_verification_loop_task(self, company_id: str, domain: str) -> Dict[str, Any]:
    """
    Agentic Data Completeness Verification & Bounded Re-Crawl Task.
    Runs multi-round verification (MAX_ROUNDS=3) for missing fields.
    """
    from app.persistence.database import staging_engine
    from sqlalchemy.orm import sessionmaker
    SessionLocalStaging = sessionmaker(autocommit=False, autoflush=False, bind=staging_engine)
    db = SessionLocalStaging()
    try:
        from app.crawler.agentic_verifier import agentic_verifier
        dossier = agentic_verifier.execute_agentic_verification(company_id, domain, db)
        return {
            "status": "success",
            "company_id": company_id,
            "domain": domain,
            "data_completeness": dossier.get("data_completeness")
        }
    except Exception as e:
        logger.error(f"[Agentic Verifier Task] Verification failed for {domain}: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()

