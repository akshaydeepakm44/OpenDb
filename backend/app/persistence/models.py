import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, BigInteger, Numeric, Float,
    DateTime, ForeignKey, JSON, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.persistence.database import Base

JSONB_TYPE = JSON().with_variant(JSONB, "postgresql")

# pgvector: graceful fallback if not installed in environment
try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False
    Vector = None

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Metadata(Base):
    """
    Central metadata registry — answers 'does X exist in the system?'
    entity_type: 'domain' | 'subdomain' | 'keyword' | 'url' | 'schema' | 'batch'
    entity_key:  the actual value (e.g. 'Technology', 'healthcare.org')
    is_present:  True = active/exists, False = deprecated/removed
    """
    __tablename__ = "metadata"
    __table_args__ = {'extend_existing': True}

    id           = Column(Integer, primary_key=True, index=True)
    entity_type  = Column(String(100), nullable=False)
    entity_key   = Column(Text, nullable=False)
    domain       = Column(String(100), nullable=True)
    subdomain    = Column(String(100), nullable=True)
    is_present   = Column(Boolean, default=True)
    source_table = Column(String(100), nullable=True)
    source_id    = Column(Text, nullable=True)
    extra        = Column(JSONB_TYPE, default=dict)
    created_at   = Column(DateTime(timezone=True), default=utc_now)
    updated_at   = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False)  # website, API, RSS, user-provided URL
    base_url = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    documents = relationship("Document", back_populates="source")

class Domain(Base):
    __tablename__ = "domains"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    subdomains = relationship("Subdomain", back_populates="domain")

class Subdomain(Base):
    __tablename__ = "subdomains"

    id = Column(Integer, primary_key=True, index=True)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    domain = relationship("Domain", back_populates="subdomains")

class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    starting_url = Column(Text, nullable=False)
    query = Column(Text, nullable=True)
    domain_name = Column(String(100), nullable=True)
    max_depth = Column(Integer, default=2)
    max_pages = Column(Integer, default=20)
    status = Column(String(50), default="pending")  # pending, running, completed, failed
    pages_discovered = Column(Integer, default=0)
    pages_crawled = Column(Integer, default=0)
    documents_count = Column(Integer, default=0)
    resources_count = Column(Integer, default=0)
    successful_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    pipeline_stage = Column(String(100), default="INITIALIZED")
    pipeline_details = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    documents = relationship("Document", back_populates="crawl_job")
    errors = relationship("CrawlError", back_populates="crawl_job")

class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    crawl_job_id = Column(String(36), ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=True)
    url = Column(Text, unique=True, nullable=False)
    canonical_url = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    content_type = Column(String(100), nullable=True)
    language = Column(String(20), nullable=True)
    http_status = Column(Integer, nullable=True)
    content_hash = Column(String(64), nullable=False)
    raw_path = Column(Text, nullable=True)
    markdown_path = Column(Text, nullable=True)
    text_path = Column(Text, nullable=True)
    word_count = Column(Integer, default=0)
    links_count = Column(Integer, default=0)
    images_count = Column(Integer, default=0)
    content_embedding = Column(Vector(384)) if HAS_PGVECTOR else Column(Text, nullable=True)  # pgvector 384-dim
    retrieved_at = Column(DateTime(timezone=True), default=utc_now)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)

    source = relationship("Source", back_populates="documents")
    crawl_job = relationship("CrawlJob", back_populates="documents")
    versions = relationship("DocumentVersion", back_populates="document")
    resources = relationship("Resource", back_populates="source_document")
    universal_records = relationship("UniversalRecord", back_populates="document")

class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, default=1)
    content_hash = Column(String(64), nullable=False)
    raw_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    document = relationship("Document", back_populates="versions")

class Resource(Base):
    __tablename__ = "resources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    source_url = Column(Text, nullable=False)
    parent_page_url = Column(Text, nullable=True)
    resource_url = Column(Text, nullable=False)
    resource_type = Column(String(50), nullable=False)  # document, media, api, asset
    mime_type = Column(String(100), nullable=True)
    file_extension = Column(String(20), nullable=True)
    file_name = Column(Text, nullable=True)
    anchor_text = Column(Text, nullable=True)
    http_status = Column(Integer, nullable=True)
    content_length = Column(BigInteger, nullable=True)
    hash = Column(String(64), nullable=True)
    raw_path = Column(Text, nullable=True)
    downloaded = Column(Boolean, default=False)
    discovered_at = Column(DateTime(timezone=True), default=utc_now)

    source_document = relationship("Document", back_populates="resources")

