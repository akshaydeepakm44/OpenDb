"""
PostgreSQL Synchronization Worker — §3 & §8 of Master Rules

Promotes verified company intelligence records from the SQLite operational staging database
into the permanent PostgreSQL verified intelligence database using atomic transactions.

If PostgreSQL is unavailable:
Record status remains safely stored in SQLite as POSTGRES_SYNC_PENDING.
No verified data is lost, and the pipeline continues operating uninterrupted.
"""
import logging
from typing import Dict, Any
from app.persistence.database import StagingSessionLocal, VerifiedSessionLocal, IS_POSTGRES_AVAILABLE
from app.persistence.models import UniversalRecord, DomainRecord, GlobalLead, ExtractedFact, Evidence, RecordState, PostgresSyncOutbox, Company, utc_now

logger = logging.getLogger(__name__)

def process_postgres_outbox() -> Dict[str, Any]:
    """
    Durable Outbox pattern: Process pending PostgresSyncOutbox entries from SQLite staging DB to PostgreSQL master database.
    """
    staging_db = StagingSessionLocal()
    processed_count = 0

    try:
        pending_outbox = (
            staging_db.query(PostgresSyncOutbox)
            .filter(PostgresSyncOutbox.sync_status == "PENDING")
            .limit(100)
            .all()
        )

        if not pending_outbox:
            return {"status": "NO_PENDING_OUTBOX", "processed": 0}

        if not IS_POSTGRES_AVAILABLE or VerifiedSessionLocal is None:
            logger.info(f"ℹ️ PostgreSQL unavailable. {len(pending_outbox)} outbox items safely retained in SQLite PostgresSyncOutbox.")
            return {"status": "POSTGRES_OFFLINE", "retained_in_outbox": len(pending_outbox)}

        pg_db = VerifiedSessionLocal()
        try:
            for item in pending_outbox:
                try:
                    payload = item.payload or {}
                    domain = payload.get("domain")

                    if item.entity_type == "COMPANY" and domain:
                        existing_lead = pg_db.query(GlobalLead).filter(GlobalLead.domain == domain).first()
                        if not existing_lead:
                            lead = GlobalLead(
                                id=payload.get("id"),
                                domain=domain,
                                company_name=payload.get("company_name", "Qualified Company"),
                                industry=payload.get("industry", "Technology"),
                                quality_score=float(payload.get("quality_score", 60.0)),
                                summary=f"Company qualified with confidence score {payload.get('quality_score')}."
                            )
                            pg_db.add(lead)
                        else:
                            existing_lead.company_name = payload.get("company_name", existing_lead.company_name)
                            existing_lead.quality_score = float(payload.get("quality_score", existing_lead.quality_score))
                        
                        pg_db.commit()

                    item.sync_status = "SYNCED"
                    item.synced_at = utc_now()
                    staging_db.commit()
                    processed_count += 1

                except Exception as outbox_err:
                    pg_db.rollback()
                    item.sync_status = "FAILED"
                    item.error_message = str(outbox_err)[:500]
                    item.retry_count += 1
                    staging_db.commit()
                    logger.error(f"[Outbox Sync] Entry {item.id} error: {outbox_err}")

        finally:
            pg_db.close()

        return {"status": "SUCCESS", "processed": processed_count}

    except Exception as e:
        logger.error(f"[Outbox Sync] Pipeline error: {e}")
        return {"status": "ERROR", "error": str(e)}
    finally:
        staging_db.close()


def sync_verified_records_to_postgres() -> Dict[str, Any]:
    """
    Synchronize pending verified records from SQLite Staging DB to PostgreSQL Permanent DB.
    Also processes outbox queue.
    """
    # 1. First process outbox
    process_postgres_outbox()

    staging_db = StagingSessionLocal()
    synced_count = 0
    pending_count = 0
    
    try:
        # Query SQLite staging DB for records ready for PostgreSQL synchronization
        pending_records = (
            staging_db.query(UniversalRecord)
            .filter(
                (UniversalRecord.status == RecordState.VERIFIED) |
                (UniversalRecord.postgres_sync_status == "PENDING")
            )
            .all()
        )

        if not pending_records:
            return {"status": "NO_PENDING", "synced": 0}

        pending_count = len(pending_records)
        publish_pipeline_event(EventType.POSTGRES_SYNC_STARTED, {"pending_records": pending_count})

        # Check if PostgreSQL permanent DB is active
        if not IS_POSTGRES_AVAILABLE or VerifiedSessionLocal is None:
            logger.warning(f"⚠️ PostgreSQL unavailable. {pending_count} verified records safely retained in SQLite (POSTGRES_SYNC_PENDING).")
            for rec in pending_records:
                rec.status = RecordState.POSTGRES_SYNC_PENDING
                rec.postgres_sync_status = "PENDING"
                rec.sync_error = "PostgreSQL permanent DB currently offline"
            staging_db.commit()
            return {"status": "POSTGRES_OFFLINE", "pending_retained": pending_count}

        pg_db = VerifiedSessionLocal()
        try:
            for rec in pending_records:
                try:
                    # Atomic Transaction in PostgreSQL
                    # 1. Promote/Insert Master Lead into PostgreSQL
                    existing_pg_lead = (
                        pg_db.query(GlobalLead)
                        .filter(GlobalLead.domain == rec.url)
                        .first()
                    )
                    
                    dom_rec = (
                        staging_db.query(DomainRecord)
                        .filter(DomainRecord.universal_record_id == rec.id)
                        .first()
                    )
                    dom_data = dom_rec.data if dom_rec and isinstance(dom_rec.data, dict) else {}

                    if not existing_pg_lead:
                        pg_lead = GlobalLead(
                            domain=rec.url,
                            company_name=rec.canonical_name or rec.title or "Verified Company",
                            industry=rec.entity_type or "Technology",
                            quality_score=float(rec.confidence or 0.8) * 10.0,
                            headquarters=rec.location or dom_data.get("headquarters"),
                            summary=rec.description or dom_data.get("description"),
                            verified_emails=dom_data.get("contact_emails") or [],
                            decision_makers=dom_data.get("key_people") or [],
                            technology_stack=dom_data.get("technologies") or [],
                        )
                        pg_db.add(pg_lead)

                    pg_db.commit()

                    # 2. Update SQLite staging status to POSTGRES_SYNCED
                    rec.status = RecordState.POSTGRES_SYNCED
                    rec.postgres_sync_status = "SYNCED"
                    rec.postgres_synced_at = utc_now()
                    rec.sync_error = None
                    staging_db.commit()

                    synced_count += 1
                    publish_pipeline_event(
                        EventType.POSTGRES_SYNC_COMPLETED,
                        {"record_id": rec.id, "company_name": rec.canonical_name},
                        entity_url=rec.url
                    )

                except Exception as sync_err:
                    pg_db.rollback()
                    rec.status = RecordState.POSTGRES_SYNC_PENDING
                    rec.postgres_sync_status = "FAILED"
                    rec.sync_error = str(sync_err)
                    staging_db.commit()
                    logger.error(f"[Sync Worker] Record {rec.id} sync exception: {sync_err}")

        finally:
            pg_db.close()

        logger.info(f"✅ [PostgreSQL Sync Worker] Synchronized {synced_count}/{pending_count} verified records into PostgreSQL.")
        return {"status": "SUCCESS", "synced": synced_count, "pending": pending_count - synced_count}

    except Exception as e:
        logger.error(f"[Sync Worker] Overall sync failed: {e}")
        return {"status": "ERROR", "error": str(e)}
    finally:
        staging_db.close()

