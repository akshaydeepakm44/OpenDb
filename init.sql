-- OpenDB PostgreSQL Schema
-- Requires pgvector extension for semantic search

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Core Lookup Tables ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    base_url TEXT,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS domains (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subdomains (
    id SERIAL PRIMARY KEY,
    domain_id INT REFERENCES domains(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ─── Metadata Table ───────────────────────────────────────────────────────────
-- Central registry that tracks every entity type in the system.
-- Allows fast lookup: "does a domain/subdomain/entity/keyword exist?"
-- Used during crawl, retrieval, and deduplication.
CREATE TABLE IF NOT EXISTS metadata (
    id SERIAL PRIMARY KEY,
    entity_type  VARCHAR(100) NOT NULL,   -- 'domain','subdomain','keyword','url','schema','batch'
    entity_key   TEXT NOT NULL,            -- the value being tracked (e.g. 'Technology', 'healthcare.org')
    domain       VARCHAR(100),             -- optional parent domain
    subdomain    VARCHAR(100),             -- optional parent subdomain
    is_present   BOOLEAN DEFAULT TRUE,     -- TRUE = exists/active, FALSE = deprecated/removed
    source_table VARCHAR(100),             -- which table this entry points to
    source_id    TEXT,                     -- FK value in that table (string to handle UUIDs/ints)
    extra        JSONB DEFAULT '{}'::jsonb,-- any extra metadata (count, score, notes)
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entity_type, entity_key)
);

CREATE INDEX IF NOT EXISTS idx_metadata_entity_type  ON metadata (entity_type);
CREATE INDEX IF NOT EXISTS idx_metadata_entity_key   ON metadata (entity_key);
CREATE INDEX IF NOT EXISTS idx_metadata_domain        ON metadata (domain);
CREATE INDEX IF NOT EXISTS idx_metadata_is_present    ON metadata (is_present);

-- ─── Crawl Jobs ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    starting_url TEXT NOT NULL,
    query TEXT,
    domain_name VARCHAR(100),
    max_depth INT DEFAULT 2,
    max_pages INT DEFAULT 20,
    status VARCHAR(50) DEFAULT 'pending',
    pages_discovered INT DEFAULT 0,
    pages_crawled INT DEFAULT 0,
    documents_count INT DEFAULT 0,
    resources_count INT DEFAULT 0,
    successful_count INT DEFAULT 0,
    failed_count INT DEFAULT 0,
    pipeline_stage VARCHAR(100) DEFAULT 'INITIALIZED',
    pipeline_details JSONB DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ
);

-- ─── Documents ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id INT REFERENCES sources(id) ON DELETE SET NULL,
    crawl_job_id UUID REFERENCES crawl_jobs(id) ON DELETE CASCADE,
    url TEXT UNIQUE NOT NULL,
    canonical_url TEXT,
    title TEXT,
    content_type VARCHAR(100),
    language VARCHAR(20),
    http_status INT,
    content_hash VARCHAR(64) NOT NULL,
    raw_path TEXT,
    markdown_path TEXT,
    text_path TEXT,
    word_count INT DEFAULT 0,
    links_count INT DEFAULT 0,
    images_count INT DEFAULT 0,
    content_embedding vector(384),   -- pgvector: 384-dim for all-MiniLM-L6-v2
    retrieved_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_content_embedding
    ON documents USING ivfflat (content_embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS document_versions (
    id SERIAL PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    version_number INT DEFAULT 1,
    content_hash VARCHAR(64) NOT NULL,
    raw_path TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ─── Resources ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    parent_page_url TEXT,
    resource_url TEXT NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    mime_type VARCHAR(100),
    file_extension VARCHAR(20),
    file_name TEXT,
    anchor_text TEXT,
    http_status INT,
    content_length BIGINT,
    hash VARCHAR(64),
    raw_path TEXT,
    downloaded BOOLEAN DEFAULT FALSE,
    discovered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resource_links (
    id SERIAL PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    resource_id UUID REFERENCES resources(id) ON DELETE CASCADE,
    anchor_text TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ─── Universal & Domain Records ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS universal_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    domain_id INT REFERENCES domains(id) ON DELETE SET NULL,
    subdomain_id INT REFERENCES subdomains(id) ON DELETE SET NULL,
    entity_type VARCHAR(100),
    canonical_name TEXT,
    title TEXT,
    description TEXT,
    url TEXT NOT NULL,
    language VARCHAR(20),
    country VARCHAR(100),
    location TEXT,
    status VARCHAR(50),
    confidence DECIMAL(5,4),
    metadata_json JSONB DEFAULT '{}'::jsonb,  -- renamed from 'metadata' (reserved word)
    entity_embedding vector(384),              -- pgvector: entity semantic search
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_universal_records_entity_embedding
    ON universal_records USING ivfflat (entity_embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS domain_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    universal_record_id UUID REFERENCES universal_records(id) ON DELETE CASCADE,
    domain_id INT REFERENCES domains(id) ON DELETE SET NULL,
    schema_version VARCHAR(50) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ─── Extraction Tracking ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS extracted_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    universal_record_id UUID REFERENCES universal_records(id) ON DELETE SET NULL,
    field_name VARCHAR(100) NOT NULL,
    field_value TEXT,
    value_type VARCHAR(50),
    confidence DECIMAL(5,4),
    extractor VARCHAR(100) NOT NULL,
    schema_version VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id UUID REFERENCES extracted_facts(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    text_snippet TEXT,
    selector TEXT,
    page_number INT,
    line_reference TEXT,
    confidence DECIMAL(5,4),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crawl_job_id UUID REFERENCES crawl_jobs(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    domain_id INT REFERENCES domains(id) ON DELETE SET NULL,
    extractor_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    duration_ms INT,
    fields_extracted INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_definitions (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(100) UNIQUE NOT NULL,
    version VARCHAR(50) NOT NULL,
    schema_definition JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_errors (
    id SERIAL PRIMARY KEY,
    crawl_job_id UUID REFERENCES crawl_jobs(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    url TEXT NOT NULL,
    stage VARCHAR(100) NOT NULL,
    error_type VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    stack_trace TEXT,
    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ─── Agent & Discovery Tracking ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_state (
    id SERIAL PRIMARY KEY,
    status VARCHAR(50) NOT NULL DEFAULT 'PAUSED',
    current_domain VARCHAR(100),
    current_subdomain VARCHAR(100),
    current_keyword TEXT,
    last_run_at TIMESTAMPTZ,
    state_data JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS search_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword TEXT NOT NULL,
    domain VARCHAR(100),
    sources_found INT DEFAULT 0,
    relevant_sources INT DEFAULT 0,
    entities_discovered INT DEFAULT 0,
    batch_id UUID,
    is_fallback BOOLEAN DEFAULT FALSE,
    log_message TEXT,
    executed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batch_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(50) DEFAULT 'RUNNING',
    searches_planned INT DEFAULT 0,
    searches_executed INT DEFAULT 0,
    urls_discovered INT DEFAULT 0,
    urls_crawled INT DEFAULT 0,
    entities_discovered INT DEFAULT 0,
    entities_verified INT DEFAULT 0,
    duplicates_removed INT DEFAULT 0,
    feedback_generated BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS keyword_performance (
    keyword TEXT PRIMARY KEY,
    domain VARCHAR(100),
    usage_count INT DEFAULT 0,
    success_rate DECIMAL(5,4) DEFAULT 0,
    last_used TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_deprecated BOOLEAN DEFAULT FALSE,
    feedback_notes TEXT
);

CREATE TABLE IF NOT EXISTS verification_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    universal_record_id UUID REFERENCES universal_records(id) ON DELETE CASCADE,
    is_verified BOOLEAN DEFAULT FALSE,
    confidence DECIMAL(5,4) DEFAULT 0,
    verification_notes TEXT,
    verified_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ─── Seed Domains ─────────────────────────────────────────────────────────────

INSERT INTO domains (name, description) VALUES
    ('Technology', 'Technology companies, software, hardware, IT services'),
    ('Healthcare',  'Hospitals, clinics, medical services, healthcare organizations'),
    ('Education',   'Universities, schools, online learning, educational institutions'),
    ('Business',    'General business enterprises, services, products, corporate data')
ON CONFLICT (name) DO NOTHING;

-- ─── Seed Metadata rows for domains ──────────────────────────────────────────

INSERT INTO metadata (entity_type, entity_key, domain, is_present, source_table, extra)
    SELECT 'domain', name, name, TRUE, 'domains', jsonb_build_object('description', description)
    FROM domains
ON CONFLICT (entity_type, entity_key) DO NOTHING;
