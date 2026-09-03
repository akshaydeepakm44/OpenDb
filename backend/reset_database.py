import os
import sys
import shutil
import logging
from sqlalchemy.orm import Session

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.persistence.database import engine, init_db, IS_FALLBACK_ACTIVE
from app.persistence.models import (
    Base, Document, UniversalRecord, VerificationRecord, Domain, Source, Subdomain,
    CrawlActivityLog, SearchHistory, BatchResult, AgentState, KeywordPerformance,
    BlockedDomain, QuarantinedContent, ManualReviewQueue, CrawlJob, CrawlError,
    DomainRecord, ExtractedFact, Evidence, ExtractionRun, DocumentVersion, Resource,
    ResourceLink, Metadata
)
from app.cache.redis_cache import cache_delete_namespace
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def reset_all_scraped_data():
    logger.info("==================================================")
    logger.info("🧹 RESETTING OPENDB PIPELINE DATA")
    logger.info("==================================================")
    logger.info(f"Target Database: {engine.url}")
    logger.info(f"Using SQLite Fallback: {IS_FALLBACK_ACTIVE}")

    db = Session(bind=engine)
    try:
        logger.info("Clearing database tables...")
        # Delete dependent child tables first
        db.query(VerificationRecord).delete()
        db.query(Evidence).delete()
        db.query(ExtractedFact).delete()
        db.query(DomainRecord).delete()
        db.query(UniversalRecord).delete()
        db.query(ResourceLink).delete()
        db.query(Resource).delete()
        db.query(DocumentVersion).delete()
        db.query(ExtractionRun).delete()
        db.query(CrawlError).delete()
        db.query(Document).delete()
        db.query(CrawlJob).delete()
        db.query(Subdomain).delete()
        db.query(Domain).delete()
        db.query(Source).delete()
        db.query(CrawlActivityLog).delete()
        db.query(SearchHistory).delete()
        db.query(BatchResult).delete()
        db.query(AgentState).delete()
        db.query(KeywordPerformance).delete()
        db.query(QuarantinedContent).delete()
        db.query(ManualReviewQueue).delete()
        db.query(BlockedDomain).delete()
        db.query(Metadata).delete()

        db.commit()
        logger.info("✅ Database tables cleared successfully.")

        # Re-initialize clean tables
        init_db()

        # Clear Local Data Storage Directory
        raw_storage_path = os.path.abspath(settings.RAW_STORAGE_DIR)
        logger.info(f"Clearing local storage directory: {raw_storage_path}")
        if os.path.exists(raw_storage_path):
            for item in os.listdir(raw_storage_path):
                item_path = os.path.join(raw_storage_path, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as file_err:
                    logger.warning(f"Could not delete {item_path}: {file_err}")
            logger.info("✅ Storage directory cleared.")

        # Flush Redis Cache & Task Queues
        logger.info("Flushing Redis search, entity cache, and Celery task queues...")
        cache_delete_namespace("search")
        cache_delete_namespace("entity")
        cache_delete_namespace("safety_block")
        cache_delete_namespace("doc")
        try:
            from app.cache.redis_client import get_redis
            r = get_redis()
            if r:
                r.delete("celery")
                r.flushall()
        except Exception as red_err:
            logger.warning(f"Redis queue flush notice: {red_err}")
        logger.info("✅ Redis cache & Celery queue flushed.")

        logger.info("==================================================")
        logger.info("🎉 OPENDB PIPELINE DATA RESET COMPLETE!")
        logger.info("The system is now completely clean and ready for new B2B discovery.")
        logger.info("==================================================")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Reset failed: {e}")
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    reset_all_scraped_data()
