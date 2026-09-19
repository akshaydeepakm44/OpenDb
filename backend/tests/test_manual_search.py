"""
Tests for Manual Company Search API — backend/tests/test_manual_search.py

Validates that:
1. Manual search endpoints conform strictly to the OpenDB architecture guardrails.
2. No agents, state machines, or celery task signatures are bypassed or duplicated.
3. Safety checks (private IP, heuristics, blocklist) protect both resolution and investigation.
4. Existing PostgreSQL models (Company, VerificationSession, CanonicalEvidence, KeyPerson) remain authoritative.
5. Cache checks correctly detect fresh vs stale sessions using COMPANY_INTELLIGENCE_TTL_DAYS.
"""

import datetime
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.persistence.database import get_db
from app.persistence.models import Company, VerificationSession, CanonicalEvidence, KeyPerson
from app.api.manual_search import (
    _normalize_company_name,
    _is_private_ip_domain,
    _is_session_fresh,
    _build_candidate_from_company,
    _build_candidate_from_url,
    _deduplicate_candidates,
)


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ============================================================================
# 1. Normalization and Safety Unit Tests
# ============================================================================

def test_normalize_company_name():
    assert _normalize_company_name("Acme Corp.") == "acme"
    assert _normalize_company_name("Stripe, Inc.") == "stripe"
    assert _normalize_company_name("OpenAI LLC") == "openai"
    assert _normalize_company_name("Google UK Limited") == "google uk"


def test_is_private_ip_domain():
    assert _is_private_ip_domain("127.0.0.1") is True
    assert _is_private_ip_domain("localhost") is True
    assert _is_private_ip_domain("192.168.1.1") is True
    assert _is_private_ip_domain("10.0.0.5") is True
    assert _is_private_ip_domain("172.16.0.1") is True
    assert _is_private_ip_domain("stripe.com") is False
    assert _is_private_ip_domain("example.org") is False


def test_build_candidate_from_url_filters_private():
    res = _build_candidate_from_url("http://127.0.0.1/company", "Local", "")
    assert res is None

    res_valid = _build_candidate_from_url("https://stripe.com/about", "Stripe: Financial Infrastructure", "Payments")
    assert res_valid is not None
    assert res_valid["domain"] == "stripe.com"
    assert res_valid["source"] == "searxng"


def test_deduplicate_candidates():
    c1 = {"domain": "stripe.com", "source": "searxng", "canonical_name": "Stripe"}
    c2 = {"domain": "www.stripe.com", "source": "searxng", "canonical_name": "Stripe Payments"}
    c3 = {"domain": "stripe.com", "source": "postgres", "canonical_name": "Stripe Inc"}

    deduped = _deduplicate_candidates([c1, c2, c3])
    assert len(deduped) == 1
    # postgres candidate should take priority
    assert deduped[0]["source"] == "postgres"


# ============================================================================
# 2. Freshness and TTL Logic Tests
# ============================================================================

def test_is_session_fresh_terminal_and_within_ttl():
    sess = MagicMock()
    sess.status = "VERIFIED"
    sess.updated_at = datetime.datetime.utcnow() - datetime.timedelta(days=2)
    sess.verified_at = sess.updated_at
    assert _is_session_fresh(sess) is True


def test_is_session_stale_when_exceeding_ttl():
    sess = MagicMock()
    sess.status = "VERIFIED"
    sess.updated_at = datetime.datetime.utcnow() - datetime.timedelta(days=10)
    sess.verified_at = sess.updated_at
    assert _is_session_fresh(sess) is False


def test_is_session_not_fresh_if_not_verified():
    sess = MagicMock()
    sess.status = "PHASE1_VERIFYING"
    sess.updated_at = datetime.datetime.utcnow()
    assert _is_session_fresh(sess) is False


# ============================================================================
# 3. /resolve Endpoint Integration Tests
# ============================================================================

def test_resolve_company_name_validation(client):
    res = client.post("/api/manual-search/resolve", json={"company_name": " "})
    assert res.status_code == 422  # validation error


