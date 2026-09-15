"""
OpenDB — Agent 2 API Endpoints
REST interface for Agent 2 company-intelligence verification:
Queue management, explicit card processing, field-level evidence inspection,
LinkedIn candidate audit, and chronological session timelines.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.persistence.database import get_db
from app.persistence.models import (
    Document, Agent2VerificationSession, Agent2Evidence, Agent2PersonCandidate
)
from app.agent.agent2_orchestrator import agent2_orchestrator
from app.worker.tasks import agent2_process_card_task, agent2_rank_cards_task

router = APIRouter()


@router.get("/status")
def get_agent2_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return Agent 2 operational status and queue metrics."""
    pending_count = db.query(Document).filter(
        Document.lifecycle_state == "CRAWLED_PENDING_AGENT_2"
    ).count()

    total_sessions = db.query(Agent2VerificationSession).count()
    verifying_count = db.query(Agent2VerificationSession).filter(
        Agent2VerificationSession.status.in_([
            "AGENT2_QUEUED", "PHASE1_RANKED", "PHASE1_VERIFYING", "PHASE1_RECRAWL_REQUIRED",
            "PHASE1_VERIFIED", "PHASE2_SYNTHESIS", "LINKEDIN_DISCOVERY", "LINKEDIN_CANDIDATES_FOUND",
            "LINKEDIN_PROFILE_CRAWL", "PERSON_MATCHING", "FINAL_VERIFICATION", "POSTGRES_SYNC_PENDING"
        ])
    ).count()
    verified_count = db.query(Agent2VerificationSession).filter(
        Agent2VerificationSession.status.in_(["VERIFIED", "POSTGRES_VERIFIED"])
    ).count()
    blocked_count = db.query(Agent2VerificationSession).filter(
        Agent2VerificationSession.status.in_([
            "PHASE1_BLOCKED", "CRAWL_FAILED", "INSUFFICIENT_EVIDENCE", "LLM_DEGRADED",
            "LINKEDIN_SEARCH_FAILED", "LINKEDIN_EVIDENCE_INSUFFICIENT", "VERIFICATION_FAILED"
        ])
    ).count()

    return {
        "status": "operational",
        "queue": {
            "pending_crawled_cards": pending_count,
            "total_agent2_sessions": total_sessions,
            "actively_verifying": verifying_count,
            "verified": verified_count,
            "blocked": blocked_count
        }
    }


