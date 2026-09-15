import os
import logging
import socket
from urllib.parse import urlparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

DATABASE_MODE = "POSTGRESQL"
IS_FALLBACK_ACTIVE = False

def get_database_status() -> dict:
    return {
        "mode": DATABASE_MODE,
        "status": "CONNECTED" if not IS_FALLBACK_ACTIVE else "DEGRADED",
        "degraded": IS_FALLBACK_ACTIVE
    }

def mark_postgres_degraded():
    global DATABASE_MODE, IS_FALLBACK_ACTIVE
    from app.audit.tracer import tracer, Checkpoint
    tracer.log_event(
        level="ERROR",
        checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
        event="POSTGRESQL_CONNECTION_FAILED",
        message="POSTGRESQL_CONNECTION_FAILED — Primary database connection lost or unreachable.",
        status="FAILED"
    )
    tracer.log_event(
        level="WARNING",
        checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
        event="DATABASE_FALLBACK_STARTED",
        message="DATABASE FALLBACK ACTIVATED: Switching persistence path to SQLite fallback store (opendb_fallback.db).",
        status="DEGRADED"
    )
    DATABASE_MODE = "SQLITE_FALLBACK"
    IS_FALLBACK_ACTIVE = True
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
        event="DATABASE_MODE_CHANGED",
        message="DATABASE MODE=SQLITE_FALLBACK (Operational in DEGRADED persistence mode).",
        status="DEGRADED"
    )

def _is_postgres_listening(host: str, port: int) -> bool:
    try:
        import select
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setblocking(False)
        s.connect_ex((host, port))
        _, writable, _ = select.select([], [s], [], 0.05)
        if not writable:
            s.close()
            return False
        s.setblocking(True)
        s.settimeout(0.05)
        s.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")
        data = s.recv(1)
        s.close()
        return data in (b"S", b"N")
    except Exception:
        return False

def _create_sqlite_fallback_engine():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    fallback_db_path = os.path.join(base_dir, "opendb_fallback.db").replace("\\", "/")
    eng = create_engine(
        f"sqlite:///{fallback_db_path}",
        connect_args={"check_same_thread": False, "timeout": 30}
    )
    try:
        with eng.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.execute(text("PRAGMA busy_timeout=30000;"))
            conn.commit()
    except Exception as pragma_err:
        logger.warning(f"SQLite PRAGMA setup warning: {pragma_err}")
    return eng

def create_db_engine():
    global DATABASE_MODE, IS_FALLBACK_ACTIVE
    from app.audit.tracer import tracer, Checkpoint
    db_url = settings.DATABASE_URL.replace("localhost", "127.0.0.1")
    
    if "postgresql" in db_url:
        parsed = urlparse(db_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
        
        if _is_postgres_listening(host, port):
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
                    pool_size=5,
                    max_overflow=10,
                    echo=False,
                    connect_args={"connect_timeout": 2}
                )
                DATABASE_MODE = "POSTGRESQL"
                IS_FALLBACK_ACTIVE = False
                tracer.log_event(
                    level="INFO",
                    checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
                    event="DATABASE_CONNECTED",
                    message=f"PostgreSQL PRIMARY connected successfully ({host}:{port})",
                    status="ONLINE"
                )
                return eng
            except Exception as pg_err:
                tracer.log_event(
                    level="WARNING",
                    checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
                    event="POSTGRESQL_CONNECT_ERROR",
                    message=f"PostgreSQL connection probe failed: {pg_err}",
                    status="DEGRADED"
                )

        if settings.OPENDB_ENV.lower() == "production":
            tracer.log_event(
                level="CRITICAL",
                checkpoint=Checkpoint.CP25_DATABASE_PERSISTENCE,
                event="POSTGRESQL_FATAL_PRODUCTION",
                message="POSTGRES_CONNECTION_FAILED in PRODUCTION mode. Refusing fallback.",
                status="FAILED"
            )
            raise RuntimeError("PostgreSQL connection failed in PRODUCTION mode.")

        mark_postgres_degraded()
        return _create_sqlite_fallback_engine()

    DATABASE_MODE = "SQLITE_FALLBACK"
    IS_FALLBACK_ACTIVE = True
    return _create_sqlite_fallback_engine()

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    __table_args__ = {'extend_existing': True}

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception as err:
        err_str = str(err).lower()
        if "psycopg2" in err_str or "connection" in err_str or "disconnection" in err_str or "could not connect" in err_str:
            mark_postgres_degraded()
        raise
    finally:
        db.close()

def init_db():
    """Ensure database tables exist using SQLAlchemy metadata."""
    try:
        import app.persistence.models  # Ensure all model tables are registered with Base.metadata
        Base.metadata.create_all(bind=engine)
        
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE search_history ADD COLUMN is_fallback BOOLEAN DEFAULT 0"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE search_history ADD COLUMN log_message TEXT"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE global_leads ADD COLUMN linkedin_url TEXT"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE global_lead_people ADD COLUMN linkedin_url TEXT"))
                conn.commit()
            except Exception:
                pass

            outbox_cols = [
                "ALTER TABLE postgres_sync_outbox ADD COLUMN record_id VARCHAR(36)",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN source_url TEXT",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN source_type VARCHAR(50)",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN observed_at TIMESTAMP",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN extracted_at TIMESTAMP",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN evidence JSON",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN sync_attempts INTEGER DEFAULT 0",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN last_sync_error TEXT",
                "ALTER TABLE postgres_sync_outbox ADD COLUMN updated_at TIMESTAMP",
            ]
            for col_stmt in outbox_cols:
                try:
                    conn.execute(text(col_stmt))
                    conn.commit()
                except Exception:
                    pass


            if "sqlite" in str(engine.url):
                try:
                    conn.execute(text("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS global_leads_fts USING fts5(
                            domain, company_name, industry, technology_stack, summary
                        );
                    """))
                    conn.commit()
                except Exception as fts_err:
                    logger.debug(f"FTS5 setup note: {fts_err}")

        logger.info(f"Database initialized. Mode: {DATABASE_MODE} (Fallback Active: {IS_FALLBACK_ACTIVE})")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

