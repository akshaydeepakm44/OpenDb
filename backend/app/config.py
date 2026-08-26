import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    APP_NAME: str = "OpenDB Crawler Lab"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    
    POSTGRES_USER: str = "admin"
    POSTGRES_PASSWORD: str = "password123"
    POSTGRES_DB: str = "opendb"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5433
    DATABASE_URL: str = "postgresql://admin:password123@localhost:5433/opendb"
    
    RAW_STORAGE_DIR: str = "./data"
    
    CRAWL_MAX_DEPTH: int = 2
    CRAWL_MAX_PAGES: int = 20
    CRAWL_CONCURRENCY: int = 5
    RESOURCE_MAX_FILE_SIZE_MB: int = 10
    
    OPENAI_API_KEY: Optional[str] = None
    LLM_MODEL: str = "gpt-4o-mini"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
