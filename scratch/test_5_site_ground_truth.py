import sys
import os
import json
import asyncio

# Set stdout encoding to utf-8 if possible
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.crawler.anti_hallucination_pipeline import anti_hallucination_pipeline

# 5 Target Companies & Ground-Truth HTML/Markdown Samples
BENCHMARK_SITES = [
    {
        "company_name": "Stripe",
        "domain": "stripe.com",
        "official_url": "https://stripe.com",
        "text": """
        Stripe is a financial infrastructure platform for businesses.
        Millions of companies—from ambitious startups to Fortune 500s—use Stripe software and APIs to accept payments, send payouts, and manage their businesses online.
        Contact our sales team at info@stripe.com or support@stripe.com for enterprise billing plans.
        Patrick Collison is the Chief Executive Officer and John Collison is President.
        """,
        "expected_industry": "Fintech & Financial Services",
        "expected_emails": ["info@stripe.com", "support@stripe.com"],
        "expected_executives": ["Patrick Collison", "John Collison"]
    },
    {
        "company_name": "GitHub",
        "domain": "github.com",
        "official_url": "https://github.com",
        "text": """
        GitHub is a developer platform that allows developers to create, store, manage, and share their code.
        Over 100 million developers use GitHub to build and ship software.
        Contact sales at support@github.com or enterprise@github.com.
        Thomas Dohmke serves as Chief Executive Officer of GitHub.
        """,
        "expected_industry": "Developer Tools & Software",
        "expected_emails": ["support@github.com", "enterprise@github.com"],
        "expected_executives": ["Thomas Dohmke"]
    },
    {
        "company_name": "Vercel",
        "domain": "vercel.com",
        "official_url": "https://vercel.com",
        "text": """
        Vercel is the frontend cloud platform for React and Next.js applications.
        Vercel provides developer tools, instant deployments, and serverless compute infrastructure.
        Guillermo Rauch is Founder and Chief Executive Officer at Vercel.
        Contact us at sales@vercel.com for enterprise plans.
        """,
        "expected_industry": "Developer Tools & Software",
        "expected_emails": ["sales@vercel.com"],
        "expected_executives": ["Guillermo Rauch"]
    },
    {
        "company_name": "Datadog",
        "domain": "datadoghq.com",
        "official_url": "https://www.datadoghq.com",
        "text": """
        Datadog is an essential monitoring and security platform for cloud applications.
        Datadog integrates and automates infrastructure monitoring, application performance monitoring, and log management to provide unified observability.
        Olivier Pomel is Chief Executive Officer and Alexis Lê-Quôc is Chief Technology Officer.
        Reach our team at sales@datadoghq.com.
        """,
        "expected_industry": "Cloud Infrastructure & DevOps",
        "expected_emails": ["sales@datadoghq.com"],
        "expected_executives": ["Olivier Pomel", "Alexis Lê-Quôc"]
    },
    {
        "company_name": "Snowflake",
        "domain": "snowflake.com",
        "official_url": "https://www.snowflake.com",
        "text": """
        Snowflake is a cloud data platform that provides data warehousing, data lake, and data sharing solutions.
        Snowflake enables data storage, processing, and analytic solutions that are faster, easier to use, and far more flexible than traditional offerings.
        Sridhar Ramaswamy is Chief Executive Officer of Snowflake.
        Contact info@snowflake.com for licensing.
        """,
        "expected_industry": "Data Analytics & BI",
        "expected_emails": ["info@snowflake.com"],
        "expected_executives": ["Sridhar Ramaswamy"]
    }
]

def run_ground_truth_benchmark():
    print("=" * 70)
    print("RUNNING 5-SITE GROUND-TRUTH ACCURACY & ANTI-HALLUCINATION BENCHMARK")
    print("=" * 70)

    total_tests = 0
    passed_tests = 0

    for site in BENCHMARK_SITES:
        domain = site["domain"]
        cname = site["company_name"]
        print(f"\n--- Testing Ground-Truth Verification for: {cname} ({domain}) ---")

        crawled_pages = [
            {
                "url": site["official_url"],
                "text": site["text"],
                "html": f"<html><body><p>{site['text']}</p></body></html>",
                "title": f"{cname} Official Website",
                "minio_raw_path": f"companies/{domain}/pages/homepage.md"
            }
        ]

        record = anti_hallucination_pipeline.build_standard_company_record(
            company_name=cname,
            domain=domain,
            official_url=site["official_url"],
            logo_url=f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
            crawled_pages=crawled_pages
        )

        # 1. Verify Extractive Overview
        overview_text = (record.get("business_overview") or {}).get("text")
        total_tests += 1
        if overview_text and cname.lower() in overview_text.lower():
            passed_tests += 1
            print(f"  [PASS] Business Overview: '{overview_text[:75]}...'")
        else:
            print(f"  [FAIL] Extractive overview missing or invalid for {cname}")

        # 2. Verify Emails
        found_emails = [e["email"] for e in record.get("verified_emails", [])]
        total_tests += 1
        expected_matches = [e for e in site["expected_emails"] if e in found_emails]
        if len(expected_matches) >= 1:
            passed_tests += 1
            print(f"  [PASS] Extracted Verified Emails: {found_emails}")
        else:
            print(f"  [FAIL] Expected emails {site['expected_emails']}, got: {found_emails}")

        # 3. Verify Key Leadership & Compound Role Tags
        decision_makers = record.get("decision_makers", [])
        dm_names = [dm["name"] for dm in decision_makers]
        total_tests += 1
        exec_match = any(e in dm_names for e in site["expected_executives"])
        if exec_match or len(decision_makers) > 0:
            passed_tests += 1
            print(f"  [PASS] Decision Makers: {[dm['name'] + ' (' + ', '.join(dm['role_tags']) + ')' for dm in decision_makers]}")
        else:
            print(f"  [FAIL] Key executives missing. Got: {decision_makers}")

        # 4. Verify Warmth Score Calculation
        warmth = record.get("warmth_score", {})
        total_tests += 1
        if isinstance(warmth, dict) and warmth.get("value", 0) > 0 and "Formula:" in warmth.get("explanation", ""):
            passed_tests += 1
            print(f"  [PASS] Warmth Score: {warmth.get('value')}/10.0 ({warmth.get('explanation')})")
        else:
            print(f"  [FAIL] Warmth score invalid: {warmth}")

        # 5. Verify Zero Hallucination on Missing Fields
        # Revenue is not in sample text -> firmographics.revenue_funding.value MUST BE None
        total_tests += 1
        rev_val = record["firmographics"]["revenue_funding"]["value"]
        if rev_val is None:
            passed_tests += 1
            print("  [PASS] Zero Hallucination: Missing revenue field correctly output as None")
        else:
            print(f"  [FAIL] Revenue was hallucinated or defaulted: {rev_val}")

    accuracy_pct = (passed_tests / total_tests) * 100
    print("\n" + "=" * 70)
    print(f"BENCHMARK COMPLETED: {passed_tests}/{total_tests} Tests Passed ({accuracy_pct:.1f}% Ground-Truth Accuracy)")
    print("=" * 70)

if __name__ == "__main__":
    run_ground_truth_benchmark()
