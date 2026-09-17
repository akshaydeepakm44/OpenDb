import unittest
from unittest.mock import patch, AsyncMock, MagicMock

from app.agent.agent2_orchestrator import Agent2Orchestrator
from app.persistence.models import Agent2VerificationSession, Document

class TestAgent2PeopleDiscovery(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.orchestrator = Agent2Orchestrator()

    @patch("app.agent.agent2_orchestrator.searxng_service")
    async def test_website_discovery_preferred_over_linkedin(self, mock_searxng):
        # Mock database and session
        mock_db = MagicMock()
        mock_session = MagicMock(spec=Agent2VerificationSession)
        mock_session.id = "session_123"
        mock_session.domain = "example.com"
        mock_session.company_name = "Example Corp"
        mock_session.investigation_log = []
        
        # We don't need doc raw_metadata for the search fallback in Website-First discovery
        mock_doc = MagicMock(spec=Document)
        mock_doc.raw_metadata = {}
        
        mock_db.query().filter().first.return_value = mock_doc
        
        # Setup searxng response for Website-First search
        mock_searxng.search_with_meta = AsyncMock()
        mock_searxng.search_with_meta.return_value = (
            [
                {
                    "url": "https://example.com/about",
                    "title": "About Us | Example Corp",
                    "content": "Our Founder and CEO Jane Doe leads the company."
                }
            ],
            False,
            {}
        )
        
        # Let's mock evaluate_person_company_match since we don't want to run Haystack/LLM
        with patch("app.agent.agent2_orchestrator.evaluate_person_company_match") as mock_eval:
            mock_eval.return_value = {
                "company_match": True,
                "is_leadership": True,
                "verification_status": "VERIFIED",
                "evidence_snippet": "Verified via about page"
            }
            
            result = await self.orchestrator.discover_people(mock_session, mock_db)
            
            # Since min_target_candidates is usually 5, it will still proceed to LinkedIn Enrichment
            # But the candidates_pool should have the Website-First candidate.
            self.assertIn("verified_people", result)
            self.assertEqual(len(result["verified_people"]), 1)
            
            # The person match should have been called with source_url="https://example.com/about"
            mock_eval.assert_called_with(
                target_company="Example Corp",
                target_domain="example.com",
                candidate_name="Found on about",
                candidate_title="CEO",
                candidate_company="Example Corp",
                evidence_text="Our Founder and CEO Jane Doe leads the company.",
                source_url="https://example.com/about",
                linkedin_url=""
            )

if __name__ == "__main__":
    unittest.main()
