"""
OpenDB -- Manual Company Search API
Thin new entry point into the existing OpenDB intelligence pipeline.
"""
import logging
import re
import uuid
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.config import settings
from app.persistence.database import get_db
from app.persistence.models import (
    Company, VerificationSession, CanonicalEvidence, KeyPerson, utc_now
)
from app.safety.guardrails import (
    extract_domain, get_root_domain, check_content_heuristics, is_domain_blocked
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/manual-search", tags=["Manual Search"])


class ResolveRequest(BaseModel):
    company_name: str

    @field_validator("company_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 2:
            raise ValueError("company_name must be at least 2 characters")
        if len(v) > 200:
            raise ValueError("company_name must be under 200 characters")
        return v


class InvestigateRequest(BaseModel):
    company_name: str
    domain: str

    @field_validator("domain")
    @classmethod
    def domain_not_empty(cls, v: str) -> str:
        v = v.strip().lower().replace("www.", "")
        if not v or "." not in v:
            raise ValueError("domain must be a valid domain name")
        return v


_LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|incorporated|llc\.?|ltd\.?|limited|corp\.?|corporation|"
    r"co\.?|company|group|holdings|gmbh|ag|plc|sa|sas|bv|nv|oy|ab)\b",
    re.IGNORECASE,
)
_PRIVATE_IP_RE = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0|localhost)"
)
_SKIP_URL_PATTERNS = [
    "linkedin.com/company", "crunchbase.com", "glassdoor.com", "indeed.com",
    "twitter.com", "facebook.com", "wikipedia.org", "bloomberg.com",
    "reuters.com", "techcrunch.com", "wired.com", "forbes.com",
    "businessinsider.com", "ycombinator.com", "pitchbook.com", "owler.com",
    "zoominfo.com", "dnb.com",
]


def _normalize_company_name(name: str) -> str:
    clean = _LEGAL_SUFFIXES.sub("", name).strip(" ,.-")
    return re.sub(r"\s+", " ", clean).lower()


def _is_private_ip_domain(domain: str) -> bool:
    return bool(_PRIVATE_IP_RE.match(domain.strip()))


def _ttl_seconds() -> int:
    days = getattr(settings, "COMPANY_INTELLIGENCE_TTL_DAYS", 7)
    return int(days) * 86400


def _is_session_fresh(session) -> bool:
    if not session:
        return False
    if session.status not in {"VERIFIED", "POSTGRES_VERIFIED"}:
        return False
    ts = session.updated_at or session.verified_at
    if not ts:
        return False
    now = utc_now()
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return (now - ts).total_seconds() < _ttl_seconds()


def _build_candidate_from_company(company) -> Dict[str, Any]:
    return {
        "company_id": company.id,
        "canonical_name": company.canonical_name,
        "domain": company.primary_domain,
        "source": "postgres",
        "status": company.status,
    }


def _build_candidate_from_url(url: str, title: str, snippet: str) -> Optional[Dict[str, Any]]:
    domain = extract_domain(url)
    if not domain:
        return None
    if _is_private_ip_domain(domain):
        return None
    root = get_root_domain(domain)
    if not root:
        return None
    return {
        "company_id": None,
        "canonical_name": title or root.split(".")[0].title(),
        "domain": root,
        "source": "searxng",
        "snippet": snippet or "",
        "status": "NEW",
    }


def _deduplicate_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        root = get_root_domain(c.get("domain", ""))
        if not root:
            continue
        if root not in seen or c.get("source") == "postgres":
            seen[root] = c
    return list(seen.values())


def _postgres_lookup(name: str, db: Session) -> List[Dict[str, Any]]:
    norm = _normalize_company_name(name)
    patterns = list({f"%{name}%", f"%{norm}%"})
    clauses = []
    for pat in patterns:
        clauses.append(Company.canonical_name.ilike(pat))
        clauses.append(Company.primary_domain.ilike(pat))
        if Company.legal_name is not None:
            clauses.append(Company.legal_name.ilike(pat))
    companies = db.query(Company).filter(or_(*clauses)).limit(10).all()
    return [_build_candidate_from_company(c) for c in companies]