@router.get("/cards")
def list_agent2_cards(
    status: Optional[str] = None,
    query: Optional[str] = None,
    page: int = 1,
    limit: int = 24,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    List verification cards across states:
    CRAWLED_PENDING_AGENT_2 (unprocessed), in-verification sessions, or verified.
    """
    # 1. Look up Agent 2 sessions
    q = db.query(Agent2VerificationSession)
    if status and status != "ALL":
        if status == "VERIFIED":
            q = q.filter(Agent2VerificationSession.status.in_(["VERIFIED", "POSTGRES_VERIFIED"]))
        elif status == "BLOCKED":
            q = q.filter(Agent2VerificationSession.status.like("%BLOCKED%"))
        else:
            q = q.filter(Agent2VerificationSession.status == status)

    if query:
        search_pat = f"%{query}%"
        q = q.filter(
            (Agent2VerificationSession.domain.ilike(search_pat)) |
            (Agent2VerificationSession.company_name.ilike(search_pat))
        )

    total = q.count()
    sessions = q.order_by(
        Agent2VerificationSession.priority_score.desc(),
        Agent2VerificationSession.created_at.desc()
    ).offset((page - 1) * limit).limit(limit).all()

    results = []
    for s in sessions:
        results.append({
            "session_id": s.id,
            "document_id": s.document_id,
            "domain": s.domain,
            "company_name": s.company_name,
            "status": s.status,
            "priority_score": s.priority_score,
            "priority_reasons": s.priority_reasons or [],
            "recrawl_count": s.recrawl_count,
            "search_rounds": s.search_rounds,
            "verified_at": s.verified_at.isoformat() if s.verified_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "business_overview": (s.phase2_data or {}).get("business_overview", {}).get("text"),
            "verified_industry": (s.phase1_data or {}).get("industry_sector", {}).get("value"),
            "verified_location": (s.phase1_data or {}).get("location_region", {}).get("value"),
            "verified_contact": (s.phase1_data or {}).get("verified_contact_email", {}).get("value"),
        })

    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total > 0 else 1,
        "results": results
    }


@router.get("/cards/{session_id}")
def get_agent2_card_detail(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return full card detail including Phase 1 evidence, Phase 2 synthesis, and LinkedIn candidate audit."""
    session = db.query(Agent2VerificationSession).filter(
        Agent2VerificationSession.id == session_id
    ).first()

    if not session:
        # Check if caller passed document_id
        session = db.query(Agent2VerificationSession).filter(
            Agent2VerificationSession.document_id == session_id
        ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Agent 2 session not found")

    evidence_rows = db.query(Agent2Evidence).filter(
        Agent2Evidence.session_id == session.id
    ).all()

    candidates_rows = db.query(Agent2PersonCandidate).filter(
        Agent2PersonCandidate.session_id == session.id
    ).all()

    return {
        "session_id": session.id,
        "document_id": session.document_id,
        "domain": session.domain,
        "company_name": session.company_name,
        "status": session.status,
        "priority_score": session.priority_score,
        "priority_reasons": session.priority_reasons or [],
        "phase1_data": session.phase1_data or {},
        "phase2_data": session.phase2_data or {},
        "recrawl_count": session.recrawl_count,
        "search_rounds": session.search_rounds,
        "verified_at": session.verified_at.isoformat() if session.verified_at else None,
        "evidence": [
            {
                "field": e.field_name,
                "value": e.value,
                "status": e.verification_status,
                "source_url": e.source_url,
                "evidence_snippet": e.evidence_snippet,
                "verification_method": e.verification_method,
                "investigation": e.investigation_record or {}
            }
            for e in evidence_rows
        ],
        "person_candidates": [
            {
                "name": c.person_name,
                "linkedin_url": c.linkedin_url,
                "title": c.title,
                "company": c.company,
                "candidate_status": c.candidate_status,
                "company_match_status": c.company_match_status,
                "is_leadership": c.is_leadership,
                "rejection_reason": c.rejection_reason,
                "evidence_snippet": c.evidence_snippet
            }
            for c in candidates_rows
        ],
        "timeline": session.investigation_log or []
    }


@router.post("/process/{document_id}")
def trigger_agent2_process(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Explicitly triggers Agent 2 verification on a Document card in state CRAWLED_PENDING_AGENT_2.
    Ensures that Agent 1 NEVER automatically invokes Agent 2.
    """
    doc_id_str = str(document_id)
    doc = None
    try:
        doc = db.query(Document).filter(Document.id == doc_id_str).first()
    except Exception:
        pass
    if not doc:
        try:
            import uuid as _uuid
            doc = db.query(Document).filter(Document.id == _uuid.UUID(doc_id_str)).first()
        except Exception:
            pass

    if not doc:
        raise HTTPException(status_code=404, detail="Document card not found")

    session = agent2_orchestrator.get_or_create_session(doc_id_str, db)
    if not session:
        raise HTTPException(status_code=500, detail="Failed to initialize Agent 2 session")

    # Dispatch Celery background task
    try:
        agent2_process_card_task.delay(doc_id_str)
        dispatch_method = "celery_async"
    except Exception as e:
        # If Celery broker is temporarily unreachable in dev, execute via background_tasks
        import asyncio
        background_tasks.add_task(asyncio.run, agent2_orchestrator.execute_full_verification(doc_id_str))
        dispatch_method = f"fastapi_background (celery fallback: {e})"

    return {
        "status": "queued",
        "session_id": session.id,
        "document_id": doc_id_str,
        "domain": session.domain,
        "dispatch_method": dispatch_method
    }


@router.post("/rank")
def trigger_agent2_ranking(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Explicitly trigger priority ranking on all unverified crawled cards."""
    try:
        res = agent2_rank_cards_task.delay()
        return {"status": "dispatched", "task_id": res.id}
    except Exception:
        # Synchronous execution fallback
        pending_docs = db.query(Document).filter(
            Document.lifecycle_state == "CRAWLED_PENDING_AGENT_2"
        ).all()
        ranked = []
        for doc in pending_docs:
            session = agent2_orchestrator.get_or_create_session(doc.id, db)
            if session:
                score = agent2_orchestrator.rank_card(session, db)
                ranked.append({"domain": session.domain, "priority_score": score})
        return {"status": "success", "ranked_count": len(ranked), "results": ranked}


@router.get("/{session_id}/evidence")
def get_agent2_evidence(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve field-by-field provenance, snippets, and investigation audit records."""
    evidence_rows = db.query(Agent2Evidence).filter(
        Agent2Evidence.session_id == session_id
    ).all()
    return {
        "session_id": session_id,
        "total_evidence_fields": len(evidence_rows),
        "evidence": [
            {
                "field": e.field_name,
                "value": e.value,
                "status": e.verification_status,
                "source_url": e.source_url,
                "evidence_snippet": e.evidence_snippet,
                "verification_method": e.verification_method,
                "investigation": e.investigation_record or {}
            }
            for e in evidence_rows
        ]
    }


@router.get("/{session_id}/linkedin")
def get_agent2_linkedin(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve discovered candidate profiles, match statuses, and explicit rejection reasons."""
    candidates_rows = db.query(Agent2PersonCandidate).filter(
        Agent2PersonCandidate.session_id == session_id
    ).all()
    return {
        "session_id": session_id,
        "total_candidates": len(candidates_rows),
        "candidates": [
            {
                "name": c.person_name,
                "linkedin_url": c.linkedin_url,
                "title": c.title,
                "company": c.company,
                "candidate_status": c.candidate_status,
                "company_match_status": c.company_match_status,
                "is_leadership": c.is_leadership,
                "rejection_reason": c.rejection_reason,
                "evidence_snippet": c.evidence_snippet
            }
            for c in candidates_rows
        ]
    }


@router.get("/{session_id}/timeline")
def get_agent2_timeline(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve chronological session timeline audit log."""
    session = db.query(Agent2VerificationSession).filter(
        Agent2VerificationSession.id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Agent 2 session not found")
    return {
        "session_id": session.id,
        "domain": session.domain,
        "current_status": session.status,
        "timeline": session.investigation_log or []
    }
