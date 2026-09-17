import unittest
from unittest.mock import patch, AsyncMock, MagicMock

from app.agent.agent2_orchestrator import Agent2Orchestrator
from app.persistence.models import Agent2VerificationSession, Document

class TestAgent2PeopleDiscovery(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.orchestrator = Agent2Orchestrator()

    @patch("app.agent.agent2_orchestrator.searxng_service")
    async def test_linkedin_founder_discovery_zero_crawl(self, mock_searxng):
        mock_db = MagicMock()
        mock_session = MagicMock(spec=Agent2VerificationSession)
        mock_session.id = "session_123"
        mock_session.domain = "example.com"
        mock_session.company_name = "Example Corp"
        mock_session.investigation_log = []
        
        mock_doc = MagicMock(spec=Document)
        mock_doc.raw_metadata = {}
        mock_db.query().filter().first.return_value = mock_doc
        
        # Setup SearXNG response for site:linkedin.com/in/ "Example Corp" founder OR CEO
        mock_searxng.search_with_meta = AsyncMock()
        mock_searxng.search_with_meta.return_value = (
            [
                {
                    "url": "https://www.linkedin.com/in/janedoe",
                    "title": "Jane Doe - Co-Founder & CEO - Example Corp | LinkedIn",
                    "content": "Jane Doe is the Co-Founder and CEO of Example Corp, leading operations in San Francisco."
                }
            ],
            False,
            {}
        )
        
        with patch("app.agent.agent2_orchestrator.evaluate_person_company_match") as mock_eval:
            mock_eval.return_value = {
                "company_match": True,
                "is_leadership": True,
                "verification_status": "VERIFIED",
                "evidence_snippet": "Verified executive leadership"
            }
            
            result = await self.orchestrator.discover_people(mock_session, mock_db)
            
            self.assertIn("verified_people", result)
            self.assertEqual(len(result["verified_people"]), 1)
            self.assertEqual(result["verified_people"][0], "Jane Doe")
            
            # Verify candidate was matched with clean personal LinkedIn URL and zero crawling
            mock_eval.assert_called_with(
                target_company="Example Corp",
                target_domain="example.com",
                candidate_name="Jane Doe",
                candidate_title="Co-Founder & CEO",
                candidate_company="Example Corp",
                evidence_text="Jane Doe is the Co-Founder and CEO of Example Corp, leading operations in San Francisco.",
                source_url="https://www.linkedin.com/in/janedoe",
                linkedin_url="https://www.linkedin.com/in/janedoe"
            )

if __name__ == "__main__":
    unittest.main()
