import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.persistence.database import get_db
from app.persistence.models import BlockedDomain, QuarantinedContent, ManualReviewQueue
from app.safety.guardrails import add_to_blocklist, extract_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/safety", tags=["Admin Safety Guardrails"])


@router.get("/blocked-domains")
def list_blocked_domains(
    reason: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """List all blocked domains with reason, category, and provenance source."""
    query = db.query(BlockedDomain)
    if reason:
        query = query.filter(BlockedDomain.reason_category == reason)
    if source:
        query = query.filter(BlockedDomain.source == source)

    total = query.count()
    items = query.order_by(BlockedDomain.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": b.id,
                "domain": b.domain,
                "reason_category": b.reason_category,
                "source": b.source,
                "created_at": b.created_at.isoformat() if b.created_at else None
            }
            for b in items
        ]
    }


@router.post("/blocked-domains")
def add_blocked_domain_admin(
    domain: str,
    reason_category: str = "manual_admin_override",
    db: Session = Depends(get_db)
):
    """Manually add a domain to the blocklist table."""
    entry = add_to_blocklist(db, url_or_domain=domain, reason_category=reason_category, source="manual_admin")
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid domain or database error.")
    return {"status": "blocked", "domain": entry.domain, "id": entry.id}


@router.delete("/blocked-domains/{domain}")
def remove_blocked_domain(domain: str, db: Session = Depends(get_db)):
    """Remove a domain from the blocklist."""
    clean = extract_domain(domain)
    entry = db.query(BlockedDomain).filter(BlockedDomain.domain == clean).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Domain not found in blocklist.")
    db.delete(entry)
    db.commit()
    return {"status": "unblocked", "domain": clean}


@router.get("/review-queue")
def list_review_queue(
    status: str = Query("pending"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """List items in the manual review queue."""
    items = db.query(ManualReviewQueue).filter(ManualReviewQueue.status == status).order_by(ManualReviewQueue.created_at.desc()).limit(limit).all()
    return {
        "count": len(items),
        "items": [
            {
                "id": r.id,
                "url": r.url,
                "domain": r.domain,
                "item_type": r.item_type,
                "content_snippet": r.content_snippet,
                "score": float(r.score or 0.0),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in items
        ]
    }


@router.post("/review-queue/{item_id}/approve")
def approve_review_item(item_id: str, db: Session = Depends(get_db)):
    """Approve a manual review queue item (marked safe by admin)."""
    item = db.query(ManualReviewQueue).filter(ManualReviewQueue.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found.")
    item.status = "approved"
    db.commit()
    return {"status": "approved", "item_id": item_id, "url": item.url}


@router.post("/review-queue/{item_id}/reject")
def reject_review_item(item_id: str, reason_category: str = "manual_review_reject", db: Session = Depends(get_db)):
    """Reject a manual review item & add its domain to the blocklist."""
    item = db.query(ManualReviewQueue).filter(ManualReviewQueue.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found.")
    item.status = "rejected"
    add_to_blocklist(db, url_or_domain=item.domain, reason_category=reason_category, source="manual_review")
    db.commit()
    return {"status": "rejected_and_blocked", "item_id": item_id, "domain": item.domain}


@router.get("/quarantine")
def list_quarantine_events(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """List quarantined content events."""
    items = db.query(QuarantinedContent).order_by(QuarantinedContent.created_at.desc()).limit(limit).all()
    return {
        "count": len(items),
        "items": [
            {
                "id": q.id,
                "document_id": q.document_id,
                "url": q.url,
                "domain": q.domain,
                "content_type": q.content_type,
                "flagged_categories": q.flagged_categories,
                "confidence_score": float(q.confidence_score or 0.0),
                "reason": q.reason,
                "created_at": q.created_at.isoformat() if q.created_at else None
            }
            for q in items
        ]
    }
