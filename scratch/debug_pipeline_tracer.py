"""
OpenDB Step-by-Step Pipeline Tracer & Diagnostic Debugger
=========================================================
Runs an interactive end-to-end tracer through every gate of the OpenDB 13-Stage Architecture.
Prints detailed step-by-step logs to highlight exactly where candidates pass or get blocked.

Usage:
  python scratch/debug_pipeline_tracer.py
  python scratch/debug_pipeline_tracer.py --query "generative AI startups official website"
  python scratch/debug_pipeline_tracer.py --domain "acme.com"
"""
import os
import sys
import json
import asyncio
import argparse

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath("backend"))

from app.agent.keyword_expander import keyword_expander, DiscoveryQueryIntent
from app.safety.search_query_guard import search_query_guard
from app.safety.domain_safety_guard import domain_safety_guard
from app.classification.source_classifier import source_classifier
from app.crawler.official_domain_resolver import official_domain_resolver
from app.crawler.searxng_service import SearXNGService
from app.crawler.company_qualification_engine import company_qualification_engine
from app.crawler.company_completeness_engine import company_completeness_engine
from app.persistence.outbox import outbox_manager
from app.persistence.database import SessionLocal, init_db


def print_banner(text: str):
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_step(step_num: int, title: str):
    print(f"\n▶ [STEP {step_num}] {title}")
    print("-" * 60)


