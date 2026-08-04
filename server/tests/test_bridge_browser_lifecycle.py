import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "services" / "torznab_1337x_bridge"))

from browser_worker import BrowserWorker


class FakeManager:
    def __init__(self) -> None:
        self.closed = False

    async def __aexit__(self, *_args) -> None:
        self.closed = True


class AutoClearingPage:
    def __init__(self) -> None:
        self.title_calls = 0

    async def title(self) -> str:
        self.title_calls += 1
        if self.title_calls == 1:
            return "Just a moment..."
        return "Search results"

    async def content(self) -> str:
        return "<html><title>Search results</title></html>"

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class BrowserWorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_does_not_start_browser(self):
        worker = BrowserWorker(
            idle_seconds=1,
            max_age_seconds=10,
            reaper_interval_seconds=1,
        )

        await worker.start_monitor()

        self.assertFalse(worker.ready)
        await worker.stop()

    async def test_idle_browser_is_closed(self):
        worker = BrowserWorker(
            idle_seconds=1,
            max_age_seconds=10,
            reaper_interval_seconds=1,
        )
        manager = FakeManager()
        worker._manager = manager
        worker._browser = object()
        worker._started_at = time.monotonic()
        worker._last_used_at = time.monotonic() - 2

        await worker.start_monitor()
        await asyncio.sleep(1.1)

        self.assertFalse(worker.ready)
        self.assertTrue(manager.closed)
        await worker.stop()

    def test_maximum_age_requests_recycle(self):
        worker = BrowserWorker(idle_seconds=60, max_age_seconds=10)
        worker._browser = object()
        worker._started_at = 10
        worker._last_used_at = 19

        self.assertEqual(worker._recycle_reason(21), "maximum browser age")

    async def test_automatic_cloudflare_handoff_clears_without_solver(self):
        worker = BrowserWorker(challenge_auto_wait_seconds=1)
        page = AutoClearingPage()

        await worker._solve_challenge(page)

        self.assertEqual(page.title_calls, 2)


if __name__ == "__main__":
    unittest.main()
