import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    APP_NAME: str = "OpenDB Crawler Lab"
    APP_ENV: str = "development"
    OPENDB_ENV: str = "development"  # 'development' (allows fallbacks) or 'production' (raises errors on failure)
    LOG_LEVEL: str = "INFO"
    
    POSTGRES_USER: str = "admin"
    POSTGRES_PASSWORD: str = "password123"
    POSTGRES_DB: str = "opendb"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5433
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://admin:password123@localhost:5433/opendb")
    
    RAW_STORAGE_DIR: str = "./data"
    
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin123"
    MINIO_SECURE: bool = False
    STORAGE_BACKEND: str = "minio"
    
    REDIS_PASSWORD: str = "opendb_redis_secret"
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://:opendb_redis_secret@localhost:6379/0")
    
    # Celery Broker & Backend
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://:opendb_redis_secret@localhost:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "redis://:opendb_redis_secret@localhost:6379/0")
    
    SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://localhost:8080")
    
    CRAWL_MAX_DEPTH: int = 2
    CRAWL_MAX_PAGES: int = 20
    CRAWL_CONCURRENCY: int = 5
    RESOURCE_MAX_FILE_SIZE_MB: int = 10
    
    OPENAI_API_KEY: Optional[str] = None
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "ollama"  # 'ollama', 'openai', 'qwen_local', 'heuristics'
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    QWEN_MODEL_NAME: str = "qwen2.5:7b"

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
