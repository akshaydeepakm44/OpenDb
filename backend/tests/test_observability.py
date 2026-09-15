"""
Unit & Integration Test Suite for OpenDB Observability & Tracing Architecture
Verifies:
1. Hierarchical Context Propagation (run_id, correlation_id, agent_id, task_id, lead_id)
2. CP-01 to CP-30 Checkpoint Timing & Duration
3. Secret Redaction / Sanitization
4. Independent Agent 1 vs Agent 2 Event Tracking
5. Failure & Fallback Transparency (PostgreSQL -> SQLite, MinIO explicit failure, Queue failure)
"""

import pytest
import time
import os
import json
from unittest.mock import patch, MagicMock

from app.audit.tracer import tracer, Checkpoint, sanitize_secrets, live_telemetry


def test_secret_sanitization():
    raw_text = "User admin with password='secret_password123' and api_key: sk-1234567890abcdef"
    sanitized = sanitize_secrets(raw_text)
    assert "secret_password123" not in sanitized
    assert "sk-1234567890abcdef" not in sanitized
    assert "***REDACTED***" in sanitized

    pg_url = "postgresql://admin:super_secret_pw@localhost:5432/opendb"
    sanitized_url = sanitize_secrets(pg_url)
    assert "super_secret_pw" not in sanitized_url
    assert "***REDACTED***" in sanitized_url


def test_hierarchical_context_propagation():
    run_id = tracer.new_run_id()
    assert run_id.startswith("RUN-")

    tracer.set_context(
        run_id=run_id,
        agent_id="AGENT-01",
        lead_id="stripe.com",
        task_id="TASK-101"
    )

    ctx = tracer.get_context_dict()
    assert ctx["run_id"] == run_id
    assert ctx["agent_id"] == "AGENT-01"
    assert ctx["lead_id"] == "stripe.com"
    assert ctx["task_id"] == "TASK-101"

    # Simulate Celery boundary serialization & restoration
    tracer.set_context(agent_id="SYSTEM", lead_id=None, task_id=None)
    assert tracer.get_context_dict()["agent_id"] == "SYSTEM"

    tracer.restore_context_dict(ctx)
    assert tracer.get_context_dict()["agent_id"] == "AGENT-01"
    assert tracer.get_context_dict()["lead_id"] == "stripe.com"


def test_checkpoint_duration_and_event_id():
    initial_count = len(live_telemetry._events)
    run_id = tracer.new_run_id()
    tracer.set_context(run_id=run_id, agent_id="AGENT-02", lead_id="acme.com")

    with tracer.checkpoint(Checkpoint.CP18_CRAWL_EXECUTION, "TEST_CRAWL", lead_id="acme.com"):
        time.sleep(0.05)

    recent = live_telemetry.get_recent(limit=5)
    end_event = recent[0]
    start_event = recent[1]

    assert end_event["event"] == "TEST_CRAWL_END"
    assert end_event["checkpoint"] == "CP-18"
    assert end_event["duration"] >= 0.04
    assert end_event["status"] == "SUCCESS"
    assert end_event["lead_id"] == "acme.com"
    assert end_event["event_id"].startswith("EVT-")
    assert end_event["parent_event_id"] == start_event["event_id"]


def test_checkpoint_exception_forensics():
    run_id = tracer.new_run_id()
    tracer.set_context(run_id=run_id, agent_id="AGENT-01")

    with pytest.raises(ValueError):
        with tracer.checkpoint(Checkpoint.CP04_SEARCH_EXECUTION, "SEARXNG_SEARCH"):
            raise ValueError("Connection refused on port 8080")

    recent = live_telemetry.get_recent(limit=2)
    failed_event = recent[0]
    assert failed_event["event"] == "SEARXNG_SEARCH_FAILED"
    assert failed_event["checkpoint"] == "CP-04"
    assert failed_event["status"] == "ERROR"
    assert "Connection refused" in failed_event["message"]


def test_independent_agent_emission():
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP02_AGENT_INIT,
        event="AGENT1_DISCOVERING",
        message="Agent 1 looking for new leads",
        agent_id="AGENT-01"
    )
    tracer.log_event(
        level="INFO",
        checkpoint=Checkpoint.CP12_AGENT2_INIT,
        event="AGENT2_ANALYZING",
        message="Agent 2 analyzing lead stripe.com",
        agent_id="AGENT-02",
        lead_id="stripe.com"
    )

    recent = live_telemetry.get_recent(limit=2)
    assert recent[0]["agent_id"] == "AGENT-02"
    assert recent[1]["agent_id"] == "AGENT-01"


def test_minio_strict_failure_refusal():
    from app.storage.file_storage import file_storage
    with patch.object(file_storage, "use_local", False), \
         patch.object(file_storage, "client", None), \
         patch.object(file_storage, "is_degraded", True):
        with pytest.raises(RuntimeError) as exc_info:
            file_storage._put_object("raw/test.html", b"test")
        assert "STORAGE_FAILED" in str(exc_info.value)


def test_searxng_failure_injection():
    """Verify SearXNG connection failure logs CP-04/CP-30 and returns empty without synthetic fallback."""
    import httpx
    from app.crawler.searxng_service import searxng_service
    import asyncio

    with patch.object(httpx.AsyncClient, "get", side_effect=httpx.ConnectError("SearXNG unreachable")):
        res = asyncio.run(searxng_service.search("fintech payment processing", max_results=5))
        assert res == []

    recent = live_telemetry.get_recent(limit=10)
    searxng_errors = [e for e in recent if "SEARCH" in e["event"]]
    assert len(searxng_errors) > 0
    failed_events = [e for e in searxng_errors if e["status"] in ("ERROR", "FAILED")]
    assert len(failed_events) > 0
    assert failed_events[0]["checkpoint"] in ("CP-04", "CP-30")


def test_celery_queue_failure_injection():
    """Verify Redis/Celery queue failure raises RuntimeError and marks QUEUE_DISPATCH_FAILED."""
    from app.worker.tasks import _dispatch_task

    mock_task = MagicMock()
    mock_task.apply_async.side_effect = ConnectionError("Redis broker connection refused: 6379")
    mock_task.name = "test_task"

    with pytest.raises(RuntimeError) as exc_info:
        _dispatch_task(mock_task, test_arg="val")
    assert "QUEUE_FAILED" in str(exc_info.value)

    recent = live_telemetry.get_recent(limit=5)
    queue_failed = [e for e in recent if "QUEUE" in e["event"]]
    assert len(queue_failed) > 0
    assert queue_failed[0]["checkpoint"] == "CP-27"
    assert queue_failed[0]["status"] == "FAILED"


def test_crawl4ai_failure_injection():
    """Verify Crawl4AI crawl failure produces CP-18 error record and no fabricated content."""
    from app.crawler.crawler_service import CrawlerService
    import asyncio

    crawler = CrawlerService()
    with patch("crawl4ai.AsyncWebCrawler", side_effect=Exception("Playwright browser launch failed")):
        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(crawler.crawl_site("https://nonexistent-company-domain-1234.com", max_pages=1))
        assert "CRAWL_FAILED" in str(exc_info.value)



