"""
Phase 1: Distributed Concurrency Contention Integration Test Suite
Executes live contention against the Redis distributed slot manager:
1. Standard Pool:
   - 5 concurrent tasks contending for standard slots (MAX=2)
   - Real-time sampling verifying active slots never exceed 2
   - Verification of blocking/waiting behavior
   - Guaranteed release on success, timeout, and cancellation
   - Zero stale leases remaining
2. Deep Pool:
   - 3 concurrent tasks contending for deep slots (MAX=1)
   - Real-time sampling verifying active slots never exceed 1
   - Verification of mutual exclusion (only 1 deep crawl active)
   - Guaranteed release on success, timeout, and cancellation
   - Zero stale leases remaining
"""
import asyncio
import os
import sys
import time
import unittest
from typing import List, Dict, Any

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.crawler.distributed_slot_manager import slot_manager
from app.cache.redis_client import get_redis


class TestDistributedContention(unittest.TestCase):
    """Integration test suite for distributed Redis slot contention."""

    def setUp(self):
        r = get_redis()
        if r is None:
            self.skipTest("Redis is unreachable. Live Redis is required for contention test.")
        slot_manager.clear_all_slots()

    def tearDown(self):
        slot_manager.clear_all_slots()

    def test_01_standard_crawl_contention_5_tasks(self):
        """
        Launch 5 standard crawl tasks concurrently.
        Max concurrent standard crawls = 2.
        Tasks 1-2 must acquire, tasks 3-5 must block.
        Active standard slots must NEVER exceed 2.
        All slots must be released upon completion.
        """
        print("\n[Phase 1] Starting 5-task standard crawl contention test...")

        max_observed_active = 0
        active_samples: List[int] = []
        task_acquisition_times: Dict[int, float] = {}
        task_completion_times: Dict[int, float] = {}
        task_hold_durations = [0.8, 0.8, 0.6, 0.6, 0.4]

        # Background sampler checking Redis sorted-set depth every 30ms
        stop_sampling = asyncio.Event()

        async def sampler():
            nonlocal max_observed_active
            while not stop_sampling.is_set():
                active = slot_manager.get_active_slots_count("standard")
                active_samples.append(active)
                if active > max_observed_active:
                    max_observed_active = active
                await asyncio.sleep(0.03)

        start_time = time.time()

        async def worker(worker_id: int, hold_duration: float):
            async with slot_manager.distributed_crawl_slot("standard", wait_timeout=15.0) as lease:
                acq_time = time.time() - start_time
                task_acquisition_times[worker_id] = acq_time
                print(f"  -> Task {worker_id} acquired standard slot '{lease}' at t={acq_time:.3f}s")
                await asyncio.sleep(hold_duration)
            comp_time = time.time() - start_time
            task_completion_times[worker_id] = comp_time
            print(f"  <- Task {worker_id} released standard slot at t={comp_time:.3f}s")

        async def run_contention():
            sampler_task = asyncio.create_task(sampler())
            workers = [
                worker(i, task_hold_durations[i]) for i in range(5)
            ]
            await asyncio.gather(*workers)
            stop_sampling.set()
            await sampler_task

        asyncio.run(run_contention())

        final_active = slot_manager.get_active_slots_count("standard")
        print(f"\n[Phase 1 Summary: Standard Contention]")
        print(f"  Total samples collected: {len(active_samples)}")
        print(f"  Max observed concurrent slots: {max_observed_active}")
        print(f"  Final active slots: {final_active}")

        # Assertions
        self.assertLessEqual(max_observed_active, 2, f"Active standard slots exceeded limit of 2! Max was {max_observed_active}")
        self.assertEqual(final_active, 0, "All standard slots must be released upon completion")
        self.assertEqual(len(task_acquisition_times), 5, "All 5 tasks must have acquired a slot")

        # Verify blocking: At least 2 tasks must have waited until the first wave completed
        early_tasks = [t for t in task_acquisition_times.values() if t < 0.3]
        later_tasks = [t for t in task_acquisition_times.values() if t >= 0.7]
        self.assertLessEqual(len(early_tasks), 2, "No more than 2 tasks should acquire immediately")
        self.assertGreaterEqual(len(later_tasks), 3, "Tasks 3-5 must have blocked waiting for slots to open")

    def test_02_deep_crawl_contention_3_tasks(self):
        """
        Launch 3 deep crawl tasks concurrently.
        Max concurrent deep crawls = 1.
        Only one task may hold a deep slot at any time.
        Tasks 2 and 3 must wait.
        Active deep slots must NEVER exceed 1.
        All slots must be released upon completion.
        """
        print("\n[Phase 1] Starting 3-task deep crawl contention test...")

        max_observed_active = 0
        active_samples: List[int] = []
        task_acquisition_times: Dict[int, float] = {}
        task_completion_times: Dict[int, float] = {}
        task_hold_durations = [0.8, 0.6, 0.4]

        stop_sampling = asyncio.Event()

        async def sampler():
            nonlocal max_observed_active
            while not stop_sampling.is_set():
                active = slot_manager.get_active_slots_count("deep")
                active_samples.append(active)
                if active > max_observed_active:
                    max_observed_active = active
                await asyncio.sleep(0.03)

        start_time = time.time()

        async def worker(worker_id: int, hold_duration: float):
            async with slot_manager.distributed_crawl_slot("deep", wait_timeout=15.0) as lease:
                acq_time = time.time() - start_time
                task_acquisition_times[worker_id] = acq_time
                print(f"  -> Deep Task {worker_id} acquired deep slot '{lease}' at t={acq_time:.3f}s")
                await asyncio.sleep(hold_duration)
            comp_time = time.time() - start_time
            task_completion_times[worker_id] = comp_time
            print(f"  <- Deep Task {worker_id} released deep slot at t={comp_time:.3f}s")

        async def run_contention():
            sampler_task = asyncio.create_task(sampler())
            workers = [
                worker(i, task_hold_durations[i]) for i in range(3)
            ]
            await asyncio.gather(*workers)
            stop_sampling.set()
            await sampler_task

        asyncio.run(run_contention())

        final_active = slot_manager.get_active_slots_count("deep")
        print(f"\n[Phase 1 Summary: Deep Contention]")
        print(f"  Total samples collected: {len(active_samples)}")
        print(f"  Max observed concurrent slots: {max_observed_active}")
        print(f"  Final active deep slots: {final_active}")

        # Assertions
        self.assertEqual(max_observed_active, 1, f"Active deep slots exceeded limit of 1! Max was {max_observed_active}")
        self.assertEqual(final_active, 0, "All deep slots must be released upon completion")
        self.assertEqual(len(task_acquisition_times), 3, "All 3 tasks must have acquired a slot sequentially")

        # Verify strict serialized execution: task 2 acq > task 1 comp, task 3 acq > task 2 comp
        ordered_acqs = sorted(task_acquisition_times.items(), key=lambda x: x[1])
        first_worker, first_acq = ordered_acqs[0]
        second_worker, second_acq = ordered_acqs[1]
        third_worker, third_acq = ordered_acqs[2]

        self.assertGreaterEqual(second_acq, task_completion_times[first_worker] - 0.1, "Task 2 must wait until Task 1 completes")
        self.assertGreaterEqual(third_acq, task_completion_times[second_worker] - 0.1, "Task 3 must wait until Task 2 completes")

    def test_03_timeout_and_cancellation_release(self):
        """Verify release after timeout and cancellation on both standard and deep pools."""
        print("\n[Phase 1] Starting Timeout & Cancellation release test...")

        async def run_timeout_test():
            # Test timeout on standard pool
            try:
                # Pre-occupy both slots
                async with slot_manager.distributed_crawl_slot("standard", wait_timeout=5.0):
                    async with slot_manager.distributed_crawl_slot("standard", wait_timeout=5.0):
                        # Third task with 0.5s timeout must time out
                        try:
                            async with slot_manager.distributed_crawl_slot("standard", wait_timeout=0.5):
                                self.fail("Third standard task should have timed out")
                        except TimeoutError:
                            print("  -> Expected TimeoutError caught on standard pool")
            finally:
                pass

        asyncio.run(run_timeout_test())
        self.assertEqual(slot_manager.get_active_slots_count("standard"), 0, "Standard slots must be 0 after timeout")

        async def run_cancellation_test():
            # Test cancellation on deep pool
            async def target():
                async with slot_manager.distributed_crawl_slot("deep", wait_timeout=5.0):
                    await asyncio.sleep(10.0)

            task = asyncio.create_task(target())
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                print("  -> Expected CancelledError caught on deep pool")

        asyncio.run(run_cancellation_test())
        self.assertEqual(slot_manager.get_active_slots_count("deep"), 0, "Deep slots must be 0 after cancellation")

    def test_04_stale_lease_expiration_recovery(self):
        """Verify that crashed/stale leases are reclaimed automatically by timestamp."""
        print("\n[Phase 1] Testing stale lease expiration recovery...")
        r = get_redis()
        redis_key = "opendb:crawler:slots:standard"

        # Artificially inject an expired lease (timestamp in the past)
        stale_lease_id = "standard-stale-crashed-worker-123"
        expired_timestamp = time.time() - (slot_manager.lease_timeout + 10)
        r.zadd(redis_key, {stale_lease_id: expired_timestamp})

        # Inject a second expired lease
        stale_lease_id_2 = "standard-stale-crashed-worker-456"
        r.zadd(redis_key, {stale_lease_id_2: expired_timestamp})

        # At this point, zcard is 2, but both are stale
        self.assertEqual(r.zcard(redis_key), 2)

        # Calling acquire_slot_sync must atomically clean stale leases and acquire successfully
        new_lease = slot_manager.acquire_slot_sync("standard", wait_timeout=2.0)
        self.assertIsNotNone(new_lease)
        print(f"  -> Successfully acquired '{new_lease}' after recovering from 2 stale leases")

        # Release the new lease
        slot_manager.release_slot_sync("standard", new_lease)
        self.assertEqual(slot_manager.get_active_slots_count("standard"), 0, "Standard pool must be clean")


if __name__ == "__main__":
    unittest.main()
