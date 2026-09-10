import os
import shutil
import sys

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.persistence.database import SessionLocal, init_db
from app.persistence.models import (
    GlobalLeadSubpage, GlobalLeadPerson, GlobalLead, OpenLakeRecord,
    KeyPersonCandidate, ManualReviewQueue,
    ResourceLink, Resource, ExtractionRun, DocumentVersion,
    Evidence, ExtractedFact, VerificationRecord, DomainRecord,
    UniversalRecord, Document, CrawlJob, CrawlError,
    CrawlActivityLog, SearchHistory, BatchResult, AgentState,
    PostgresSyncOutbox
)
from app.agent.discovery_agent import discovery_agent
from app.config import settings

def reset_all():
    print("🧹 [Reset] Pausing autonomous discovery agent...")
    try:
        discovery_agent.set_status("PAUSED")
    except Exception as e:
        print(f"Notice: Agent pause: {e}")

    init_db()

    db = SessionLocal()
    try:
        models = [
            GlobalLeadSubpage, GlobalLeadPerson, GlobalLead, OpenLakeRecord,
            KeyPersonCandidate, ManualReviewQueue,
            ResourceLink, Resource, ExtractionRun, DocumentVersion,
            Evidence, ExtractedFact, VerificationRecord, DomainRecord,
            UniversalRecord, Document, CrawlJob, CrawlError,
            CrawlActivityLog, SearchHistory, BatchResult, AgentState,
            PostgresSyncOutbox
        ]
        for m in models:
            try:
                count = db.query(m).delete()
                db.commit()
                print(f"  ✓ Deleted {count} records from {m.__tablename__}")
            except Exception as ex:
                db.rollback()
                print(f"  ⚠ Notice on {m.__tablename__}: {ex}")

        # Also reset FTS5 table if sqlite
        try:
            from sqlalchemy import text
            db.execute(text("DELETE FROM global_leads_fts"))
            db.commit()
        except Exception:
            pass

    finally:
        db.close()

    # Clear disk storage
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dirs = [
        os.path.join(base_dir, "data"),
        os.path.join(base_dir, "backend", "data"),
        os.path.abspath("data")
    ]

    for d in set(target_dirs):
        if os.path.exists(d):
            print(f"🧹 Clearing storage directory: {d}")
            for item in os.listdir(d):
                p = os.path.join(d, item)
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.unlink(p)
                except Exception as fe:
                    print(f"  ⚠ Failed to delete {p}: {fe}")

    # Flush Redis
    try:
        import redis
        r = redis.Redis.from_url(settings.REDIS_URL.replace("localhost", "127.0.0.1"), socket_connect_timeout=0.5, socket_timeout=0.5)
        r.flushdb()
        print("  ✓ Redis cache & queue flushed.")
    except Exception:
        pass

    print("✨ [Reset Complete] All crawled, scraped, and verified data has been wiped clean.")

if __name__ == "__main__":
    reset_all()
