import sys
import asyncio
import logging
import uuid
from typing import Dict, Any, List
from app.worker.celery_app import celery_app
from app.persistence.database import SessionLocal
from app.persistence.repositories import repo
from app.persistence.models import (
    SearchHistory, Document, UniversalRecord, DomainRecord, 
    VerificationRecord, ExtractedFact, BatchResult
)
from app.crawler.searxng_service import searxng_service
from app.crawler.crawler_service import crawler_service
from app.storage.file_storage import file_storage
from app.extraction.extractor import extraction_pipeline
from app.normalization.normalizer import normalizer

logger = logging.getLogger(__name__)

def run_async(coro):
    """Helper to run async code inside synchronous Celery workers."""
    if sys.platform == "win32":
        loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


@celery_app.task(name="tasks.search_and_discover")
def search_and_discover_task(keyword: str, domain: str, batch_id: str = None) -> Dict[str, Any]:
    """Worker Task: Query SearXNG for candidate lead sources."""
    logger.info(f"[Task Search] Querying SearXNG for keyword='{keyword}', domain='{domain}'")
    db = SessionLocal()
    try:
        search_results, is_fallback, log_msg = run_async(searxng_service.search_with_meta(query=f"{domain} {keyword}", max_results=15))
        
        # Save Search History Record
        history = SearchHistory(
            id=str(uuid.uuid4()),
            keyword=keyword,
            domain=domain,
            sources_found=len(search_results),
            batch_id=batch_id
        )
        if hasattr(history, 'is_fallback'):
            history.is_fallback = is_fallback
        if hasattr(history, 'log_message'):
            history.log_message = log_msg
        db.add(history)
        db.commit()

        # Enqueue Crawl tasks for each discovered source URL
        enqueued_count = 0
        for res in search_results:
            target_url = res.get("url")
            if target_url:
                crawl_and_extract_task.delay(target_url, domain, batch_id)
                enqueued_count += 1

        return {
            "keyword": keyword,
            "sources_found": len(search_results),
            "enqueued_crawls": enqueued_count
        }
    except Exception as e:
        logger.error(f"Search task failed for keyword '{keyword}': {e}")
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.crawl_and_extract")
def crawl_and_extract_task(url: str, domain: str, batch_id: str = None) -> Dict[str, Any]:
    """Worker Task A (Discovery & Extraction): Crawl single URL, save raw object to MinIO, extract facts."""
    logger.info(f"[Task Crawl/Extract] Target URL='{url}'")
    db = SessionLocal()
    try:
        # 1. Crawl Page
        crawled_items = run_async(
            crawler_service.crawl_site(starting_url=url, max_depth=1, max_pages=1)
        )
        if not crawled_items:
            return {"status": "failed", "reason": "No content retrieved"}

        item = crawled_items[0]

        # 2. Save Raw HTML & Text to MinIO
        html_hash, raw_rel = file_storage.save_raw_page(item.html_content)
        md_rel = file_storage.save_processed_markdown(item.markdown, html_hash)
        txt_rel = file_storage.save_processed_text(item.text, html_hash)

        # Get or create Source
        base_host = url.split("//")[-1].split("/")[0]
        source = repo.get_or_create_source(db, name=base_host, base_url=url)

        # 3. Create Document Record
        doc = repo.create_document(
            db=db,
            crawl_job_id=None,
            source_id=source.id,
            url=item.url,
            canonical_url=item.metadata.get("canonical_url", item.url),
            title=item.title,
            content_type=item.content_type,
            http_status=item.http_status,
            content_hash=html_hash,
            raw_path=raw_rel,
            markdown_path=md_rel,
            text_path=txt_rel,
            word_count=item.metadata.get("word_count", 0),
            links_count=item.metadata.get("links_count", 0),
            images_count=item.metadata.get("images_count", 0)
        )

        # 4. Extract Information
        payload = run_async(
            extraction_pipeline.process_document_extraction(
                document_id=doc.id,
                url=item.url,
                html_content=item.html_content,
                text_content=item.text,
                user_domain=domain
            )
        )

        # Save JSON Payload to MinIO
        file_storage.save_extracted_json(doc.id, payload)

        # Save Preliminary Entities in Postgres
        repo.save_extraction_results(db, payload)

        # Find created Universal Record
        univ_rec = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc.id).first()
        if univ_rec:
            # Trigger Worker B: Worker B handles Verification & Enrichment asynchronously
            enrich_and_verify_task.delay(univ_rec.id)

        return {
            "status": "success",
            "document_id": doc.id,
            "universal_record_id": univ_rec.id if univ_rec else None
        }

    except Exception as e:
        logger.error(f"Crawl and extract failed for {url}: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.enrich_and_verify")
def enrich_and_verify_task(universal_record_id: str) -> Dict[str, Any]:
    """Worker Task B (Enrichment, Normalization & Deduplication & Verification)."""
    logger.info(f"[Task Worker B] Enriching and Verifying Record '{universal_record_id}'")
    db = SessionLocal()
    try:
        record = db.query(UniversalRecord).filter(UniversalRecord.id == universal_record_id).first()
        if not record:
            return {"status": "error", "reason": "Record not found"}

        # 1. Normalization
        canonical_name = normalizer.normalize_string(record.canonical_name or record.title or "")
        normalized_country = normalizer.normalize_country(record.country) if record.country else None

        record.canonical_name = canonical_name
        record.country = normalized_country

        # 2. Simple Deduplication Check based on Canonical Name or Website
        existing_duplicate = db.query(UniversalRecord).filter(
            UniversalRecord.id != record.id,
            (UniversalRecord.canonical_name == canonical_name) | (UniversalRecord.url == record.url)
        ).first()

        is_duplicate = False
        if existing_duplicate:
            logger.info(f"Duplicate entity found for '{canonical_name}'. Merging / flag as duplicate.")
            is_duplicate = True
            record.status = "Duplicate"

        # 3. Verification Heuristics
        # Confidence score derived from extracted fields presence & domain match
        confidence = float(record.confidence or 0.8)
        if len(canonical_name) > 2 and record.url and not is_duplicate:
            is_verified = True
            confidence = min(1.0, confidence + 0.15)
            if not is_duplicate:
                record.status = "Verified"
        else:
            is_verified = False

        db.commit()

        # 4. Save Verification Record
        v_record = VerificationRecord(
            id=str(uuid.uuid4()),
            universal_record_id=record.id,
            is_verified=is_verified,
            confidence=confidence,
            verification_notes=f"Deduplicated: {is_duplicate}, Name length: {len(canonical_name)}"
        )
        db.add(v_record)
        db.commit()

        return {
            "status": "success",
            "record_id": record.id,
            "is_verified": is_verified,
            "is_duplicate": is_duplicate
        }

    except Exception as e:
        logger.error(f"Enrichment and verification task failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
