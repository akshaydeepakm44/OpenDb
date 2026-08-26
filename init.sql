-- OpenDB PostgreSQL Ingestion & Extraction Core Schema

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
    retrieved_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_versions (
    id SERIAL PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    version_number INT DEFAULT 1,
    content_hash VARCHAR(64) NOT NULL,
    raw_path TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

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
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS domain_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    universal_record_id UUID REFERENCES universal_records(id) ON DELETE CASCADE,
    domain_id INT REFERENCES domains(id) ON DELETE SET NULL,
    schema_version VARCHAR(50) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

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

-- Seed Initial Domains
INSERT INTO domains (name, description) VALUES
('Technology', 'Technology companies, software, hardware, IT services')
ON CONFLICT (name) DO NOTHING;

INSERT INTO domains (name, description) VALUES
('Healthcare', 'Hospitals, clinics, medical services, healthcare organizations')
ON CONFLICT (name) DO NOTHING;

INSERT INTO domains (name, description) VALUES
('Education', 'Universities, schools, online learning, educational institutions')
ON CONFLICT (name) DO NOTHING;

INSERT INTO domains (name, description) VALUES
('Business', 'General business enterprises, services, products, corporate data')
ON CONFLICT (name) DO NOTHING;
