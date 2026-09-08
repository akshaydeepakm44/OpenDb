import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    APP_NAME: str = "OpenDB Crawler Lab"
    APP_ENV: str = "development"
    OPENDB_ENV: str = "development"  # 'development' (allows fallbacks) or 'production' (raises errors on failure)
    LOG_LEVEL: str = "INFO"
    
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "admin")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password123")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "opendb")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5433"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"postgresql://{os.getenv('POSTGRES_USER', 'admin')}:{os.getenv('POSTGRES_PASSWORD', 'password123')}@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}/{os.getenv('POSTGRES_DB', 'opendb')}")
    
    RAW_STORAGE_DIR: str = os.getenv("RAW_STORAGE_DIR", "./data")
    
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
    MINIO_SECURE: bool = os.getenv("MINIO_SECURE", "false").lower() == "true"
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "minio")
    
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "opendb_redis_secret")
    REDIS_URL: str = os.getenv("REDIS_URL", f"redis://:{os.getenv('REDIS_PASSWORD', 'opendb_redis_secret')}@localhost:6379/0")
    
    # Celery Broker & Backend
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", f"redis://:{os.getenv('REDIS_PASSWORD', 'opendb_redis_secret')}@localhost:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", f"redis://:{os.getenv('REDIS_PASSWORD', 'opendb_redis_secret')}@localhost:6379/0")
    
    SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://localhost:8080")
    
    CRAWL_MAX_DEPTH: int = int(os.getenv("CRAWL_MAX_DEPTH", "2"))
    CRAWL_MAX_PAGES: int = int(os.getenv("CRAWL_MAX_PAGES", "20"))
    CRAWL_CONCURRENCY: int = int(os.getenv("CRAWL_CONCURRENCY", "5"))
    RESOURCE_MAX_FILE_SIZE_MB: int = int(os.getenv("RESOURCE_MAX_FILE_SIZE_MB", "10"))
    
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY", "sk-datai2i-a100-qwen35-27b-8x3f9z")
    QWEN_API_KEY: Optional[str] = os.getenv("QWEN_API_KEY", os.getenv("OPENAI_API_KEY", "sk-datai2i-a100-qwen35-27b-8x3f9z"))
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "http://115.244.46.68:8000/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "current-model")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "qwen_gpu")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    QWEN_MODEL_NAME: str = os.getenv("QWEN_MODEL_NAME", "current-model")

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
