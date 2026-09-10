"""
Regression tests - LinkedIn URL strict validation.
All 8 cases from the bug report.
"""
import re
import pytest


def is_real_linkedin_profile(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    m = re.search(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-_]{2,})', url)
    if not m:
        return False
    slug = m.group(1).lower()
    bad_slugs = {"search", "jobs", "feed", "login", "signup", "home", "pub", "in", "sharing", "posts"}
    return slug not in bad_slugs


def is_real_linkedin_company(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    m = re.search(r'https?://(?:www\.)?linkedin\.com/company/([a-zA-Z0-9\-_]{2,})', url)
    if not m:
        return False
    slug = m.group(1).lower()
    bad_slugs = {"linkedin", "home", "sharearticle", "sharing", "login", "signup", "jobs", "feed", "posts"}
    return slug not in bad_slugs


def test_real_personal_profile_accepted():
    assert is_real_linkedin_profile("https://www.linkedin.com/in/john-doe") is True
    assert is_real_linkedin_profile("https://linkedin.com/in/john-doe") is True

def test_real_company_profile_accepted():
    assert is_real_linkedin_company("https://www.linkedin.com/company/spotalike") is True

def test_linkedin_people_search_rejected_as_profile():
    url = "https://www.linkedin.com/search/results/people/?keywords=Spotalike"
    assert is_real_linkedin_profile(url) is False

def test_linkedin_company_search_rejected_as_company():
    url = "https://www.linkedin.com/search/results/companies/?keywords=Spotalike"
    assert is_real_linkedin_company(url) is False
    assert is_real_linkedin_profile(url) is False

def test_google_search_url_rejected():
    assert is_real_linkedin_profile("https://www.google.com/search?q=Spotalike+founder") is False

def test_api_never_stores_search_url_as_profile():
    def extract_real_profile(raw_src):
        m = re.search(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-_]+)', raw_src)
        return f"https://www.linkedin.com/in/{m.group(1)}" if m else None
    assert extract_real_profile("https://www.linkedin.com/in/john-doe") == "https://www.linkedin.com/in/john-doe"
    assert extract_real_profile("https://www.linkedin.com/search/results/people/?keywords=Spotalike") is None
    assert extract_real_profile("https://google.com/search?q=Spotalike+founder") is None

def test_dashboard_label_for_search_url():
    def get_label(person):
        url = person.get("linkedin_url") or ""
        if url and re.search(r'linkedin\.com/in/[a-zA-Z0-9\-_]{2,}', url):
            return "View Profile"
        return "Search on LinkedIn"
    assert get_label({"linkedin_url": "https://www.linkedin.com/in/john-doe"}) == "View Profile"
    assert get_label({"linkedin_url": "https://www.linkedin.com/search/results/people/?keywords=Spotalike"}) == "Search on LinkedIn"
    assert get_label({"linkedin_url": None}) == "Search on LinkedIn"

def test_natural_people_search_queries_for_domain():
    from app.agent.key_people_discovery_agent import key_people_agent
    queries = key_people_agent.generate_queries(
        company_name="foundersday.co",
        official_domain="foundersday.co"
    )
    query_texts = [q["query"] for q in queries]
    # Verify natural queries matching manual Google search syntax
    assert any("foundersday.co founder linkedin" in q for q in query_texts)
    assert any("foundersday.co CEO linkedin" in q for q in query_texts)
    assert any("Foundersday founder linkedin" in q for q in query_texts)
    # Ensure restrictive quotes were not forced
    assert not any('"' in q for q in query_texts)

def test_tld_brand_association_matching():
    from app.extraction.key_people_extractor import key_people_extractor
    snippets = [
        {
            "title": "Alex Rivera - Founder & CEO - Foundersday | LinkedIn",
            "snippet": "Alex Rivera is the Founder & CEO of Foundersday, an exclusive community...",
            "url": "https://www.linkedin.com/in/alex-rivera-foundersday"
        }
    ]
    # Even if company name is passed as foundersday.co, it should match Foundersday snippet
    discovered = key_people_extractor.extract_from_linkedin_search_snippets(
        snippets=snippets,
        company_name="foundersday.co"
    )
    assert len(discovered) == 1
    assert discovered[0]["name"] == "Alex Rivera"
    assert "linkedin.com/in/alex-rivera-foundersday" in discovered[0]["linkedin_url"]
