# OpenDB API Specification

## Endpoints Summary

### Crawl Endpoints

- `POST /api/crawl`: Trigger asynchronous crawl job
- `GET /api/crawl/{job_id}`: Retrieve crawl status and pipeline progress
- `GET /api/crawl/{job_id}/pages`: List discovered & crawled pages
- `GET /api/crawl/{job_id}/results`: Retrieve structured extraction records & evidence

### Document Inspection Endpoints

- `GET /api/documents/{document_id}`: Get document metadata & raw file references
- `GET /api/documents/{document_id}/raw`: Retrieve raw HTML, Markdown, and Text contents
- `GET /api/documents/{document_id}/extraction`: Retrieve extraction payload

### Schema Registry Endpoints

- `GET /api/schemas`: List registered schemas
- `GET /api/schemas/{domain}`: Get schema definition for specified domain
- `POST /api/schemas`: Register or update domain schema definition

### System Health

- `GET /api/health`: Health status check
