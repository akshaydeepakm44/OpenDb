import sys
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List, Callable
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from app.persistence.database import get_db, SessionLocal
from app.persistence.repositories import repo
from app.persistence.models import CrawlJob, Document, UniversalRecord, DomainRecord, Resource, ExtractedFact, Evidence
from app.crawler.crawler_service import crawler_service
from app.crawler.resource_discovery import resource_discovery
from app.extraction.extractor import extraction_pipeline
from app.storage.file_storage import file_storage
from app.normalization.normalizer import normalizer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crawl", tags=["Crawl"])

# Domain fallback seeds when no URL is explicitly provided
DOMAIN_DEFAULT_SEEDS = {
    "Technology": "https://news.ycombinator.com",
    "Healthcare": "https://www.who.int",
    "Education": "https://www.mit.edu",
    "Business": "https://www.reuters.com"
}

class CrawlRequest(BaseModel):
    url: Optional[str] = None
    query: Optional[str] = None
    domain: Optional[str] = "Technology"
    max_depth: Optional[int] = 3
    max_pages: Optional[int] = 20

async def run_in_proactor_loop(async_fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """
    Run an async function in a dedicated thread with a Windows ProactorEventLoop.
    This bypasses Windows SelectorEventLoop limitations in FastAPI background threads.
    """
    def worker():
        if sys.platform == "win32":
            loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(async_fn(*args, **kwargs))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    return await asyncio.to_thread(worker)

async def execute_crawl_pipeline(job_id: str, request_data: Dict[str, Any]):
    db = SessionLocal()
    try:
        url = request_data["url"]
        query = request_data.get("query")
        domain_name = request_data.get("domain")
        max_depth = request_data.get("max_depth", 3)
        max_pages = request_data.get("max_pages", 20)

        # 1. Pipeline Stage: DISCOVERY & CRAWLING
        repo.update_crawl_job_status(db, job_id, status="running", stage="URL_DISCOVERY")
        base_host = url.split("//")[-1].split("/")[0]
        source = repo.get_or_create_source(db, name=base_host, base_url=url)

        repo.update_crawl_job_status(db, job_id, status="running", stage="CRAWLING")
        
        async def progress_cb(stage, pages_discovered, pages_crawled, current_url):
            repo.update_crawl_job_status(
                db, job_id, status="running", stage=stage,
                pages_discovered=pages_discovered, pages_crawled=pages_crawled
            )

        crawled_pages = await run_in_proactor_loop(
            crawler_service.crawl_site,
            starting_url=url,
            max_depth=max_depth,
            max_pages=max_pages,
            progress_callback=progress_cb
        )

        repo.update_crawl_job_status(
            db, job_id, status="running", stage="RAW_CONTENT_STORAGE",
            pages_crawled=len(crawled_pages)
        )

        documents_processed = []
        total_resources_count = 0

        # Process each crawled page through pipeline
        for item in crawled_pages:
            try:
                # 2. RAW CONTENT STORAGE
                html_hash, raw_rel = file_storage.save_raw_page(item.html_content)
                md_rel = file_storage.save_processed_markdown(item.markdown, html_hash)
                txt_rel = file_storage.save_processed_text(item.text, html_hash)

                doc = repo.create_document(
                    db=db,
                    crawl_job_id=job_id,
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

                # 3. RESOURCE DISCOVERY
                discovered_res = await resource_discovery.process_discovered_resources(
                    page_url=item.url,
                    links_and_media=item.links + item.media,
                    document_id=doc.id
                )
                if discovered_res:
                    repo.save_resources(db, discovered_res)
                    total_resources_count += len(discovered_res)

                # 4. CONTENT EXTRACTION, DOMAIN CLASSIFICATION & NORMALIZATION
                repo.update_crawl_job_status(db, job_id, status="running", stage="EXTRACTION_AND_VALIDATION")
                
                payload = await extraction_pipeline.process_document_extraction(
                    document_id=doc.id,
                    url=item.url,
                    html_content=item.html_content,
                    text_content=item.text,
                    user_domain=domain_name
                )

                # Save raw JSON extraction file
                file_storage.save_extracted_json(doc.id, payload)

                # 5. POSTGRESQL PERSISTENCE
                repo.save_extraction_results(db, payload)
                documents_processed.append(doc)

            except Exception as e:
                logger.error(f"Error processing page pipeline for {item.url}: {e}")
                repo.record_error(db, job_id, item.url, "PIPELINE_STAGE", type(e).__name__, str(e))

        # Update final Crawl Job state
        repo.update_crawl_job_status(
            db, job_id,
            status="completed",
            stage="PERSISTENCE_COMPLETE",
            pages_discovered=len(crawled_pages),
            pages_crawled=len(crawled_pages),
            documents_count=len(documents_processed),
            resources_count=total_resources_count,
            successful_count=len(documents_processed),
            failed_count=0
        )

    except Exception as e:
        logger.error(f"Crawl job {job_id} failed: {e}")
        repo.update_crawl_job_status(db, job_id, status="failed", stage="FAILED", error_message=str(e))
    finally:
        db.close()


@router.post("")
def start_crawl_job(request: CrawlRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    target_domain = request.domain or "Technology"
    raw_url = request.url.strip() if request.url and request.url.strip() else None

    # If URL is not provided, use default domain seed
    if not raw_url:
        raw_url = DOMAIN_DEFAULT_SEEDS.get(target_domain, "https://news.ycombinator.com")

    norm_url = normalizer.normalize_url(raw_url)
    if not norm_url:
        norm_url = raw_url

    job = repo.create_crawl_job(
        db=db,
        starting_url=norm_url,
        query=request.query,
        domain_name=target_domain,
        max_depth=request.max_depth or 3,
        max_pages=request.max_pages or 20
    )

    req_data = {
        "url": norm_url,
        "query": request.query,
        "domain": target_domain,
        "max_depth": request.max_depth or 3,
        "max_pages": request.max_pages or 20
    }

    background_tasks.add_task(execute_crawl_pipeline, job.id, req_data)

    return {
        "job_id": job.id,
        "status": job.status,
        "message": f"Crawl job initialized for target starting seed {norm_url}."
    }


@router.get("/{job_id}")
def get_crawl_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Crawl job not found.")

    return {
        "job_id": job.id,
        "starting_url": job.starting_url,
        "query": job.query,
        "domain_name": job.domain_name,
        "status": job.status,
        "pipeline_stage": job.pipeline_stage,
        "pages_discovered": job.pages_discovered,
        "pages_crawled": job.pages_crawled,
        "documents_count": job.documents_count,
        "resources_count": job.resources_count,
        "successful_count": job.successful_count,
        "failed_count": job.failed_count,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "finished_at": job.finished_at
    }


@router.get("/{job_id}/pages")
def get_crawl_job_pages(job_id: str, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.crawl_job_id == job_id).all()
    pages = []
    for doc in docs:
        univ = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc.id).first()
        pages.append({
            "document_id": doc.id,
            "url": doc.url,
            "title": doc.title,
            "status": doc.http_status,
            "content_type": doc.content_type,
            "word_count": doc.word_count,
            "domain": univ.entity_type if univ else "Unknown",
            "confidence": float(univ.confidence) if univ and univ.confidence else 0.0
        })
    return pages


@router.get("/{job_id}/results")
def get_crawl_job_results(job_id: str, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.crawl_job_id == job_id).all()
    results = []
    for doc in docs:
        univ = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc.id).first()
        if univ:
            dom_rec = db.query(DomainRecord).filter(DomainRecord.universal_record_id == univ.id).first()
            facts = db.query(ExtractedFact).filter(ExtractedFact.document_id == doc.id).all()
            
            evidence_list = []
            for f in facts:
                ev_objs = db.query(Evidence).filter(Evidence.fact_id == f.id).all()
                for ev in ev_objs:
                    evidence_list.append({
                        "field": f.field_name,
                        "value": f.field_value,
                        "source_url": ev.source_url,
                        "evidence_text": ev.text_snippet,
                        "confidence": float(ev.confidence) if ev.confidence else 0.9
                    })

            resources = db.query(Resource).filter(Resource.source_document_id == doc.id).all()

            results.append({
                "document_id": doc.id,
                "source": {
                    "url": doc.url,
                    "title": doc.title,
                    "retrieved_at": doc.retrieved_at
                },
                "classification": {
                    "domain": univ.domain.name if univ.domain else "Technology",
                    "subdomain": univ.subdomain.name if univ.subdomain else "General",
                    "confidence": float(univ.confidence) if univ.confidence else 0.90
                },
                "universal": {
                    "resource_id": doc.id,
                    "canonical_name": univ.canonical_name,
                    "title": univ.title,
                    "description": univ.description,
                    "url": univ.url,
                    "domain": univ.domain.name if univ.domain else "Technology",
                    "subdomain": univ.subdomain.name if univ.subdomain else "General",
                    "entity_type": univ.entity_type,
                    "language": univ.language,
                    "country": univ.country,
                    "location": univ.location,
                    "status": univ.status,
                    "confidence": float(univ.confidence) if univ.confidence else 0.90
                },
                "domain_data": dom_rec.data if dom_rec else {},
                "evidence": evidence_list,
                "resources": [
                    {
                        "id": r.id,
                        "url": r.resource_url,
                        "type": r.resource_type,
                        "mime_type": r.mime_type,
                        "size": r.content_length,
                        "stored_path": r.raw_path,
                        "downloaded": r.downloaded
                    }
                    for r in resources
                ]
            })
    return results
