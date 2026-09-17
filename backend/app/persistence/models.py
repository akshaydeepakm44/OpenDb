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

    @property
    def people(self):
        return self.key_people

    @property
    def subpages(self):
        return self.documents


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
    url = Column(Text, unique=True, nullable=False)
    domain = Column(String(255), nullable=False, index=True)
    discovery_query = Column(Text, nullable=True)
    search_engine = Column(String(50), default="searxng")
    rank = Column(Integer, nullable=True)
    discovered_at = Column(DateTime(timezone=True), default=utc_now)
    status = Column(String(50), default="discovered")

    documents = relationship("Document", back_populates="source")


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
    batch_id = Column(String(36), nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now, index=True)
    stage = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)
    url = Column(Text, nullable=True)
    domain = Column(String(255), nullable=True, index=True)
    message = Column(Text, nullable=True)
    extra_metadata = Column(JSONB_TYPE, default=dict)


class CrawlError(Base):
    """
    Operational crawl failure records.
    """
    __tablename__ = "crawl_errors"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    crawl_job_id = Column(String(36), nullable=True)
    url = Column(Text, nullable=False)
    error_type = Column(String(100), nullable=False)
    error_message = Column(Text, nullable=True)
    status_code = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class BatchResult(Base):
    """
    Operational batch crawl summary and performance statistics.
    """
    __tablename__ = "batch_results"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    started_at = Column(DateTime(timezone=True), default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    queries_run = Column(Integer, default=0)
    sources_discovered = Column(Integer, default=0)
    domains_processed = Column(Integer, default=0)
    records_created = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    metrics = Column(JSONB_TYPE, default=dict)


class SearchHistory(Base):
    """
    Operational record of discovery search queries run against SearXNG.
    """
    __tablename__ = "search_history"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query = Column(Text, nullable=False)
    search_engine = Column(String(50), default="searxng")
    searched_at = Column(DateTime(timezone=True), default=utc_now)
    sources_found = Column(Integer, default=0)
    sources_new = Column(Integer, default=0)
    batch_id = Column(String(36), nullable=True, index=True)


class KeywordPerformance(Base):
    """
    Operational search keyword efficiency telemetry.
    """
    __tablename__ = "keyword_performance"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    keyword = Column(String(255), unique=True, nullable=False)
    times_used = Column(Integer, default=0)
    results_found = Column(Integer, default=0)
    companies_identified = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    last_used_at = Column(DateTime(timezone=True), default=utc_now)


# ─────────────────────────────────────────────────────────────────────────────
# 3. DURABLE INFRASTRUCTURE STATE
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(Base):
    """
    Durable state of autonomous background discovery loops and workers.
    """
    __tablename__ = "agent_state"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default="default")
    status = Column(String(50), default="idle")
    current_batch_id = Column(String(36), nullable=True)
    last_active_at = Column(DateTime(timezone=True), default=utc_now)
    total_runs = Column(Integer, default=0)
    total_records_processed = Column(Integer, default=0)
    state_data = Column(JSONB_TYPE, default=dict)


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

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(255), unique=True, nullable=False, index=True)
    reason = Column(String(255), nullable=False)
    blocked_at = Column(DateTime(timezone=True), default=utc_now)


class ManualReviewQueue(Base):
    """
    Items flagged for manual verification or operator review.
    """
    __tablename__ = "manual_review_queue"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(36), nullable=False)
    reason = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution = Column(String(50), nullable=True)


class QuarantinedContent(Base):
    """
    Content flagged as toxic, malware, or policy-violating.
    """
    __tablename__ = "quarantined_content"
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(Text, nullable=False)
    reason = Column(String(255), nullable=False)
    content_hash = Column(String(64), nullable=True)
    quarantined_at = Column(DateTime(timezone=True), default=utc_now)


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