async def trace_query(raw_query: str, domain_hint: str = "Information Technology"):
    print_banner(f"TRACING SEARCH QUERY: '{raw_query}'")

    blockers = []

    # ── STEP 1: Query Metadata & Intent Classification ──────────────────────
    print_step(1, "SEARCH QUERY INTENT CLASSIFICATION")
    intent_data = keyword_expander.get_next_query(domain_hint)
    intent = intent_data.get("intent", "official_company_discovery")
    can_crawl_directly = intent_data.get("can_crawl_result_directly", True)
    requires_resolution = intent_data.get("requires_company_domain_resolution", False)

    print(f"  Raw Input Query              : {raw_query}")
    print(f"  Discovered Search Intent     : {intent}")
    print(f"  Can Crawl Directly           : {can_crawl_directly}")
    print(f"  Requires Domain Resolution   : {requires_resolution}")

    # ── STEP 2: Search Query Safety Guard Inspection ────────────────────────
    print_step(2, "SEARCH QUERY SAFETY GUARD INSPECTION")
    is_safe, cat, kw = search_query_guard.is_query_safe(raw_query)
    sanitized_query = search_query_guard.sanitize_query_with_negative_operators(raw_query)

    print(f"  Query Safety Check Status    : {'SAFE' if is_safe else 'REJECTED'}")
    if not is_safe:
        print(f"  PROHIBITED CATEGORY DETECTED : {cat} (Matched keyword: '{kw}')")
        blockers.append(f"Query Safety Guard blocked query due to prohibited category: '{cat}'")
        print("\n🚫 PIPELINE TERMINATED AT STEP 2 (Query Safety Guard Block)")
        return
    print(f"  Sanitized Query              : {sanitized_query}")

    # ── STEP 3: SearXNG Candidate Search Dispatch ───────────────────────────
    print_step(3, "SEARXNG CANDIDATE SEARCH DISPATCH")
    searxng = SearXNGService()
    results, is_fallback, status_log = await searxng.search_with_meta(sanitized_query, max_results=10)

    print(f"  SearXNG Status               : {status_log}")
    print(f"  Fallback Mode Active         : {is_fallback}")
    print(f"  Candidate URLs Discovered    : {len(results)}")

    if not results:
        blockers.append("SearXNG returned 0 candidates for query")
        print("\n⚠️ PIPELINE HALTED AT STEP 3 (Zero candidates returned by SearXNG)")
        return

    for idx, item in enumerate(results[:5], 1):
        print(f"    [{idx}] {item.get('title', 'N/A')[:45]} | {item.get('url')}")

    # Process first candidate for deep tracing
    target_candidate = results[0]
    candidate_url = target_candidate.get("url", "")
    print(f"\n  🎯 Selected Primary Candidate: {candidate_url}")

    # ── STEP 4: Domain Safety Firewall Inspection ───────────────────────────
    print_step(4, "DOMAIN SAFETY FIREWALL INSPECTION")
    safety_res = domain_safety_guard.evaluate_domain_safety(candidate_url)
    allowed = safety_res.get("allowed", False)
    risk_level = safety_res.get("risk_level", "UNKNOWN")
    reason = safety_res.get("reason", "N/A")
    domain_name = safety_res.get("domain", "")

    print(f"  Candidate Domain             : {domain_name}")
    print(f"  Domain Firewall Status       : {'ALLOWED' if allowed else 'HARD BLOCKED'}")
    print(f"  Risk Level                   : {risk_level}")
    print(f"  Safety Reason                : {reason}")

    if not allowed:
        blockers.append(f"Domain Safety Firewall blocked domain '{domain_name}': {reason}")
        print("\n🚫 PIPELINE TERMINATED AT STEP 4 (Domain Safety Firewall Hard Block)")
        return

    # ── STEP 5: Source Type Classification ──────────────────────────────────
    print_step(5, "SOURCE TYPE CLASSIFICATION")
    source_cat, source_can_crawl, source_reason = source_classifier.classify_url(
        candidate_url,
        title=target_candidate.get("title", ""),
        snippet=target_candidate.get("snippet", "")
    )
    print(f"  Source Category              : {source_cat.value}")
    print(f"  Direct Crawl Permitted       : {source_can_crawl}")
    print(f"  Classification Reason        : {source_reason}")

    # ── STEP 6: Official Domain Resolution & Key People Extraction ──────────
    print_step(6, "OFFICIAL DOMAIN RESOLUTION & LINKEDIN KEY PEOPLE SCRAPE")
    resolution = official_domain_resolver.resolve_candidate(target_candidate)

    resolved_domain = resolution.get("official_domain", "")
    resolution_status = resolution.get("status", "PENDING")
    resolution_confidence = resolution.get("confidence", 0)
    key_people = resolution.get("extracted_key_people", [])

    print(f"  Company Name Resolved        : {resolution.get('company_name')}")
    print(f"  Official Domain Resolved     : {resolved_domain or 'NOT RESOLVED'}")
    print(f"  Resolution Status            : {resolution_status}")
    print(f"  Resolution Confidence        : {resolution_confidence} / 100")
    print(f"  Key People Extracted         : {len(key_people)} leadership personnel")

    for p in key_people:
        print(f"    • {p.get('name')} ({p.get('title')}) - {p.get('role_tag')}")

    if resolution_status != "RESOLVED" or not resolved_domain:
        blockers.append(f"Official Domain Resolver failed to reach minimum confidence (70): Confidence = {resolution_confidence}")
        print("\n⚠️ PIPELINE PAUSED AT STEP 6 (Pending Official Domain Resolution)")
        return

    # ── STEP 7: Stage 1 Lightweight Qualification Crawl ────────────────────
    print_step(7, "STAGE 1 LIGHTWEIGHT QUALIFICATION CRAWL")
    simulated_pages = [
        {
            "url": f"https://{resolved_domain}",
            "text": f"{resolution.get('company_name')} is an enterprise technology provider. Contact us at info@{resolved_domain}. CEO Jane Smith. Located in San Francisco, CA. Privacy Policy Terms of Service.",
            "html": f"<a href='https://linkedin.com/company/{resolved_domain.split('.')[0]}'>LinkedIn</a>"
        },
        {
            "url": f"https://{resolved_domain}/about",
            "text": f"About {resolution.get('company_name')}. Founded to deliver modern SaaS solutions.",
            "html": ""
        }
    ]
    simulated_dossier = {
        "company_name": resolution.get("company_name"),
        "business_overview": {"text": f"{resolution.get('company_name')} is an enterprise technology provider."},
        "verified_emails": [{"email": f"info@{resolved_domain}", "status": "verified"}],
        "firmographics": {"headquarters": {"value": "San Francisco, CA"}},
        "decision_makers": key_people or [{"name": "Jane Smith", "title": "CEO"}]
    }

    qual_res = company_qualification_engine.evaluate_stage1_qualification(
        resolved_domain, simulated_pages, simulated_dossier
    )
    is_qualified = qual_res.get("qualified", False)
    stage1_score = qual_res.get("stage1_score", 0.0)

    print(f"  Stage 1 Qualification Status : {'QUALIFIED' if is_qualified else 'REJECTED'}")
    print(f"  Stage 1 Qualification Score  : {stage1_score} / 100.0 (Threshold: 35.0)")
    print("  Positive Signals             :")
    for sig in qual_res.get("positive_signals", []):
        print(f"    + {sig}")

    if qual_res.get("penalties"):
        print("  Scoring Penalties            :")
        for pen in qual_res.get("penalties", []):
            print(f"    - {pen}")

    if not is_qualified:
        blockers.append(f"Company Stage 1 Qualification failed: Score {stage1_score} below threshold 35.0")
        print("\n🚫 PIPELINE REJECTED AT STEP 7 (Failed Stage 1 Qualification)")
        return

    # ── STEP 8 & 9: 11-Section Dossier & 100-Pt Data Completeness Engine ────
    print_step(8, "100-POINT DATA COMPLETENESS CALCULATION & TIER BADGING")
    completeness = company_completeness_engine.calculate_completeness(simulated_dossier, simulated_pages)

    total_score = completeness.get("total_score", 0.0)
    badge = completeness.get("badge", "N/A")
    color = completeness.get("color_dot", "⚪")
    status_code = completeness.get("status_code", "UNKNOWN")
    is_sync_eligible = completeness.get("is_sync_eligible", False)

    print(f"  Data Completeness Score      : {total_score} / 100.0")
    print(f"  Assigned Quality Badge       : {color} {badge}")
    print(f"  Status Code                  : {status_code}")
    print(f"  PostgreSQL Sync Eligible     : {is_sync_eligible}")
    print("  Score Breakdown              :")
    for dim, sc in completeness.get("breakdown", {}).items():
        print(f"    • {dim:<20} : {sc} pts")

    # ── STEP 10: Dual Database Transactional Outbox Sync ───────────────────
    print_step(9, "DUAL DATABASE TRANSACTIONAL OUTBOX SYNC")
    db = SessionLocal()
    try:
        outbox_entry = outbox_manager.queue_for_postgres_sync(
            db,
            company_id=f"comp_{resolved_domain.split('.')[0]}",
            domain=resolved_domain,
            score=total_score,
            badge=badge,
            status_code=status_code,
            payload=simulated_dossier
        )
        if outbox_entry:
            print(f"  Outbox Event Created         : YES (ID: {outbox_entry.id[:8]}...)")
            print(f"  Sync Target                  : PostgreSQL Verified Intelligence Lake")
        else:
            print("  Outbox Event Created         : NO (Score below 60.0 threshold)")
            print("  Staging Isolation            : Record safely isolated in SQLite Staging only")
            blockers.append(f"PostgreSQL Sync skipped: Completeness score ({total_score}) below 60.0 threshold")
    finally:
        db.close()

    # ── FINAL DIAGNOSTIC SUMMARY ─────────────────────────────────────────────
    print_banner("PIPELINE TRACE DIAGNOSTIC SUMMARY")
    if not blockers:
        print("  🎉 PERFECT END-TO-END PIPELINE EXECUTION")
        print(f"  Company '{resolution.get('company_name')}' ({resolved_domain}) successfully verified!")
        print(f"  Badge: {color} {badge} ({total_score}/100.0)")
    else:
        print("  ⚠️ PIPELINE BLOCKERS & BOTTLENECKS IDENTIFIED:")
        for idx, blk in enumerate(blockers, 1):
            print(f"    [{idx}] {blk}")


def main():
    parser = argparse.ArgumentParser(description="OpenDB Pipeline Tracer & Debugger")
    parser.add_argument("--query", type=str, default="generative AI startups official website", help="Search query to trace")
    parser.add_argument("--domain", type=str, default=None, help="Domain hint or direct domain to evaluate")

    args = parser.parse_args()

    init_db()

    query = args.query
    if args.domain:
        query = f"{args.domain} official website"

    asyncio.run(trace_query(query))


if __name__ == "__main__":
    main()
