import os
import shutil
import logging
from sqlalchemy import text
from app.persistence.database import engine, SessionLocal
from app.cache.redis_client import get_redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clear_db")

TABLES_TO_CLEAR = [
    "postgres_sync_outbox",
    "global_lead_people",
    "global_lead_subpages",
    "global_leads",
    "key_person_candidates",
    "open_lake_records",
    "verification_records",
    "universal_records",
    "search_history",
    "crawl_activity_log",
    "crawl_jobs",
    "crawl_errors",
    "documents",
    "document_versions",
    "extracted_facts",
    "domains",
    "subdomains",
    "keyword_performance",
    "evidence",
    "quarantined_content",
    "resources",
    "resource_links",
    "batch_results",
    "manual_review_queue",
    "schema_definitions"
]

def clear_database():
    logger.info(f"Target DB: {engine.url}")
    with engine.begin() as conn:
        for tbl in TABLES_TO_CLEAR:
            try:
                conn.execute(text(f"DELETE FROM {tbl};"))
                logger.info(f"Cleared table: {tbl}")
            except Exception as e:
                logger.debug(f"Table {tbl} not present or skipped: {e}")

        if "sqlite" in str(engine.url):
            try:
                conn.execute(text("DELETE FROM global_leads_fts;"))
                logger.info("Cleared FTS table: global_leads_fts")
            except Exception:
                pass
    if "sqlite" in str(engine.url):
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as raw_conn:
                raw_conn.execute(text("VACUUM;"))
        except Exception:
            pass

def clear_disk_cache():
    data_companies = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "companies")
    if os.path.exists(data_companies):
        try:
            shutil.rmtree(data_companies)
            os.makedirs(data_companies, exist_ok=True)
            logger.info(f"Purged cached company files in {data_companies}")
        except Exception as e:
            logger.warning(f"Failed to clear {data_companies}: {e}")

def clear_redis():
    try:
        r = get_redis()
        if r:
            keys = r.keys("opendb:*")
            if keys:
                r.delete(*keys)
                logger.info(f"Flushed {len(keys)} Redis cache keys.")
            else:
                logger.info("Redis cache is already clean.")
    except Exception as e:
        logger.debug(f"Redis cleanup skipped: {e}")

if __name__ == "__main__":
    clear_database()
    clear_disk_cache()
    clear_redis()
    logger.info("ALL CRAWLED DATA AND CACHE SUCCESSFULLY PURGED!")
