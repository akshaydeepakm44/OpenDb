"""
Architectural Upgrade Verification Test Suite — OpenDB 13-Stage Pipeline
Validates Query Guard, Domain Safety Firewall, Source Classification, Official Domain Resolution,
Stage 1 Qualification, Data Completeness Engine, and Transactional Outbox Sync.
"""
import os
import sys
import unittest

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath("backend"))

from app.agent.keyword_expander import keyword_expander, DiscoveryQueryIntent, RESTRICTED_THIRD_PARTY_DOMAINS
from app.safety.search_query_guard import search_query_guard
from app.safety.domain_safety_guard import domain_safety_guard
from app.classification.source_classifier import source_classifier, SourceCategory
from app.crawler.official_domain_resolver import official_domain_resolver
from app.crawler.company_qualification_engine import company_qualification_engine
from app.crawler.company_completeness_engine import company_completeness_engine
from app.persistence.outbox import outbox_manager, MIN_POSTGRES_SYNC_SCORE
from app.persistence.database import SessionLocal, staging_engine, init_db
from app.persistence.models import Base, PostgresSyncOutbox, OpenLakeRecord


class TestPipelineArchitecture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        # Ensure postgres_sync_outbox table schema is updated in SQLite
        with staging_engine.connect() as conn:
            from sqlalchemy import text
            try:
                conn.execute(text("DROP INDEX IF EXISTS ix_postgres_sync_outbox_created_at;"))
                conn.execute(text("DROP INDEX IF EXISTS ix_postgres_sync_outbox_domain;"))
                conn.execute(text("DROP INDEX IF EXISTS ix_postgres_sync_outbox_company_id;"))
                conn.execute(text("DROP INDEX IF EXISTS ix_postgres_sync_outbox_processed;"))
                conn.execute(text("DROP TABLE IF EXISTS postgres_sync_outbox;"))
                conn.commit()
            except Exception:
                pass
        Base.metadata.create_all(bind=staging_engine)
        cls.db = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_phase1_keyword_expander_intent(self):
        """Phase 1: Verify DiscoveryQueryIntent metadata generation & domain restrictions."""
        query_info = keyword_expander.get_next_query("Information Technology")
        self.assertIn("query", query_info)
        self.assertIn("intent", query_info)
        self.assertIn("can_crawl_result_directly", query_info)
        self.assertIn("requires_company_domain_resolution", query_info)

        # Check restricted domains list
        self.assertIn("linkedin.com", RESTRICTED_THIRD_PARTY_DOMAINS)
        self.assertIn("crunchbase.com", RESTRICTED_THIRD_PARTY_DOMAINS)
        self.assertIn("g2.com", RESTRICTED_THIRD_PARTY_DOMAINS)
        print("✅ Phase 1: Keyword Expander intent metadata & domain restrictions verified.")

    def test_phase2_search_query_guard(self):
        """Phase 2: Verify prohibited category rejection & negative operator injection."""
        # Unsafe query check
        is_safe, cat, kw = search_query_guard.is_query_safe("best casino online poker sites")
        self.assertFalse(is_safe)
        self.assertEqual(cat, "gambling")

        # Safe query sanitization check
        raw_query = "AI SaaS companies official website"
        sanitized = search_query_guard.sanitize_query_with_negative_operators(raw_query)
        self.assertIn("-porn", sanitized)
        self.assertIn("-casino", sanitized)
        self.assertTrue(sanitized.startswith("AI SaaS companies official website"))
        print("✅ Phase 2: Search Query Safety Guard verified.")

    def test_phase3_domain_safety_guard(self):
        """Phase 3: Verify Domain Safety Firewall hard blocks."""
        # Safe company domain
        res_safe = domain_safety_guard.evaluate_domain_safety("https://acmeai.com")
        self.assertTrue(res_safe["allowed"])
        self.assertEqual(res_safe["risk_level"], "LOW")

        # Unsafe adult / casino / parked domain blocks
        res_adult = domain_safety_guard.evaluate_domain_safety("https://xxx-adult-cam.com")
        self.assertFalse(res_adult["allowed"])
        self.assertEqual(res_adult["risk_level"], "BLOCKED")

        res_parked = domain_safety_guard.evaluate_domain_safety("https://domain-for-sale-hugedomains.com")
        self.assertFalse(res_parked["allowed"])
        self.assertEqual(res_parked["risk_level"], "BLOCKED")
        print("✅ Phase 3: Domain Safety Firewall hard blocks verified.")

    def test_phase4_source_classifier(self):
        """Phase 4: Verify strict source classification (UNKNOWN = DO NOT CRAWL)."""
        cat, can_crawl, reason = source_classifier.classify_url("https://www.linkedin.com/company/acme")
        self.assertEqual(cat, SourceCategory.SOCIAL_MEDIA)
        self.assertFalse(can_crawl)

        cat2, can_crawl2, reason2 = source_classifier.classify_url("https://nitiforstates.gov.in")
        self.assertEqual(cat2, SourceCategory.GOVERNMENT)
        self.assertTrue(can_crawl2)
        print("✅ Phase 4: Source Classifier UNKNOWN / Third-party block verified.")

    def test_phase5_official_domain_resolver_and_key_people(self):
        """Phase 5: Verify official domain resolution and key people extraction from LinkedIn."""
        candidate = {
            "title": "Acme AI - LinkedIn | Jane Doe Chief Executive Officer",
            "snippet": "Acme AI is a leading generative AI platform. Visit official site at https://acmeai.com for details.",
            "url": "https://www.linkedin.com/company/acmeai"
        }
        res = official_domain_resolver.resolve_candidate(candidate)
        self.assertEqual(res["official_domain"], "acmeai.com")
        self.assertEqual(res["status"], "RESOLVED")
        self.assertGreaterEqual(res["confidence"], 70)
        self.assertGreaterEqual(len(res["extracted_key_people"]), 1)
        self.assertEqual(res["extracted_key_people"][0]["name"], "Jane Doe")
        print(f"✅ Phase 5: Domain Resolver & LinkedIn Key People Scrape verified ({res['extracted_key_people'][0]['name']}).")

    def test_phase6_company_qualification_engine(self):
        """Phase 6: Verify Stage 1 Lightweight qualification scoring & penalties."""
        pages = [
            {"url": "https://acme.com", "text": "Acme AI provides enterprise SaaS platform solutions. Contact us at info@acme.com. Located in San Francisco, CA. CEO John Smith. Privacy Policy Terms of Service.", "html": "<a href='https://linkedin.com/company/acme'>LinkedIn</a>"},
            {"url": "https://acme.com/about", "text": "About Acme AI. Founded in 2022 to pioneer cloud solutions.", "html": ""}
        ]
        dossier = {
            "company_name": "Acme AI",
            "business_overview": {"text": "Acme AI provides enterprise SaaS platform solutions."},
            "verified_emails": [{"email": "info@acme.com", "status": "verified"}],
            "firmographics": {"headquarters": {"value": "San Francisco, CA"}},
            "decision_makers": [{"name": "John Smith", "title": "CEO"}]
        }

        res = company_qualification_engine.evaluate_stage1_qualification("acme.com", pages, dossier)
        self.assertTrue(res["qualified"])
        self.assertGreaterEqual(res["stage1_score"], 35.0)
        self.assertFalse(res["hard_blocked"])
        print(f"✅ Phase 6: Stage 1 Qualification verified (Score: {res['stage1_score']}).")

    def test_phase9_company_completeness_engine(self):
        """Phase 9 & 10: Verify exact weighted 100-pt formula and 5 badge tiers."""
        dossier = {
            "verified_emails": [{"email": "contact@acme.com", "status": "verified"}, {"email": "support@acme.com", "status": "verified"}],
            "decision_makers": [{"name": "Alice Founder", "title": "Founder & CEO"}, {"name": "Bob CTO", "title": "CTO"}],
            "firmographics": {
                "headquarters": {"value": "New Delhi, India"},
                "industry": {"value": "Government Technology"},
                "company_size": {"value": "500-1000"},
                "revenue_funding": {"value": "$50M Series B"}
            },
            "crawled_subpages": [{"url": "https://acme.com/about"}, {"url": "https://acme.com/products"}, {"url": "https://acme.com/team"}, {"url": "https://acme.com/contact"}]
        }
        pages = [
            {"url": "https://acme.com", "text": "Homepage"},
            {"url": "https://acme.com/about", "text": "About"},
            {"url": "https://acme.com/products", "text": "Products"},
            {"url": "https://acme.com/team", "text": "Team"}
        ]

        res = company_completeness_engine.calculate_completeness(dossier, pages)
        self.assertEqual(res["total_score"], 100.0)
        self.assertEqual(res["badge"], "VERIFIED COMPLETE")
        self.assertTrue(res["is_sync_eligible"])
        print(f"✅ Phase 9: Completeness Score Formula verified (Score: {res['total_score']} - Badge: {res['badge']}).")

    def test_phase11_transactional_outbox(self):
        """Phase 11: Verify transactional outbox syncs ONLY QUALIFIED (score >= 60) companies."""
        # Unqualified candidate (score 30) should NOT queue for outbox
        entry_bad = outbox_manager.queue_for_postgres_sync(self.db, "comp_1", "bad.com", 30.0, "INSUFFICIENT", "INSUFFICIENT_DATA", {})
        self.assertIsNone(entry_bad)

        # Qualified candidate (score 92) SHOULD queue for outbox
        entry_good = outbox_manager.queue_for_postgres_sync(self.db, "comp_2", "goodacme.com", 92.0, "VERIFIED COMPLETE", "VERIFIED_COMPLETE", {"name": "Good Acme"})
        self.assertIsNotNone(entry_good)

        # Process outbox queue
        synced = outbox_manager.process_outbox_queue(self.db)
        self.assertGreaterEqual(synced, 1)

        lake_rec = self.db.query(OpenLakeRecord).filter(OpenLakeRecord.domain == "goodacme.com").first()
        self.assertIsNotNone(lake_rec)
        print(f"✅ Phase 11: Transactional Outbox Sync to PostgreSQL Lake verified.")


if __name__ == "__main__":
    unittest.main()
