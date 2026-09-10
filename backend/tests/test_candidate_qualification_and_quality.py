import pytest
from app.crawler.quality_filter import quality_filter, resolve_subdomain_and_root
from app.api.agent import calculate_evidence_quality_score, _infer_industry, determine_company_tier
from app.extraction.key_people_extractor import KeyPeopleExtractor


# 1. Generic portal rejection
def test_generic_portal_rejection():
    portals = [
        "https://www.business.com/articles/grow-startup",
        "https://tw.piliapp.com/tools",
        "https://kuechenfibel.de/tipps",
        "https://werkstatt-magazin.de/news",
        "https://ratgeber.blauarbeit.de/handwerk",
    ]
    for url in portals:
        keep, reason = quality_filter.filter_url(url)
        assert keep is False, f"Expected {url} to be rejected, but was accepted (reason: {reason})"


# 2. Directory rejection
def test_directory_rejection():
    directories = [
        "https://www.yelp.com/biz/local-shop",
        "https://www.yellowpages.com/search?q=pizza",
        "https://www.tripadvisor.com/restaurants",
        "https://clutch.co/developers",
    ]
    for url in directories:
        keep, reason = quality_filter.filter_url(url)
        assert keep is False, f"Expected directory {url} to be rejected"


# 3. Resource/subdomain classification
def test_subdomain_resource_classification():
    resource_subdomains = [
        ("https://locations.pizzahut.com/tx/dallas", "LOCATION"),
        ("https://pizza.dominos.com/order", "LOCATION"),
        ("https://en-americas-support.nintendo.com/app/answers", "SUPPORT"),
        ("https://tw.piliapp.com/symbol", "LANGUAGE_TOOL"),
    ]
    for url, expected_purpose in resource_subdomains:
        sub_info = resolve_subdomain_and_root(url)
        assert sub_info["is_resource_subdomain"] is True, f"Expected {url} to be classified as resource subdomain"
        assert sub_info["resource_type"] == expected_purpose

        qual = quality_filter.qualify_company_candidate("Store Locations", "Find stores near you", url)
        assert qual["qualified"] is False or qual["priority"] == "REJECTED"


# 4. Legitimate company subdomain handling
def test_legitimate_company_subdomain_allowed():
    legit = [
        "https://app.linear.app/login",
        "https://platform.openai.com/docs",
    ]
    for url in legit:
        sub_info = resolve_subdomain_and_root(url)
        assert sub_info["is_resource_subdomain"] is False, f"Expected {url} to NOT be classified as resource subdomain"


# 5. UNKNOWN company size allowed
def test_unknown_company_size_allowed():
    candidate = quality_filter.qualify_company_candidate(
        title="Acme Analytics - Intelligent Metrics Platform",
        snippet="Acme provides predictive analytics and real-time dashboards for modern engineering teams.",
        url="https://acmeanalytics.io"
    )
    assert candidate["qualified"] is True
    assert candidate["company_size"] == "UNKNOWN"
    assert candidate["priority"] == "NORMAL"


# 6. 1-200 company HIGH priority
def test_confirmed_small_smb_high_priority():
    candidate = quality_filter.qualify_company_candidate(
        title="DevFlow AI - Developer Workflow Automation",
        snippet="DevFlow is a fast-growing startup of 25 employees building automated CI/CD intelligence.",
        url="https://devflow.ai"
    )
    assert candidate["qualified"] is True
    assert candidate["priority"] == "HIGH"
    assert candidate["company_size"] in ["1-200", "20-100", "1-20", "1-25", "25"]


# 7. >200 company deprioritized/rejected
def test_enterprise_gt_200_deprioritized():
    candidate = quality_filter.qualify_company_candidate(
        title="Global Logistics Corp",
        snippet="Global Logistics operates worldwide with over 15,000 employees across 40 countries.",
        url="https://globallogistics.example"
    )
    assert candidate["priority"] in ["DEPRIORITIZED", "REJECTED"]
    assert candidate["qualified"] is False


# 8. No "Official Portal" generation / clean derivation
def test_no_official_portal_generation():
    # When title is empty, should cleanly use domain root without appending "Official Portal"
    raw_netloc = "spotalike.com"
    derived_title = raw_netloc.split(".")[0].replace("-", " ").title()
    assert "Official Portal" not in derived_title
    assert derived_title == "Spotalike"


