import logging
from typing import Any, Dict
from sqlalchemy import or_

logger = logging.getLogger(__name__)


def get_operational_snapshot(db) -> Dict[str, Any]:
    # Central Agent 1 telemetry snapshot - single source of truth for all dashboard stats.
    from app.persistence.models import Document, Company, VerificationSession, CanonicalEvidence

    snapshot: Dict[str, Any] = {
        "raw_documents_count": 0,
        "tab_counts": {"crawled": 0, "in_verification": 0, "verified": 0},
        "queues": {"crawl_queued": 0, "discovery_queued": 0, "verification_queued": 0, "active_crawl_queue": 0, "redis_available": False},
        "slots": {"standard_active": 0, "deep_active": 0, "total_active": 0},
        "companies": {"persisted": 0, "verified": 0},
        "decision_makers": 0,
        "degraded": False,
    }

    try:
        snapshot["raw_documents_count"] = db.query(Document).count()
    except Exception:
        try: db.rollback()
        except Exception: pass

    try:
        snapshot["companies"]["persisted"] = db.query(Company).count()
        snapshot["companies"]["verified"] = db.query(Company).filter(
            Company.status.in_(["VERIFIED", "Verified", "POSTGRES_VERIFIED"])
        ).count()
    except Exception:
        try: db.rollback()
        except Exception: pass

    try:
        snapshot["tab_counts"]["in_verification"] = db.query(VerificationSession).count()
        snapshot["tab_counts"]["verified"] = snapshot["companies"]["verified"]
    except Exception:
        try: db.rollback()
        except Exception: pass

    try:
        snapshot["decision_makers"] = db.query(CanonicalEvidence).filter(
            or_(
                CanonicalEvidence.field_name.like("%people%"),
                CanonicalEvidence.field_name.like("%founder%"),
                CanonicalEvidence.field_name.like("%ceo%"),
                CanonicalEvidence.field_name.like("%executive%"),
            )
        ).count()
    except Exception:
        try: db.rollback()
        except Exception: pass

    redis_available = False
    crawl_queued = 0
    discovery_queued = 0
    verification_queued = 0

    try:
        from app.config import settings
        from urllib.parse import urlparse
        import redis as redis_lib
        p = urlparse(settings.REDIS_URL.replace("localhost", "127.0.0.1"))
        r = redis_lib.Redis(
            host=p.hostname or "127.0.0.1",
            port=p.port or 6379,
            password=p.password or getattr(settings, "REDIS_PASSWORD", None),
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            retry_on_timeout=False,
        )
        r.ping()
        redis_available = True
        # Query real named queues used by Celery routing.
        # The "celery" key is NOT used for routing and must NOT be queried.
        crawl_queued = r.llen("crawl") or 0
        discovery_queued = r.llen("discovery") or 0
        verification_queued = r.llen("verification") or 0
    except Exception as redis_err:
        logger.debug(f"[Telemetry] Redis unavailable: {redis_err}")
        snapshot["degraded"] = True

    snapshot["queues"].update({
        "redis_available": redis_available,
        "crawl_queued": crawl_queued,
        "discovery_queued": discovery_queued,
        "verification_queued": verification_queued,
    })

    slot_info = {"standard_active": 0, "deep_active": 0, "total_active": 0}
    if redis_available:
        try:
            from app.crawler.distributed_slot_manager import slot_manager
            slot_info = slot_manager.get_total_active_crawls()
        except Exception:
            pass
    snapshot["slots"] = slot_info

    # Active Crawl Queue = queued tasks + currently executing browser slots.
    # Returns None (not 0) when Redis is offline - frontend shows 'Unavailable'.
    if redis_available:
        snapshot["queues"]["active_crawl_queue"] = crawl_queued + slot_info.get("total_active", 0)
    else:
        snapshot["queues"]["active_crawl_queue"] = None

    # Crawled tab count equals raw document count (both from PostgreSQL Document table).
    snapshot["tab_counts"]["crawled"] = snapshot["raw_documents_count"]
    return snapshot
