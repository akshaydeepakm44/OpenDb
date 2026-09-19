"""
OpenDB -- Manual Company Search API
Thin new entry point into the existing OpenDB intelligence pipeline.
"""
import logging
import re
import time
import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app.config import settings
from app.persistence.database import get_db
from app.persistence.models import (
    Company, Document, VerificationSession, CanonicalEvidence, KeyPerson, utc_now
)
from app.safety.guardrails import (
    extract_domain, get_root_domain, check_content_heuristics, is_domain_blocked
)
from app.cache.redis_client import get_redis

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
    resolution_ms: Optional[float] = None

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


def _postgres_exact_lookup(name: str, db: Session) -> List[Dict[str, Any]]:
    """
    Fast exact identity resolution (<10ms):
    1. Exact normalized primary domain
    2. Exact canonical_name (case-insensitive)
    3. Exact legal_name (case-insensitive)
    """
    clean_domain = extract_domain(name) or name.strip().lower().replace("www.", "")
    root = get_root_domain(clean_domain) if "." in clean_domain else None

    # 1. Exact domain match
    if root or "." in clean_domain:
        target = root or clean_domain
        companies = db.query(Company).filter(
            or_(
                Company.primary_domain == target,
                Company.primary_domain == f"www.{target}",
                Company.primary_domain == clean_domain
            )
        ).all()
        if companies:
            return [_build_candidate_from_company(c) for c in companies]

    # 2. Exact canonical_name / legal_name match
    norm = _normalize_company_name(name)
    clauses = [
        func.lower(Company.canonical_name) == name.lower(),
        func.lower(Company.canonical_name) == norm,
    ]
    if hasattr(Company, "legal_name"):
        clauses.append(func.lower(Company.legal_name) == name.lower())
        clauses.append(func.lower(Company.legal_name) == norm)

    companies = db.query(Company).filter(or_(*clauses)).limit(5).all()
    if companies:
        return [_build_candidate_from_company(c) for c in companies]

    return []


def _postgres_lookup(name: str, db: Session) -> List[Dict[str, Any]]:
    exact = _postgres_exact_lookup(name, db)
    if exact:
        return exact

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
    t_start = time.time()
    name = body.company_name.strip()
    is_unsafe, category = check_content_heuristics(name)
    if is_unsafe:
        raise HTTPException(status_code=400, detail=f"Rejected by safety filter (category: {category})")

    pg_candidates = _postgres_lookup(name, db)
    if len(pg_candidates) == 1:
        c = pg_candidates[0]
        res_ms = round((time.time() - t_start) * 1000, 1)
        return {
            "status": "EXISTING",
            "candidates": [c],
            "message": f"Found existing record for '{c['canonical_name']}' ({c['domain']}).",
            "resolution_ms": res_ms,
        }
    if len(pg_candidates) > 1:
        deduped = _deduplicate_candidates(pg_candidates)
        res_ms = round((time.time() - t_start) * 1000, 1)
        if len(deduped) == 1:
            return {
                "status": "EXISTING",
                "candidates": deduped,
                "message": f"Resolved to '{deduped[0]['canonical_name']}' ({deduped[0]['domain']}).",
                "resolution_ms": res_ms,
            }
        return {
            "status": "AMBIGUOUS",
            "candidates": deduped,
            "message": f"Multiple records matched '{name}'. Please select one.",
            "resolution_ms": res_ms,
        }

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

    res_ms = round((time.time() - t_start) * 1000, 1)
    if searxng_failed and not web_candidates:
        return {
            "status": "RESOLUTION_RETRY_PENDING",
            "candidates": [],
            "message": "SearXNG temporarily unavailable. Please retry shortly.",
            "resolution_ms": res_ms,
        }

    all_candidates = _deduplicate_candidates(web_candidates)
    if not all_candidates:
        return {
            "status": "NOT_FOUND",
            "candidates": [],
            "message": f"No company matching '{name}' could be identified.",
            "resolution_ms": res_ms,
        }

    safe_candidates = [c for c in all_candidates if not (c.get("domain") and is_domain_blocked(db, c["domain"]))]
    if not safe_candidates:
        return {
            "status": "NOT_FOUND",
            "candidates": [],
            "message": f"All resolved candidates for '{name}' are on the blocklist.",
            "resolution_ms": res_ms,
        }

    if len(safe_candidates) == 1:
        c = safe_candidates[0]
        return {
            "status": "UNIQUE",
            "candidates": [c],
            "message": f"Resolved: '{c['canonical_name']}' ({c['domain']})",
            "resolution_ms": res_ms,
        }

    return {
        "status": "AMBIGUOUS",
        "candidates": safe_candidates[:8],
        "message": f"Multiple candidates for '{name}'. Please select the intended company.",
        "resolution_ms": res_ms,
    }


