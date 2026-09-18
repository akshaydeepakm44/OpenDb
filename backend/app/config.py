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
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://admin:password123@localhost:5432/opendb")
    
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
    
    OPENAI_API_KEY: Optional[str] = "sk-datai2i-a100-qwen35-27b-8x3f9z"
    QWEN_API_KEY: Optional[str] = "sk-datai2i-a100-qwen35-27b-8x3f9z"
    OPENAI_BASE_URL: str = "http://115.244.46.68:8000/v1"
    LLM_MODEL: str = "current-model"
    QWEN_MODEL_NAME: str = "current-model"
    LLM_PROVIDER: str = "openai"  # 'ollama', 'openai', 'qwen_local', 'heuristics'
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Production Hardening & Autonomous Concurrency Controls
    AGENT_LOOP_PACE_SECONDS: int = 15
    MAX_DISCOVERY_RESULTS_PER_CYCLE: int = 25
    MAX_CONCURRENT_CRAWLS: int = 2
    MAX_CONCURRENT_DEEP_CRAWLS: int = 1
    MAX_BROWSER_CONTEXTS: int = 2
    SAFE_BROWSER_PROCESS_LIMIT: int = 4

    # Queue Watermarks (Hysteresis)
    DISCOVERY_QUEUE_HIGH_WATERMARK: int = 100
    DISCOVERY_QUEUE_LOW_WATERMARK: int = 60
    VERIFICATION_QUEUE_HIGH_WATERMARK: int = 50
    VERIFICATION_QUEUE_LOW_WATERMARK: int = 25

    # Timeouts & Retries
    CRAWL_TIMEOUT_SECONDS: int = 60
    MAX_CRAWL_RETRIES: int = 2
    TASK_LEASE_TIMEOUT_SECONDS: int = 300
    STALE_TASK_TIMEOUT_SECONDS: int = 300

    # Resource Governor & Circuit Breaker
    RESOURCE_PAUSE_MEMORY_PERCENT: float = 85.0
    RESOURCE_EMERGENCY_MEMORY_PERCENT: float = 95.0
    RESOURCE_PAUSE_CPU_PERCENT: float = 90.0
    RESOURCE_EMERGENCY_CPU_PERCENT: float = 95.0
    GOVERNOR_CACHE_TTL_SECONDS: float = 5.0

    # Crawler Depth Limits
    MAX_PAGES_PER_DOMAIN_AGENT1: int = 4
    MAX_PAGES_PER_DOMAIN_AGENT2: int = 10

    # Database Pool Configuration
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10
    MAX_TASKS_PER_WORKER: int = 50

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
