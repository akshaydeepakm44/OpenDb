import unittest
from unittest.mock import patch, MagicMock

from app.worker.tasks import _safe_dispatch

class TestAgent2Dispatch(unittest.TestCase):

    @patch("app.worker.tasks._has_active_celery_worker")
    def test_agent2_fails_closed_without_celery(self, mock_has_worker):
        # Production mode must fail closed for agent2 tasks if no worker exists
        mock_has_worker.return_value = False
        
        # Mock task function
        def tasks_agent2_process_card():
            pass
        
        # In testing environments, we might want to override settings, but _safe_dispatch checks settings.OPENDB_ENV
        with patch("app.worker.tasks.settings") as mock_settings:
            mock_settings.OPENDB_ENV = "production"
            
            with self.assertRaises(RuntimeError) as context:
                _safe_dispatch(tasks_agent2_process_card)
                
            self.assertIn("QUEUE_FAILED: Agent 2 tasks must execute on dedicated Celery verification worker", str(context.exception))

if __name__ == "__main__":
    unittest.main()
