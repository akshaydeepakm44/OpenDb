import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

IS_FALLBACK_ACTIVE = False

import socket
from urllib.parse import urlparse

def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

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

def create_db_engine():
    global IS_FALLBACK_ACTIVE
    db_url = settings.DATABASE_URL.replace("localhost", "127.0.0.1")
    
    if "postgresql" in db_url:
        parsed = urlparse(db_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
        
        # 1ms protocol probe
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
                IS_FALLBACK_ACTIVE = False
                logger.info(f"PostgreSQL database connected successfully ({db_url})")
                return eng
            except Exception as pg_err:
                logger.warning(f"PostgreSQL auth/connect failed ({pg_err}). Fallback active.")

        if settings.OPENDB_ENV.lower() == "production":
            logger.error("PostgreSQL connection failed in PRODUCTION mode.")
            raise RuntimeError("PostgreSQL connection failed in PRODUCTION mode.")

        IS_FALLBACK_ACTIVE = True
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

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    __table_args__ = {'extend_existing': True}

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Ensure database tables exist using SQLAlchemy metadata."""
    try:
        import app.persistence.models  # Ensure all model tables are registered with Base.metadata
        Base.metadata.create_all(bind=engine)
        
        # Migrations for search_history new columns
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

            # Setup FTS5 Virtual Table for SQLite Master Vault as per architecture diagram
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

        logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

        # Soft fallback if Postgres is starting up or in test environment
