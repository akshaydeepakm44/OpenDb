import hashlib
import json
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.persistence.models import (
    GlobalLead, GlobalLeadPerson, GlobalLeadSubpage, OpenLakeRecord
)
from app.cache.redis_cache import (
    acquire_crawl_lock, release_crawl_lock, get_master_lead_cache,
    set_master_lead_cache, is_verified_domain_set, add_verified_domain_set
)
from app.storage.file_storage import file_storage

logger = logging.getLogger(__name__)


def md5_hash(val: str) -> str:
    """Generate deterministic md5 hash for primary keys."""
    return hashlib.md5(val.lower().strip().encode("utf-8")).hexdigest()


class MasterVaultService:
    """
    Enterprise Master Vault Service implementing:
    1. Redis L1 Cache (Locking, Hot Cache, Duplicate Check)
    2. SQLite WAL Master Vault (global_leads, global_lead_people, global_lead_subpages, FTS5)
    3. MinIO L3 Object Storage (brand-assets: companies/{domain}/brand_kit.json, logo.png, pages/{slug}.md)
    4. PostgreSQL Open Lake (open_lake_records candidate dispatching)
    """

    @staticmethod
    def is_candidate_locked(domain: str) -> bool:
        """Check duplicate crawl lock or verified set in Redis L1."""
        clean_domain = domain.lower().replace("www.", "").strip()
        if is_verified_domain_set(clean_domain):
            return True
        return not acquire_crawl_lock(clean_domain, ttl=300)

    @staticmethod
    def dispatch_open_lake_candidate(db: Session, domain: str) -> OpenLakeRecord:
        """Dispatch domain candidate in PostgreSQL Open Lake repository."""
        clean_domain = domain.lower().replace("www.", "").strip()
        rec = db.query(OpenLakeRecord).filter(OpenLakeRecord.domain == clean_domain).first()
        if not rec:
            rec = OpenLakeRecord(domain=clean_domain, enrichment_status="in_progress")
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    @staticmethod
    def persist_master_lead(
        db: Session,
        domain: str,
        company_name: str,
        logo_url: str = None,
        logo_bytes: bytes = None,
        technology_stack: List[str] = None,
        quality_score: float = 8.5,
        headquarters: str = None,
        industry: str = None,
        company_size: str = None,
        revenue_funding: str = None,
        verified_emails: List[str] = None,
        summary: str = None,
        decision_makers: List[Dict[str, Any]] = None,
        crawled_subpages: List[Dict[str, Any]] = None
    ) -> GlobalLead:
        """
        Store full brand kit, logo assets, subpage Markdown DOMs, and SQLite WAL master lead.
        Pushes to Redis L1 Hot Cache and marks PostgreSQL Open Lake record enriched.
        """
        clean_domain = domain.lower().replace("www.", "").strip()
        lead_id = md5_hash(clean_domain)

        # 1. Store Brand Kit & Logo in MinIO L3 Storage
        brand_kit_payload = {
            "domain": clean_domain,
            "company_name": company_name,
            "logo_url": logo_url,
            "headquarters": headquarters,
            "industry": industry,
            "company_size": company_size,
            "revenue_funding": revenue_funding,
            "technology_stack": technology_stack or [],
            "verified_emails": verified_emails or [],
            "summary": summary
        }
        minio_brand_path = file_storage.save_brand_kit(clean_domain, brand_kit_payload)

        if logo_bytes:
            file_storage.save_logo_asset(clean_domain, logo_bytes, ext="png")

        # 2. Store Crawled Subpages Markdown DOM in MinIO L3
        subpage_objects = []
        if crawled_subpages:
            for sub in crawled_subpages:
                sub_url = sub.get("url") or f"https://{clean_domain}"
                sub_md = sub.get("markdown") or sub.get("content") or f"# {company_name}\n\n{summary}"
                sub_path = file_storage.save_markdown_dom(clean_domain, sub_url, sub_md)
                subpage_objects.append({
                    "page_url": sub_url,
                    "minio_object_path": sub_path
                })

        # 3. Save / Update SQLite WAL Master Vault (global_leads)
        lead = db.query(GlobalLead).filter(GlobalLead.id == lead_id).first()
        if not lead:
            lead = GlobalLead(
                id=lead_id,
                domain=clean_domain,
                company_name=company_name,
                minio_asset_path=minio_brand_path,
                logo_url=logo_url or f"https://www.google.com/s2/favicons?domain={clean_domain}&sz=128",
                technology_stack=technology_stack or [],
                quality_score=float(quality_score or 8.5),
                headquarters=headquarters,
                industry=industry,
                company_size=company_size,
                revenue_funding=revenue_funding,
                verified_emails=verified_emails or [],
                summary=summary
            )
            db.add(lead)
        else:
            lead.company_name = company_name
            lead.minio_asset_path = minio_brand_path
            if logo_url:
                lead.logo_url = logo_url
            lead.technology_stack = technology_stack or []
            lead.quality_score = float(quality_score or 8.5)
            lead.headquarters = headquarters
            lead.industry = industry
            lead.company_size = company_size
            lead.revenue_funding = revenue_funding
            lead.verified_emails = verified_emails or []
            lead.summary = summary

        db.commit()

        # 4. Save Decision Makers (global_lead_people)
        if decision_makers:
            for person in decision_makers:
                name = person.get("name") if isinstance(person, dict) else str(person)
                if not name:
                    continue
                role = person.get("title") or person.get("role") or "Executive / Key Person" if isinstance(person, dict) else "Executive"
                p_id = md5_hash(f"{clean_domain}:{name}")
                p_rec = db.query(GlobalLeadPerson).filter(GlobalLeadPerson.id == p_id).first()
                if not p_rec:
                    p_rec = GlobalLeadPerson(
                        id=p_id,
                        global_lead_id=lead_id,
                        domain=clean_domain,
                        full_name=name,
                        title=role,
                        linkedin_search_url=f"https://www.linkedin.com/search/results/all/?keywords={name}%20{company_name}"
                    )
                    db.add(p_rec)
                else:
                    p_rec.title = role
            db.commit()

        # 5. Save Subpages (global_lead_subpages)
        if subpage_objects:
            for sub in subpage_objects:
                sub_rec = db.query(GlobalLeadSubpage).filter(GlobalLeadSubpage.page_url == sub["page_url"]).first()
                if not sub_rec:
                    sub_rec = GlobalLeadSubpage(
                        global_lead_id=lead_id,
                        domain=clean_domain,
                        page_url=sub["page_url"],
                        minio_object_path=sub["minio_object_path"]
                    )
                    db.add(sub_rec)
            db.commit()

        # 6. Push to Redis L1 Hot Cache (master:lead:{domain}) & Set
        lead_dict = {
            "id": lead.id,
            "domain": lead.domain,
            "company_name": lead.company_name,
            "minio_asset_path": lead.minio_asset_path,
            "logo_url": lead.logo_url,
            "technology_stack": lead.technology_stack,
            "quality_score": lead.quality_score,
            "headquarters": lead.headquarters,
            "industry": lead.industry,
            "company_size": lead.company_size,
            "revenue_funding": lead.revenue_funding,
            "verified_emails": lead.verified_emails,
            "summary": lead.summary,
            "people": [
                {"name": p.full_name, "title": p.title, "linkedin_search_url": p.linkedin_search_url}
                for p in db.query(GlobalLeadPerson).filter(GlobalLeadPerson.global_lead_id == lead_id).all()
            ],
            "subpages": [
                {"url": s.page_url, "minio_object_path": s.minio_object_path}
                for s in db.query(GlobalLeadSubpage).filter(GlobalLeadSubpage.global_lead_id == lead_id).all()
            ]
        }
        set_master_lead_cache(clean_domain, lead_dict, ttl=604800) # 7 days
        add_verified_domain_set(clean_domain)
        release_crawl_lock(clean_domain)

        # 7. Update PostgreSQL Open Lake Record Status
        lake_rec = db.query(OpenLakeRecord).filter(OpenLakeRecord.domain == clean_domain).first()
        if lake_rec:
            lake_rec.enrichment_status = "enriched"
            db.commit()

        logger.info(f"✅ Vault Master Record successfully persisted for {clean_domain} (ID: {lead_id})")
        return lead

    @staticmethod
    def get_master_lead(db: Session, domain_or_id: str) -> Optional[Dict[str, Any]]:
        """
        Instant Retrieval Flow:
        1. Check Redis L1 Hot Cache: master:lead:{domain} (0.04ms)
        2. Fallback to SQLite WAL Master Vault: global_leads
        """
        clean_input = domain_or_id.lower().replace("www.", "").strip()

        # Check Hot Cache
        cached = get_master_lead_cache(clean_input)
        if cached:
            return cached

        # Check Database Vault
        lead = db.query(GlobalLead).filter(
            (GlobalLead.domain == clean_input) | (GlobalLead.id == clean_input)
        ).first()

        if not lead:
            return None

        # Build payload & backfill Hot Cache
        people = db.query(GlobalLeadPerson).filter(GlobalLeadPerson.global_lead_id == lead.id).all()
        subpages = db.query(GlobalLeadSubpage).filter(GlobalLeadSubpage.global_lead_id == lead.id).all()

        lead_dict = {
            "id": lead.id,
            "domain": lead.domain,
            "company_name": lead.company_name,
            "minio_asset_path": lead.minio_asset_path,
            "logo_url": lead.logo_url,
            "technology_stack": lead.technology_stack,
            "quality_score": lead.quality_score,
            "headquarters": lead.headquarters,
            "industry": lead.industry,
            "company_size": lead.company_size,
            "revenue_funding": lead.revenue_funding,
            "verified_emails": lead.verified_emails,
            "summary": lead.summary,
            "people": [
                {"name": p.full_name, "title": p.title, "linkedin_search_url": p.linkedin_search_url}
                for p in people
            ],
            "subpages": [
                {"url": s.page_url, "minio_object_path": s.minio_object_path}
                for s in subpages
            ]
        }
        set_master_lead_cache(lead.domain, lead_dict, ttl=604800)
        return lead_dict