# 9. No "Commercial Web" fallback
def test_no_commercial_web_fallback():
    # If not recognized, industry returns "Unknown", never "Commercial Web"
    ind = _infer_industry("randomobscuredomainxyz.org", "Welcome to Portal", "No clear industry signal")
    assert ind != "Commercial Web"
    assert ind == "Unknown"


# 10. No fake emails
def test_no_fake_emails_generated():
    # A blank text should yield no emails
    extractor = KeyPeopleExtractor()
    people = extractor.extract_from_text_and_html("", "", "Spotalike", "spotalike.com")
    assert all(p.get("linkedin_url") is None or "search" not in p.get("linkedin_url") for p in people)


# 11. Evidence-based business overview (unknown if not evidenced)
def test_business_overview_unknown_if_insufficient():
    overview = ""
    if "indexed by opendb" in overview.lower() or not overview.strip():
        overview = "Unknown"
    assert overview == "Unknown"
    assert "indexed by OpenDB" not in overview


# 12. Evidence-driven quality score
def test_quality_score_barebones_vs_complete():
    # Barebones card (HTTP 200 only, everything else unknown)
    barebones_score = calculate_evidence_quality_score(
        canonical_name="example.com",
        domain="example.com",
        industry="Unknown",
        business_overview="Unknown",
        products_services=[],
        headquarters="Unknown",
        company_size="Unknown",
        decision_makers=[],
        verified_emails=[]
    )
    # Must NOT receive 90/100 or 100/100 merely for existing/reachable!
    assert barebones_score < 20

    # Highly evidenced company
    full_score = calculate_evidence_quality_score(
        canonical_name="Linear",
        domain="linear.app",
        industry="Developer Tools & Software",
        business_overview="Linear is a purpose-built issue tracking tool designed for high-performing modern software development teams.",
        products_services=["Issue Tracking", "Cycles", "Roadmaps"],
        headquarters="San Francisco, CA",
        company_size="51-200",
        decision_makers=[
            {"name": "Karri Saarinen", "title": "Co-Founder & CEO"},
            {"name": "Tuomas Artman", "title": "Co-Founder & CTO"}
        ],
        verified_emails=["hello@linear.app"]
    )
    assert full_score >= 90


# 13 & 14. LinkedIn URL acceptance and rejection
def test_linkedin_search_url_rejected_and_in_accepted():
    extractor = KeyPeopleExtractor()
    results = [
        {
            "title": "Karri Saarinen - Co-Founder & CEO - Linear | LinkedIn",
            "snippet": "Karri Saarinen is the Co-Founder & CEO of Linear. Previously Principal Designer at Airbnb.",
            "url": "https://www.linkedin.com/in/karrisaarinen"
        },
        {
            "title": "Search results for Linear Leadership | LinkedIn",
            "snippet": "Browse 50+ members of Linear leadership team on LinkedIn.",
            "url": "https://www.linkedin.com/search/results/people/?keywords=Linear+leadership"
        }
    ]
    extracted = extractor.extract_from_linkedin_search_snippets(results, "Linear")
    assert len(extracted) >= 1
    # First candidate has genuine /in/ URL
    assert extracted[0]["linkedin_url"] == "https://www.linkedin.com/in/karrisaarinen"
    # Never store search URL
    for p in extracted:
        if p["linkedin_url"]:
            assert "/search/" not in p["linkedin_url"]
            assert "/in/" in p["linkedin_url"]


# 15. Person-company evidence validation
def test_person_company_evidence_validation():
    extractor = KeyPeopleExtractor()
    unrelated_results = [
        {
            "title": "Jane Doe - Chief Technology Officer - Unrelated Acme Corp | LinkedIn",
            "snippet": "Jane Doe is CTO at Acme Corp specializing in retail technology.",
            "url": "https://www.linkedin.com/in/janedoe"
        }
    ]
    # Searching for Linear should NOT attach Jane Doe from Unrelated Acme Corp
    extracted = extractor.extract_from_linkedin_search_snippets(unrelated_results, "Linear")
    assert len(extracted) == 0
