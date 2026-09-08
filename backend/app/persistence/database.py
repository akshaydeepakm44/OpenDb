"""
Dual-Layer Database Architecture — §1 & §2 of Master Architecture

1. STAGING / OPERATIONAL DATABASE (SQLite):
   - Fast, local, always-available operational staging layer.
   - Stores: Discovered URLs, crawl pipeline states, temporary crawl records,
     raw ingested metadata, agent checkpoints, retry queues, batch membership,
     and records waiting to be synchronized to PostgreSQL.

2. PERMANENT INTELLIGENCE DATABASE (PostgreSQL):
   - Structured, long-term verified intelligence layer.
   - Stores: Fully verified companies, master company profiles, domains,
     evidence/provenance, relationships, historical verified leads, pgvector embeddings.

NO FALLBACK ARCHITECTURE: SQLite and PostgreSQL run as separate databases with distinct responsibilities.
If PostgreSQL is temporarily offline, records remain safely stored in SQLite as POSTGRES_SYNC_PENDING.
"""
import os
import logging
import socket
from urllib.parse import urlparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

# Base Declarative Class for ORM Models
class Base(DeclarativeBase):
    __table_args__ = {'extend_existing': True}


# ── 1. OPERATIONAL STAGING DATABASE (SQLite) ───────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STAGING_DB_PATH = os.path.join(BASE_DIR, "opendb_staging.db").replace("\\", "/")

staging_engine = create_engine(
    f"sqlite:///{STAGING_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False
)

# Enable WAL mode for high-concurrency SQLite operational staging
try:
    with staging_engine.connect() as _conn:
        _conn.execute(text("PRAGMA journal_mode=WAL;"))
        _conn.execute(text("PRAGMA synchronous=NORMAL;"))
        _conn.execute(text("PRAGMA temp_store=MEMORY;"))
        _conn.execute(text("PRAGMA busy_timeout=30000;"))
        _conn.commit()
except Exception as _pragma_err:
    logger.warning(f"[SQLite Staging] PRAGMA setup warning: {_pragma_err}")

StagingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=staging_engine)


# ── 2. PERMANENT VERIFIED INTELLIGENCE DATABASE (PostgreSQL) ─────────
IS_POSTGRES_AVAILABLE = False

def _is_port_open(host: str, port: int, timeout: float = 0.1) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def _create_verified_engine():
    global IS_POSTGRES_AVAILABLE
    db_url = settings.DATABASE_URL.replace("localhost", "127.0.0.1")
    
    if "postgresql" in db_url:
        parsed = urlparse(db_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
        
        if _is_port_open(host, port, timeout=0.1):
            try:
                import psycopg2
                conn = psycopg2.connect(
                    dbname=parsed.path.lstrip("/") or "postgres",
                    user=parsed.username,
                    password=parsed.password,
                    host=host,
                    port=port,
                    connect_timeout=1,
                    gssencmode="disable"
                )
                conn.close()
                eng = create_engine(
                    db_url,
                    pool_pre_ping=True,
                    pool_size=10,
                    max_overflow=20,
                    echo=False,
                    connect_args={"connect_timeout": 3}
                )
                IS_POSTGRES_AVAILABLE = True
                logger.info(f"✅ PostgreSQL Verified Intelligence DB connected successfully ({db_url})")
                return eng
            except Exception as pg_err:
                logger.warning(f"⚠️ PostgreSQL connection/auth notice: {pg_err}. Sync worker will retry later.")
        else:
            logger.warning(f"⚠️ PostgreSQL port {port} not reachable. Staging continues in SQLite.")
            
    IS_POSTGRES_AVAILABLE = False
    return None

verified_engine = _create_verified_engine()
VerifiedSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=verified_engine) if verified_engine else None


# ── 3. SESSION PROVIDERS ──────────────────────────────────────────────
# Default SessionLocal points to Operational Staging DB (SQLite)
SessionLocal = StagingSessionLocal

def get_staging_db():
    """Dependency provider for SQLite Operational Staging DB."""
    db = StagingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_verified_db():
    """Dependency provider for PostgreSQL Permanent Intelligence DB."""
    if not VerifiedSessionLocal:
        yield None
        return
    db = VerifiedSessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db():
    """Default DB dependency (SQLite Operational Staging DB)."""
    db = StagingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initialize database schemas in both SQLite Staging and PostgreSQL (if available)."""
    try:
        import app.persistence.models  # Register all models with Base.metadata
        
        # 1. Initialize SQLite Staging Tables
        Base.metadata.create_all(bind=staging_engine)
        logger.info("✅ SQLite Operational Staging DB tables initialized.")

        # Setup FTS5 Virtual Table for SQLite Staging
        with staging_engine.connect() as conn:
            try:
                # Dynamic column migrations for SQLite
                res = conn.execute(text("PRAGMA table_info(companies);")).fetchall()
                existing_cols = [r[1] for r in res]
                if "contact_numbers" not in existing_cols:
                    conn.execute(text("ALTER TABLE companies ADD COLUMN contact_numbers TEXT DEFAULT '[]';"))
                    conn.commit()
                    logger.info("✅ SQLite Auto-migration: Added 'contact_numbers' column to 'companies' table.")
            except Exception as mig_err:
                logger.debug(f"[SQLite Staging] Auto-migration notice: {mig_err}")

            try:
                conn.execute(text("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS global_leads_fts USING fts5(
                        domain, company_name, industry, technology_stack, summary
                    );
                """))
                conn.commit()
            except Exception as fts_err:
                logger.debug(f"[SQLite Staging] FTS5 setup note: {fts_err}")

        # 2. Initialize PostgreSQL Verified Intelligence Tables (if reachable)
        if verified_engine and IS_POSTGRES_AVAILABLE:
            try:
                Base.metadata.create_all(bind=verified_engine)
                logger.info("✅ PostgreSQL Permanent Verified DB tables initialized.")
            except Exception as pg_init_err:
                logger.warning(f"⚠️ PostgreSQL schema creation notice: {pg_init_err}")

    except Exception as e:
        logger.error(f"Error initializing dual-layer databases: {e}")