class ResourceLink(Base):
    __tablename__ = "resource_links"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(String(36), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    anchor_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

class RecordState:
    DISCOVERED = "DISCOVERED"
    CLASSIFIED = "CLASSIFIED"
    QUEUED_FOR_CRAWL = "QUEUED_FOR_CRAWL"
    CRAWLING = "CRAWLING"
    CRAWLED = "CRAWLED"
    RAW_INGESTED = "RAW_INGESTED"
    QUEUED_FOR_ENRICHMENT = "QUEUED_FOR_ENRICHMENT"
    VERIFYING = "VERIFYING"
    EXTRACTING = "EXTRACTING"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    POSTGRES_SYNC_PENDING = "POSTGRES_SYNC_PENDING"
    POSTGRES_SYNCED = "POSTGRES_SYNCED"
    ARCHIVED = "ARCHIVED"

    # Alternative / Terminal Failure States
    REJECTED = "REJECTED"
    DEDUPLICATED = "DEDUPLICATED"
    FAILED = "FAILED"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"


class UniversalRecord(Base):
    __tablename__ = "universal_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="SET NULL"), nullable=True)
    subdomain_id = Column(Integer, ForeignKey("subdomains.id", ondelete="SET NULL"), nullable=True)
    entity_type = Column(String(100), nullable=True)
    canonical_name = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    url = Column(Text, nullable=False)
    language = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True)
    location = Column(Text, nullable=True)
    status = Column(String(50), default=RecordState.DISCOVERED, index=True)
    confidence    = Column(Numeric(5, 4), nullable=True)
    metadata_json = Column(JSONB_TYPE, default=dict)

    # Postgres Sync Dual-Layer Tracking
    postgres_sync_status = Column(String(50), default="PENDING")  # PENDING | SYNCED | FAILED
    postgres_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_error = Column(Text, nullable=True)

    entity_embedding = Column(Vector(384)) if HAS_PGVECTOR else Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at    = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    document = relationship("Document", back_populates="universal_records")
    domain_records = relationship("DomainRecord", back_populates="universal_record")
    facts = relationship("ExtractedFact", back_populates="universal_record")
    domain = relationship("Domain")
    subdomain = relationship("Subdomain")

