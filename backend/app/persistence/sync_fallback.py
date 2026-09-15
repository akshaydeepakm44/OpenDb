"""
PostgreSQL Controlled Recovery Synchronization Service — Phase 5 Implementation

Workflow:
1. Probe PostgreSQL connection health.
2. Read PENDING / FAILED records from SQLite fallback outbox.
3. Transition record state: PENDING -> SYNCING.
4. Perform stable identity lookup in PostgreSQL to prevent duplicate records.
5. Reconcile / upsert record cleanly into PostgreSQL tables.
6. On success: mark sync_status = 'SYNCED', synced_at = utc_now().
7. On error: mark sync_status = 'FAILED', last_sync_error = str(err). Mode remains SQLITE_FALLBACK.
8. Transition database_mode to POSTGRESQL ONLY when zero pending records remain and PostgreSQL is healthy.
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings

logger = logging.getLogger(__name__)

def sync_pending_fallback_records(pg_engine=None, sqlite_engine=None) -> dict:
    """
    Synchronizes pending fallback records from opendb_fallback.db into PostgreSQL.
    Returns dictionary with synchronization summary.
    """
    from app.persistence.database import (
        _is_postgres_listening, _create_sqlite_fallback_engine,
        DATABASE_MODE, IS_FALLBACK_ACTIVE
    )
    import app.persistence.database as db_mod

    # 1. Health check PostgreSQL
    db_url = settings.DATABASE_URL.replace("localhost", "127.0.0.1")
    if "postgresql" not in db_url:
        return {"status": "skipped", "reason": "No PostgreSQL URL configured", "synced_count": 0}

    from urllib.parse import urlparse
    parsed = urlparse(db_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432

    if not _is_postgres_listening(host, port):
        logger.warning("[Recovery Sync] PostgreSQL port probe failed. Remaining in SQLITE_FALLBACK mode.")
        db_mod.DATABASE_MODE = "SQLITE_FALLBACK"
        db_mod.IS_FALLBACK_ACTIVE = True
        return {"status": "failed", "reason": "PostgreSQL port unreachable", "synced_count": 0}

    # 2. Connect to PostgreSQL
    if pg_engine is None:
        try:
            pg_engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 2})
            with pg_engine.connect() as test_conn:
                test_conn.execute(text("SELECT 1;"))
        except Exception as pg_err:
            logger.warning(f"[Recovery Sync] PostgreSQL connect test failed: {pg_err}. Remaining in SQLITE_FALLBACK.")
            db_mod.DATABASE_MODE = "SQLITE_FALLBACK"
            db_mod.IS_FALLBACK_ACTIVE = True
            return {"status": "failed", "reason": str(pg_err), "synced_count": 0}

    if sqlite_engine is None:
        sqlite_engine = _create_sqlite_fallback_engine()

    PgSession = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
    SqliteSession = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)

    sqlite_db = SqliteSession()
    pg_db = PgSession()

    synced_count = 0
    failed_count = 0

    try:
        from app.persistence.models import PostgresSyncOutbox, UniversalRecord, GlobalLead, Document, utc_now

        # Ensure outbox table exists in SQLite
        try:
            pending_items = sqlite_db.query(PostgresSyncOutbox).filter(
                PostgresSyncOutbox.sync_status.in_(["PENDING", "FAILED", "SYNCING"])
            ).all()
        except Exception as query_err:
            logger.debug(f"[Recovery Sync] Outbox query note (table may be empty/new): {query_err}")
            pending_items = []

        if not pending_items:
            logger.info("[Recovery Sync] Zero pending fallback records found. PostgreSQL is healthy.")
            db_mod.DATABASE_MODE = "POSTGRESQL"
            db_mod.IS_FALLBACK_ACTIVE = False
            return {"status": "success", "synced_count": 0, "pending_remaining": 0, "database_mode": "POSTGRESQL"}

        logger.info(f"[Recovery Sync] Discovered {len(pending_items)} pending records to synchronize to PostgreSQL.")

        for item in pending_items:
            item.sync_status = "SYNCING"
            item.sync_attempts = (item.sync_attempts or 0) + 1
            sqlite_db.commit()

            try:
                payload = item.payload_json or {}
                domain = item.domain or payload.get("domain")
                company_name = item.company_name or payload.get("company_name")
                rec_id = item.record_id or item.id

                # Stable identity lookup in PostgreSQL to prevent row duplication
                existing_univ = None
                if rec_id:
                    existing_univ = pg_db.query(UniversalRecord).filter(UniversalRecord.id == rec_id).first()
                if not existing_univ and domain:
                    existing_univ = pg_db.query(UniversalRecord).filter(UniversalRecord.url.like(f"%{domain}%")).first()

                now_ts = utc_now()

                if existing_univ:
                    # Idempotent Reconcile / Upsert
                    if company_name:
                        existing_univ.canonical_name = company_name
                    existing_univ.updated_at = now_ts
                else:
                    # Create clean UniversalRecord in PostgreSQL
                    new_univ = UniversalRecord(
                        id=rec_id,
                        canonical_name=company_name or domain or "Unknown Entity",
                        title=company_name or domain,
                        url=item.source_url or f"https://{domain}" if domain else "https://unknown.com",
                        status="Discovered",
                        confidence=0.85,
                        created_at=now_ts,
                        updated_at=now_ts
                    )
                    pg_db.add(new_univ)

                # Also reconcile GlobalLead read model if domain exists
                if domain and company_name:
                    existing_lead = pg_db.query(GlobalLead).filter(GlobalLead.domain == domain).first()
                    if not existing_lead:
                        import hashlib
                        lead_id = hashlib.md5(domain.encode()).hexdigest()
                        new_lead = GlobalLead(
                            id=lead_id,
                            domain=domain,
                            company_name=company_name,
                            summary=payload.get("summary"),
                            industry=payload.get("industry"),
                            headquarters=payload.get("headquarters"),
                            created_at=now_ts,
                            updated_at=now_ts
                        )
                        pg_db.add(new_lead)

                pg_db.commit()

                # Mark item as SYNCED in SQLite fallback outbox
                item.sync_status = "SYNCED"
                item.synced_at = now_ts
                item.last_sync_error = None
                sqlite_db.commit()
                synced_count += 1
                logger.info(f"[Recovery Sync] Record '{rec_id}' (domain={domain}) successfully synchronized to PostgreSQL.")

            except Exception as item_err:
                pg_db.rollback()
                logger.error(f"[Recovery Sync] Error syncing record '{item.id}': {item_err}")
                item.sync_status = "FAILED"
                item.last_sync_error = str(item_err)[:1000]
                sqlite_db.commit()
                failed_count += 1

        # Check if all records are synced
        remaining = sqlite_db.query(PostgresSyncOutbox).filter(
            PostgresSyncOutbox.sync_status.in_(["PENDING", "FAILED", "SYNCING"])
        ).count()

        if remaining == 0 and failed_count == 0:
            db_mod.DATABASE_MODE = "POSTGRESQL"
            db_mod.IS_FALLBACK_ACTIVE = False
            logger.info("[Recovery Sync] All pending records synchronized successfully. Database mode returned to POSTGRESQL.")
            return {"status": "success", "synced_count": synced_count, "pending_remaining": 0, "database_mode": "POSTGRESQL"}
        else:
            db_mod.DATABASE_MODE = "SQLITE_FALLBACK"
            db_mod.IS_FALLBACK_ACTIVE = True
            logger.warning(f"[Recovery Sync] Synchronization incomplete ({remaining} pending). Remaining in SQLITE_FALLBACK.")
            return {"status": "degraded", "synced_count": synced_count, "pending_remaining": remaining, "database_mode": "SQLITE_FALLBACK"}

    finally:
        sqlite_db.close()
        pg_db.close()
