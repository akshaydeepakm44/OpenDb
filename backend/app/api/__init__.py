"""
API Router Package Initialization
"""
from app.api import health, crawl, documents, schemas, agent

__all__ = ["health", "crawl", "documents", "schemas", "agent"]
