"""
Transactional Outbox Synchronization Service
§18 of Master Directive: SQLite (Operational Staging) -> PostgreSQL (Durable Intelligence Store)
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from urllib.parse import urlparse

from app.config import settings
from app.persistence.models import PostgresSyncOutbox, GlobalLead, GlobalLeadPerson, utc_now

logger = logging.getLogger(__name__)

class OutboxSyncService:
    """
    Manages the two-stage storage pipeline:
    1. Operational staging in SQLite (global_leads, global_lead_people, crawl_activity_log).
    2. Reliable, asynchronous synchronization of qualified leads to PostgreSQL.
    3. Failure resilience: Never silently claims sync succeeded if PostgreSQL is down.
    """

    def __init__(self):
        self._pg_engine = None
        self._pg_sessionmaker = None

    def get_postgres_session(self) -> Optional[Session]:
        """Attempt to establish session with PostgreSQL Lake. Returns None if unreachable."""
        db_url = settings.DATABASE_URL.replace("localhost", "127.0.0.1")
        if "postgresql" not in db_url:
            return None

        try:
            parsed = urlparse(db_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 5432
            
            import socket
            with socket.create_connection((host, port), timeout=0.5):
                pass

            if not self._pg_engine:
                self._pg_engine = create_engine(
                    db_url,
                    pool_pre_ping=True,
                    connect_args={"connect_timeout": 2}
                )
                self._pg_sessionmaker = sessionmaker(autocommit=False, autoflush=False, bind=self._pg_engine)

            return self._pg_sessionmaker()
        except Exception as e:
            logger.debug(f"[OutboxSync] PostgreSQL not reachable ({e})")
            return None

    def queue_for_postgres_sync(
        self,
        db: Session,
        domain: str,
        company_name: str,
        payload: Dict[str, Any]
    ) -> PostgresSyncOutbox:
        """Queue a qualified lead into SQLite outbox for synchronization to PostgreSQL."""
        clean_domain = domain.lower().replace("www.", "").strip()
        
        # Check if already in outbox
        existing = db.query(PostgresSyncOutbox).filter(
            PostgresSyncOutbox.domain == clean_domain
        ).first()

        if existing:
            existing.company_name = company_name
            existing.payload_json = payload
            existing.sync_status = "PENDING_SYNC"
            existing.created_at = utc_now()
            db.commit()
            return existing
        else:
            entry = PostgresSyncOutbox(
                domain=clean_domain,
                company_name=company_name,
                payload_json=payload,
                sync_status="PENDING_SYNC",
                retry_count=0
            )
            db.add(entry)
            db.commit()
            return entry

    def process_outbox_queue(self, db: Session, limit: int = 10) -> Dict[str, Any]:
        """
        Processes pending sync items from SQLite outbox to PostgreSQL Lake.
        Accurately records sync success, failure, and timestamps without pretending success.
        """
        pending_items = db.query(PostgresSyncOutbox).filter(
            PostgresSyncOutbox.sync_status.in_(["PENDING_SYNC", "SYNC_FAILED"])
        ).limit(limit).all()

        if not pending_items:
            return {"processed": 0, "synced": 0, "failed": 0, "status": "idle"}

        pg_db = self.get_postgres_session()
        if not pg_db:
            # PostgreSQL is offline: honestly record status without data loss
            for item in pending_items:
                item.sync_status = "PENDING_SYNC"
                item.error_message = "PostgreSQL durable store is temporarily unreachable. Data safely preserved in SQLite staging."
            db.commit()
            logger.info(f"[OutboxSync] {len(pending_items)} items staged in SQLite waiting for PostgreSQL availability.")
            return {"processed": len(pending_items), "synced": 0, "failed": len(pending_items), "status": "postgres_unreachable"}

        synced_count = 0
        failed_count = 0

        try:
            for item in pending_items:
                try:
                    payload = item.payload_json or {}
                    # Sync to PostgreSQL GlobalLead table
                    pg_lead = pg_db.query(GlobalLead).filter(GlobalLead.domain == item.domain).first()
                    if not pg_lead:
                        pg_lead = GlobalLead(
                            id=payload.get("id") or item.id,
                            domain=item.domain,
                            company_name=item.company_name,
                            logo_url=payload.get("logo_url"),
                            technology_stack=payload.get("technology_stack", []),
                            quality_score=float(payload.get("quality_score", 8.5)),
                            headquarters=payload.get("headquarters"),
                            industry=payload.get("industry"),
                            company_size=payload.get("company_size", "UNKNOWN"),
                            revenue_funding=payload.get("revenue_funding"),
                            verified_emails=payload.get("verified_emails", []),
                            summary=payload.get("summary"),
                            linkedin_url=payload.get("company_linkedin_url")
                        )
                        pg_db.add(pg_lead)
                    else:
                        pg_lead.company_name = item.company_name
                        pg_lead.summary = payload.get("summary")
                        pg_lead.company_size = payload.get("company_size", "UNKNOWN")
                        if payload.get("headquarters"):
                            pg_lead.headquarters = payload.get("headquarters")
                        if payload.get("industry"):
                            pg_lead.industry = payload.get("industry")
                    
                    pg_db.commit()

                    # Mark SQLite outbox entry as SYNCED
                    item.sync_status = "SYNCED"
                    item.synced_at = utc_now()
                    item.error_message = None
                    synced_count += 1

                except Exception as sync_err:
                    pg_db.rollback()
                    item.sync_status = "SYNC_FAILED"
                    item.retry_count = (item.retry_count or 0) + 1
                    item.error_message = str(sync_err)[:300]
                    failed_count += 1
                    logger.warning(f"[OutboxSync] Failed syncing {item.domain} to PostgreSQL: {sync_err}")

            db.commit()
        finally:
            pg_db.close()

        logger.info(f"✅ [OutboxSync] Processed {len(pending_items)}: Synced={synced_count}, Failed={failed_count}")
        return {"processed": len(pending_items), "synced": synced_count, "failed": failed_count, "status": "completed"}


outbox_sync_service = OutboxSyncService()
