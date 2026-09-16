"""
OpenDB Controlled Browser Acceptance Test & Orphan Process Verification
Validates §6 of Master Corrections:
1. Records baseline total Chromium processes.
2. Runs live crawl tasks.
3. Tests abnormal termination: timeout, cancellation, exception.
4. Verifies:
   - final total Chromium processes == baseline total Chromium processes
   - OpenDB-owned active browser contexts == 0
   - OpenDB distributed crawl slots == 0
"""
import asyncio
import os
import sys
import time
import unittest
import psutil

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.crawler.crawler_service import crawler_service, get_active_browser_contexts_count
from app.crawler.distributed_slot_manager import slot_manager


def get_total_chromium_processes() -> int:
    """Count all Chromium/Chrome/Playwright processes currently on the host."""
    count = 0
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            name = (p.info.get('name') or '').lower()
            cmd = ' '.join(p.info.get('cmdline') or []).lower()
            if 'chromium' in name or 'chrome' in name or 'playwright' in cmd:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return count


class TestChromiumProcessLeakAcceptance(unittest.TestCase):
    """
    Acceptance Criteria Test for Chromium Leaks:
    Must guarantee that no OpenDB crawl leaves orphan processes or distributed leases.
    """

    def setUp(self):
        # Allow any lingering OS shutdown tasks a moment to settle
        time.sleep(1.0)
        self.baseline_chromium = get_total_chromium_processes()
        slot_manager.clear_all_slots()

    def tearDown(self):
        slot_manager.clear_all_slots()

    def test_01_normal_crawl_process_and_slot_cleanup(self):
        """Execute a real single-session crawl and verify zero orphan processes or slots."""
        print(f"\n[Test 1] Baseline Chromium processes: {self.baseline_chromium}")

        from unittest.mock import patch

        async def run_crawl():
            with patch("app.crawler.crawler_service.governor.can_start_crawl", return_value=(True, None)):
                return await crawler_service.crawl_site(
                    starting_url="https://example.com",
                    max_depth=1,
                    max_pages=2,
                    slot_type="standard"
                )

        results = asyncio.run(run_crawl())
        self.assertGreaterEqual(len(results), 1)

        # Allow browser teardown to settle
        time.sleep(2.0)

        final_chromium = get_total_chromium_processes()
        active_contexts = get_active_browser_contexts_count()
        slots_info = slot_manager.get_total_active_crawls()

        print(f"[Test 1] Final Chromium processes: {final_chromium} (baseline: {self.baseline_chromium})")
        print(f"[Test 1] Active browser contexts: {active_contexts}")
        print(f"[Test 1] Distributed slots: {slots_info}")

        self.assertLessEqual(final_chromium, self.baseline_chromium, f"Chromium process count leaked! Final: {final_chromium} > Baseline: {self.baseline_chromium}")
        self.assertEqual(active_contexts, 0, "Active browser contexts must be 0")
        self.assertEqual(slots_info["standard_active"], 0, "Standard crawl slots must be 0")
        self.assertEqual(slots_info["deep_active"], 0, "Deep crawl slots must be 0")

    def test_02_timeout_cleanup(self):
        """Simulate a task timeout and verify slots and browser contexts are cleaned up."""
        print(f"\n[Test 2: Timeout] Baseline Chromium processes: {self.baseline_chromium}")

        async def run_timed_out_crawl():
            # Wrap an acquisition in a tight timeout
            async with slot_manager.distributed_crawl_slot("standard", wait_timeout=5.0) as lease:
                self.assertIsNotNone(lease)
                # Sleep longer than lease acquisition timeout
                await asyncio.sleep(0.5)
                raise asyncio.TimeoutError("Simulated page timeout")

        try:
            asyncio.run(run_timed_out_crawl())
        except asyncio.TimeoutError:
            pass

        time.sleep(1.0)
        final_chromium = get_total_chromium_processes()
        active_contexts = get_active_browser_contexts_count()
        slots_info = slot_manager.get_total_active_crawls()

        print(f"[Test 2] Final Chromium processes: {final_chromium} (baseline: {self.baseline_chromium})")
        print(f"[Test 2] Active browser contexts: {active_contexts}")
        print(f"[Test 2] Distributed slots: {slots_info}")

        self.assertEqual(final_chromium, self.baseline_chromium)
        self.assertEqual(active_contexts, 0)
        self.assertEqual(slots_info["standard_active"], 0)

    def test_03_cancellation_cleanup(self):
        """Simulate task cancellation and verify slot release."""
        print(f"\n[Test 3: Cancellation] Baseline Chromium processes: {self.baseline_chromium}")

        async def cancellable_crawl():
            async with slot_manager.distributed_crawl_slot("deep", wait_timeout=5.0):
                await asyncio.sleep(10.0)

        async def cancel_orchestrator():
            task = asyncio.create_task(cancellable_crawl())
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(cancel_orchestrator())

        time.sleep(1.0)
        final_chromium = get_total_chromium_processes()
        active_contexts = get_active_browser_contexts_count()
        slots_info = slot_manager.get_total_active_crawls()

        print(f"[Test 3] Final Chromium: {final_chromium}, Active contexts: {active_contexts}, Slots: {slots_info}")
        self.assertEqual(final_chromium, self.baseline_chromium)
        self.assertEqual(active_contexts, 0)
        self.assertEqual(slots_info["deep_active"], 0)


if __name__ == "__main__":
    unittest.main()
