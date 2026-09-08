import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.crawler.anti_hallucination_pipeline import anti_hallucination_pipeline

def test_extractor_no_fallback():
    print("[TEST] Verifying extraction pipeline returns NULL on empty/garbage pages (Zero Fallback Assertions)...")

    # Garbage / empty crawled page input
    garbage_pages = [{
        "url": "https://unknown-test-domain.org",
        "text": "<html><body><div>Welcome to website</div></body></html>",
        "html": "<html><body><div>Welcome to website</div></body></html>",
        "title": "Welcome Page"
    }]

    record = anti_hallucination_pipeline.build_standard_company_record(
        company_name="UnknownTestDomain",
        domain="unknown-test-domain.org",
        official_url="https://unknown-test-domain.org",
        logo_url=None,
        crawled_pages=garbage_pages
    )

    # 1. Assert business_overview text is None
    overview = record.get("business_overview", {})
    ov_text = overview.get("text") if isinstance(overview, dict) else overview
    print(f"Extracted Business Overview: {ov_text}")
    assert ov_text is None, f"FAIL: Extractor returned canned sentence '{ov_text}' instead of None"

    # 2. Assert tech stack is empty list
    tech_stack = record.get("technology_stack", [])
    print(f"Extracted Tech Stack: {tech_stack}")
    assert len(tech_stack) == 0, f"FAIL: Extractor returned fallback tech stack {tech_stack} instead of empty list"

    # 3. Assert firmographics properties are None
    fg = record.get("firmographics", {})
    hq_val = (fg.get("headquarters") or {}).get("value")
    ind_val = (fg.get("industry") or {}).get("value")
    size_val = (fg.get("company_size") or {}).get("value")
    rev_val = (fg.get("revenue_funding") or {}).get("value")

    print(f"Extracted Firmographics — HQ: {hq_val}, Industry: {ind_val}, Size: {size_val}, Rev: {rev_val}")
    assert hq_val is None or hq_val == "Not Specified", "FAIL: HQ fallback text returned"
    assert ind_val is None, "FAIL: Industry fallback text returned"
    assert size_val is None, "FAIL: Size fallback text returned"
    assert rev_val is None, "FAIL: Revenue fallback text returned"

    print("\n[SUCCESS] ZERO FALLBACK ASSERTIONS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_extractor_no_fallback()