class SearchCandidate(Base):
    """Rule 1 & Rule A: Raw SearXNG output stored ONLY as SEARCH_CANDIDATE."""
    __tablename__ = "search_candidates"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query = Column(Text, nullable=True)
    search_query = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    raw_url = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    snippet = Column(Text, nullable=True)
    canonical_domain = Column(String(255), nullable=True, index=True)
    source_type = Column(String(50), default="UNKNOWN")  # COMPANY_OFFICIAL_SITE | BLOG | NEWS | DIRECTORY | etc.
    source_category = Column(String(50), nullable=True)
    status = Column(String(50), default="SEARCH_CANDIDATE", index=True) # SEARCH_CANDIDATE | REJECTED | ALLOWED | QUALIFIED
    gate1_passed = Column(Boolean, default=False)
    rejection_reason = Column(Text, nullable=True)
    confidence_score = Column(Integer, default=0)
    batch_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Company(Base):
    """SQLite Operational Truth — Only Qualified or Verified Company Entities."""
    __tablename__ = "companies"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_domain = Column(String(255), nullable=False, unique=True, index=True)
    company_name = Column(String(255), nullable=False)
    company_type = Column(String(100), nullable=True)  # B2B SaaS, IT Services, E-commerce, etc.
    industry = Column(String(100), nullable=True, index=True)
    subindustry = Column(String(100), nullable=True)
    company_confidence_score = Column(Float, default=0.0) # 0 to 100
    qualification_stage = Column(String(50), default="GATE1_PASSED") # GATE1_PASSED | STAGE1_CRAWLED | QUALIFIED | DEEP_CRAWLED | VERIFIED
    status = Column(String(50), default="QUALIFIED_COMPANY", index=True) # QUALIFIED_COMPANY | VERIFIED_COMPANY | REJECTED
    official_website = Column(Text, nullable=True)
    official_url = Column(Text, nullable=True)
    hq_country = Column(String(100), nullable=True)
    hq_city = Column(String(100), nullable=True)
    employee_size = Column(String(50), nullable=True)
    employee_count_range = Column(String(50), nullable=True)
    revenue_range = Column(String(50), nullable=True)
    logo_url = Column(Text, nullable=True)
    business_overview = Column(Text, nullable=True)
    technology_stack = Column(JSONB_TYPE, default=list)
    verified_emails = Column(JSONB_TYPE, default=list)
    contact_numbers = Column(JSONB_TYPE, default=list)
    decision_makers = Column(JSONB_TYPE, default=list)
    evidence_data = Column(JSONB_TYPE, default=dict)
    qualification_reasons = Column(JSONB_TYPE, default=list)
    meta_info = Column(JSONB_TYPE, default=dict)

    postgres_sync_status = Column(String(50), default="PENDING", index=True) # PENDING | SYNCED | FAILED
    postgres_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_error = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PostgresSyncOutbox(Base):
    """Rule D & Phase 11: Transactional Outbox for Durable Async Sync from SQLite Staging to PostgreSQL."""
    __tablename__ = "postgres_sync_outbox"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(50), default="COMPANY") # COMPANY | SEARCH_CANDIDATE
    entity_id = Column(String(36), nullable=True)
    company_id = Column(String(36), nullable=True)
    domain = Column(String(255), nullable=True, index=True)
    canonical_domain = Column(String(255), nullable=True)
    completeness_score = Column(Float, default=0.0)
    badge = Column(String(100), nullable=True)
    status_code = Column(String(50), nullable=True) # QUALIFIED_COMPANY, HIGH_QUALITY_COMPANY, VERIFIED_COMPLETE
    action = Column(String(50), default="UPSERT")
    payload = Column(JSONB_TYPE, nullable=True)
    payload_json = Column(JSONB_TYPE, nullable=True)
    sync_status = Column(String(50), default="PENDING", index=True) # PENDING | SYNCED | FAILED
    status = Column(String(50), default="PENDING")
    processed = Column(Boolean, default=False, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    synced_at = Column(DateTime(timezone=True), nullable=True)



class DomainRecord(Base):
    __tablename__ = "domain_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    universal_record_id = Column(String(36), ForeignKey("universal_records.id", ondelete="CASCADE"), nullable=False, index=True)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="SET NULL"), nullable=True)
    schema_version = Column(String(50), nullable=False)
    data = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    universal_record = relationship("UniversalRecord", back_populates="domain_records")

class ExtractedFact(Base):
    __tablename__ = "extracted_facts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    universal_record_id = Column(String(36), ForeignKey("universal_records.id", ondelete="SET NULL"), nullable=True)
    field_name = Column(String(100), nullable=False)
    field_value = Column(Text, nullable=True)
    value_type = Column(String(50), nullable=True)  # string, array, int, null
    confidence = Column(Numeric(5, 4), nullable=True)
    extractor = Column(String(100), nullable=False)  # deterministic, llm, document
    schema_version = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    universal_record = relationship("UniversalRecord", back_populates="facts")
    evidence_items = relationship("Evidence", back_populates="fact")

class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    fact_id = Column(String(36), ForeignKey("extracted_facts.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    source_url = Column(Text, nullable=False)
    text_snippet = Column(Text, nullable=True)
    selector = Column(Text, nullable=True)
    page_number = Column(Integer, nullable=True)
    line_reference = Column(Text, nullable=True)
    confidence = Column(Numeric(5, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    fact = relationship("ExtractedFact", back_populates="evidence_items")

class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    crawl_job_id = Column(String(36), ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=True)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="SET NULL"), nullable=True)
    extractor_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    duration_ms = Column(Integer, nullable=True)
    fields_extracted = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)

class SchemaDefinition(Base):
    __tablename__ = "schema_definitions"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(100), unique=True, nullable=False)
    version = Column(String(50), nullable=False)
    schema_definition = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

class CrawlError(Base):
    __tablename__ = "crawl_errors"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    crawl_job_id = Column(String(36), ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=True)  # nullable — agent tasks have no job
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    url = Column(Text, nullable=False)
    stage = Column(String(100), nullable=False)
    error_type = Column(String(100), nullable=False)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)

    crawl_job = relationship("CrawlJob", back_populates="errors")


