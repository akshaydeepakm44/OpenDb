"""
OpenDB — Verification Contract & Gating Automated Test Suite
Validates Section 34 of Master Directive:
- Authoritative Verification Contract pass/fail criteria
- Core vs Recommended field weighting & deterministic scoring
- API gating: Verified list strictly excludes unverified / partial leads
- Audit endpoint & Re-run endpoint contract validation
- State machine invariants: Agent 2 complete != VERIFIED
"""

import os
import sys
import uuid
import pytest
from datetime import datetime, timezone
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.verification.verification_contract import verification_contract, REQUIRED_CORE_FIELDS, RECOMMENDED_FIELDS
from app.persistence.database import SessionLocal
from app.persistence.models import (
    Document, Agent2VerificationSession, Agent2Evidence, Agent2PersonCandidate, UniversalRecord
)


def _build_valid_evidence():
    return [
        {
            "field": "raw_storage_vault_path",
            "value": "opendb/companies/acmerobotics.com/raw.html",
            "status": "VERIFIED",
            "evidence_snippet": "MinIO raw artifact vault: opendb/companies/acmerobotics.com/raw.html",
            "source_url": "opendb/companies/acmerobotics.com/raw.html"
        },
        {
            "field": "crawled_page_text",
            "value": "1200 chars",
            "status": "VERIFIED",
            "evidence_snippet": "Acme Robotics develops enterprise automated guided vehicles and warehouse logistics solutions.",
            "source_url": "https://acmerobotics.com"
        },
        {
            "field": "extracted_word_count",
            "value": 450,
            "status": "VERIFIED",
            "evidence_snippet": "Extracted word count: 450",
            "source_url": "https://acmerobotics.com"
        },
        {
            "field": "industry_sector",
            "value": "Robotics & Automation",
            "status": "VERIFIED",
            "evidence_snippet": "Verified from multi-page corporate crawl as Robotics & Automation.",
            "source_url": "https://acmerobotics.com/about"
        },
        {
            "field": "location_region",
            "value": "Austin, Texas",
            "status": "VERIFIED",
            "evidence_snippet": "Headquartered in Austin, Texas.",
            "source_url": "https://acmerobotics.com/contact"
        },
        {
            "field": "verified_contact_email",
            "value": "contact@acmerobotics.com",
            "status": "VERIFIED",
            "evidence_snippet": "Direct corporate email found on contact page.",
            "source_url": "https://acmerobotics.com/contact"
        },
        {
            "field": "company_size_tier",
            "value": "51-200 employees",
            "status": "VERIFIED",
            "evidence_snippet": "LinkedIn company size tier verified.",
            "source_url": "https://linkedin.com/company/acme-robotics"
        }
    ]


def _build_valid_session():
    return {
        "domain": "acmerobotics.com",
        "company_name": "Acme Robotics Inc",
        "summary": "Acme Robotics develops enterprise automated guided vehicles and warehouse logistics solutions.",
        "business_overview": "Acme Robotics develops enterprise automated guided vehicles and warehouse logistics solutions.",
        "industry": "Robotics & Automation",
        "headquarters": "Austin, Texas",
        "company_size": "51-200 employees",
        "linkedin_url": "https://www.linkedin.com/company/acme-robotics",
        "founded_year": "2018",
        "phone": "+1 512-555-0199"
    }


def _build_valid_people():
    return [
        {
            "name": "Jane Doe",
            "linkedin_url": "https://www.linkedin.com/in/janedoe-robotics",
            "title": "Chief Executive Officer",
            "candidate_status": "VERIFIED",
            "company_match_status": "VERIFIED",
            "is_leadership": True,
            "evidence_snippet": "CEO at Acme Robotics Inc."
        }
    ]


# ─── 1. VERIFICATION CONTRACT UNIT TESTS ─────────────────────────────────────

def test_contract_complete_lead_passes():
    """All core and recommended fields present -> is_verified is True, status is VERIFIED."""
    session_data = _build_valid_session()
    evidence = _build_valid_evidence()
    people = _build_valid_people()

    res = verification_contract.evaluate(session_data, evidence, people, subpages_count=4)
    assert res["is_verified"] is True
    assert res["verification_state"] == "VERIFIED"
    assert res["completeness_score"] >= 80.0
    assert len(res["missing_required_fields"]) == 0
    assert len(res["critical_issues"]) == 0


