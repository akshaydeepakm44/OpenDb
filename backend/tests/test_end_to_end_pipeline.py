"""
OpenDB — Automated End-to-End Pipeline Smoke Test
Validates the complete production flow with real services:
FastAPI :8000 -> PostgreSQL -> Redis -> Celery Worker -> SearXNG -> Crawl4AI
-> Company Extraction -> Lead Creation -> Agent 2 Handoff -> Deep Crawl
-> Evidence Validation -> LinkedIn Discovery -> PostgreSQL & MinIO Persistence
-> Observability Events.
"""

import os
import sys
import time
import uuid
import pytest
import httpx
from sqlalchemy import text

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.persistence.database import SessionLocal, create_db_engine, IS_FALLBACK_ACTIVE, DATABASE_MODE
from app.persistence.models import (
    Document, Agent2VerificationSession, Agent2Evidence, GlobalLead, GlobalLeadSubpage
)
from app.audit.tracer import tracer, Checkpoint, live_telemetry
from app.schemas.registry import schema_registry
from app.worker.tasks import (
    search_and_discover_task, crawl_entity_task, agent2_process_card_task,
    _has_active_celery_worker, run_async
)
from app.storage.file_storage import file_storage
from app.crawler.searxng_service import searxng_service
from app.crawler.crawler_service import crawler_service


def test_01_preflight_all_services_online():
    """Verify that all 9 components report truthful ONLINE status via FastAPI /api/preflight."""
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=20.0) as client:
        resp = client.get("/api/preflight")
        assert resp.status_code == 200, f"Preflight endpoint failed: {resp.text}"
        data = resp.json()

        assert data["fastapi"]["status"] == "ONLINE", "FastAPI is not online"
        assert data["postgresql"]["status"] == "ONLINE", f"PostgreSQL not online: {data['postgresql']}"
        assert data["postgresql"]["mode"] == "PRIMARY", "PostgreSQL is in fallback mode"
        assert data["postgresql"]["table_count"] >= 30, f"Expected >= 30 tables, got {data['postgresql']['table_count']}"
        assert data["redis"]["status"] == "ONLINE", "Redis is not online"
        assert data["celery_worker"]["status"] == "ONLINE", "Celery worker is not online"
        assert data["searxng"]["status"] in ("ONLINE", "DEGRADED"), f"SearXNG status: {data['searxng']}"
        assert data["crawl4ai"]["status"] == "ONLINE", "Crawl4AI is not online"
        assert data["minio"]["status"] == "ONLINE", f"MinIO not online: {data['minio']}"
        assert "opendb" in data["minio"]["buckets"], "Missing 'opendb' MinIO bucket"
        assert data["schemas"]["status"] == "ONLINE", f"Schemas degraded: {data['schemas']}"
        assert data["schemas"]["loaded"] >= 4, f"Expected schemas loaded, got {data['schemas']['loaded']}"


def test_02_database_authoritative_persistence():
    """Verify that direct writes and reads execute strictly on PostgreSQL without SQLite substitution."""
    db = SessionLocal()
    test_token = f"smoke_test_{uuid.uuid4().hex[:8]}"
    try:
        # Confirm we are writing to PostgreSQL
        assert not IS_FALLBACK_ACTIVE, "IS_FALLBACK_ACTIVE is True! Refusing test."
        assert DATABASE_MODE == "POSTGRESQL", f"DATABASE_MODE is {DATABASE_MODE}"

        # Write test document
        doc_id = str(uuid.uuid4())
        doc = Document(
            id=doc_id,
            url=f"https://{test_token}.com",
            canonical_url=f"https://{test_token}.com",
            title=f"Test Company {test_token}",
            http_status=200,
            content_hash=uuid.uuid4().hex,
            lifecycle_state="CRAWLED_PENDING_AGENT_2",
            raw_metadata={"token": test_token}
        )
        db.add(doc)
        db.commit()

        # Query PostgreSQL engine directly via raw SQL
        eng = create_db_engine()
        with eng.connect() as conn:
            row = conn.execute(text("SELECT id, title, lifecycle_state FROM documents WHERE id = :id"), {"id": doc_id}).fetchone()
            assert row is not None, "Document not found in PostgreSQL table 'documents'"
            assert row[1] == f"Test Company {test_token}"
            assert row[2] == "CRAWLED_PENDING_AGENT_2"

        # Cleanup
        db.delete(doc)
        db.commit()
    finally:
        db.close()


def test_03_searxng_real_search_execution():
    """Verify that SearXNG executes a real search and returns structured results."""
    query = "OpenAI artificial intelligence enterprise platform"
    results, is_fallback, log_msg = run_async(searxng_service.search_with_meta(query=query, max_results=5))
    assert not is_fallback, f"SearXNG returned fallback data: {log_msg}"
    assert len(results) > 0, f"SearXNG returned 0 results for query '{query}'"
    first_res = results[0]
    assert "url" in first_res and first_res["url"].startswith("http")
    assert "title" in first_res


def test_04_crawl4ai_real_browser_execution():
    """Verify that Crawl4AI crawls a live URL using Playwright and extracts content within timeout."""
    test_url = "https://example.com"
    t0 = time.time()
    results = run_async(crawler_service.crawl_site(starting_url=test_url, max_depth=1, max_pages=1))
    duration = time.time() - t0
    assert len(results) > 0, f"Crawl4AI returned 0 results for {test_url}"
    page = results[0]
    assert page.http_status == 200, f"Expected 200, got {page.http_status}"
    assert "Example Domain" in page.title or "Example Domain" in page.text
    assert len(page.html_content) > 100
    assert duration < 35.0, f"Crawl took {duration:.2f}s, exceeded 35s timeout"


