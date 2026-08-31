import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

def create_db_engine():
    try:
        db_url = settings.DATABASE_URL.replace("localhost", "127.0.0.1")
        connect_args = {"connect_timeout": 1} if "postgresql" in db_url else {}
        eng = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False,
            connect_args=connect_args
        )
        # Test connection
        with eng.connect() as conn:
            pass
        return eng
    except Exception as e:
        logger.warning(f"Primary PostgreSQL database connection failed ({e}). Falling back to local SQLite database.")
        from sqlalchemy.pool import StaticPool
        sqlite_url = "sqlite:///./opendb_fallback.db"
        return create_engine(
            sqlite_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=StaticPool
        )

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

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

        logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

        # Soft fallback if Postgres is starting up or in test environment