class CrawlActivityLog(Base):
    """
    Live crawl activity log — one row per URL crawled by the agent.
    Surfaced in the 'Live Crawl Activity Stream' tab of the UI.
    """
    __tablename__ = "crawl_activity_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(Text, nullable=False)
    domain = Column(String(100), nullable=True)
    stage = Column(String(100), nullable=False)   # SEARCH | CRAWL | EXTRACT | VERIFY | FILTER
    status = Column(String(50), nullable=False)   # OK | FILTERED | DUPLICATE | ERROR
    message = Column(Text, nullable=True)
    entity_name = Column(Text, nullable=True)     # if entity was resolved
    batch_id = Column(String(36), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)

class AgentState(Base):
    __tablename__ = "agent_state"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(50), nullable=False, default='PAUSED')
    current_domain = Column(String(100), nullable=True)
    current_subdomain = Column(String(100), nullable=True)
    current_keyword = Column(Text, nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    state_data = Column(JSON, default=dict)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    keyword = Column(Text, nullable=False)
    domain = Column(String(100), nullable=True)
    sources_found = Column(Integer, default=0)
    relevant_sources = Column(Integer, default=0)
    entities_discovered = Column(Integer, default=0)
    batch_id = Column(String(36), nullable=True)
    is_fallback = Column(Boolean, default=False)
    log_message = Column(Text, nullable=True)
    executed_at = Column(DateTime(timezone=True), default=utc_now)

class BatchResult(Base):
    __tablename__ = "batch_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status = Column(String(50), default="RUNNING")
    searches_planned = Column(Integer, default=0)
    searches_executed = Column(Integer, default=0)
    urls_discovered = Column(Integer, default=0)
    urls_crawled = Column(Integer, default=0)
    entities_discovered = Column(Integer, default=0)
    entities_verified = Column(Integer, default=0)
    duplicates_removed = Column(Integer, default=0)
    feedback_generated = Column(Boolean, default=False)
    started_at = Column(DateTime(timezone=True), default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

class KeywordPerformance(Base):
    __tablename__ = "keyword_performance"

    keyword = Column(Text, primary_key=True)
    domain = Column(String(100), nullable=True)
    usage_count = Column(Integer, default=0)
    success_rate = Column(Numeric(5, 4), default=0)
    last_used = Column(DateTime(timezone=True), default=utc_now)
    is_deprecated = Column(Boolean, default=False)
    feedback_notes = Column(Text, nullable=True)

class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    universal_record_id = Column(String(36), ForeignKey("universal_records.id", ondelete="CASCADE"), nullable=False)
    is_verified = Column(Boolean, default=False)
    confidence = Column(Numeric(5, 4), default=0)
    verification_notes = Column(Text, nullable=True)
    verified_at = Column(DateTime(timezone=True), default=utc_now)
    
    universal_record = relationship("UniversalRecord")


class BlockedDomain(Base):
    __tablename__ = "blocked_domains"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String(255), nullable=False, unique=True, index=True)
    reason_category = Column(String(100), nullable=False)
    source = Column(String(50), nullable=False) # searxng_block, reputation_api, content_moderation, manual_review, manual_admin
    created_at = Column(DateTime(timezone=True), default=utc_now)


class QuarantinedContent(Base):
    __tablename__ = "quarantined_content"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), nullable=True)
    url = Column(Text, nullable=False)
    domain = Column(String(255), nullable=False)
    content_type = Column(String(50), nullable=False) # text, logo, image
    flagged_categories = Column(JSONB_TYPE, default=list)
    confidence_score = Column(Numeric(5, 4), default=0)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class ManualReviewQueue(Base):
    __tablename__ = "manual_review_queue"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(Text, nullable=False)
    domain = Column(String(255), nullable=False)
    item_type = Column(String(50), nullable=False) # text_ambiguous, logo_ambiguous, entity_uncertain
    content_snippet = Column(Text, nullable=True)
    score = Column(Numeric(5, 4), default=0)
    status = Column(String(30), default="pending") # pending, approved, rejected
    created_at = Column(DateTime(timezone=True), default=utc_now)


