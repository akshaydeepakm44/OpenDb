# OpenDB PostgreSQL Database Specification

## Relational Schema Design

OpenDB replaces graph entity-property traversal with a domain-oriented, source-traceable relational structure.

### Core Tables Summary

1. `sources`: Information provenance (websites, APIs, RSS feeds).
2. `domains` & `subdomains`: Configurable domain taxonomy.
3. `crawl_jobs`: Asynchronous job state, execution stage, and execution stats.
4. `documents`: Crawled web pages, HTTP status, word counts, content hashes, and relative raw storage file paths.
5. `document_versions`: Revision history per document URL.
6. `resources`: Discovered downloadable files (PDF, CSV, media) and download status.
7. `resource_links`: Join table linking documents to discovered resources.
8. `universal_records`: Core domain-agnostic metadata (canonical name, title, description, entity type, location, country, confidence).
9. `domain_records`: Dynamic domain payload stored in `JSONB` for schema flexibility and evolutionary discovery.
10. `extracted_facts`: Atomized field-level facts (`field_name`, `field_value`, `value_type`, `confidence`).
11. `evidence`: Source snippet, text snippet, selector, and confidence mapping back to exact source URLs.
12. `extraction_runs`: Extraction performance metrics and field counts.
13. `schema_definitions`: Registered domain schemas.
14. `crawl_errors`: Granular error log by job, stage, and URL.

## Schema Discovery & Promotion Strategy

For this POC, `domain_records` stores dynamic domain attributes inside `JSONB`. This deliberate design choice allows new domain attributes to be extracted immediately without requiring DDL migration scripts. When specific domain fields demonstrate high frequency and stability, Alembic migrations can promote them to typed PostgreSQL columns.
