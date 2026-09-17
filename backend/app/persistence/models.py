import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, BigInteger, Numeric, Float,
    DateTime, ForeignKey, JSON, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
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


# ─────────────────────────────────────────────────────────────────────────────
# 1. CORE BUSINESS ENTITIES
# ─────────────────────────────────────────────────────────────────────────────

class IndustryTaxonomy(Base):
    """
    Canonical Industry Taxonomy.
    Separates conceptual business industry classifications from DNS web domains.
    """
    __tablename__ = "industry_taxonomies"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Company(Base):
    """
    Canonical Company Intelligence Entity.
    Single authoritative owner consolidating discovery leads, universal records,
    and verified corporate profiles.
    """
    __tablename__ = "companies"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_name = Column(String(255), nullable=False, index=True)
    legal_name = Column(String(255), nullable=True)
    primary_domain = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    industry = Column(String(100), nullable=True, index=True)
    headquarters = Column(Text, nullable=True)
    country = Column(String(100), nullable=True)
    employee_range = Column(String(50), nullable=True)
    revenue_range = Column(String(50), nullable=True)
    linkedin_url = Column(Text, nullable=True)
    logo_url = Column(Text, nullable=True)
    technology_stack = Column(JSONB_TYPE, default=list)
    verified_emails = Column(JSONB_TYPE, default=list)
    quality_score = Column(Float, default=0.0)
    status = Column(String(50), default="DISCOVERED", index=True)  # DISCOVERED, CRAWLED, VERIFIED, REJECTED
    confidence = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    domains = relationship("Domain", back_populates="company")
    documents = relationship("Document", back_populates="company")
    key_people = relationship("KeyPerson", back_populates="company", cascade="all, delete-orphan")
    verification_sessions = relationship("VerificationSession", back_populates="company", cascade="all, delete-orphan")
    evidence_items = relationship("CanonicalEvidence", back_populates="company", cascade="all, delete-orphan")

    # Seamless backward compatibility hybrid properties
    @hybrid_property
    def domain(self):
        return self.primary_domain

    @domain.setter
    def domain(self, val):
        self.primary_domain = val

    @hybrid_property
    def company_name(self):
        return self.canonical_name

    @company_name.setter
    def company_name(self, val):
        self.canonical_name = val

    @hybrid_property
    def name(self):
        return self.canonical_name

    @name.setter
    def name(self, val):
        self.canonical_name = val

    @hybrid_property
    def url(self):
        return f"https://{self.primary_domain}" if self.primary_domain else ""

    @url.setter
    def url(self, val):
        if val:
            parsed = urlparse(val)
            self.primary_domain = (parsed.netloc or val).replace("www.", "").strip()

    @hybrid_property
    def entity_type(self):
        return self.industry or "Organization"

    @entity_type.setter
    def entity_type(self, val):
        self.industry = val

    @hybrid_property
    def summary(self):
        return self.description

    @summary.setter
    def summary(self, val):
        self.description = val

    @hybrid_property
    def company_size(self):
        return self.employee_range

    @company_size.setter
    def company_size(self, val):
        self.employee_range = val

    @hybrid_property
    def revenue_funding(self):
        return self.revenue_range

    @revenue_funding.setter
    def revenue_funding(self, val):
        self.revenue_range = val

    @hybrid_property
    def location(self):
        return self.headquarters

    @location.setter
    def location(self, val):
        self.headquarters = val

    @property
    def metadata_json(self):
        return {}

    @metadata_json.setter
    def metadata_json(self, val):
        # No backing column — stored in VerificationSession.investigation_log instead
        pass

    @property
    def people(self):
        return self.key_people

    @property
    def subpages(self):
        return self.documents

    @hybrid_property
    def document_id(self):
        return self.documents[0].id if self.documents else None

    @document_id.setter
    def document_id(self, val):
        pass


class Domain(Base):
    """
    Canonical Internet Web Domain Entity.
    Tracks hostname reputation, crawl status, and maps to owning company.
    """
    __tablename__ = "domains"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(36), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(100), unique=True, nullable=False)
    domain = Column(String(255), unique=True, nullable=True, index=True)
    canonical_url = Column(Text, nullable=True)
    domain_type = Column(String(50), default="primary")
    status = Column(String(50), default="active")
    description = Column(Text, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), default=utc_now)
    last_crawled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    company = relationship("Company", back_populates="domains")

    # Seamless backward compatibility hybrid properties
    @hybrid_property
    def universal_record_id(self):
        return self.company_id

    @universal_record_id.setter
    def universal_record_id(self, val):
        self.company_id = val

    @property
    def data(self):
        return {}


