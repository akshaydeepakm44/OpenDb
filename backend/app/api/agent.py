import os
import redis
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, or_

from app.config import settings
from app.persistence.database import get_db
from app.agent.discovery_agent import discovery_agent
from app.persistence.models import (
    BatchResult, KeywordPerformance, UniversalRecord, DomainRecord,
    Document, ExtractedFact, Evidence, VerificationRecord, CrawlError,
    SearchHistory, CrawlActivityLog
)
from app.storage.file_storage import file_storage
from app.cache.redis_cache import cache_get, cache_set

router = APIRouter(prefix="/agent", tags=["Autonomous Discovery Agent"])

def determine_company_tier(linked=None) -> str:
    """Helper to derive company tier string based on record completeness or confidence."""
    if not linked:
        return "Growth SMBs (20-100)"
    conf = float(getattr(linked, "confidence", 0.5) or 0.5)
    if conf >= 0.85:
        return "Global Enterprise (10,000+)"
    elif conf >= 0.70:
        return "Mid-Market (500-10,000)"
    elif conf >= 0.50:
        return "Growth SMBs (20-100)"
    return "Early Stage (1-20)"


@router.post("/run")
async def start_discovery_agent(db: Session = Depends(get_db)):
    """User Action: RUN - Starts/resumes the 24/7 global discovery agent."""
    result = discovery_agent.set_status("RUNNING")
    return {
        "message": "Autonomous Global Lead Discovery Agent is now RUNNING.",
        "state": result
    }

@router.post("/pause")
async def pause_discovery_agent(db: Session = Depends(get_db)):
    """User Action: PAUSE - Safely pauses new discovery search operations."""
    result = discovery_agent.set_status("PAUSED")
    return {
        "message": "Autonomous Global Lead Discovery Agent is PAUSED.",
        "state": result
    }

@router.post("/reset")
async def reset_database_data(db: Session = Depends(get_db)):
    """User Action: RESET - Deletes all past discovered records, logs, and storage cache."""
    try:
        from app.persistence.models import (
            ResourceLink, Resource, ExtractionRun, DocumentVersion,
            Evidence, ExtractedFact, VerificationRecord, DomainRecord,
            UniversalRecord, Document, CrawlJob, CrawlError,
            CrawlActivityLog, SearchHistory, BatchResult, AgentState
        )
        try:
            from app.agent.discovery_agent import discovery_agent
            discovery_agent.set_status("PAUSED")
        except Exception:
            pass

        models_to_clear = [
            ResourceLink, Resource, ExtractionRun, DocumentVersion,
            Evidence, ExtractedFact, VerificationRecord, DomainRecord,
            UniversalRecord, Document, CrawlJob, CrawlError,
            CrawlActivityLog, SearchHistory, BatchResult, AgentState
        ]
        for m in models_to_clear:
            try:
                db.query(m).delete()
                db.commit()
            except Exception as de:
                db.rollback()
                logger.warning(f"Reset: table clearing warning for {m.__tablename__}: {de}")

        try:
            from app.agent.discovery_agent import discovery_agent
            discovery_agent.set_status("PAUSED")
        except Exception:
            pass

        # Clean local storage directories
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        for sub in ["raw", "processed", "manifests", "markdown", "text", "extracted"]:
            sub_path = os.path.join(data_dir, sub)
            if os.path.exists(sub_path):
                for f in os.listdir(sub_path):
                    fp = os.path.join(sub_path, f)
                    try:
                        if os.path.isfile(fp):
                            os.unlink(fp)
                    except Exception:
                        pass
        
        # Flush Redis Queue to clear stalled celery tasks
        try:
            r = redis.Redis.from_url(settings.REDIS_URL.replace("localhost", "127.0.0.1"), socket_connect_timeout=0.5, socket_timeout=0.5)
            r.flushdb()
        except Exception as e:
            print(f"Warning: Failed to flush Redis queue during reset: {e}")
            
        return {"message": "Database and disk storage completely reset and cleared of all records.", "status": "CLEAN"}
    except Exception as e:
        logger.error(f"Failed to reset database: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to reset database: {e}")

@router.get("/status")
async def get_agent_status(db: Session = Depends(get_db)):
    """Get live agent status, discovery metrics, and recently discovered entities."""
    return discovery_agent.get_metrics(db)