# ─────────────────────────────────────────────────────────────────────────────
# ENTERPRISE ARCHITECTURE MODELS (PostgreSQL Open Lake & SQLite WAL Vault)
# ─────────────────────────────────────────────────────────────────────────────

class OpenLakeRecord(Base):
    """PostgreSQL Open Lake dispatch candidates repository."""
    __tablename__ = "open_lake_records"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String(255), nullable=False, unique=True, index=True)
    enrichment_status = Column(String(50), default="pending") # pending, in_progress, enriched, failed
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class GlobalLead(Base):
    """SQLite WAL Master Vault - Primary lead repository."""
    __tablename__ = "global_leads"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True) # md5 hash of domain
    domain = Column(String(255), nullable=False, unique=True, index=True)
    company_name = Column(String(255), nullable=False)
    minio_asset_path = Column(Text, nullable=True) # companies/{domain}/brand_kit.json
    logo_url = Column(Text, nullable=True)
    technology_stack = Column(JSONB_TYPE, default=list) # JSON array of tech stack
    quality_score = Column(Float, default=0.0)
    headquarters = Column(Text, nullable=True)
    industry = Column(Text, nullable=True)
    company_size = Column(Text, nullable=True)
    revenue_funding = Column(Text, nullable=True)
    verified_emails = Column(JSONB_TYPE, default=list)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    people = relationship("GlobalLeadPerson", back_populates="lead", cascade="all, delete-orphan")
    subpages = relationship("GlobalLeadSubpage", back_populates="lead", cascade="all, delete-orphan")


class GlobalLeadPerson(Base):
    """Key decision makers & leadership associated with a global lead."""
    __tablename__ = "global_lead_people"
    __table_args__ = (
        UniqueConstraint('domain', 'full_name', name='uix_domain_full_name'),
        {'extend_existing': True}
    )

    id = Column(String(36), primary_key=True) # md5 hash of domain + full_name
    global_lead_id = Column(String(36), ForeignKey("global_leads.id", ondelete="CASCADE"), nullable=False)
    domain = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    title = Column(String(255), nullable=True)
    linkedin_search_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    lead = relationship("GlobalLead", back_populates="people")


class GlobalLeadSubpage(Base):
    """Crawled Markdown DOM subpages stored in MinIO L3 object storage."""
    __tablename__ = "global_lead_subpages"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    global_lead_id = Column(String(36), ForeignKey("global_leads.id", ondelete="CASCADE"), nullable=False)
    domain = Column(String(255), nullable=False)
    page_url = Column(Text, nullable=False, unique=True)
    minio_object_path = Column(Text, nullable=False) # pages/{slug}.md
    created_at = Column(DateTime(timezone=True), default=utc_now)

    lead = relationship("GlobalLead", back_populates="subpages")


class VerificationRun(Base):
    """Audit log for each agentic verification round for a company."""
    __tablename__ = "verification_runs"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(100), nullable=False, index=True)
    domain = Column(String(255), nullable=False, index=True)
    verification_round = Column(Integer, default=1)
    status = Column(String(50), default="INITIATED")  # INITIATED, IN_PROGRESS, COMPLETED, RECRAWL_NEEDED, MAX_ROUNDS_REACHED
    score_before = Column(Float, default=0.0)
    score_after = Column(Float, default=0.0)
    missing_fields = Column(JSONB_TYPE, default=list)
    fields_found = Column(JSONB_TYPE, default=list)
    agent_decision = Column(String(50), default="RECRAWL_REQUIRED")
    details = Column(JSONB_TYPE, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class VerificationRequirement(Base):
    """Field-level completeness requirement checklist for a company."""
    __tablename__ = "verification_requirements"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(100), nullable=False, index=True)
    field_name = Column(String(100), nullable=False)
    status = Column(String(50), default="MISSING")  # OBSERVED, INFERRED, MISSING, CONFLICTING, UNAVAILABLE_PUBLICLY
    confidence = Column(Float, default=0.0)
    evidence_source_url = Column(Text, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), default=utc_now)


