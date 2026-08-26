# OpenDB Architecture Specification

## Overview

OpenDB ingests unstructured web content into structured domain-aware records. It separates raw web crawling (handled by Crawl4AI) from data ingestion, normalization, schema mapping, and relational persistence in PostgreSQL.

## Modular Architecture Layers

1. **Crawler Layer (`app/crawler/`)**:
   - `crawler_service.py`: Orchestrates AsyncWebCrawler (Crawl4AI) with BFS link traversal, max depth/pages constraints, and Windows proactor loop support.
   - `url_discovery.py`: Enforces same-domain filtering, link extraction, and URL normalization (removing tracking parameters and fragments).
   - `resource_discovery.py`: Identifies downloadable documents (PDF, CSV, TXT) and media assets, downloading text/doc resources up to 10MB to raw storage.

2. **Storage Layer (`app/storage/`)**:
   - `file_storage.py`: Manages raw storage directories using SHA-256 content hashes (`data/raw/pages/{hash}.html`, `data/processed/markdown/{hash}.md`, `data/processed/extracted/{doc_id}.json`). Abstracted to allow drop-in replacement with MinIO / AWS S3.

3. **Classification & Normalization Layer (`app/classification/`, `app/normalization/`)**:
   - `domain_classifier.py`: Keyword signal and metadata classifier categorizing pages into Technology, Healthcare, Education, or Business.
   - `normalizer.py`: Sanitizes text, HTML entities, country names, language codes, emails, and phone numbers.

4. **Extraction Layer (`app/extraction/`)**:
   - `css_extractor.py`: Mode 1 deterministic HTML metadata extraction (Title, Meta Description, OpenGraph, JSON-LD, H1).
   - `llm_extractor.py`: Mode 2 domain semantic extraction using LiteLLM/OpenAI or deterministic rule heuristics when offline.
   - `document_extractor.py`: Extracts column names, row counts, and text previews from CSV/PDF/TXT files.
   - `extractor.py`: Unified pipeline orchestrator returning universal records, domain payloads, facts, and evidence snippets.

5. **Persistence Layer (`app/persistence/`)**:
   - SQLAlchemy 2.x declarative models mapping 15 relational tables in PostgreSQL.

## Future Scaling Strategy

- **Task Queue**: Current FastAPI BackgroundTasks can be swapped with Celery / RQ / Redis Queue without altering crawler or extractor services.
- **Object Storage**: `file_storage.py` can be upgraded to S3/MinIO by overriding standard file IO methods with Boto3 calls.
- **Database Promotion**: Stable JSONB fields in `domain_records` can be promoted to dedicated typed PostgreSQL columns as field usage stabilizes.