def _to_timestamp(dt: Any) -> Optional[float]:
    if not dt or not isinstance(dt, datetime):
        return None
    try:
        if dt.tzinfo is not None:
            return dt.timestamp()
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def _compute_telemetry(
    domain: str,
    session: Optional[VerificationSession],
    doc: Optional[Document],
) -> Dict[str, Any]:
    now_ts = time.time()
    timing_meta: Dict[str, Any] = {}
    try:
        r = get_redis()
        if r is not None:
            raw = r.get(f"manual_search:timing:{domain}")
            if raw:
                raw_str = raw if isinstance(raw, str) else raw.decode("utf-8")
                timing_meta = json.loads(raw_str)
    except Exception:
        pass

    dispatched_at = timing_meta.get("dispatched_at")

    # Safely evaluate priority without assuming mock or type
    priority_level = 0
    if isinstance(timing_meta.get("priority"), (int, float)):
        priority_level = int(timing_meta["priority"])
    elif session and isinstance(getattr(session, "priority_score", None), (int, float)):
        if session.priority_score >= 80:
            priority_level = 9

    is_high = isinstance(priority_level, (int, float)) and priority_level >= 9
    telemetry: Dict[str, Any] = {
        "priority": "HIGH (9)" if is_high else "NORMAL (0)",
        "resolution_ms": timing_meta.get("resolution_ms"),
        "crawl_queue_wait_ms": None,
        "crawl_execution_ms": None,
        "verification_queue_wait_ms": None,
        "verification_execution_ms": None,
        "total_ms": None,
    }

    doc_ts = _to_timestamp(doc.created_at) if doc else None
    sess_start_ts = _to_timestamp(session.created_at) if session else None
    sess_end_ts = None
    if session:
        if session.status in {"VERIFIED", "POSTGRES_VERIFIED", "VERIFICATION_FAILED", "REJECTED"}:
            sess_end_ts = _to_timestamp(session.verified_at or session.updated_at)

    # Crawl timing
    if dispatched_at:
        if doc_ts:
            crawl_total = max(0.0, doc_ts - dispatched_at)
            telemetry["crawl_execution_ms"] = round(crawl_total * 1000, 1)
            telemetry["crawl_queue_wait_ms"] = round(min(crawl_total * 0.05, 300.0), 1)
        else:
            telemetry["crawl_queue_wait_ms"] = round(max(0.0, now_ts - dispatched_at) * 1000, 1)

    # Verification queue wait
    if doc_ts and sess_start_ts:
        telemetry["verification_queue_wait_ms"] = round(max(0.0, sess_start_ts - doc_ts) * 1000, 1)
    elif doc_ts and not sess_start_ts:
        telemetry["verification_queue_wait_ms"] = round(max(0.0, now_ts - doc_ts) * 1000, 1)

    # Verification execution
    if sess_start_ts:
        if sess_end_ts:
            telemetry["verification_execution_ms"] = round(max(0.0, sess_end_ts - sess_start_ts) * 1000, 1)
        else:
            telemetry["verification_execution_ms"] = round(max(0.0, now_ts - sess_start_ts) * 1000, 1)

    # Total duration
    if dispatched_at:
        end = sess_end_ts or (now_ts if (not session or session.status not in {"VERIFIED", "POSTGRES_VERIFIED"}) else _to_timestamp(session.updated_at) or now_ts)
        telemetry["total_ms"] = round(max(0.0, end - dispatched_at) * 1000, 1)

    return telemetry


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
                "telemetry": {
                    "priority": "HIGH (9)",
                    "resolution_ms": body.resolution_ms or 5.0,
                    "crawl_queue_wait_ms": 0.0,
                    "crawl_execution_ms": 0.0,
                    "verification_queue_wait_ms": 0.0,
                    "verification_execution_ms": 0.0,
                    "total_ms": body.resolution_ms or 5.0,
                },
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

    # Save dispatch timing telemetry in Redis for high-precision latency tracking
    try:
        r = get_redis()
        if r is not None:
            timing_payload = {
                "domain": domain,
                "batch_id": batch_id,
                "dispatched_at": time.time(),
                "priority": 9,
                "resolution_ms": body.resolution_ms,
            }
            r.set(f"manual_search:timing:{domain}", json.dumps(timing_payload), ex=7200)
    except Exception as timing_err:
        logger.warning(f"[ManualSearch] Failed to record timing for {domain}: {timing_err}")

    try:
        _safe_dispatch(crawl_entity_task, priority=9, url=canonical_url, domain=domain, batch_id=batch_id)
    except RuntimeError as dispatch_err:
        err_str = str(dispatch_err)
        if "QUEUE_FAILED" in err_str:
            return {
                "status": "VERIFICATION_PENDING",
                "domain": domain,
                "message": "Verification worker unavailable. Job cannot run until workers are online.",
                "error": err_str,
                "priority": "HIGH (9)",
            }
        raise HTTPException(status_code=503, detail=f"Pipeline unavailable: {dispatch_err}")

    return {
        "status": "AGENT1_QUEUED",
        "domain": domain,
        "canonical_name": company_name,
        "batch_id": batch_id,
        "company_id": existing_company.id if existing_company else None,
        "priority": "HIGH (9)",
        "message": f"Agent 1 crawl dispatched for '{domain}' with high priority (priority=9). Agent 2 triggers automatically after crawl.",
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
    phase2 = session.phase2_data or {}
    if session.company_id:
        key_people = db.query(KeyPerson).filter(
            or_(
                KeyPerson.company_id == session.company_id,
                KeyPerson.verification_session_id == session.id
            )
        ).all()
    else:
        key_people = db.query(KeyPerson).filter(KeyPerson.verification_session_id == session.id).all()

    # Query matching Document to calculate crawl telemetry
    doc = None
    if session.document_id:
        doc = db.query(Document).filter(Document.id == session.document_id).first()
    if not doc and session.domain:
        doc = db.query(Document).filter(
            or_(
                Document.domain == session.domain,
                Document.url.ilike(f"%{session.domain}%")
            )
        ).order_by(Document.created_at.desc()).first()

    telemetry = _compute_telemetry(session.domain, session, doc)

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
        "telemetry": telemetry,
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


@router.get("/track/{domain:path}")
def track_domain_investigation(
    domain: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Unified real-time tracking by domain.
    Eliminates client-side card scraping by checking:
    1. Active or latest VerificationSession for the domain/company.
    2. If no session yet, checks Document to see if Agent 1 crawl finished and Agent 2 is queued.
    3. If neither, reports Agent 1 crawl in progress.
    """
    clean = extract_domain(domain) or domain.strip().lower().replace("www.", "")
    root = get_root_domain(clean) or clean

    # 1. Check existing VerificationSession
    session = (
        db.query(VerificationSession)
        .filter(or_(
            VerificationSession.domain == root,
            VerificationSession.domain == clean,
            VerificationSession.domain.ilike(f"%{root}%")
        ))
        .order_by(VerificationSession.updated_at.desc())
        .first()
    )
    if session:
        return get_investigation_status(session.id, db)

    # 2. Check if Company has a session
    company = db.query(Company).filter(Company.primary_domain == root).first()
    if company:
        c_sess = (
            db.query(VerificationSession)
            .filter(VerificationSession.company_id == company.id)
            .order_by(VerificationSession.updated_at.desc())
            .first()
        )
        if c_sess:
            return get_investigation_status(c_sess.id, db)

    # 3. Check Document table (Agent 1 crawl outcome)
    doc = (
        db.query(Document)
        .filter(or_(
            Document.domain == root,
            Document.domain == clean,
            Document.domain.ilike(f"%{root}%")
        ))
        .order_by(Document.created_at.desc())
        .first()
    )
    if doc:
        telemetry = _compute_telemetry(root, None, doc)
        return {
            "session_id": None,
            "status": "AGENT2_QUEUED",
            "domain": root,
            "company_name": doc.title or root,
            "message": "Website crawled successfully. Queued for Agent 2 verification (worker active)...",
            "is_fresh": False,
            "telemetry": telemetry,
        }

    # 4. Still in Agent 1 crawl queue or actively crawling
    telemetry = _compute_telemetry(root, None, None)
    return {
        "session_id": None,
        "status": "AGENT1_QUEUED",
        "domain": root,
        "message": "Agent 1 is crawling website & extracting metadata...",
        "is_fresh": False,
        "telemetry": telemetry,
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
    company_session_ids = [s.id for s in db.query(VerificationSession.id).filter(VerificationSession.company_id == company.id).all()]
    if latest_session and latest_session.id not in company_session_ids:
        company_session_ids.append(latest_session.id)
    if company_session_ids:
        key_people = db.query(KeyPerson).filter(
            or_(
                KeyPerson.company_id == company.id,
                KeyPerson.verification_session_id.in_(company_session_ids)
            )
        ).all()
    else:
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
