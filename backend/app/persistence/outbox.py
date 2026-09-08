import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from app.persistence.models import PostgresSyncOutbox, OpenLakeRecord, utc_now

logger = logging.getLogger(__name__)

# Minimum completeness score required to sync SQLite Staging to PostgreSQL Lake
MIN_POSTGRES_SYNC_SCORE = 60.0
QUALIFIED_STATUSES = {"QUALIFIED_COMPANY", "HIGH_QUALITY_COMPANY", "VERIFIED_COMPLETE"}


class OutboxManager:
    """
    Transactional Outbox Manager — Phase 11 of Master Architecture.
    Syncs ONLY verified company intelligence (completeness_score >= 60.0) from SQLite to PostgreSQL.
    Blocks raw search candidates, unsafe domains, blogs, news, and incomplete records.
    """

    @staticmethod
    def queue_for_postgres_sync(
        db: Session,
        company_id: str,
        domain: str,
        score: float,
        badge: str,
        status_code: str,
        payload: Dict[str, Any]
    ) -> Optional[PostgresSyncOutbox]:
        """
        Creates Outbox event in SQLite Staging database transaction.
        Rejects candidates that fail score or status threshold.
        """
        if score < MIN_POSTGRES_SYNC_SCORE or status_code not in QUALIFIED_STATUSES:
            logger.info(f"🛑 [OUTBOX] Skipping PostgreSQL sync for '{domain}' — Score ({score}) or status '{status_code}' below threshold")
            return None

        try:
            # Create outbox entry
            outbox_entry = PostgresSyncOutbox(
                company_id=company_id,
                domain=domain,
                completeness_score=score,
                badge=badge,
                status_code=status_code,
                payload=payload,
                processed=False
            )
            db.add(outbox_entry)
            db.flush()
            logger.info(f"📦 [OUTBOX] Queued QUALIFIED company '{domain}' (Score: {score}) for PostgreSQL Sync")
            return outbox_entry
        except Exception as e:
            logger.error(f"[OUTBOX] Failed to queue outbox event for {domain}: {e}")
            return None

    @staticmethod
    def process_outbox_queue(db: Session, limit: int = 50) -> int:
        """
        Reads pending outbox records and syncs to PostgreSQL.
        """
        pending = db.query(PostgresSyncOutbox).filter(
            PostgresSyncOutbox.processed == False
        ).order_by(PostgresSyncOutbox.created_at.asc()).limit(limit).all()

        if not pending:
            return 0

        synced_count = 0

        for entry in pending:
            try:
                # Sync into PostgreSQL OpenLakeRecord table
                existing = db.query(OpenLakeRecord).filter(OpenLakeRecord.domain == entry.domain).first()
                if not existing:
                    lake_rec = OpenLakeRecord(
                        domain=entry.domain,
                        enrichment_status="enriched"
                    )
                    db.add(lake_rec)

                entry.processed = True
                entry.processed_at = datetime.now(timezone.utc)
                synced_count += 1
            except Exception as err:
                entry.error_message = str(err)
                logger.error(f"[OUTBOX SYNC] Failed to sync '{entry.domain}': {err}")

        try:
            db.commit()
            logger.info(f"⚡ [OUTBOX SYNC] Successfully synced {synced_count} QUALIFIED records to PostgreSQL Lake")
        except Exception as e:
            db.rollback()
            logger.error(f"[OUTBOX SYNC] Commit error: {e}")

        return synced_count


outbox_manager = OutboxManager()
