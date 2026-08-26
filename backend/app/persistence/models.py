import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, BigInteger, Numeric,
    DateTime, ForeignKey, JSON
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.persistence.database import Base

def utc_now():
    return datetime.now(timezone.utc)

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
    retrieved_at = Column(DateTime(timezone=True), default=utc_now)
    created_at = Column(DateTime(timezone=True), default=utc_now)

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

class UniversalRecord(Base):
    __tablename__ = "universal_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
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
    status = Column(String(50), nullable=True)
    confidence = Column(Numeric(5, 4), nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    document = relationship("Document", back_populates="universal_records")
    domain_records = relationship("DomainRecord", back_populates="universal_record")
    facts = relationship("ExtractedFact", back_populates="universal_record")
    domain = relationship("Domain")
    subdomain = relationship("Subdomain")

class DomainRecord(Base):
    __tablename__ = "domain_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    universal_record_id = Column(String(36), ForeignKey("universal_records.id", ondelete="CASCADE"), nullable=False)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="SET NULL"), nullable=True)
    schema_version = Column(String(50), nullable=False)
    data = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    universal_record = relationship("UniversalRecord", back_populates="domain_records")

class ExtractedFact(Base):
    __tablename__ = "extracted_facts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
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

    id = Column(Integer, primary_key=True, index=True)
    crawl_job_id = Column(String(36), ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    url = Column(Text, nullable=False)
    stage = Column(String(100), nullable=False)
    error_type = Column(String(100), nullable=False)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)

    crawl_job = relationship("CrawlJob", back_populates="errors")
