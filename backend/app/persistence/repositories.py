import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from app.persistence.models import (
    Source, Domain, Subdomain, CrawlJob, Document, DocumentVersion,
    Resource, ResourceLink, UniversalRecord, DomainRecord,
    ExtractedFact, Evidence, ExtractionRun, SchemaDefinition, CrawlError,
    Metadata
)

logger = logging.getLogger(__name__)

class Repository:
    @staticmethod
    def get_or_create_source(db: Session, name: str, base_url: str) -> Source:
        src = db.query(Source).filter(Source.base_url == base_url).first()
        if not src:
            src = Source(
                name=name,
                source_type="website",
                base_url=base_url,
                description=f"Automated Web Source for {base_url}"
            )
            db.add(src)
            db.commit()
            db.refresh(src)
        return src

    @staticmethod
    def create_crawl_job(
        db: Session,
        starting_url: str,
        query: Optional[str],
        domain_name: Optional[str],
        max_depth: int,
        max_pages: int
    ) -> CrawlJob:
        job = CrawlJob(
            starting_url=starting_url,
            query=query,
            domain_name=domain_name,
            max_depth=max_depth,
            max_pages=max_pages,
            status="running",
            pipeline_stage="URL_DISCOVERY",
            pipeline_details={"stages": []}
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    @staticmethod
    def update_crawl_job_status(
        db: Session,
        job_id: str,
        status: str,
        stage: Optional[str] = None,
        pages_discovered: Optional[int] = None,
        pages_crawled: Optional[int] = None,
        documents_count: Optional[int] = None,
        resources_count: Optional[int] = None,
        successful_count: Optional[int] = None,
        failed_count: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> CrawlJob:
        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        if job:
            job.status = status
            if stage:
                job.pipeline_stage = stage
            if pages_discovered is not None:
                job.pages_discovered = pages_discovered
            if pages_crawled is not None:
                job.pages_crawled = pages_crawled
            if documents_count is not None:
                job.documents_count = documents_count
            if resources_count is not None:
                job.resources_count = resources_count
            if successful_count is not None:
                job.successful_count = successful_count
            if failed_count is not None:
                job.failed_count = failed_count
            if error_message:
                job.error_message = error_message
            if status in ["completed", "failed"]:
                job.finished_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(job)
        return job

    @staticmethod
    def create_document(
        db: Session,
        crawl_job_id: str,
        source_id: Optional[int],
        url: str,
        canonical_url: Optional[str],
        title: Optional[str],
        content_type: str,
        http_status: int,
        content_hash: str,
        raw_path: str,
        markdown_path: str,
        text_path: str,
        word_count: int,
        links_count: int,
        images_count: int
    ) -> Document:
        doc = db.query(Document).filter(Document.url == url).first()
        if doc:
            doc.crawl_job_id = crawl_job_id
            doc.source_id = source_id
            doc.canonical_url = canonical_url
            doc.title = title
            doc.content_type = content_type
            doc.http_status = http_status
            doc.content_hash = content_hash
            doc.raw_path = raw_path
            doc.markdown_path = markdown_path
            doc.text_path = text_path
            doc.word_count = word_count
            doc.links_count = links_count
            doc.images_count = images_count
            db.commit()
            db.refresh(doc)
        else:
            doc = Document(
                crawl_job_id=crawl_job_id,
                source_id=source_id,
                url=url,
                canonical_url=canonical_url,
                title=title,
                content_type=content_type,
                http_status=http_status,
                content_hash=content_hash,
                raw_path=raw_path,
                markdown_path=markdown_path,
                text_path=text_path,
                word_count=word_count,
                links_count=links_count,
                images_count=images_count
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
        return doc

    @staticmethod
    def save_resources(db: Session, resource_dicts: List[Dict[str, Any]]) -> List[Resource]:
        created_resources = []
        for r_dict in resource_dicts:
            res = Resource(**r_dict)
            db.add(res)
            created_resources.append(res)
        db.commit()
        return created_resources

    @staticmethod
    def save_extraction_results(db: Session, payload: Dict[str, Any]) -> Tuple[UniversalRecord, DomainRecord]:
        doc_id = payload["document_id"]
        class_info = payload["classification"]
        univ_data = payload["universal"]
        domain_data = payload["domain_data"]
        evidence_items = payload.get("evidence", [])

        # Clean up existing records for doc_id if re-crawled
        existing_univ = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc_id).all()
        for old_univ in existing_univ:
            db.delete(old_univ)
        db.commit()

        # Get or create domain record in Postgres
        domain_obj = db.query(Domain).filter(Domain.name == class_info["domain"]).first()

        univ_rec = UniversalRecord(
            document_id=doc_id,
            domain_id=domain_obj.id if domain_obj else None,
            entity_type=univ_data.get("entity_type"),
            canonical_name=univ_data.get("canonical_name"),
            title=univ_data.get("title"),
            description=univ_data.get("description"),
            url=univ_data.get("url"),
            language=univ_data.get("language"),
            country=univ_data.get("country"),
            location=univ_data.get("location"),
            status=univ_data.get("status"),
            confidence=univ_data.get("confidence")
        )
        db.add(univ_rec)
        db.commit()
        db.refresh(univ_rec)

        dom_rec = DomainRecord(
            universal_record_id=univ_rec.id,
            domain_id=domain_obj.id if domain_obj else None,
            schema_version="1.0.0",
            data=domain_data
        )
        db.add(dom_rec)
        db.commit()
        db.refresh(dom_rec)

        # ── Register in metadata table ─────────────────────────────────────
        # 1. Track the domain
        if class_info.get("domain"):
            Repository.upsert_metadata(
                db,
                entity_type="domain",
                entity_key=class_info["domain"],
                domain=class_info["domain"],
                source_table="domains",
                source_id=str(domain_obj.id) if domain_obj else None,
                extra={"subdomain": class_info.get("subdomain"), "confidence": univ_data.get("confidence")}
            )
        # 2. Track the subdomain
        if class_info.get("subdomain"):
            Repository.upsert_metadata(
                db,
                entity_type="subdomain",
                entity_key=class_info["subdomain"],
                domain=class_info.get("domain"),
                subdomain=class_info["subdomain"],
                source_table="subdomains",
                extra={"confidence": univ_data.get("confidence")}
            )
        # 3. Track the URL (entity presence)
        if univ_data.get("url"):
            Repository.upsert_metadata(
                db,
                entity_type="url",
                entity_key=univ_data["url"],
                domain=class_info.get("domain"),
                subdomain=class_info.get("subdomain"),
                source_table="universal_records",
                source_id=str(univ_rec.id),
                extra={"canonical_name": univ_data.get("canonical_name"), "title": univ_data.get("title")}
            )
        # 4. Track the canonical name as a keyword
        if univ_data.get("canonical_name"):
            Repository.upsert_metadata(
                db,
                entity_type="keyword",
                entity_key=univ_data["canonical_name"].lower(),
                domain=class_info.get("domain"),
                subdomain=class_info.get("subdomain"),
                source_table="universal_records",
                source_id=str(univ_rec.id),
                extra={"confidence": univ_data.get("confidence")}
            )

        # Store Extracted Facts & Evidence
        for k, v in domain_data.items():
            val_str = json.dumps(v) if isinstance(v, (list, dict)) else (str(v) if v is not None else None)
            val_type = "array" if isinstance(v, list) else ("object" if isinstance(v, dict) else ("null" if v is None else "string"))
            
            fact = ExtractedFact(
                document_id=doc_id,
                universal_record_id=univ_rec.id,
                field_name=k,
                field_value=val_str,
                value_type=val_type,
                confidence=univ_data.get("confidence"),
                extractor="llm_and_deterministic",
                schema_version="1.0.0"
            )
            db.add(fact)
            db.commit()
            db.refresh(fact)

            # Match evidence item
            ev_match = next((item for item in evidence_items if item.get("field") == k), None)
            if ev_match:
                ev_obj = Evidence(
                    fact_id=fact.id,
                    document_id=doc_id,
                    source_url=payload["source"]["url"],
                    text_snippet=ev_match.get("text_snippet"),
                    confidence=ev_match.get("confidence", 0.90)
                )
                db.add(ev_obj)

        db.commit()
        return univ_rec, dom_rec

    @staticmethod
    def record_error(db: Session, job_id: str, url: str, stage: str, err_type: str, msg: str):
        err = CrawlError(
            crawl_job_id=job_id,
            url=url,
            stage=stage,
            error_type=err_type,
            error_message=msg
        )
        db.add(err)
        db.commit()

    # ─── Metadata Registry Helpers ───────────────────────────────────────────

    @staticmethod
    def upsert_metadata(
        db: Session,
        entity_type: str,
        entity_key: str,
        domain: Optional[str] = None,
        subdomain: Optional[str] = None,
        is_present: bool = True,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None
    ) -> Metadata:
        """Insert or update a metadata registry entry."""
        record = db.query(Metadata).filter(
            Metadata.entity_type == entity_type,
            Metadata.entity_key == entity_key
        ).first()

        if record:
            record.is_present = is_present
            record.domain = domain or record.domain
            record.subdomain = subdomain or record.subdomain
            record.source_table = source_table or record.source_table
            record.source_id = source_id or record.source_id
            if extra:
                record.extra = {**(record.extra or {}), **extra}
        else:
            record = Metadata(
                entity_type=entity_type,
                entity_key=entity_key,
                domain=domain,
                subdomain=subdomain,
                is_present=is_present,
                source_table=source_table,
                source_id=str(source_id) if source_id else None,
                extra=extra or {}
            )
            db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def check_presence(
        db: Session,
        entity_type: str,
        entity_key: str
    ) -> bool:
        """Returns True if the entity is registered and is_present=True."""
        record = db.query(Metadata).filter(
            Metadata.entity_type == entity_type,
            Metadata.entity_key == entity_key,
            Metadata.is_present == True
        ).first()
        return record is not None

    @staticmethod
    def mark_absent(
        db: Session,
        entity_type: str,
        entity_key: str
    ) -> bool:
        """Mark an entity as no longer present (soft delete)."""
        record = db.query(Metadata).filter(
            Metadata.entity_type == entity_type,
            Metadata.entity_key == entity_key
        ).first()
        if record:
            record.is_present = False
            db.commit()
            return True
        return False

    @staticmethod
    def search_metadata(
        db: Session,
        entity_type: Optional[str] = None,
        domain: Optional[str] = None,
        subdomain: Optional[str] = None,
        is_present: Optional[bool] = True
    ) -> List[Metadata]:
        """Query the metadata registry with optional filters."""
        q = db.query(Metadata)
        if entity_type:
            q = q.filter(Metadata.entity_type == entity_type)
        if domain:
            q = q.filter(Metadata.domain == domain)
        if subdomain:
            q = q.filter(Metadata.subdomain == subdomain)
        if is_present is not None:
            q = q.filter(Metadata.is_present == is_present)
        return q.order_by(Metadata.updated_at.desc()).all()


repo = Repository()
