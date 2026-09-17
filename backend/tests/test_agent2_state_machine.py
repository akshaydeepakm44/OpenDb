import unittest
from unittest.mock import patch, MagicMock

from app.agent.agent2_orchestrator import Agent2Orchestrator
from app.persistence.models import Agent2VerificationSession

class TestAgent2StateMachine(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.orchestrator = Agent2Orchestrator()

    @patch("app.agent.agent2_orchestrator.SessionLocal")
    async def test_resumes_from_interrupted_state(self, mock_session_local):
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        
        mock_session = MagicMock(spec=Agent2VerificationSession)
        mock_session.id = "session_123"
        mock_session.status = "PHASE2_SYNTHESIS"
        
        self.orchestrator.get_or_create_session = MagicMock(return_value=mock_session)
        
        # Mock the individual phase methods
        self.orchestrator.rank_card = MagicMock()
        self.orchestrator.verify_phase1 = MagicMock()
        self.orchestrator.synthesize_business = MagicMock()
        self.orchestrator.discover_people = MagicMock()
        self.orchestrator.finalize_verification_and_sync = MagicMock()
        
        # Setup side effects to simulate state progression
        async def mock_discover(*args, **kwargs):
            mock_session.status = "PERSON_MATCHING"
            
        async def mock_finalize(*args, **kwargs):
            mock_session.status = "POSTGRES_SYNC_PENDING"
            return {"is_verified": True}
            
        self.orchestrator.discover_people.side_effect = mock_discover
        self.orchestrator.finalize_verification_and_sync.side_effect = mock_finalize

        result = await self.orchestrator.execute_full_verification("doc_123")
        
        # Assertions
        self.assertEqual(result["status"], "success")
        
        # Since it started at PHASE2_SYNTHESIS, rank_card and verify_phase1 should NOT be called!
        self.orchestrator.rank_card.assert_not_called()
        self.orchestrator.verify_phase1.assert_not_called()
        
        # Subsequent phases should be called
        self.orchestrator.discover_people.assert_called_once()
        self.orchestrator.finalize_verification_and_sync.assert_called_once()

if __name__ == "__main__":
    unittest.main()
