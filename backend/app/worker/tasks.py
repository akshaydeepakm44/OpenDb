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
    CrawlActivityLog, utc_now,
)

from app.crawler.searxng_service import searxng_service
from app.crawler.crawler_service import crawler_service
from app.crawler.listing_detector import listing_detector
from app.crawler.quality_filter import quality_filter
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
    Attempts direct Celery enqueueing to the Redis queue.
    If Redis or Celery enqueueing fails, falls back to a background daemon thread.
    """
    dispatched = False
    try:
        task_func.apply_async(kwargs=kwargs, queue="celery")
        dispatched = True
        logger.info(f"[Safe Dispatch] Enqueued task '{task_func.name}' to Redis Celery queue.")
    except Exception as e:
        logger.debug(f"[Safe Dispatch] Celery queue push unavailable ({e}), falling back to background thread.")

    if not dispatched:
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
) -> Dict[str, Any]:
    """
    §11 — Worker A: Search SearXNG → classify results → enqueue entity crawls.
    Each SearXNG URL is classified as listing page or entity page.
    Listing pages get their entity links extracted and each entity enqueued separately.
    """
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

        # Process each search result URL
        enqueued = 0
        for res in search_results:
            target_url = res.get("url")
            # Stage 1: Pre-crawl qualification (focus on 1-200 employee startups/SMBs, allow UNKNOWN)
            qual = quality_filter.qualify_company_candidate(
                title=res.get("title", ""),
                snippet=res.get("snippet", ""),
                url=target_url
            )
            if not qual["qualified"] or qual.get("priority") in ["REJECTED", "DEPRIORITIZED"]:
                _log_activity(db, url=target_url, stage="FILTER", domain=domain,
                              status="FILTERED", message=f"Candidate filtered ({qual.get('priority')}): {qual['reason']}", batch_id=batch_id)
                continue

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
                              status="QUEUED", message=f"Qualified ({qual['company_size']}) — queuing root entity crawl ({parsed_u.netloc})",
                              batch_id=batch_id)
                _safe_dispatch(crawl_entity_task, url=entity_root_url, domain=domain, batch_id=batch_id)
                enqueued += 1

                # Immediate Parallel Key-People Search: Dispatched in parallel without blocking company crawler
                company_title_for_people = res.get("title")
                if company_title_for_people:
                    clean_cname = company_title_for_people.split("|")[0].split("-")[0].strip()
                    if len(clean_cname) > 2:
                        _safe_dispatch(
                            search_company_people_task,
                            company_name=clean_cname,
                            domain=domain,
                            official_domain=parsed_u.netloc,
                            batch_id=batch_id
                        )

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

# Targeted corporate subpages to crawl for company intelligence (§12, §13)
ENTITY_SUBPAGES = [
    "/about", "/about-us", "/company", "/products", "/services",
    "/solutions", "/team", "/leadership", "/contact", "/careers"
]
MAX_ENTITY_PAGES = 6


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
) -> Dict[str, Any]:
    """
    §12, §13 — Worker B: Deep entity crawl + extraction.
    Crawls homepage + targeted subpages (/about, /company, /team, /leadership, etc.)
    for maximum verified field extraction.
    """
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
        db = SessionLocal()
        try:
            _log_activity(db, url=url, stage="CRAWL", domain=domain,
                          status="ERROR", message=f"Homepage crawl failed: {crawl_err}",
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

    # ── Stage 3: Save to MinIO raw storage & persist Document Record ───────────
    html_hash, raw_rel = file_storage.save_raw_page(item.html_content or "")
    md_rel = file_storage.save_processed_markdown(item.markdown or "", html_hash)
    txt_rel = file_storage.save_processed_text(item.text or "", html_hash)

    # Derive genuine title from evidence — NEVER append "Official Portal"
    derived_title = (item.title or "").strip()
    if not derived_title or len(derived_title) < 2 or derived_title.lower() in ["home", "index", "welcome", "default", "404", "error"]:
        netloc = base_host.replace("www.", "")
        derived_title = netloc.split(".")[0].replace("-", " ").title() if "." in netloc else netloc
    if not derived_title:
        derived_title = "Unknown"

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
            db.commit()

        doc_id = doc.id
        _log_activity(db, url=url, stage="CRAWL", domain=domain,
                      status="OK", message=f"Crawled OK — Persisted Document ID={doc_id[:8]} ({word_count} words)",
                      batch_id=batch_id)
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

        # Optional: Attempt to discover company LinkedIn URL — attach if found, skip gracefully if not
        from app.extraction.key_people_extractor import key_people_extractor
        company_linkedin = key_people_extractor.extract_company_linkedin_url(
            item.html_content or "", enriched_text, entity_name, domain_key
        )
        if not company_linkedin:
            try:
                company_linkedin = run_async(
                    key_people_extractor.find_company_linkedin_via_search(entity_name, domain_key)
                )
            except Exception as li_err:
                logger.debug(f"[Worker B] LinkedIn search skipped for {entity_name}: {li_err}")

        # Attach company LinkedIn URL to payload if found (not a hard requirement)
        univ_data = payload.get("universal") or {}
        if "metadata_json" not in univ_data or not isinstance(univ_data["metadata_json"], dict):
            univ_data["metadata_json"] = {}
        if company_linkedin:
            univ_data["metadata_json"]["company_linkedin_url"] = company_linkedin
        payload["universal"] = univ_data

        dom_data = payload.get("domain_data") or {}
        if company_linkedin:
            dom_data["company_linkedin_url"] = company_linkedin
        payload["domain_data"] = dom_data

        if company_linkedin:
            logger.info(f"[Worker B] LinkedIn found for {entity_name}: {company_linkedin}")
        else:
            logger.debug(f"[Worker B] No LinkedIn found for {entity_name} — continuing without it")

        repo.save_extraction_results(db, payload)

        # Enterprise Master Vault Integration (Redis L1, SQLite WAL Vault, MinIO L3, Postgres Open Lake)
        try:
            from app.persistence.vault_service import MasterVaultService
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
                crawled_subpages=subpages_list,
                company_linkedin_url=company_linkedin
            )

            # Also persist discovered key people to KeyPersonCandidate table
            if d_makers and isinstance(d_makers, list):
                from app.persistence.models import KeyPersonCandidate
                for dm in d_makers:
                    if isinstance(dm, dict) and dm.get("name"):
                        p_name = dm["name"].strip()
                        if not p_name.lower().startswith("leadership team"):
                            existing_kp = db.query(KeyPersonCandidate).filter(
                                KeyPersonCandidate.company_name == entity_name,
                                KeyPersonCandidate.person_name == p_name
                            ).first()
                            if not existing_kp:
                                db.add(KeyPersonCandidate(
                                    company_name=entity_name,
                                    person_name=p_name,
                                    role=dm.get("title") or "Executive / Leadership",
                                    source_url=dm.get("linkedin_url") or dm.get("linkedin_search_url") or url,
                                    discovery_query="Webpage HTML / Team Extraction",
                                    confidence_score=0.90
                                ))
                db.commit()
        except Exception as vault_err:
            logger.warning(f"[Worker B] MasterVault persistence notice for {domain_key}: {vault_err}")

        univ_rec = db.query(UniversalRecord).filter(
            UniversalRecord.document_id == doc_id
        ).first()

        if univ_rec:
            _log_activity(db, url=url, stage="POSTGRES", domain=domain,
                          status="OK",
                          message=f"UniversalRecord persisted — name='{entity_name}' ID={univ_rec.id[:8]} confidence={entity_confidence:.0%}",
                          entity_name=entity_name, batch_id=batch_id)
            _safe_dispatch(enrich_and_verify_task, universal_record_id=univ_rec.id)

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
        db.commit()

        # Sync to Master Vault if verified
        if is_verified and record.url:
            try:
                from app.persistence.vault_service import MasterVaultService
                domain_key = urlparse(record.url).netloc.lower().lstrip("www.")
                dom_data = dom_rec.data if dom_rec and isinstance(dom_rec.data, dict) else {}
                MasterVaultService.persist_master_lead(
                    db=db,
                    domain=domain_key,
                    company_name=record.canonical_name,
                    technology_stack=dom_data.get("technologies") or [],
                    quality_score=confidence * 10.0,
                    headquarters=dom_data.get("headquarters") or record.location,
                    industry=record.domain.name if (record.domain and hasattr(record.domain, "name")) else "Technology",
                    summary=record.description or f"{record.canonical_name} corporate profile.",
                    decision_makers=dom_data.get("key_people") or dom_data.get("leadership")
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
        
        clean_company = key_people_extractor.clean_company_name(company_name)

        # Generate prioritized queries (cap 5)
        queries = key_people_agent.generate_queries(
            company_name=clean_company,
            official_domain=official_domain
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
                    batch_people = key_people_extractor.extract_from_linkedin_search_snippets(results, clean_company)
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
            return {"status": "no_results", "company_name": company_name}

        clean_dom = (official_domain or "").replace("www.", "").lower().strip()
        site_domain = clean_dom if clean_dom else domain

        saved_count = 0
        for p in all_discovered:
            target_names = [company_name, clean_company]
            existing = db.query(KeyPersonCandidate).filter(
                (KeyPersonCandidate.company_name.in_(target_names)) | (KeyPersonCandidate.source_domain == site_domain),
                KeyPersonCandidate.person_name == p["name"]
            ).first()
            
            p_profile = p.get("linkedin_url")
            if p_profile and not _is_real_linkedin_profile(p_profile):
                p_profile = None
            
            # Genuine profile URL only — never store a search or query URL
            valid_profile_url = p_profile if p_profile else None
            
            if not existing:
                cand = KeyPersonCandidate(
                    company_name=company_name,
                    person_name=p["name"],
                    role=p["title"],
                    source_url=valid_profile_url, # Genuine profile URL or None, NEVER a search query!
                    source_domain=site_domain,
                    source_type=p.get("source_type", "search_discovery"),
                    discovery_query=last_query,
                    evidence_text=p.get("evidence", ""),
                    confidence_score=p.get("confidence_score", 0.85),
                    verification_status="HIGH_CONFIDENCE" if valid_profile_url else "DISCOVERED"
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
                
                glead = db.query(GlobalLead).filter(GlobalLead.domain == site_domain).first()
                if glead:
                    from app.persistence.models import GlobalLeadPerson
                    existing_gl_people = {glp.full_name.lower() for glp in db.query(GlobalLeadPerson).filter(GlobalLeadPerson.global_lead_id == glead.id).all()}
                    for p in all_discovered:
                        if p["name"].lower() not in existing_gl_people:
                            p_url = p.get("linkedin_url")
                            db.add(GlobalLeadPerson(
                                global_lead_id=glead.id,
                                full_name=p["name"],
                                title=p["title"],
                                linkedin_url=p_url,
                                linkedin_search_url=p_url
                            ))
                            existing_gl_people.add(p["name"].lower())
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
