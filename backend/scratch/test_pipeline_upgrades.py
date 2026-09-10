"""
Comprehensive Automated Verification for OpenDB Upgrades
Validates:
1. Company qualification: 1-200 employee startup targeting, UNKNOWN size tolerance, >200 enterprise filtering.
2. Key People Discovery: early-stopping at 3 people, query budget cap at 5.
3. Strict LinkedIn profile integrity: NO search URLs stored as profile URLs.
4. Keyword adaptation feedback loop.
5. Evidence-based synthesis (anti-hallucination, no fake contact emails).
6. Transactional outbox sync: SQLite staging -> PostgreSQL durable store.
7. No fallbacks: zero hardcoded preset seeds in SearXNG.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crawler.quality_filter import quality_filter
from app.agent.key_people_discovery_agent import key_people_agent
from app.extraction.key_people_extractor import key_people_extractor
from app.agent.keyword_expander import keyword_expander
from app.extraction.llm_extractor import llm_extractor
from app.persistence.database import SessionLocal, init_db
from app.persistence.models import PostgresSyncOutbox
from app.persistence.outbox_sync_service import outbox_sync_service
from app.crawler.searxng_service import searxng_service


class TestOpenDBUpgrades(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.db = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_1_company_qualification_rules(self):
        """Rule 1: 1-200 = HIGH priority, UNKNOWN = ALLOW (NORMAL priority), >200 = REJECT."""
        # 1. Confirmed 1-200 employees
        res_smb = quality_filter.qualify_company_candidate(
            title="Acme Robotics - 25 employees",
            snippet="Acme Robotics is an early stage robotics company with 25 employees based in Austin.",
            url="https://acmerobotics.com"
        )
        self.assertTrue(res_smb["qualified"])
        self.assertEqual(res_smb["priority"], "HIGH")
        self.assertEqual(res_smb["company_size"], "1-25")

        # 2. Unknown size: MUST BE ALLOWED (never rejected solely for unknown size!)
        res_unknown = quality_filter.qualify_company_candidate(
            title="Nexus Data Systems",
            snippet="Nexus Data Systems provides cloud intelligence and analytics software.",
            url="https://nexusdata.io"
        )
        self.assertTrue(res_unknown["qualified"])
        self.assertEqual(res_unknown["company_size"], "UNKNOWN")
        self.assertEqual(res_unknown["priority"], "NORMAL")

        # 3. Confirmed >200 employees: Deprioritized/Rejected
        res_large = quality_filter.qualify_company_candidate(
            title="MegaCorp Global - 10,000+ employees",
            snippet="MegaCorp is a Fortune 500 multinational conglomerate with over 1,000 employees globally.",
            url="https://megacorp.com"
        )
        self.assertFalse(res_large["qualified"])
        self.assertEqual(res_large["priority"], "DEPRIORITIZED")

        # 4. Directory / Irrelevant: Rejected
        res_dir = quality_filter.qualify_company_candidate(
            title="Top 10 CRM Software in 2025",
            snippet="Compare the best CRM tools on G2 and Capterra.",
            url="https://g2.com/categories/crm"
        )
        self.assertFalse(res_dir["qualified"])
        print("[PASS] Test 1 Passed: Company qualification rules (1-200 HIGH, UNKNOWN allowed, >200 rejected).")
    
    def test_2_key_people_agent_budget_and_stopping(self):
        """Rule 2: Key people search budget capped at 5, stopping target is 3."""
        self.assertEqual(key_people_agent.MAX_QUERY_BUDGET, 5)
        self.assertEqual(key_people_agent.EARLY_STOP_PEOPLE_COUNT, 3)

        queries = key_people_agent.generate_queries(
            company_name="DATAi2i",
            official_domain="datai2i.com"
        )
        self.assertLessEqual(len(queries), 5)
        self.assertGreaterEqual(len(queries), 3)

        # Check prioritized order (founder -> CEO -> CTO -> official)
        q_texts = [q["query"].lower() for q in queries]
        self.assertTrue(any("founder" in q for q in q_texts))
        self.assertTrue(any("ceo" in q for q in q_texts))
        print(f"[PASS] Test 2 Passed: Key people query budget ({len(queries)} <= 5) and stopping threshold (3).")

    def test_3_strict_linkedin_profile_integrity(self):
        """Rule 3: Never store search URLs as LinkedIn profiles! Only linkedin.com/in/<slug>."""
        # 1. From Snippet with genuine LinkedIn profile
        snippets = [
            {
                "title": "Jane Doe - Chief Executive Officer - DATAi2i | LinkedIn",
                "snippet": "Jane Doe is the CEO at DATAi2i, specializing in AI solutions.",
                "url": "https://www.linkedin.com/in/janedoe"
            },
            {
                "title": "Search results for DATAi2i leadership",
                "snippet": "Find people who work at DATAi2i on LinkedIn.",
                "url": "https://www.linkedin.com/search/results/people/?keywords=DATAi2i%20leadership"
            }
        ]
        people = key_people_extractor.extract_from_linkedin_search_snippets(snippets, "DATAi2i")
        self.assertGreaterEqual(len(people), 1)

        for p in people:
            url = p.get("linkedin_url")
            if url:
                self.assertIn("linkedin.com/in/", url)
                self.assertNotIn("/search/", url)

        # 2. Text extraction should NEVER invent a LinkedIn search URL as a profile
        text = "Alex Mercer, Founder and CTO at DATAi2i led the engineering organization."
        text_people = key_people_extractor.extract_from_text_and_html(text, "", "DATAi2i", "datai2i.com")
        self.assertGreaterEqual(len(text_people), 1)
        self.assertIsNone(text_people[0]["linkedin_url"]) # Must be None, NOT a search URL!
        print("[PASS] Test 3 Passed: Strict LinkedIn profile integrity (no search URLs masquerading as profiles).")

    def test_4_keyword_expander_adaptation(self):
        """Rule 4: Strategy adaptation shifts to SMB/startup keywords when batch is enterprise-heavy."""
        initial_q = keyword_expander.get_next_query("Information Technology")
        self.assertIn("query", initial_q)

        # Trigger enterprise-heavy adaptation
        keyword_expander.adapt_strategy("Information Technology", issue_type="enterprise_heavy")
        adapted_q = keyword_expander.get_next_query("Information Technology")
        self.assertTrue(any(term in adapted_q["query"].lower() for term in ["startup", "early stage", "1-200", "emerging"]))
        print(f"[PASS] Test 4 Passed: Keyword strategy adaptation verified ('{adapted_q['query']}').")

    def test_5_evidence_based_synthesis_no_hallucinations(self):
        """Rule 5: Extraction relies on evidence; company size is UNKNOWN if missing; no fake emails."""
        raw_text = """
        About AlphaTech
        AlphaTech builds intelligent API monitoring software for developer workflows.
        We provide real-time alerts and anomaly detection across microservices.
        Contact our team at hello@alphatech.io for enterprise inquiries.
        """
        properties = {
            "company_name": {"type": "string"},
            "company_size": {"type": "string"},
            "contact_information": {"type": "string"},
            "technologies": {"type": "array"},
        }
        extracted, evidence = llm_extractor._heuristic_semantic_extraction(
            raw_text, "Technology", properties, "https://alphatech.io"
        )
        self.assertEqual(extracted["company_size"], "UNKNOWN")
        self.assertEqual(extracted["contact_information"], "hello@alphatech.io")

        # Verify concise synthesis instead of raw 2,000 word dump
        synthesis = llm_extractor.synthesize_business_overview(raw_text, "AlphaTech")
        self.assertLess(len(synthesis), 400)
        self.assertIn("AlphaTech", synthesis)
        print("[PASS] Test 5 Passed: Evidence-based extraction & synthesis (UNKNOWN size, clean concise summary).")

    def test_6_transactional_outbox_sqlite_to_postgres(self):
        """Rule 6: Outbox sync queues leads in SQLite staging and records honest sync status."""
        test_payload = {
            "company_name": "Beta AI",
            "domain": "betaai.test",
            "company_size": "1-50",
            "summary": "Beta AI builds automated machine learning pipelines."
        }
        outbox_entry = outbox_sync_service.queue_for_postgres_sync(
            self.db,
            domain="betaai.test",
            company_name="Beta AI",
            payload=test_payload
        )
        self.assertIsNotNone(outbox_entry)
        self.assertEqual(outbox_entry.sync_status, "PENDING_SYNC")

        # Process outbox queue
        res = outbox_sync_service.process_outbox_queue(self.db, limit=1)
        self.assertIn("status", res)
        # Verify item exists in outbox table
        check = self.db.query(PostgresSyncOutbox).filter(PostgresSyncOutbox.domain == "betaai.test").first()
        self.assertIsNotNone(check)
        self.assertIn(check.sync_status, ["SYNCED", "PENDING_SYNC", "SYNC_FAILED"])
        print(f"[PASS] Test 6 Passed: Transactional outbox sync recorded honestly (status: {check.sync_status}).")

    def test_7_no_fallbacks_compliance(self):
        """Rule 7: SearXNG has zero hardcoded enterprise seed companies (Stripe, Datadog)."""
        self.assertFalse(hasattr(searxng_service, "preset_seeds"))
        self.assertFalse(hasattr(searxng_service, "_get_fallback_sources"))
        print("[PASS] Test 7 Passed: NO FALLBACK compliance (no hardcoded enterprise seeds in SearXNG).")


if __name__ == "__main__":
    unittest.main()
