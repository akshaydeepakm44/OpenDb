"""
OpenDB v2 Hardening Unit & Integration Test Suite
Validates:
1. Multi-part public suffix canonical domain extraction
2. Resource Governor sustained CPU windowing & Queue Hysteresis
3. Distributed Slot Manager independent pools (standard=2, deep=1)
4. Fail-closed production behavior when Redis is unavailable
5. Durable MinIO Artifact Outbox persistence
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.crawler.url_discovery import url_discovery
from app.safety.resource_governor import ResourceGovernor
from app.crawler.distributed_slot_manager import DistributedSlotManager


class TestDomainCanonicalization(unittest.TestCase):
    """Test public-suffix-aware registrable domain extraction."""

    def test_canonical_domains(self):
        cases = {
            "https://www.example.com/path": "example.com",
            "http://sub.domain.co.uk/page?x=1": "domain.co.uk",
            "https://deep.sub.service.gov.uk/": "service.gov.uk",
            "https://test.com.au/about": "test.com.au",
            "https://company.org/team": "company.org",
            "https://www.opendb.io": "opendb.io",
            "https://app.dev.internal.co.jp": "internal.co.jp",
        }
        for url, expected in cases.items():
            result = url_discovery.get_canonical_registrable_domain(url)
            self.assertEqual(result, expected, f"Failed for {url}: got {result}, expected {expected}")


class TestResourceGovernorSustainedCPU(unittest.TestCase):
    """Test sustained CPU windowing to ensure transient Chromium startup spikes do not pause system."""

    def setUp(self):
        self.gov = ResourceGovernor()

    def test_single_spike_does_not_pause(self):
        # 1 sample of 96% (transient Chromium startup spike) should NOT trigger pause or emergency
        self.gov._cpu_history.clear()
        self.gov._cpu_history.append(96.0)
        pressure, paused, emergency = self.gov._evaluate_sustained_cpu()
        self.assertFalse(emergency, "Single sample should not trigger EMERGENCY")
        self.assertFalse(paused, "Single sample should not trigger PAUSED")
        self.assertFalse(pressure, "Single sample should not trigger PRESSURE")

    def test_sustained_pressure(self):
        # 2 samples >= 85% triggers PRESSURE
        self.gov._cpu_history.clear()
        self.gov._cpu_history.extend([86.0, 88.0])
        pressure, paused, emergency = self.gov._evaluate_sustained_cpu()
        self.assertTrue(pressure, "2 samples >=85% must trigger PRESSURE")
        self.assertFalse(paused)
        self.assertFalse(emergency)

    def test_sustained_paused(self):
        # 3 samples >= 90% triggers PAUSED
        self.gov._cpu_history.clear()
        self.gov._cpu_history.extend([91.0, 92.0, 91.5])
        pressure, paused, emergency = self.gov._evaluate_sustained_cpu()
        self.assertTrue(paused, "3 samples >=90% must trigger PAUSED")
        self.assertFalse(emergency)

    def test_sustained_emergency(self):
        # 2 samples >= 95% triggers EMERGENCY
        self.gov._cpu_history.clear()
        self.gov._cpu_history.extend([96.0, 97.0])
        pressure, paused, emergency = self.gov._evaluate_sustained_cpu()
        self.assertTrue(emergency, "2 samples >=95% must trigger EMERGENCY")

    def test_queue_hysteresis(self):
        # Discovery queue: pause at >=100, unpause at <=60
        # Mock metrics with high depth
        with patch.object(self.gov, "get_system_metrics") as mock_metrics:
            mock_metrics.return_value = {
                "memory_percent": 50.0,
                "cpu_percent": 30.0,
                "chromium_process_count": 1,
                "discovery_queue_depth": 105,
                "verification_queue_depth": 10,
            }
            decision = self.gov.evaluate_circuit_breaker()
            self.assertTrue(self.gov.discovery_queue_paused)
            self.assertFalse(decision["can_discover"], "Discovery must pause at depth >= 100")
            self.assertTrue(decision["can_crawl"], "Existing crawlers must continue to drain queue")

            # At depth 75 (between 60 and 100), still paused due to hysteresis
            mock_metrics.return_value["discovery_queue_depth"] = 75
            decision = self.gov.evaluate_circuit_breaker()
            self.assertTrue(self.gov.discovery_queue_paused)
            self.assertFalse(decision["can_discover"], "Discovery must remain paused at depth 75")

            # At depth 55 (<= 60), unpauses
            mock_metrics.return_value["discovery_queue_depth"] = 55
            decision = self.gov.evaluate_circuit_breaker()
            self.assertFalse(self.gov.discovery_queue_paused)
            self.assertTrue(decision["can_discover"], "Discovery must unpause at depth <= 60")


class TestDistributedSlotManager(unittest.TestCase):
    """Test distributed slot pools and fail-closed production semantics."""

    def setUp(self):
        self.mgr = DistributedSlotManager()

    def test_pool_separation_configs(self):
        std_key, std_max = self.mgr._get_pool_config("standard")
        deep_key, deep_max = self.mgr._get_pool_config("deep")
        self.assertEqual(std_key, "opendb:crawler:slots:standard")
        self.assertEqual(std_max, 2)
        self.assertEqual(deep_key, "opendb:crawler:slots:deep")
        self.assertEqual(deep_max, 1)

    def test_fail_closed_in_production_when_redis_none(self):
        """In production mode, Redis being None MUST raise PAUSED — REDIS_UNAVAILABLE."""
        with patch.object(self.mgr, "is_production", return_value=True):
            with patch("app.crawler.distributed_slot_manager.get_redis", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    self.mgr.acquire_slot_sync("standard", wait_timeout=1.0)
                self.assertIn("REDIS_UNAVAILABLE", str(ctx.exception))

    def test_fail_closed_in_production_when_redis_errors(self):
        """In production mode, Redis error during pipeline MUST raise PAUSED — REDIS_UNAVAILABLE."""
        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = ConnectionError("Redis connection lost")
        with patch.object(self.mgr, "is_production", return_value=True):
            with patch("app.crawler.distributed_slot_manager.get_redis", return_value=mock_redis):
                with self.assertRaises(RuntimeError) as ctx:
                    self.mgr.acquire_slot_sync("standard", wait_timeout=1.0)
                self.assertIn("REDIS_UNAVAILABLE", str(ctx.exception))

    def test_distributed_slot_async_guaranteed_release(self):
        """Verify context manager guarantees release even if an exception occurs."""
        async def run_test():
            released = False
            mock_redis = MagicMock()
            pipe = MagicMock()
            pipe.execute.return_value = (0, 0)
            mock_redis.pipeline.return_value = pipe
            mock_redis.zadd.return_value = True
            mock_redis.zcard.return_value = 1

            def mock_zrem(key, member):
                nonlocal released
                released = True

            mock_redis.zrem.side_effect = mock_zrem

            with patch("app.crawler.distributed_slot_manager.get_redis", return_value=mock_redis):
                try:
                    async with self.mgr.distributed_crawl_slot("standard", wait_timeout=2.0) as lease:
                        self.assertIsNotNone(lease)
                        raise ValueError("Simulated crawler crash")
                except ValueError:
                    pass

                self.assertTrue(released, "Slot must be released even when task crashes")

        asyncio.run(run_test())


class TestMinIODurableOutbox(unittest.TestCase):
    """Test durable staging to PostgreSQL ArtifactOutbox when MinIO is unavailable."""

    def test_staged_to_outbox_when_minio_none(self):
        from app.storage.file_storage import file_storage
        with patch.object(file_storage, "client", None):
            with patch.object(file_storage, "is_degraded", True):
                with patch("app.persistence.database.SessionLocal") as mock_session:
                    mock_db = MagicMock()
                    mock_session.return_value.__enter__.return_value = mock_db

                    c_hash, rel_path = file_storage.save_raw_page("<html>Test</html>")
                    self.assertTrue(rel_path.startswith("s3://opendb/"))
                    # Assert ArtifactOutbox record was added to db
                    self.assertTrue(mock_db.add.called)
                    added_item = mock_db.add.call_args[0][0]
                    self.assertEqual(added_item.status, "PENDING")


if __name__ == "__main__":
    unittest.main()
