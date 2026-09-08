import sys
import os

# Set stdout encoding to utf-8 if possible
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.extraction.placeholder_guard import placeholder_guard
from app.extraction.key_people_extractor import key_people_extractor
from app.crawler.anti_hallucination_pipeline import anti_hallucination_pipeline

def test_scoped_placeholder_guard():
    print("\n--- 1. Testing Scoped PlaceholderGuard ---")
    
    # Generic free-text fields SHOULD be nulled when containing placeholders
    assert placeholder_guard.clean_value("N/A", "business_overview.text") is None
    assert placeholder_guard.clean_value("TBD", "firmographics.revenue_funding.value") is None
    assert placeholder_guard.clean_value("Contact us for pricing", "firmographics.revenue_funding.value") is None
    assert placeholder_guard.clean_value("Unknown", "company_name") is None
    print("  [PASS] Generic string placeholders correctly filtered to None")

    # Controlled vocabulary fields MUST NOT be nulled out for 'Unknown'
    assert placeholder_guard.clean_value("Unknown", "role_tag") == "Unknown"
    assert placeholder_guard.clean_value("Unknown", "role_tags") == "Unknown"
    assert placeholder_guard.clean_value("Unknown", "source_type") == "Unknown"
    assert placeholder_guard.clean_value("Unknown", "status") == "Unknown"
    print("  [PASS] Controlled vocabulary enum terms ('Unknown' role_tag) preserved")


def test_extractive_business_overview():
    print("\n--- 2. Testing Extractive Business Overview Verification ---")
    
    sample_text = """
    Acme Analytics is a provider of cloud-native data observability for enterprise software.
    We deliver real-time data monitoring and pipeline quality checks across hybrid multi-cloud systems.
    Contact us for pricing details.
    """
    crawled_pages = [
        {
            "url": "https://acme.com",
            "text": sample_text,
            "html": f"<p>{sample_text}</p>"
        }
    ]

    overview = anti_hallucination_pipeline.synthesize_business_overview(crawled_pages)
    text = overview.get("text")
    source_pages = overview.get("source_pages")

    assert text is not None, "Extractive overview should be extracted"
    assert "Acme Analytics is a provider" in text, f"Unexpected overview: {text}"
    assert "https://acme.com" in source_pages
    
    # Test fuzzy matching pass threshold >= 0.85
    sentence = "Acme Analytics is a provider of cloud-native data observability for enterprise software."
    assert anti_hallucination_pipeline.verify_sentence_in_text(sentence, sample_text, threshold=0.85)
    print(f"  [PASS] Business Overview extracted sentence-by-sentence: '{text[:60]}...'")
    print(f"  [PASS] Verified fuzzy sentence match >= 0.85 against raw text")


def test_compound_role_tags_and_linkedin():
    print("\n--- 3. Testing Compound Role Tags & LinkedIn Search URL ---")
    
    # 'Director' -> ["Contact Person", "Economic Buyer"]
    tags_director = key_people_extractor.derive_role_tags("Director")
    assert "Contact Person" in tags_director and "Economic Buyer" in tags_director, f"Got: {tags_director}"
    
    # 'Managing Director' -> ["Economic Buyer"]
    tags_md = key_people_extractor.derive_role_tags("Managing Director")
    assert "Economic Buyer" in tags_md, f"Got: {tags_md}"
    
    # Unmatched title -> ["Unknown"]
    tags_unknown = key_people_extractor.derive_role_tags("Quantum Wizard")
    assert tags_unknown == ["Unknown"], f"Got: {tags_unknown}"

    # LinkedIn Public Search URL Construction
    person = key_people_extractor.build_person_record(
        name="Jane Doe",
        title="Director of Engineering",
        company_name="Acme Corp",
        source_url="https://acme.com/team"
    )
    assert person["name"] == "Jane Doe"
    assert "Contact Person" in person["role_tags"]
    assert "Economic Buyer" in person["role_tags"] or "Technical Buyer" in person["role_tags"]
    assert person["linkedin_search_url"] == "https://www.linkedin.com/search/results/all/?keywords=Jane%20Doe%20Acme%20Corp"
    print(f"  [PASS] Compound role_tags for Director: {person['role_tags']}")
    print(f"  [PASS] Generated LinkedIn search URL: {person['linkedin_search_url']}")


def test_exact_warmth_score_formula():
    print("\n--- 4. Testing Exact Math-Formula Warmth Score ---")
    
    verified_emails = [{"email": "sales@acme.com", "status": "verified", "source_url": "https://acme.com"}]
    decision_makers = [
        {"name": "Jane Doe", "title": "CTO", "confidence": "high"},
        {"name": "John Smith", "title": "VP Sales", "confidence": "medium"}
    ]
    firmographics = {
        "headquarters": {"value": "San Francisco, CA"},
        "industry": {"value": "Software"}
    }
    subpages = [{"path": "/", "title": "Home"}]
    
    score_obj = anti_hallucination_pipeline.calculate_warmth_score(
        verified_emails=verified_emails,
        decision_makers=decision_makers,
        firmographics=firmographics,
        subpages=subpages,
        crawl_finished_at="2026-09-07T12:00:00Z"
    )

    # Email: 3.0, Leadership: 2 * 0.75 = 1.5, Firmographics: 2 * 0.5 = 1.0, Vault & Recency: 1.0 + 1.0 = 2.0
    # Expected Total = 3.0 + 1.5 + 1.0 + 2.0 = 7.5
    assert score_obj["value"] == 7.5, f"Expected 7.5, got {score_obj['value']}"
    assert "Formula: Emails (3.0)" in score_obj["explanation"]
    print(f"  [PASS] Calculated Warmth Score: {score_obj['value']}/10.0")
    print(f"  [PASS] Score Explanation: {score_obj['explanation']}")


def test_11_section_standard_schema():
    print("\n--- 5. Testing 11-Section Standard JSON Schema Output ---")
    
    record = anti_hallucination_pipeline.build_standard_company_record(
        company_name="Acme Corp",
        domain="acme.com",
        official_url="https://acme.com",
        logo_url="https://acme.com/logo.png",
        crawled_pages=[{
            "url": "https://acme.com",
            "text": "Acme Corp provides automated cloud infrastructure solutions. Contact sales@acme.com for inquiries.",
            "html": "<p>Acme Corp provides automated cloud infrastructure solutions. Contact sales@acme.com for inquiries.</p>",
            "title": "Acme Corp Homepage",
            "minio_raw_path": "companies/acme.com/pages/homepage.md"
        }]
    )

    required_keys = [
        "company_name", "logo_url", "website", "business_overview",
        "technology_stack", "decision_makers", "crawled_subpages",
        "firmographics", "warmth_score", "verified_emails", "extraction_audit"
    ]

    for section in required_keys:
        assert section in record, f"Missing required 11-section key: {section}"

    assert record["company_name"] == "Acme Corp"
    assert record["website"]["domain"] == "acme.com"
    assert len(record["verified_emails"]) == 1
    assert record["verified_emails"][0]["email"] == "sales@acme.com"
    print(f"  [PASS] Verified all 11 required top-level schema keys present")
    print(f"  [PASS] Verified Emails: {record['verified_emails']}")


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING ANTI-HALLUCINATION & PROVENANCE SPECIFICATION TESTS")
    print("=" * 60)
    test_scoped_placeholder_guard()
    test_extractive_business_overview()
    test_compound_role_tags_and_linkedin()
    test_exact_warmth_score_formula()
    test_11_section_standard_schema()
    print("=" * 60)
    print("ALL ANTI-HALLUCINATION ARCHITECTURAL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
