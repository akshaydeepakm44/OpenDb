# OpenDB Pipeline Debugging Checkpoints & Verification Checklist

This document serves as the operational debugging guide and checkpoint checklist for verifying the end-to-end slice of the OpenDB Web Crawling & Ingestion architecture.

---

## 1. System & Environment Checkpoints

- [ ] **CP-ENV-1**: Database connectivity check
  - **Action**: Run `python -c "from app.persistence.database import engine; print(engine.connect())"`
  - **Expected Outcome**: Successful connection to PostgreSQL on `localhost:5433` (or container `postgres:5432`).
- [ ] **CP-ENV-2**: Windows Asyncio Proactor Event Loop Policy
  - **Action**: Verify `sys.platform == 'win32'` applies `asyncio.WindowsProactorEventLoopPolicy()` in `crawler_service.py` and `main.py`.
  - **Expected Outcome**: Avoids `NotImplementedError` during Crawl4AI Playwright async execution.
- [ ] **CP-ENV-3**: Local Storage Directories
  - **Action**: Check `data/raw/pages`, `data/raw/documents`, `data/raw/media`, `data/processed/markdown`, `data/processed/text`, `data/processed/extracted`.
  - **Expected Outcome**: All directories automatically created and writable.

---

## 2. Crawler & URL Discovery Checkpoints

- [ ] **CP-CRAWL-1**: Crawl4AI Integration
  - **Action**: Trigger crawl for `https://www.example.com`.
  - **Expected Outcome**: Crawl4AI returns HTML, Markdown, page status `200`, title, and word count.
- [ ] **CP-CRAWL-2**: Same-Domain Filtering
  - **Action**: Verify `url_discovery.is_same_domain()` filters out external URLs (e.g. `twitter.com`, `facebook.com`).
  - **Expected Outcome**: Crawl stays strictly within the specified target domain.
- [ ] **CP-CRAWL-3**: Deduplication & URL Normalization
  - **Action**: Enqueue `https://example.com/` and `https://example.com/?utm_source=test#frag`.
  - **Expected Outcome**: Both resolve to `https://example.com`, preventing duplicate crawl jobs or re-insertion.

---

## 3. Raw Resource Collection Checkpoints

- [ ] **CP-RES-1**: File Extension & Type Identification
  - **Action**: Scan page HTML for downloadable documents (`.pdf`, `.csv`, `.docx`) and media (`.png`, `.jpg`).
  - **Expected Outcome**: `ResourceDiscoveryService` categorizes items as `document`, `media`, or `web_resource`.
- [ ] **CP-RES-2**: Document Downloader & Hash Calculation
  - **Action**: Discover linked PDF/CSV resource under file size limit (10MB).
  - **Expected Outcome**: File downloaded to `data/raw/documents/{sha256}.pdf`, metadata recorded in `resources` table with `downloaded=true`.

---

## 4. Extraction & Normalization Checkpoints

- [ ] **CP-EXT-1**: Mode 1 Deterministic Metadata Extraction
  - **Action**: Inspect `css_extractor` output.
  - **Expected Outcome**: Extracts `<title>`, `<meta name="description">`, OpenGraph tags, canonical URL, H1 headers without calling LLM.
- [ ] **CP-EXT-2**: Mode 2 Domain Semantic Extraction
  - **Action**: Execute domain extraction for Technology/Healthcare schema.
  - **Expected Outcome**: Populates domain data payload (e.g., `company_name`, `products`, `founded_year`).
- [ ] **CP-EXT-3**: Missing Data Handling
  - **Action**: Extract page where a field (e.g., `founded_year`) is absent from source text.
  - **Expected Outcome**: Field returns explicit `null` (or `[]` for arrays). Never hallucinated.
- [ ] **CP-EXT-4**: Evidence & Provenance Traceability
  - **Action**: Inspect `extracted_facts` and `evidence` tables.
  - **Expected Outcome**: Every extracted fact maps to `source_url`, `text_snippet`, and confidence score.

---

## 5. PostgreSQL Persistence Checkpoints

- [ ] **CP-DB-1**: Relational Tables Population
  - **Action**: Query PostgreSQL tables after crawl completion.
  - **Expected Outcome**:
    - `crawl_jobs`: `status = 'completed'`
    - `documents`: Stores URL, raw file path, Markdown path, text path
    - `universal_records`: Relational metadata record created
    - `domain_records`: Stores `data` JSONB payload
    - `extracted_facts`: Field-level facts logged
    - `evidence`: Text snippet provenance logged

---

## 6. Frontend Visualizer Checkpoints

- [ ] **CP-UI-1**: Pipeline Stage Visualizer
  - **Action**: Open Frontend dashboard at `http://localhost:5173` (or `http://localhost:3000`).
  - **Expected Outcome**: Displays 10 sequential pipeline stages with active status indicators.
- [ ] **CP-UI-2**: Missing Field Badges
  - **Action**: View Extracted Structured Data tab.
  - **Expected Outcome**: Displays clear `FOUND` (green) and `MISSING` (red) badges for each field.
- [ ] **CP-UI-3**: Raw Content Tabs
  - **Action**: Switch between `HTML`, `Markdown`, `Plain Text`, and `JSON Extraction` tabs.
  - **Expected Outcome**: Displays file contents fetched from backend `/api/documents/{id}/raw`.