@router.post("/resolve")
async def resolve_company(
    body: ResolveRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Stage 1: Company Resolution.
    Does NOT crawl or invoke Agent 1/2.
    """
    name = body.company_name.strip()
    is_unsafe, category = check_content_heuristics(name)
    if is_unsafe:
        raise HTTPException(status_code=400, detail=f"Rejected by safety filter (category: {category})")

    pg_candidates = _postgres_lookup(name, db)
    if len(pg_candidates) == 1:
        c = pg_candidates[0]
        return {"status": "EXISTING", "candidates": [c], "message": f"Found existing record for '{c['canonical_name']}' ({c['domain']})."}
    if len(pg_candidates) > 1:
        deduped = _deduplicate_candidates(pg_candidates)
        if len(deduped) == 1:
            return {"status": "EXISTING", "candidates": deduped, "message": f"Resolved to '{deduped[0]['canonical_name']}' ({deduped[0]['domain']})."}
        return {"status": "AMBIGUOUS", "candidates": deduped, "message": f"Multiple records matched '{name}'. Please select one."}

    from app.crawler.searxng_service import searxng_service
    web_candidates: List[Dict[str, Any]] = []
    searxng_failed = False
    queries = [f'"{name}" official website', f'"{name}" company']
    for q in queries:
        try:
            results, _fb, _log = await searxng_service.search_with_meta(query=q, max_results=8)
            for r in results:
                url = r.get("url", "")
                if not url or any(p in url.lower() for p in _SKIP_URL_PATTERNS):
                    continue
                cand = _build_candidate_from_url(url=url, title=r.get("title", ""), snippet=r.get("snippet", ""))
                if cand:
                    web_candidates.append(cand)
            if web_candidates:
                break
        except Exception as e:
            logger.warning(f"[ManualSearch] SearXNG error for '{q}': {e}")
            searxng_failed = True

    if searxng_failed and not web_candidates:
        return {"status": "RESOLUTION_RETRY_PENDING", "candidates": [], "message": "SearXNG temporarily unavailable. Please retry shortly."}

    all_candidates = _deduplicate_candidates(web_candidates)
    if not all_candidates:
        return {"status": "NOT_FOUND", "candidates": [], "message": f"No company matching '{name}' could be identified."}

    safe_candidates = [c for c in all_candidates if not (c.get("domain") and is_domain_blocked(db, c["domain"]))]
    if not safe_candidates:
        return {"status": "NOT_FOUND", "candidates": [], "message": f"All resolved candidates for '{name}' are on the blocklist."}

    if len(safe_candidates) == 1:
        c = safe_candidates[0]
        return {"status": "UNIQUE", "candidates": [c], "message": f"Resolved: '{c['canonical_name']}' ({c['domain']})"}

    return {"status": "AMBIGUOUS", "candidates": safe_candidates[:8], "message": f"Multiple candidates for '{name}'. Please select the intended company."}


@router.post("/investigate")
def investigate_company(
    body: InvestigateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Stage 2: Cache Check then Pipeline Dispatch.
    Agent 1->Agent 2 handoff is performed automatically INSIDE crawl_entity_task.
    This endpoint does NOT call agent2_process_card_task directly.
    """
    company_name = body.company_name.strip()
    domain = body.domain.strip().lower().replace("www.", "")

    is_unsafe, category = check_content_heuristics(f"{company_name} {domain}")
    if is_unsafe:
        raise HTTPException(status_code=400, detail=f"Rejected by safety filter (category: {category})")
    if _is_private_ip_domain(domain):
        raise HTTPException(status_code=400, detail="Domain resolves to a private IP range -- rejected.")
    if is_domain_blocked(db, domain):
        raise HTTPException(status_code=400, detail=f"Domain '{domain}' is on the safety blocklist.")

    existing_company = db.query(Company).filter(Company.primary_domain == domain).first()
    if existing_company:
        latest_session = (
            db.query(VerificationSession)
            .filter(VerificationSession.company_id == existing_company.id)
            .order_by(VerificationSession.updated_at.desc())
            .first()
        )
        if latest_session and _is_session_fresh(latest_session):
            ts = latest_session.updated_at or latest_session.verified_at
            return {
                "status": "CACHE_HIT",
                "company_id": existing_company.id,
                "session_id": latest_session.id,
                "domain": domain,
                "canonical_name": existing_company.canonical_name,
                "message": "Fresh verified intelligence found. No new crawl required.",
                "last_verified": ts.isoformat() if ts else None,
                "ttl_days": getattr(settings, "COMPANY_INTELLIGENCE_TTL_DAYS", 7),
                "status_url": f"/api/manual-search/status/{latest_session.id}",
            }

        in_progress = {
            "AGENT2_QUEUED", "PHASE1_RANKED", "PHASE1_VERIFYING", "PHASE1_RECRAWL_REQUIRED",
            "PHASE1_VERIFIED", "PHASE2_SYNTHESIS", "LINKEDIN_DISCOVERY",
            "LINKEDIN_CANDIDATES_FOUND", "LINKEDIN_PROFILE_CRAWL",
            "PERSON_MATCHING", "FINAL_VERIFICATION", "POSTGRES_SYNC_PENDING",
        }
        running_session = (
            db.query(VerificationSession)
            .filter(VerificationSession.company_id == existing_company.id, VerificationSession.status.in_(in_progress))
            .order_by(VerificationSession.updated_at.desc())
            .first()
        )
        if running_session:
            return {
                "status": "ALREADY_RUNNING",
                "company_id": existing_company.id,
                "session_id": running_session.id,
                "domain": domain,
                "canonical_name": existing_company.canonical_name,
                "current_status": running_session.status,
                "message": "An investigation is already running. Attach to existing progress.",
                "status_url": f"/api/manual-search/status/{running_session.id}",
            }

    from app.worker.tasks import crawl_entity_task, _safe_dispatch
    canonical_url = f"https://{domain}/"
    batch_id = f"manual-{str(uuid.uuid4())[:8]}"

    try:
        _safe_dispatch(crawl_entity_task, url=canonical_url, domain=domain, batch_id=batch_id)
    except RuntimeError as dispatch_err:
        err_str = str(dispatch_err)
        if "QUEUE_FAILED" in err_str:
            return {"status": "VERIFICATION_PENDING", "domain": domain,
                    "message": "Verification worker unavailable. Job cannot run until workers are online.",
                    "error": err_str}
        raise HTTPException(status_code=503, detail=f"Pipeline unavailable: {dispatch_err}")

    return {
        "status": "AGENT1_QUEUED",
        "domain": domain,
        "canonical_name": company_name,
        "batch_id": batch_id,
        "company_id": existing_company.id if existing_company else None,
        "message": f"Agent 1 crawl dispatched for '{domain}'. Agent 2 triggers automatically after crawl.",
        "poll_hint": "Use GET /api/agent2/cards to find the verification session by domain.",
    }


@router.get("/status/{session_id}")
def get_investigation_status(
    session_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Thin read-only proxy over existing VerificationSession + CanonicalEvidence + KeyPerson."""
    session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    company = db.query(Company).filter(Company.id == session.company_id).first() if session.company_id else None
    evidence_items = db.query(CanonicalEvidence).filter(CanonicalEvidence.verification_session_id == session_id).all()
    key_people = db.query(KeyPerson).filter(KeyPerson.company_id == session.company_id).all() if session.company_id else []
    phase2 = session.phase2_data or {}

    return {
        "session_id": session.id,
        "status": session.status,
        "domain": session.domain,
        "company_name": session.company_name,
        "company_id": session.company_id,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "verified_at": session.verified_at.isoformat() if session.verified_at else None,
        "is_fresh": _is_session_fresh(session),
        "company": {
            "canonical_name": company.canonical_name,
            "primary_domain": company.primary_domain,
            "headquarters": company.headquarters,
            "industry": company.industry,
            "employee_range": company.employee_range,
            "linkedin_url": company.linkedin_url,
            "verified_emails": company.verified_emails or [],
            "description": company.description,
        } if company else None,
        "intelligence": {
            "headquarters": phase2.get("location_region") or phase2.get("headquarters"),
            "contact_email": phase2.get("verified_contact_email"),
            "company_size": phase2.get("company_size_tier") or phase2.get("company_size"),
            "linkedin_page": phase2.get("corporate_linkedin_url"),
            "description": phase2.get("business_overview"),
        },
        "evidence": [{"field": e.field_name, "value": e.value, "status": e.verification_status, "source_url": e.source_url, "snippet": e.evidence_snippet} for e in evidence_items],
        "key_people": [{"name": p.full_name, "title": p.title, "linkedin_url": p.linkedin_url, "verification_status": p.verification_status, "confidence": p.confidence_score} for p in key_people if p.verification_status != "REJECTED"],
        "error": session.error_message,
    }


@router.get("/result/{domain:path}")
def get_company_result(
    domain: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Read-only dossier by domain. Queries existing PostgreSQL tables -- no crawl triggered."""
    clean_domain = extract_domain(domain) or domain.strip().lower().replace("www.", "")
    root = get_root_domain(clean_domain) or clean_domain

    company = db.query(Company).filter(Company.primary_domain == root).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"No company record found for domain '{root}'.")

    latest_session = (
        db.query(VerificationSession)
        .filter(VerificationSession.company_id == company.id)
        .order_by(VerificationSession.updated_at.desc())
        .first()
    )
    evidence_items = db.query(CanonicalEvidence).filter(CanonicalEvidence.company_id == company.id).all()
    key_people = db.query(KeyPerson).filter(KeyPerson.company_id == company.id).all()
    phase2 = (latest_session.phase2_data or {}) if latest_session else {}

    return {
        "company_id": company.id,
        "canonical_name": company.canonical_name,
        "primary_domain": company.primary_domain,
        "status": company.status,
        "industry": company.industry,
        "description": company.description,
        "headquarters": company.headquarters,
        "employee_range": company.employee_range,
        "linkedin_url": company.linkedin_url,
        "verified_emails": company.verified_emails or [],
        "quality_score": company.quality_score,
        "confidence": company.confidence,
        "created_at": company.created_at.isoformat() if company.created_at else None,
        "updated_at": company.updated_at.isoformat() if company.updated_at else None,
        "verification_status": latest_session.status if latest_session else "NOT_VERIFIED",
        "last_verified": ((latest_session.verified_at or latest_session.updated_at).isoformat() if latest_session and (latest_session.verified_at or latest_session.updated_at) else None),
        "is_fresh": _is_session_fresh(latest_session) if latest_session else False,
        "intelligence": {
            "headquarters": phase2.get("location_region") or company.headquarters,
            "contact_email": phase2.get("verified_contact_email"),
            "company_size": phase2.get("company_size_tier") or company.employee_range,
            "linkedin_page": phase2.get("corporate_linkedin_url") or company.linkedin_url,
            "description": phase2.get("business_overview") or company.description,
        },
        "evidence": [{"field": e.field_name, "value": e.value, "status": e.verification_status, "source_url": e.source_url, "snippet": e.evidence_snippet} for e in evidence_items],
        "key_people": [{"name": p.full_name, "title": p.title, "linkedin_url": p.linkedin_url, "verification_status": p.verification_status, "confidence": p.confidence_score} for p in key_people if p.verification_status != "REJECTED"],
    }