def test_resolve_existing_postgres_match(client, mock_db):
    mock_comp = MagicMock()
    mock_comp.id = "comp-123"
    mock_comp.canonical_name = "Acme Corp"
    mock_comp.primary_domain = "acme.com"
    mock_comp.status = "ACTIVE"

    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = [mock_comp]

    res = client.post("/api/manual-search/resolve", json={"company_name": "Acme"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "EXISTING"
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["domain"] == "acme.com"


# ============================================================================
# 4. /investigate Endpoint Integration Tests
# ============================================================================

def test_investigate_private_ip_rejection(client):
    res = client.post("/api/manual-search/investigate", json={"company_name": "Evil", "domain": "127.0.0.1"})
    assert res.status_code == 400
    assert "private IP" in res.json()["detail"]


def test_investigate_blocked_domain_rejection(client, mock_db):
    with patch("app.api.manual_search.is_domain_blocked", return_value=True):
        res = client.post("/api/manual-search/investigate", json={"company_name": "Bad", "domain": "malicious.com"})
        assert res.status_code == 400
        assert "blocklist" in res.json()["detail"]


def test_investigate_cache_hit_returns_cached(client, mock_db):
    mock_comp = MagicMock()
    mock_comp.id = "comp-stripe"
    mock_comp.canonical_name = "Stripe"
    mock_comp.primary_domain = "stripe.com"

    mock_sess = MagicMock()
    mock_sess.id = "sess-stripe"
    mock_sess.status = "VERIFIED"
    mock_sess.updated_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
    mock_sess.verified_at = mock_sess.updated_at

    def query_side_effect(model):
        q = MagicMock()
        if model == Company:
            q.filter.return_value.first.return_value = mock_comp
        elif model == VerificationSession:
            q.filter.return_value.order_by.return_value.first.return_value = mock_sess
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_side_effect

    with patch("app.api.manual_search.is_domain_blocked", return_value=False):
        res = client.post("/api/manual-search/investigate", json={"company_name": "Stripe", "domain": "stripe.com"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "CACHE_HIT"
        assert data["session_id"] == "sess-stripe"


def test_investigate_already_running(client, mock_db):
    mock_comp = MagicMock()
    mock_comp.id = "comp-stripe"
    mock_comp.canonical_name = "Stripe"
    mock_comp.primary_domain = "stripe.com"

    mock_sess = MagicMock()
    mock_sess.id = "sess-stripe"
    mock_sess.status = "PHASE1_VERIFYING"
    mock_sess.updated_at = datetime.datetime.now(datetime.timezone.utc)
    mock_sess.verified_at = None

    def query_side_effect(model):
        q = MagicMock()
        if model == Company:
            q.filter.return_value.first.return_value = mock_comp
        elif model == VerificationSession:
            q.filter.return_value.order_by.return_value.first.return_value = mock_sess
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_side_effect

    with patch("app.api.manual_search.is_domain_blocked", return_value=False):
        res = client.post("/api/manual-search/investigate", json={"company_name": "Stripe", "domain": "stripe.com"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ALREADY_RUNNING"
        assert data["session_id"] == "sess-stripe"


def test_investigate_dispatches_existing_agent1_pipeline(client, mock_db):
    mock_db.query.return_value.filter.return_value.first.return_value = None

    with patch("app.worker.tasks._safe_dispatch") as mock_dispatch:
        res = client.post("/api/manual-search/investigate", json={"company_name": "NewCo", "domain": "newco.io"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "AGENT1_QUEUED"
        assert data["domain"] == "newco.io"
        assert mock_dispatch.called
        # Verify it dispatched the existing crawl_entity_task
        from app.worker.tasks import crawl_entity_task
        args, kwargs = mock_dispatch.call_args
        assert args[0] == crawl_entity_task
        assert kwargs["domain"] == "newco.io"
        assert kwargs["url"] == "https://newco.io/"


# ============================================================================
# 5. /status and /result Read-Only Proxy Tests
# ============================================================================

def test_status_endpoint_returns_session_data(client, mock_db):
    mock_sess = MagicMock()
    mock_sess.id = "sess-1"
    mock_sess.status = "VERIFIED"
    mock_sess.domain = "acme.com"
    mock_sess.company_name = "Acme"
    mock_sess.company_id = "comp-1"
    mock_sess.created_at = datetime.datetime.utcnow()
    mock_sess.updated_at = datetime.datetime.utcnow()
    mock_sess.verified_at = datetime.datetime.utcnow()
    mock_sess.phase2_data = {"location_region": "San Francisco, CA"}
    mock_sess.error_message = None

    mock_comp = MagicMock()
    mock_comp.canonical_name = "Acme"
    mock_comp.primary_domain = "acme.com"
    mock_comp.headquarters = "SF"
    mock_comp.industry = "Software"
    mock_comp.employee_range = "50-100"
    mock_comp.linkedin_url = "https://linkedin.com/company/acme"
    mock_comp.verified_emails = ["info@acme.com"]
    mock_comp.description = "B2B Software"

    def query_side_effect(model):
        q = MagicMock()
        if model == VerificationSession:
            q.filter.return_value.first.return_value = mock_sess
        elif model == Company:
            q.filter.return_value.first.return_value = mock_comp
        elif model in (CanonicalEvidence, KeyPerson):
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_side_effect

    res = client.get("/api/manual-search/status/sess-1")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == "sess-1"
    assert data["status"] == "VERIFIED"
    assert data["company"]["canonical_name"] == "Acme"