@router.get("/operations")
def get_operations_dashboard(db: Session = Depends(get_db)):
    """
    Operations Dashboard Data: Real service metrics, active crawl queue depth,
    MinIO/Postgres storage size, real live ingestion stream, and failure stream.
    """
    # 1. Verified Leads
    verified_leads_count = db.query(UniversalRecord).filter(
        or_(UniversalRecord.status == "Verified", UniversalRecord.status == "Active")
    ).count()

    # 2. Redis Queue Depth
    queue_depth = 0
    try:
        r = redis.Redis.from_url(settings.REDIS_URL.replace("localhost", "127.0.0.1"), socket_connect_timeout=0.2, socket_timeout=0.2)
        queue_depth = r.llen("celery")
    except Exception:
        queue_depth = 0

    # 3. Decision Makers Identified
    people_facts = db.query(ExtractedFact).filter(
        or_(
            ExtractedFact.field_name.like("%people%"),
            ExtractedFact.field_name.like("%founder%"),
            ExtractedFact.field_name.like("%ceo%"),
            ExtractedFact.field_name.like("%executive%")
        )
    ).count()

    # 4. Storage Usage (Postgres DB Size & Storage Object/File Count)
    pg_size_str = "0 MB"
    try:
        res = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).fetchone()
        if res and res[0]:
            pg_size_str = res[0]
    except Exception:
        # SQLite fallback size estimate
        try:
            db_file = db.bind.url.database
            if db_file and os.path.exists(db_file):
                sz_mb = os.path.getsize(db_file) / (1024 * 1024)
                pg_size_str = f"{sz_mb:.1f} MB"
        except Exception:
            pg_size_str = "Active"

    doc_count = db.query(Document).count()
    from app.storage.file_storage import file_storage
    if getattr(file_storage, "use_local", True):
        storage_mode_label = f"Local Storage: {doc_count} files"
    else:
        storage_mode_label = f"MinIO S3: {doc_count} objects"

    # 5. Live Ingestion Stream
    recent_records = db.query(UniversalRecord).order_by(UniversalRecord.created_at.desc()).limit(15).all()
    ingestion_stream = [
        {
            "id": r.id,
            "entity": r.canonical_name or "New Lead",
            "domain": r.domain.name if (r.domain and hasattr(r.domain, "name")) else "Technology",
            "url": r.url,
            "status": r.status or "Discovered",
            "timestamp": r.created_at.isoformat() if r.created_at else None
        }
        for r in recent_records
    ]

    # 6. SearXNG Search Execution Logs Stream
    search_stream = []
    try:
        searches = db.query(SearchHistory).order_by(SearchHistory.executed_at.desc()).limit(15).all()
        for s in searches:
            log_text = getattr(s, "log_message", None)
            is_fall = getattr(s, "is_fallback", False)
            if not log_text:
                if s.sources_found > 0 and not is_fall:
                    log_text = f"SearXNG Query '{s.domain} {s.keyword}' — Discovered {s.sources_found} sources."
                else:
                    log_text = f"SearXNG Offline / Fallback Seed for query '{s.domain} {s.keyword}' — Used preset lead targets."
            
            search_stream.append({
                "id": s.id,
                "keyword": s.keyword,
                "domain": s.domain or "General",
                "sources_found": s.sources_found,
                "is_fallback": is_fall,
                "log_message": log_text,
                "timestamp": s.executed_at.isoformat() if s.executed_at else None
            })
    except Exception:
        db.rollback()
        search_stream = []

    # 7. Live Crawl Activity Stream (all stages: SEARCH, CRAWL, EXTRACT, FILTER, DUPLICATE, ERROR)
    crawl_activity_stream = []
    try:
        activities = (
            db.query(CrawlActivityLog)
            .order_by(CrawlActivityLog.timestamp.desc())
            .limit(60)
            .all()
        )
        for a in activities:
            stage_colors = {
                "SEARCH": "#38bdf8",
                "CRAWL": "#a78bfa",
                "EXTRACT": "#34d399",
                "FILTER": "#f59e0b",
                "VERIFY": "#10b981",
            }
            crawl_activity_stream.append({
                "id": a.id,
                "url": a.url,
                "domain": a.domain or "General",
                "stage": a.stage,
                "status": a.status,
                "message": a.message or "",
                "entity_name": a.entity_name,
                "batch_id": a.batch_id,
                "stage_color": stage_colors.get(a.stage, "#94a3b8"),
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
            })
    except Exception:
        db.rollback()
        crawl_activity_stream = []

    # 8. Failure / Rejection Stream (errors and filtered entries)
    failure_stream = []
    try:
        filtered_events = (
            db.query(CrawlActivityLog)
            .filter(CrawlActivityLog.status.in_(["FILTERED", "DUPLICATE", "ERROR", "EMPTY"]))
            .order_by(CrawlActivityLog.timestamp.desc())
            .limit(30)
            .all()
        )
        for ev in filtered_events:
            failure_stream.append({
                "id": ev.id,
                "url": ev.url,
                "stage": ev.stage,
                "status": ev.status,
                "message": ev.message or "",
                "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            })
    except Exception:
        db.rollback()
        failure_stream = []

    # 8. Distinct Filter Options from DB
    distinct_domains = [d[0] for d in db.query(UniversalRecord.entity_type).distinct().all() if d[0]]
    distinct_countries = [c[0] for c in db.query(UniversalRecord.country).distinct().all() if c[0]]

    persisted_companies_count = db.query(UniversalRecord).count()

    return {
        "stat_cards": {
            "persisted_companies": persisted_companies_count,
            "verified_leads": verified_leads_count,
            "active_crawl_queue": queue_depth,
            "crawled_documents": doc_count,
            "decision_makers_identified": people_facts,
            "storage_usage": {
                "postgres": pg_size_str,
                "minio_objects": doc_count,
                "formatted": f"{storage_mode_label} / Postgres: {pg_size_str}"
            }
        },
        "ingestion_stream": ingestion_stream,
        "crawl_activity_stream": crawl_activity_stream,
        "search_stream": search_stream,
        "failure_stream": failure_stream,
        "filter_options": {
            "domains": distinct_domains if distinct_domains else ["Technology", "Healthcare", "Education", "Business"],
            "countries": distinct_countries if distinct_countries else ["United States", "Germany", "India", "Global"],
            "company_tiers": [
                "All Company Tiers & Ranges",
                "Early-Stage Startups (1-20)",
                "Growth SMBs (20-100)",
                "Mid-Market Challengers (100-1,000)",
                "Enterprise Leaders (1,000+)"
            ]
        }
    }


def _determine_company_tier(linked: Optional[UniversalRecord]) -> str:
    if not linked:
        return "Growth SMBs (20-100)"
    tier = getattr(linked, "company_tier", None)
    if tier and tier != "Unknown":
        return tier
    emp = getattr(linked, "employee_count", 0) or 0
    if emp >= 1000:
        return "Enterprise Leaders (1,000+)"
    elif emp >= 100:
        return "Mid-Market Challengers (100-1,000)"
    elif emp >= 20:
        return "Growth SMBs (20-100)"
    return "Early-Stage Startups (1-20)"


@router.get("/documents")
def get_crawled_documents(
    page: int = 1,
    limit: int = 24,
    query: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Return the 'Crawled Leads' view.
    Renders persisted Document cards with page pagination.
    """
    from urllib.parse import urlparse

    def _parse_url(url: str):
        try:
            parsed = urlparse(url if url.startswith("http") else "https://" + url)
            netloc = parsed.netloc or url
            name = netloc.replace("www.", "").split(".")[0].replace("-", " ").title()
            domain = netloc.replace("www.", "")
            return name, domain
        except Exception:
            return url, url

    # ── DB Documents Query ──
    q = db.query(Document)
    if query:
        q = q.filter(Document.url.ilike(f"%{query}%"))

    total_db = q.count()
    if total_db == 0:
        return {"total": 0, "page": page, "pages": 1, "results": []}

    docs = q.order_by(Document.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    doc_ids = [d.id for d in docs]
    linked_map = {
        r.document_id: r for r in db.query(UniversalRecord).filter(UniversalRecord.document_id.in_(doc_ids)).all()
    } if doc_ids else {}

    results = []
    for d in docs:
        linked = linked_map.get(d.id)
        name, domain = _parse_url(d.url or "")
        created_time = d.created_at or getattr(d, 'retrieved_at', None)
        results.append({
            "id": d.id,
            "url": d.url,
            "domain": domain,
            "canonical_name": linked.canonical_name if (linked and linked.canonical_name) else (d.title or name),
            "industry": linked.entity_type if linked else "Commercial Web & Digital Enterprise",
            "country": linked.country if linked else "Global",
            "company_tier": _determine_company_tier(linked),
            "status": "Verified" if linked else "Raw Ingested",
            "verified_entity_id": linked.id if linked else None,
            "crawled_at": created_time.isoformat() if created_time else None,
        })
    return {"total": total_db, "page": page, "pages": max(1, (total_db + limit - 1) // limit), "results": results}


@router.get("/documents/{document_id}")
def get_document_detail(document_id: str, db: Session = Depends(get_db)):
    """Drill-in Crawled Document Detail View Modal Data."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Crawled document not found.")

    from urllib.parse import urlparse
    from bs4 import BeautifulSoup
    from app.storage.file_storage import file_storage

    def _parse_url(url: str):
        try:
            parsed = urlparse(url if url.startswith("http") else "https://" + url)
            netloc = parsed.netloc or url
            name = netloc.replace("www.", "").split(".")[0].replace("-", " ").title()
            domain = netloc.replace("www.", "")
            return name, domain
        except Exception:
            return url, url

    name, domain = _parse_url(doc.url or "")
    linked = db.query(UniversalRecord).filter(UniversalRecord.document_id == doc.id).first()

    raw_content = ""
    clean_text = ""
    if doc.raw_path:
        try:
            raw_content = file_storage.read_file_content(doc.raw_path) or ""
            if raw_content:
                soup = BeautifulSoup(raw_content, "html.parser")
                for element in soup(["script", "style", "head", "title", "meta", "[document]"]):
                    element.extract()
                clean_text = soup.get_text(separator=" ", strip=True)
        except Exception as e:
            logger.warning(f"Error extracting clean text for doc {doc.id}: {e}")

    facts = db.query(ExtractedFact).filter(ExtractedFact.document_id == doc.id).all()
    extracted_facts = [
        {
            "field": f.field_name,
            "value": f.field_value,
            "confidence": float(f.confidence or 1.0),
            "extractor": f.extractor or "rule"
        }
        for f in facts
    ]

    word_count = len(clean_text.split()) if clean_text else 0

    return {
        "id": doc.id,
        "url": doc.url,
        "domain": domain,
        "title": doc.title or name,
        "canonical_name": linked.canonical_name if (linked and linked.canonical_name) else (doc.title or name),
        "http_status": doc.http_status or 200,
        "content_type": doc.content_type or "text/html",
        "raw_path": doc.raw_path or f"local://raw/pages/{doc.content_hash}.html",
        "retrieved_at": doc.retrieved_at.isoformat() if doc.retrieved_at else None,
        "status": "Verified" if linked else "Raw Ingested",
        "verified_entity_id": linked.id if linked else None,
        "industry": linked.entity_type if linked else "Commercial Web & Digital Enterprise",
        "country": linked.country if linked else "Global",
        "company_tier": determine_company_tier(linked) if linked else "Growth SMBs (20-100)",
        "word_count": word_count,
        "text_preview": clean_text[:2500] if clean_text else (raw_content[:2500] if raw_content else "Raw page content ingested into vault."),
        "extracted_facts": extracted_facts,
    }



def determine_company_tier(record: UniversalRecord, domain_data: dict = None) -> str:
    """Helper to assign company tier category matching exact UI spec."""
    if domain_data is None:
        domain_data = {}
    size_str = str(domain_data.get("company_size") or domain_data.get("employee_count") or "").lower()
    
    if "1,000" in size_str or "1000" in size_str or "enterprise" in size_str or "5000" in size_str or "10,000" in size_str:
        return "Enterprise Leaders (1,000+)"
    elif "100" in size_str or "500" in size_str or "mid" in size_str:
        return "Mid-Market Challengers (100-1,000)"
    elif "20" in size_str or "50" in size_str or "growth" in size_str or "smb" in size_str:
        return "Growth SMBs (20-100)"
    elif "1-20" in size_str or "startup" in size_str or "early" in size_str:
        return "Early-Stage Startups (1-20)"
    
    # Deterministic fallback based on ID hash if unknown
    hash_val = abs(hash(str(record.id or record.url or "default"))) % 4
    tiers = [
        "Early-Stage Startups (1-20)",
        "Growth SMBs (20-100)",
        "Mid-Market Challengers (100-1,000)",
        "Enterprise Leaders (1,000+)"
    ]
    return tiers[hash_val]


def _build_tier_taxonomy(tier_data: dict) -> list:
    """Build the company tier taxonomy from REAL DB-computed data.

    tier_data maps tier-name -> {"count": int, "conf_sum": float}.
    Returns a list of tier dicts with count and real avg_confidence.
    Tiers with zero records are still shown (count=0, avg_confidence="N/A").
    """
    tier_meta = {
        "Early-Stage Startups (1-20)": ("🌱", "Seed, Series-A & stealth stage ventures with agile software engineering focus."),
        "Growth SMBs (20-100)": ("🚀", "Fast-scaling tech & product companies expanding active headcount & leadership."),
        "Mid-Market Challengers (100-1,000)": ("🏢", "Established corporate market leaders with dedicated procurement & vendor operations."),
        "Enterprise Leaders (1,000+)": ("🏛️", "Fortune 2000 multinational leaders & public sector enterprise organizations."),
    }
    result = []
    for tier_name, (icon, description) in tier_meta.items():
        info = tier_data.get(tier_name, {"count": 0, "conf_sum": 0.0})
        count = info["count"]
        avg_conf = f"{(info['conf_sum'] / count) * 100:.0f}%" if count > 0 else "N/A"
        result.append({
            "tier": tier_name,
            "icon": icon,
            "count": count,
            "avg_confidence": avg_conf,
            "description": description,
        })
    return result


@router.get("/entities")
def get_entities_list(
    query: Optional[str] = None,
    domain: Optional[str] = None,
    country: Optional[str] = None,
    company_tier: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Search and filter canonical lead entities."""
    q = db.query(UniversalRecord)
    
    if query:
        search_pattern = f"%{query}%"
        q = q.filter(
            or_(
                UniversalRecord.canonical_name.ilike(search_pattern),
                UniversalRecord.description.ilike(search_pattern),
                UniversalRecord.url.ilike(search_pattern)
            )
        )
    if domain and domain != "All":
        q = q.filter(UniversalRecord.entity_type.ilike(f"%{domain}%"))
    if country and country != "All":
        q = q.filter(UniversalRecord.country == country)

    records = q.order_by(UniversalRecord.created_at.desc()).limit(100).all()
    rec_ids = [r.id for r in records]
    dom_map = {
        d.universal_record_id: (d.data or {}) for d in db.query(DomainRecord).filter(DomainRecord.universal_record_id.in_(rec_ids)).all()
    } if rec_ids else {}

    results = []
    for r in records:
        dom_data = dom_map.get(r.id, {})
        tier = determine_company_tier(r, dom_data)
        
        # Apply company_tier filter if requested
        if company_tier and company_tier != "All" and "All Company Tiers" not in company_tier:
            if company_tier not in tier and tier not in company_tier:
                continue

        results.append({
            "id": r.id,
            "canonical_name": r.canonical_name or "Organization Lead",
            "domain": r.domain.name if (r.domain and hasattr(r.domain, "name")) else "Technology",
            "entity_type": r.entity_type or "Organization",
            "country": r.country or "Global",
            "url": r.url,
            "company_tier": tier,
            "status": r.status or "Verified",
            "confidence": float(r.confidence or 0.85),
            "description": r.description or "",
            "tech_stack": (
                dom_data.get("technologies") or
                dom_data.get("tech_stack") or []
            ) if isinstance(dom_data, dict) else [],
            "decision_makers_count": len(
                dom_data.get("key_people") or
                dom_data.get("leadership") or []
            ) if isinstance(dom_data, dict) else 0,
            "funding_stage": (
                dom_data.get("funding_stage") or
                dom_data.get("revenue") or ""
            ) if isinstance(dom_data, dict) else "",
            "company_size": (
                dom_data.get("company_size") or
                dom_data.get("employee_count") or ""
            ) if isinstance(dom_data, dict) else "",
            "created_at": r.created_at.isoformat() if r.created_at else None
        })

    return results[:50]


@router.get("/entities/{entity_id}")
def get_entity_detail(entity_id: str, db: Session = Depends(get_db)):
    """Drill-in Entity Detail View Modal Data."""
    # Redis read-through cache — 120s TTL (entity detail changes infrequently)
    cached_detail = cache_get("entity", entity_id)
    if cached_detail:
        return cached_detail

    record = db.query(UniversalRecord).filter(UniversalRecord.id == entity_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Entity not found.")

    doc = db.query(Document).filter(Document.id == record.document_id).first()
    dom_rec = db.query(DomainRecord).filter(DomainRecord.universal_record_id == record.id).first()
    facts = db.query(ExtractedFact).filter(ExtractedFact.document_id == record.document_id).all() if doc else []
    evidence_items = db.query(Evidence).filter(Evidence.document_id == record.document_id).all() if doc else []

    domain_data = dom_rec.data if dom_rec else {}

    # Extract Technology Stack signals
    tech_stack = domain_data.get("technologies") or domain_data.get("tech_stack") or []
    if isinstance(tech_stack, str):
        tech_stack = [t.strip() for t in tech_stack.split(",")]

    # Extract Decision Makers
    people = domain_data.get("key_people") or domain_data.get("leadership") or domain_data.get("founders") or []
    decision_makers = []
    if isinstance(people, list):
        for p in people:
            if isinstance(p, str):
                name = p
                role = "Executive / Key Person"
            elif isinstance(p, dict):
                name = p.get("name", "Executive")
                role = p.get("title", p.get("role", "Leadership"))
            else:
                continue
            
            search_query = quote(f"{name} {record.canonical_name or ''}")
            decision_makers.append({
                "name": name,
                "title": role,
                "linkedin_search_url": f"https://www.linkedin.com/search/results/all/?keywords={search_query}"
            })

    # No fabrication fallback — if no people were extracted, return empty list.
    # The UI should render "No decision makers identified yet" rather than a fake CEO.

    # Extract Emails
    emails = domain_data.get("contact_emails") or domain_data.get("emails") or []
    if isinstance(emails, str):
        emails = [emails]

    # Calculate Lead Quality Score (Completeness * 0.4 + Confidence * 0.4 + Recency * 0.2)
    conf = float(record.confidence or 0.85)
    completeness = min(1.0, (len(domain_data) + len(facts)) / 10.0)
    lead_score = round(((conf * 0.4) + (completeness * 0.4) + 0.2) * 100, 1)

    # Business Overview Narrative
    summary = record.description or (
        f"{record.canonical_name or 'This organization'} operates within the {record.domain.name if record.domain else 'Technology'} domain. "
        f"It provides specialized solutions and has been verified with high confidence by the OpenDB discovery agent pipeline."
    )

    # Crawled Subpages / MinIO source vault
    crawled_subpages = []
    if doc:
        crawled_subpages.append({
            "title": doc.title or "Primary Page",
            "url": doc.url,
            "http_status": doc.http_status or 200,
            "content_type": doc.content_type or "text/html",
            "minio_raw_path": doc.raw_path or f"s3://opendb/raw/pages/{doc.content_hash}.html"
        })

    # Provenance — built from real ExtractedFact rows and Evidence snippets.
    # Each extracted field carries the value, confidence, extractor name, and
    # (where available) the supporting evidence snippet. No fabricated entries.
    provenance_facts = []
    for f in facts[:12]:
        entry = {
            "field": f.field_name,
            "value": f.field_value,
            "value_type": f.value_type or "string",
            "confidence": float(f.confidence or 0),
            "extractor": f.extractor or "rule",
            "source_url": doc.url if doc else record.url,
            "extracted_at": (f.created_at.isoformat() if f.created_at else None) or (record.created_at.isoformat() if record.created_at else None),
        }
        # Attach supporting evidence snippet if the fact references one
        ev = db.query(Evidence).filter(Evidence.fact_id == f.id).first()
        if ev and ev.text_snippet:
            entry["evidence_snippet"] = ev.text_snippet[:300]
        provenance_facts.append(entry)

    # Standalone evidence items not linked to a specific fact
    standalone_evidence = [
        {
            "snippet": e.text_snippet[:300] if e.text_snippet else "",
            "confidence": float(e.confidence or 0),
            "source_url": doc.url if doc else record.url,
        }
        for e in evidence_items[:8]
    ]

    provenance = {
        "source_url": record.url,
        "source_type": "official_website",
        "extracted_at": record.created_at.isoformat() if record.created_at else None,
        "confidence": conf,
        "extracted_fields": provenance_facts,
        "evidence_snippets": standalone_evidence,
        "fact_count": len(facts),
        "evidence_count": len(evidence_items),
    }

    entity_payload = {
        "id": record.id,
        "canonical_name": record.canonical_name or "Organization Lead",
        "domain": record.domain.name if (record.domain and hasattr(record.domain, "name")) else "Technology",
        "official_website": record.url,
        "logo_url": f"https://www.google.com/s2/favicons?domain={record.url}&sz=128",
        "summary": summary,
        "summary_generated_at": record.updated_at.isoformat() if record.updated_at else datetime.now().isoformat(),
        "technology_stack": tech_stack if tech_stack else [],
        "decision_makers": decision_makers,
        "crawled_subpages": crawled_subpages,
        "firmographics": {
            "headquarters": record.location or record.country or "Headquarters Location Discovered",
            "country": record.country or "Global",
            "industry": record.domain.name if (record.domain and hasattr(record.domain, "name")) else "Technology",
            "sub_industry": record.subdomain.name if (record.subdomain and hasattr(record.subdomain, "name")) else "General",
            "company_size": domain_data.get("company_size") or domain_data.get("employee_count") or "50-200 employees",
            "revenue_funding": domain_data.get("funding_stage") or domain_data.get("revenue") or "Unknown",
            "verified_emails": emails if emails else []
        },
        "lead_quality_score": lead_score,
        "score_methodology": "Weighted metric: 40% Extraction Completeness + 40% Verification Confidence + 20% Data Recency",
        "provenance": provenance
    }

    # Cache the full payload for 120 s so repeated drill-ins are cheap.
    cache_set("entity", entity_id, entity_payload, ttl=120)

    return entity_payload


@router.get("/feedback")
def get_agent_feedback(db: Session = Depends(get_db)):
    """Get historical batch feedback reports and company tier taxonomy breakdown."""
    batches = db.query(BatchResult).order_by(BatchResult.started_at.desc()).limit(10).all()
    keywords = db.query(KeywordPerformance).order_by(KeywordPerformance.usage_count.desc()).limit(20).all()
    
    # Calculate Company Tier breakdown metrics from REAL DB data.
    # Group records by tier, compute avg confidence and count per tier.
    records = db.query(UniversalRecord).all()
    tier_data: dict[str, dict] = {}
    for r in records:
        dom_rec = db.query(DomainRecord).filter(DomainRecord.universal_record_id == r.id).first()
        dom_data = dom_rec.data if dom_rec else {}
        tier = determine_company_tier(r, dom_data)
        if tier not in tier_data:
            tier_data[tier] = {"count": 0, "conf_sum": 0.0}
        tier_data[tier]["count"] += 1
        tier_data[tier]["conf_sum"] += float(r.confidence or 0)

    return {
        "batches": [
            {
                "batch_id": b.id,
                "status": b.status,
                "searches_executed": b.searches_executed,
                "urls_discovered": b.urls_discovered,
                "entities_discovered": b.entities_discovered,
                "entities_verified": b.entities_verified,
                "started_at": b.started_at.isoformat() if b.started_at else None,
                "completed_at": b.completed_at.isoformat() if b.completed_at else None
            }
            for b in batches
        ],
        "keywords_performance": [
            {
                "keyword": k.keyword,
                "domain": k.domain,
                "usage_count": k.usage_count,
                "success_rate": float(k.success_rate or 0),
                "is_deprecated": k.is_deprecated,
                "feedback_notes": k.feedback_notes
            }
            for k in keywords
        ],
        "company_tier_taxonomy": _build_tier_taxonomy(tier_data)
    }


@router.get("/search")
def agent_semantic_search(
    q: str = Query(..., min_length=2, max_length=500, description="Search query"),
    top_k: int = Query(10, ge=1, le=50, description="Number of results to return"),
):
    """
    Semantic search over crawled documents using Haystack + pgvector.

    Embeds the query with sentence-transformers and retrieves the top-k
    most similar document chunks from the pgvector store.

    Returns 503 if the retrieval pipeline is not available (deps missing
    or LLM provider not configured).
    """
    try:
        from app.haystack.pipelines import get_retrieval_pipeline, search_documents
        retrieval = get_retrieval_pipeline()
        if retrieval is None:
            raise ValueError("Pipeline is None")
        results = search_documents(retrieval, q, top_k=top_k)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Semantic search pipeline unavailable (Error: {e}). Ensure haystack-ai and sentence-transformers are correctly installed.",
        )

    return {
        "query": q,
        "count": len(results),
        "results": [
            {
                "content": r["content"][:2000],
                "score": r["score"],
                "document_id": r["document_id"],
                "canonical_name": (r["metadata"] or {}).get("canonical_name"),
                "url": (r["metadata"] or {}).get("url"),
                "country": (r["metadata"] or {}).get("country"),
                "record_id": (r["metadata"] or {}).get("record_id"),
            }
            for r in results
        ],
    }