class Document(Base):
    """
    Canonical Crawled Web Document Entity.
    Authoritative pointer to raw MinIO HTML/Markdown storage artifacts with content hashes.
    """
    __tablename__ = "documents"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    crawl_job_id = Column(String(36), nullable=True)
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
    lifecycle_state = Column(String(50), default="CRAWLED_PENDING_AGENT_2", index=True)
    raw_artifacts = Column(JSON, default=list)
    raw_metadata = Column(JSON, default=dict)
    content_embedding = Column(Vector(384)) if HAS_PGVECTOR else Column(Text, nullable=True)
    retrieved_at = Column(DateTime(timezone=True), default=utc_now)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    company = relationship("Company", back_populates="documents")
    source = relationship("Source", back_populates="documents")


class KeyPerson(Base):
    """
    Canonical Key Person / Decision Maker Model.
    Single authoritative owner consolidating Agent 1 search discovery and Agent 2 LinkedIn profiles.
    """
    __tablename__ = "key_people"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True)
    verification_session_id = Column(String(36), ForeignKey("verification_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    full_name = Column(String(255), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    role = Column(String(255), nullable=True)
    linkedin_url = Column(Text, nullable=True, index=True)
    linkedin_search_url = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    source_domain = Column(String(255), nullable=True)
    source_type = Column(String(100), default="linkedin_profile")
    evidence_text = Column(Text, nullable=True)
    confidence_score = Column(Float, default=0.0)
    verification_status = Column(String(50), default="DISCOVERED", index=True)
    company_match_status = Column(Boolean, default=False)
    is_leadership = Column(Boolean, default=False)
    rejection_reason = Column(Text, nullable=True)
    discovered_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    company = relationship("Company", back_populates="key_people")
    verification_session = relationship("VerificationSession", back_populates="key_people")

    @hybrid_property
    def person_name(self):
        return self.full_name

    @person_name.setter
    def person_name(self, val):
        self.full_name = val

    @hybrid_property
    def name(self):
        return self.full_name

    @name.setter
    def name(self, val):
        self.full_name = val

    @hybrid_property
    def session_id(self):
        return self.verification_session_id

    @session_id.setter
    def session_id(self, val):
        self.verification_session_id = val

    @hybrid_property
    def candidate_status(self):
        return self.verification_status

    @candidate_status.setter
    def candidate_status(self, val):
        self.verification_status = val

    @property
    def session(self):
        return self.verification_session

    @session.setter
    def session(self, val):
        self.verification_session = val

    @property
    def evidence_snippet(self):
        return self.evidence_text

    @evidence_snippet.setter
    def evidence_snippet(self, val):
        self.evidence_text = val

    @property
    def company_name(self):
        return self.company.canonical_name if self.company else ""


class VerificationSession(Base):
    """
    Canonical Verification Session Model.
    Single authoritative owner for Agent 2 deep investigation and verification contract lifecycle.
    """
    __tablename__ = "verification_sessions"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
    document_id = Column(String(36), nullable=True, index=True)
    domain = Column(String(255), nullable=False, index=True)
    company_name = Column(String(255), nullable=False)
    status = Column(String(60), default="AGENT2_QUEUED", index=True)
    priority_score = Column(Float, default=0.0)
    priority_reasons = Column(JSONB_TYPE, default=list)
    phase1_data = Column(JSONB_TYPE, default=dict)
    phase2_data = Column(JSONB_TYPE, default=dict)
    recrawl_count = Column(Integer, default=0)
    search_rounds = Column(Integer, default=0)
    investigation_log = Column(JSONB_TYPE, default=list)
    error_message = Column(Text, nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    company = relationship("Company", back_populates="verification_sessions")
    key_people = relationship("KeyPerson", back_populates="verification_session")
    evidence_items = relationship("CanonicalEvidence", back_populates="verification_session")

    @property
    def session_id(self):
        return self.id

    @property
    def person_candidates(self):
        return self.key_people


class CanonicalEvidence(Base):
    """
    Canonical Evidence Model.
    Single authoritative owner for field-level provenance, verified facts, snippets, and investigation records.
    """
    __tablename__ = "canonical_evidence"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True)
    verification_session_id = Column(String(36), ForeignKey("verification_sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id = Column(String(36), nullable=True)
    field_name = Column(String(100), nullable=False, index=True)
    value = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    evidence_snippet = Column(Text, nullable=True)
    verification_status = Column(String(50), default="UNVERIFIED", index=True)
    verification_method = Column(String(100), nullable=True)
    investigation_record = Column(JSONB_TYPE, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    company = relationship("Company", back_populates="evidence_items")
    verification_session = relationship("VerificationSession", back_populates="evidence_items")

    @hybrid_property
    def session_id(self):
        return self.verification_session_id

    @session_id.setter
    def session_id(self, val):
        self.verification_session_id = val

    @property
    def session(self):
        return self.verification_session

    @session.setter
    def session(self, val):
        self.verification_session = val


class Source(Base):
    """
    Discovered SERP and crawl source URLs.
    """
    __tablename__ = "sources"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    source_type = Column(String(100), default="website")
    base_url = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    documents = relationship("Document", back_populates="source")

    # Seamless backward compatibility hybrid properties
    @hybrid_property
    def url(self):
        return self.base_url

    @url.setter
    def url(self, val):
        self.base_url = val

    @hybrid_property
    def domain(self):
        return self.name

    @domain.setter
    def domain(self, val):
        self.name = val

    @hybrid_property
    def discovered_at(self):
        return self.created_at


# ─────────────────────────────────────────────────────────────────────────────
# 2. OPERATIONAL & TELEMETRY ENTITIES
# ─────────────────────────────────────────────────────────────────────────────

class CrawlActivityLog(Base):
    """
    Operational log of crawling, filtering, and worker activities.
    """
    __tablename__ = "crawl_activity_log"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(Text, nullable=True)
    domain = Column(String(255), nullable=True, index=True)
    stage = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)
    message = Column(Text, nullable=True)
    entity_name = Column(Text, nullable=True)
    batch_id = Column(String(36), nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now, index=True)


class CrawlError(Base):
    """
    Operational crawl failure records.
    """
    __tablename__ = "crawl_errors"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    crawl_job_id = Column(UUID(as_uuid=True), nullable=True)
    document_id = Column(UUID(as_uuid=True), nullable=True)
    url = Column(Text, nullable=False)
    stage = Column(String(100), nullable=True)
    error_type = Column(String(100), nullable=False)
    error_message = Column(Text, nullable=True)
    stack_trace = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)

    @hybrid_property
    def created_at(self):
        return self.timestamp


class BatchResult(Base):
    """
    Operational batch crawl summary and performance statistics.
    """
    __tablename__ = "batch_results"
    __table_args__ = {'extend_existing': True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    # Seamless backward compatibility hybrid properties
    @hybrid_property
    def queries_run(self):
        return self.searches_executed

    @queries_run.setter
    def queries_run(self, val):
        self.searches_executed = val

    @hybrid_property
    def sources_discovered(self):
        return self.urls_discovered

    @sources_discovered.setter
    def sources_discovered(self, val):
        self.urls_discovered = val

    @hybrid_property
    def domains_processed(self):
        return self.urls_crawled

    @domains_processed.setter
    def domains_processed(self, val):
        self.urls_crawled = val

    @hybrid_property
    def records_created(self):
        return self.entities_discovered

    @records_created.setter
    def records_created(self, val):
        self.entities_discovered = val

    @hybrid_property
    def records_updated(self):
        return self.entities_verified

    @records_updated.setter
    def records_updated(self, val):
        self.entities_verified = val


class SearchHistory(Base):
    """
    Operational record of discovery search queries run against SearXNG.
    """
    __tablename__ = "search_history"
    __table_args__ = {'extend_existing': True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    keyword = Column(Text, nullable=False)
    domain = Column(String(100), nullable=True)
    sources_found = Column(Integer, default=0)
    relevant_sources = Column(Integer, default=0)
    entities_discovered = Column(Integer, default=0)
    batch_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    is_fallback = Column(Boolean, default=False)
    log_message = Column(Text, nullable=True)
    executed_at = Column(DateTime(timezone=True), default=utc_now)

    # Seamless backward compatibility hybrid properties
    @hybrid_property
    def query(self):
        return self.keyword

    @query.setter
    def query(self, val):
        self.keyword = val

    @hybrid_property
    def search_engine(self):
        return "searxng"

    @hybrid_property
    def searched_at(self):
        return self.executed_at

    @searched_at.setter
    def searched_at(self, val):
        self.executed_at = val

    @hybrid_property
    def sources_new(self):
        return self.relevant_sources

    @sources_new.setter
    def sources_new(self, val):
        self.relevant_sources = val


class KeywordPerformance(Base):
    """
    Operational search keyword efficiency telemetry.
    """
    __tablename__ = "keyword_performance"
    __table_args__ = {'extend_existing': True}

    keyword = Column(Text, primary_key=True)
    domain = Column(String(100), nullable=True)
    usage_count = Column(Integer, default=0)
    success_rate = Column(Numeric, default=0.0)
    last_used = Column(DateTime(timezone=True), default=utc_now)
    is_deprecated = Column(Boolean, default=False)
    feedback_notes = Column(Text, nullable=True)

    @hybrid_property
    def times_used(self):
        return self.usage_count

    @times_used.setter
    def times_used(self, val):
        self.usage_count = val

    @hybrid_property
    def last_used_at(self):
        return self.last_used

    @last_used_at.setter
    def last_used_at(self, val):
        self.last_used = val


# ─────────────────────────────────────────────────────────────────────────────
# 3. DURABLE INFRASTRUCTURE STATE
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(Base):
    """
    Durable state of autonomous background discovery loops and workers.
    """
    __tablename__ = "agent_state"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(50), default="PAUSED")
    current_domain = Column(String(100), nullable=True)
    current_subdomain = Column(String(100), nullable=True)
    current_keyword = Column(Text, nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    state_data = Column(JSONB_TYPE, default=dict)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    current_batch_id = Column(String(36), nullable=True)
    last_active_at = Column(DateTime(timezone=True), default=utc_now)
    total_runs = Column(Integer, default=0)
    total_records_processed = Column(Integer, default=0)


class ArtifactOutbox(Base):
    """
    Durable Outbox Table for Raw Crawl Artifact Uploads to MinIO.
    Ensures raw HTML, markdown, screenshots, and brand assets survive worker/MinIO outages.
    """
    __tablename__ = "artifact_outbox"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String(255), nullable=True, index=True)
    object_name = Column(String(512), nullable=False, index=True)
    bucket_name = Column(String(100), default="opendb")
    content_type = Column(String(100), default="application/octet-stream")
    file_size_bytes = Column(BigInteger, default=0)
    sha256_hash = Column(String(64), nullable=True)
    local_staging_path = Column(Text, nullable=True)
    status = Column(String(50), default="PENDING", index=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=5)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), default=utc_now)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4. SAFETY & MODERATION ENTITIES
# ─────────────────────────────────────────────────────────────────────────────

class BlockedDomain(Base):
    """
    Explicitly blacklisted or rate-limited external domains.
    """
    __tablename__ = "blocked_domains"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String(255), unique=True, nullable=False, index=True)
    reason_category = Column(String(255), nullable=True)
    source = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    @hybrid_property
    def reason(self):
        return self.reason_category

    @reason.setter
    def reason(self, val):
        self.reason_category = val

    @hybrid_property
    def blocked_at(self):
        return self.created_at

    @blocked_at.setter
    def blocked_at(self, val):
        self.created_at = val


class ManualReviewQueue(Base):
    """
    Items flagged for manual verification or operator review.
    """
    __tablename__ = "manual_review_queue"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(Text, nullable=True)
    domain = Column(String(255), nullable=True)
    item_type = Column(String(50), nullable=True)
    content_snippet = Column(Text, nullable=True)
    score = Column(Numeric, nullable=True)
    status = Column(String(50), default="PENDING")
    created_at = Column(DateTime(timezone=True), default=utc_now)


class QuarantinedContent(Base):
    """
    Content flagged as toxic, malware, or policy-violating.
    """
    __tablename__ = "quarantined_content"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), nullable=True)
    url = Column(Text, nullable=False)
    domain = Column(String(255), nullable=True)
    content_type = Column(String(100), nullable=True)
    flagged_categories = Column(JSONB_TYPE, default=list)
    confidence_score = Column(Numeric, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


# ─────────────────────────────────────────────────────────────────────────────
# 5. BACKWARD COMPATIBILITY ALIASES (Seamless single-owner redirects)
# ─────────────────────────────────────────────────────────────────────────────

GlobalLead = Company
GlobalLeadPerson = KeyPerson
GlobalLeadSubpage = Document
UniversalRecord = Company
DomainRecord = Domain
KeyPersonCandidate = KeyPerson
Agent2PersonCandidate = KeyPerson
Agent2VerificationSession = VerificationSession
Agent2Evidence = CanonicalEvidence
Metadata = IndustryTaxonomy
Evidence = CanonicalEvidence
ExtractedFact = CanonicalEvidence
VerificationRecord = VerificationSession
CrawlJob = CrawlActivityLog
PostgresSyncOutbox = ArtifactOutbox
OpenLakeRecord = Company
Subdomain = Domain
DocumentVersion = Document
Resource = Document
ResourceLink = Document
ExtractionRun = CrawlActivityLog
SchemaDefinition = IndustryTaxonomy
