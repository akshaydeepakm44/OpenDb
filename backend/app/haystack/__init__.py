"""
Haystack (deepset) integration for OpenDB.

Provides:
  - Extraction pipeline: LLM-based structured field extraction from crawled text
  - Retrieval pipeline: pgvector semantic search over indexed documents/entities

Both pipelines are lazily built and gracefully degrade to None if
dependencies are missing (callers keep the custom-orchestrator path).
"""