class VerificationCrawlRequest(Base):
    """Structured crawl plan issued by Agent for Crawler."""
    __tablename__ = "verification_crawl_requests"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(100), nullable=False, index=True)
    domain = Column(String(255), nullable=False)
    verification_round = Column(Integer, default=1)
    objective = Column(Text, nullable=False)
    missing_fields = Column(JSONB_TYPE, default=list)
    priority_pages = Column(JSONB_TYPE, default=list)
    search_patterns = Column(JSONB_TYPE, default=list)
    max_pages = Column(Integer, default=5)
    status = Column(String(50), default="PENDING")  # PENDING, IN_PROGRESS, COMPLETED, FAILED
    created_at = Column(DateTime(timezone=True), default=utc_now)


class VerificationCrawlResult(Base):
    """Structured result returned by Crawler to Agent."""
    __tablename__ = "verification_crawl_results"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(String(36), ForeignKey("verification_crawl_requests.id", ondelete="CASCADE"), nullable=False)
    company_id = Column(String(100), nullable=False, index=True)
    pages_crawled = Column(JSONB_TYPE, default=list)
    new_facts = Column(JSONB_TYPE, default=list)
    fields_filled = Column(JSONB_TYPE, default=list)
    fields_remaining = Column(JSONB_TYPE, default=list)
    vault_objects = Column(JSONB_TYPE, default=list)
    failures = Column(JSONB_TYPE, default=list)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class CompanyEvidence(Base):
    """Granular provenance evidence item linking facts to source URLs."""
    __tablename__ = "company_evidence"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(100), nullable=False, index=True)
    field_name = Column(String(100), nullable=False)
    field_value = Column(Text, nullable=True)
    source_url = Column(Text, nullable=False)
    evidence_type = Column(String(50), default="official_company_page")  # official_company_page, meta_tag, dataset, auxiliary_searxng
    confidence = Column(Float, default=1.0)
    status = Column(String(50), default="OBSERVED")  # OBSERVED, INFERRED, MISSING, CONFLICTING, UNAVAILABLE_PUBLICLY
    extracted_at = Column(DateTime(timezone=True), default=utc_now)


class DataCompletenessScore(Base):
    """100-Point Data Completeness Score breakdown and badge level."""
    __tablename__ = "data_completeness_scores"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(100), nullable=False, unique=True, index=True)
    total_score = Column(Float, default=0.0)
    badge_level = Column(String(100), default="🔴 INSUFFICIENT DATA")
    dimension_scores = Column(JSONB_TYPE, default=dict)
    checklist = Column(JSONB_TYPE, default=dict)
    formula_explanation = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class QuarantineRecord(Base):
    """Quarantine Staging Table for Records Failing Dataset-Level Verification Checkpoints."""
    __tablename__ = "quarantine_records"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), nullable=True, index=True)
    domain = Column(String(255), nullable=False, index=True)
    canonical_name = Column(String(255), nullable=True)
    rejection_reasons = Column(JSONB_TYPE, default=list)
    checkpoint_failures = Column(JSONB_TYPE, default=dict)
    quarantined_dossier = Column(JSONB_TYPE, default=dict)
    promoted = Column(Boolean, default=False, index=True)
    promoted_by = Column(String(255), nullable=True)
    promotion_reason = Column(Text, nullable=True)
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)


class GlobalVerificationReport(Base):
    """Audit Report Log for Dataset Ingestion Runs."""
    __tablename__ = "global_verification_reports"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = Column(String(36), nullable=False, index=True)
    records_ingested = Column(Integer, default=0)
    records_rejected_outright = Column(Integer, default=0)
    checkpoint_failures = Column(JSONB_TYPE, default=dict)
    batch_accepted = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)


class DenyListDomain(Base):
    """Dynamic DB-Backed Denied Aggregator and Malicious Domains Table."""
    __tablename__ = "denylist_domains"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String(255), nullable=False, unique=True, index=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class DenyListCategory(Base):
    """Dynamic DB-Backed Denied Content Categories Table."""
    __tablename__ = "denylist_categories"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category = Column(String(100), nullable=False, unique=True, index=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)