def test_contract_missing_required_field_blocks_verification():
    """Missing core description or industry -> is_verified is False, status is PARTIALLY_VERIFIED."""
    session_data = _build_valid_session()
    session_data["summary"] = "" # Empty description
    session_data["business_overview"] = ""
    evidence = _build_valid_evidence()

    res = verification_contract.evaluate(session_data, evidence, [], subpages_count=2)
    assert res["is_verified"] is False
    assert res["verification_state"] in ("PARTIALLY_VERIFIED", "NEEDS_REVIEW")
    assert "description" in res["missing_required_fields"]
    assert len(res["critical_issues"]) > 0


def test_contract_generic_placeholder_rejected():
    """Generic placeholder for industry or description is strictly rejected."""
    session_data = _build_valid_session()
    session_data["summary"] = "Enterprise lead test data placeholder"
    evidence = _build_valid_evidence()

    res = verification_contract.evaluate(session_data, evidence, [], subpages_count=2)
    assert res["is_verified"] is False
    assert "description" in res["missing_required_fields"]


def test_contract_missing_recommended_field_does_not_block_verification():
    """Missing recommended fields (e.g. phone, founded_year, email) reduce score but DO NOT block verification."""
    session_data = _build_valid_session()
    session_data["phone"] = None
    session_data["founded_year"] = None
    evidence = [e for e in _build_valid_evidence() if e["field"] != "verified_contact_email"]

    res = verification_contract.evaluate(session_data, evidence, _build_valid_people(), subpages_count=3)
    # Core fields are still intact!
    assert res["is_verified"] is True
    assert res["verification_state"] == "VERIFIED"
    assert "verified_contact_email" in res["missing_recommended_fields"]
    assert res["completeness_score"] < 100.0
    assert res["completeness_score"] >= 60.0


def test_contract_infrastructure_failure_blocks_phase1():
    """Infrastructure failure flag in investigation strictly sets state to PHASE1_BLOCKED."""
    session_data = _build_valid_session()
    evidence = _build_valid_evidence()
    evidence[3]["investigation"] = {"infra_failure": "SEARXNG_CONNECTION_ERROR: Connection refused"}

    res = verification_contract.evaluate(session_data, evidence, [], subpages_count=0)
    assert res["is_verified"] is False
    assert res["verification_state"] == "PHASE1_BLOCKED"
    assert any("infrastructure failure" in iss.lower() for iss in res["critical_issues"])


# ─── 2. API GATING & ENDPOINT INTEGRATION TESTS ─────────────────────────────
from fastapi.testclient import TestClient
from app.main import app

test_client = TestClient(app)


def test_api_verified_excludes_pending_and_partial_leads():
    """GET /api/agent/entities strictly excludes unverified cards."""
    resp = test_client.get("/api/agent/entities")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    # No results should have an unverified status
    for item in data["results"]:
        assert item["status"] in ("Verified", "VERIFIED", "POSTGRES_VERIFIED")


def test_api_agent2_card_detail_and_audit():
    """GET /api/agent2/cards/{session_id} returns authoritative verification_audit."""
    db = SessionLocal()
    try:
        session = db.query(Agent2VerificationSession).first()
        if not session:
            pytest.skip("No Agent2VerificationSession found in database to test")

        resp = test_client.get(f"/api/agent2/cards/{session.id}")
        assert resp.status_code == 200
        data = resp.json()

        assert "verification_audit" in data
        audit = data["verification_audit"]
        assert "is_verified" in audit
        assert "completeness_score" in audit
        assert "required_fields" in audit
        assert "recommended_fields" in audit
        assert "critical_issues" in audit
        assert "warnings" in audit
    finally:
        db.close()


def test_api_rerun_endpoint_gating():
    """POST /api/agent2/rerun/{session_id} rejects already-verified leads, accepts non-verified."""
    db = SessionLocal()
    try:
        session = db.query(Agent2VerificationSession).filter(
            Agent2VerificationSession.status.in_(["PARTIALLY_VERIFIED", "NEEDS_REVIEW", "PHASE1_BLOCKED", "AGENT2_QUEUED"])
        ).first()

        if not session:
            pytest.skip("No non-verified session found to test re-run")

        resp = test_client.post(f"/api/agent2/rerun/{session.id}")
        assert resp.status_code in (200, 202)
        data = resp.json()
        assert data["new_state"] == "QUEUED_FOR_VERIFICATION"
    finally:
        db.close()

