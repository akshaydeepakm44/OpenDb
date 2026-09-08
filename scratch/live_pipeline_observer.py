"""
OpenDB Continuous Live Pipeline Observer & Monitor
===================================================
Polls and monitors the live 24/7 agent discovery loop and active ingestion pipeline.
Prints real-time stream events for search activity, safety blocks, qualification,
completeness scoring, badge assignments, and outbox sync.

Usage:
  python scratch/live_pipeline_observer.py
"""
import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath("backend"))

from app.persistence.database import SessionLocal, init_db
from app.persistence.models import (
    AgentState, SearchHistory, SearchCandidate, Company,
    CrawlActivityLog, DataCompletenessScore, PostgresSyncOutbox, BlockedDomain, QuarantineRecord
)

API_BASE = "http://127.0.0.1:8000/api"


def http_get(url: str) -> dict:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {}


def main():
    print("================================================================================")
    print("📡 OPENDB LIVE PIPELINE OBSERVER & STREAM MONITOR")
    print("================================================================================")
    print("Press CTRL+C to stop observing.\n")

    init_db()

    last_log_id = None
    last_candidate_count = 0
    last_company_count = 0
    last_outbox_count = 0

    iteration = 0

    while True:
        iteration += 1
        db = SessionLocal()
        try:
            # 1. Agent Status & Current Keyword
            status_data = http_get(f"{API_BASE}/agent/status")
            agent_status = status_data.get("status", "UNKNOWN")
            current_domain = status_data.get("current_domain", "N/A")
            current_subdomain = status_data.get("current_subdomain", "N/A")
            current_kw = status_data.get("current_keyword", "N/A")

            # 2. Database Counts
            candidate_count = db.query(SearchCandidate).count()
            company_count = db.query(Company).count()
            outbox_count = db.query(PostgresSyncOutbox).count()
            blocked_count = db.query(BlockedDomain).count()
            quarantine_count = db.query(QuarantineRecord).count()

            timestamp_str = datetime.now().strftime("%H:%M:%S")

            print(f"[{timestamp_str}] [POLL #{iteration}] Agent: {agent_status:<8} | Domain: {current_domain} > {current_subdomain} | Active Keyword: '{current_kw}'")
            print(f"  📊 Candidates: {candidate_count} | Companies: {company_count} | Outbox: {outbox_count} | Blocked Domains: {blocked_count} | Quarantined: {quarantine_count}")

            # 3. New Activity Logs Stream
            activity_logs = db.query(CrawlActivityLog).order_by(CrawlActivityLog.timestamp.desc()).limit(5).all()
            if activity_logs:
                print("  ⚡ Recent Pipeline Events:")
                for log in reversed(activity_logs):
                    print(f"    [{log.timestamp.strftime('%H:%M:%S')}] [{log.stage:<6}] [{log.status:<6}] {log.url[:55]:<55} | {log.message[:40]}")

            # 4. Check for New Verified Companies
            if company_count > last_company_count:
                new_companies = db.query(Company).order_by(Company.created_at.desc()).limit(company_count - last_company_count).all()
                for comp in new_companies:
                    # Get completeness score
                    score_rec = db.query(DataCompletenessScore).filter(DataCompletenessScore.company_id == comp.id).first()
                    score_val = score_rec.total_score if score_rec else 0.0
                    badge_val = score_rec.badge_level if score_rec else "N/A"

                    print(f"\n  🎉 [NEW VERIFIED COMPANY DISCOVERED] {comp.company_name} ({comp.website})")
                    print(f"     Industry: {comp.industry} | HQ: {comp.hq_country} | Score: {score_val}/100 ({badge_val})\n")

            # 5. Check for New Outbox Sync Events
            if outbox_count > last_outbox_count:
                new_outbox = db.query(PostgresSyncOutbox).order_by(PostgresSyncOutbox.created_at.desc()).limit(outbox_count - last_outbox_count).all()
                for ob in new_outbox:
                    print(f"  📦 [POSTGRES LAKE OUTBOX EVENT] Domain: {ob.domain} | Score: {ob.completeness_score} | Badge: {ob.badge} | Status: {ob.status_code}")

            last_candidate_count = candidate_count
            last_company_count = company_count
            last_outbox_count = outbox_count

            print("-" * 80)
            time.sleep(5)

        except KeyboardInterrupt:
            print("\n👋 Live observer stopped.")
            break
        except Exception as e:
            print(f"  ⚠️ Observer error: {e}")
            time.sleep(5)
        finally:
            db.close()


if __name__ == "__main__":
    main()