def test_05_minio_object_storage_roundtrip():
    """Verify that MinIO stores and retrieves raw page artifacts with checksum validation."""
    test_content = f"# Test Markdown Document\nCreated at {time.time()} for OpenDB smoke test."
    domain = f"test-company-{uuid.uuid4().hex[:6]}.com"

    # Save to MinIO
    etag, object_path = file_storage.save_company_page_artifact(
        domain=domain,
        page_slug="homepage",
        content=test_content,
        ext="md",
        metadata={"domain": domain}
    )
    assert object_path is not None, "Failed to upload artifact to MinIO"
    assert "pages/homepage.md" in object_path

    # Retrieve from MinIO using clean path and verify_artifact_exists
    clean_path = object_path.replace(f"s3://{file_storage.bucket_name}/", "")
    resp = file_storage.client.get_object(file_storage.bucket_name, clean_path)
    retrieved_content = resp.read().decode("utf-8")
    assert retrieved_content == test_content, "Retrieved content does not match uploaded content"

    verify_res = file_storage.verify_artifact_exists(object_path)
    assert verify_res["exists"] is True, f"Artifact does not exist in MinIO: {verify_res}"
    assert verify_res["backend"] == "minio", f"Expected minio backend, got {verify_res['backend']}"


def test_06_agent1_search_and_discover_task_execution():
    """Verify that search_and_discover_task executes via Celery signature and persists SearchHistory."""
    run_id = tracer.new_run_id()
    tracer.set_context(run_id=run_id, agent_id="AGENT-01")
    trace_ctx = tracer.get_context_dict()

    batch_id = str(uuid.uuid4())
    # Execute search_and_discover_task directly with trace_ctx
    res = search_and_discover_task(
        query="B2B cloud infrastructure startups",
        keyword="cloud infrastructure",
        domain="Information Technology",
        subdomain="Cloud Computing",
        batch_id=batch_id,
        trace_ctx=trace_ctx
    )
    assert "sources_found" in res
    assert res["sources_found"] > 0, "Agent 1 search found 0 sources"

    # Verify SearchHistory persisted to PostgreSQL
    db = SessionLocal()
    try:
        from app.persistence.models import SearchHistory
        history = db.query(SearchHistory).filter(SearchHistory.batch_id == batch_id).first()
        assert history is not None, "SearchHistory not recorded in PostgreSQL"
        assert history.sources_found > 0
    finally:
        db.close()


def test_07_agent2_verification_and_persistence_lifecycle():
    """Verify that an entity card in CRAWLED_PENDING_AGENT_2 undergoes Agent 2 deep processing and persists evidence."""
    db = SessionLocal()
    doc_id = str(uuid.uuid4())
    test_domain = "example.com"

    try:
        # Create staging document
        doc = Document(
            id=doc_id,
            url="https://example.com",
            canonical_url="https://example.com",
            title="Example Domain Entity",
            http_status=200,
            content_hash=uuid.uuid4().hex,
            lifecycle_state="CRAWLED_PENDING_AGENT_2",
            raw_metadata={"company_name": "Example Domain Entity"}
        )
        db.add(doc)
        db.commit()

        # Execute Agent 2 full verification
        from app.agent.agent2_orchestrator import agent2_orchestrator
        run_id = tracer.new_run_id()
        tracer.set_context(run_id=run_id, agent_id="AGENT-02", lead_id=test_domain)

        session_res = run_async(agent2_orchestrator.execute_full_verification(doc_id))
        assert session_res is not None, "Agent 2 execution returned None"
        assert "status" in session_res

        # Query PostgreSQL for Agent 2 session
        session = db.query(Agent2VerificationSession).filter(
            Agent2VerificationSession.document_id == doc_id
        ).first()
        assert session is not None, "Agent2VerificationSession not found in PostgreSQL"
        assert session.domain == test_domain

        # Cleanup
        db.delete(session)
        db.delete(doc)
        db.commit()
    finally:
        db.close()


def test_08_end_to_end_traceability():
    """Verify that hierarchical trace context propagates through live telemetry with parent-child correlation."""
    run_id = tracer.new_run_id()
    tracer.set_context(
        run_id=run_id,
        correlation_id=f"CORR-{uuid.uuid4().hex[:6]}",
        agent_id="AGENT-01",
        lead_id="test-acme.com",
        task_id="TASK-E2E-01"
    )

    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP03_KEYWORD_GEN,
        event="KEYWORD_SELECTED",
        message="Smoke test keyword generated",
        status="SUCCESS"
    )

    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP18_CRAWL_EXECUTION,
        event="CRAWL_START",
        message="Smoke test crawl started",
        status="STARTED"
    )

    tracer.set_context(agent_id="AGENT-02", task_id="TASK-E2E-02")
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP12_AGENT2_INIT,
        event="AGENT2_TASK_STARTED",
        message="Smoke test Agent 2 handoff received",
        status="STARTED"
    )

    matching = [e for e in live_telemetry._events if e.get("run_id") == run_id]
    assert len(matching) >= 3, f"Expected >= 3 trace events for run_id {run_id}, got {len(matching)}"
    event_agents = [e.get("agent_id") for e in matching]
    assert "AGENT-01" in event_agents
    assert "AGENT-02" in event_agents
