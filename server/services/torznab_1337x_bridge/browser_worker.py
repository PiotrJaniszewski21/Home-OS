from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress

LOG = logging.getLogger("homeos.1337x_bridge")

BROWSER_IDLE_SECONDS = max(0, int(os.getenv("BROWSER_IDLE_SECONDS", "900")))
BROWSER_MAX_AGE_SECONDS = max(0, int(os.getenv("BROWSER_MAX_AGE_SECONDS", "21600")))
BROWSER_REAPER_INTERVAL_SECONDS = max(
    1,
    int(os.getenv("BROWSER_REAPER_INTERVAL_SECONDS", "30")),
)
NAVIGATION_TIMEOUT_SECONDS = int(os.getenv("NAVIGATION_TIMEOUT_SECONDS", "120"))
CHALLENGE_AUTO_WAIT_SECONDS = max(
    0,
    int(os.getenv("CHALLENGE_AUTO_WAIT_SECONDS", "15")),
)
CHALLENGE_TITLES = {"Just a moment...", "Attention Required! | Cloudflare"}


class BrowserWorker:
    def __init__(
        self,
        *,
        idle_seconds: int = BROWSER_IDLE_SECONDS,
        max_age_seconds: int = BROWSER_MAX_AGE_SECONDS,
        reaper_interval_seconds: int = BROWSER_REAPER_INTERVAL_SECONDS,
        challenge_auto_wait_seconds: int = CHALLENGE_AUTO_WAIT_SECONDS,
    ) -> None:
        self._manager = None
        self._browser = None
        self._context = None
        self._page = None
        self._contexts: dict[str, object] = {}
        self._pages: dict[str, object] = {}
        self._lock = asyncio.Lock()
        self._idle_seconds = idle_seconds
        self._max_age_seconds = max_age_seconds
        self._reaper_interval_seconds = reaper_interval_seconds
        self._challenge_auto_wait_seconds = challenge_auto_wait_seconds
        self._started_at: float | None = None
        self._last_used_at: float | None = None
        self._reaper_task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        return self._browser is not None

    async def start_monitor(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(
                self._reap_idle_browser(),
                name="bridge-browser-reaper",
            )

    async def start(self) -> None:
        if self._browser is not None:
            return
        from invisible_playwright.async_api import InvisiblePlaywright

        manager = InvisiblePlaywright(
            headless=True,
            humanize=True,
            locale="en-GB",
            timezone="Europe/London",
            extra_prefs={"network.dns.disableIPv6": True},
        )
        browser = await manager.__aenter__()
        try:
            context = await browser.new_context()
            page = await context.new_page()
        except Exception:
            await manager.__aexit__(None, None, None)
            raise
        self._manager = manager
        self._browser = browser
        self._context = context
        self._page = page
        self._contexts["1337x"] = self._context
        self._pages["1337x"] = self._page
        now = time.monotonic()
        self._started_at = now
        self._last_used_at = now
        LOG.info("Invisible Playwright browser started")

    async def stop(self) -> None:
        reaper_task = self._reaper_task
        self._reaper_task = None
        if reaper_task is not None:
            reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await reaper_task
        async with self._lock:
            await self._stop_browser("service shutdown")

    async def _stop_browser(self, reason: str) -> None:
        manager = self._manager
        self._manager = None
        self._browser = None
        self._context = None
        self._page = None
        self._contexts = {}
        self._pages = {}
        self._started_at = None
        self._last_used_at = None
        if manager is not None:
            await manager.__aexit__(None, None, None)
            LOG.info("Invisible Playwright browser stopped: %s", reason)

    def _recycle_reason(self, now: float) -> str | None:
        if self._browser is None:
            return None
        if (
            self._idle_seconds
            and self._last_used_at is not None
            and now - self._last_used_at >= self._idle_seconds
        ):
            return "idle timeout"
        if (
            self._max_age_seconds
            and self._started_at is not None
            and now - self._started_at >= self._max_age_seconds
        ):
            return "maximum browser age"
        return None

    async def _reap_idle_browser(self) -> None:
        while True:
            await asyncio.sleep(self._reaper_interval_seconds)
            async with self._lock:
                reason = self._recycle_reason(time.monotonic())
                if reason is not None:
                    await self._stop_browser(reason)

    async def _page_for(self, key: str):
        page = self._pages.get(key)
        if page is not None:
            return page
        if self._browser is None:
            await self.start()
        if self._browser is None:
            raise RuntimeError("browser failed to start")
        context = await self._browser.new_context()
        page = await context.new_page()
        self._contexts[key] = context
        self._pages[key] = page
        return page

    async def _restart_page(self, key: str):
        context = self._contexts.pop(key, None)
        self._pages.pop(key, None)
        if key == "1337x":
            self._context = None
            self._page = None
        if context is not None:
            await context.close()
        page = await self._page_for(key)
        if key == "1337x":
            self._context = self._contexts[key]
            self._page = page
        return page

    @staticmethod
    async def _is_challenge(page) -> bool:
        title = await page.title()
        if title in CHALLENGE_TITLES:
            return True
        content = (await page.content()).lower()
        return "cf-chl-" in content or "verify you are human" in content

    async def _wait_for_challenge_clear(self, page) -> bool:
        deadline = time.monotonic() + self._challenge_auto_wait_seconds
        while True:
            try:
                if not await self._is_challenge(page):
                    return True
            except Exception:
                # Cloudflare often navigates between the title and content calls.
                pass
            if time.monotonic() >= deadline:
                return False
            await page.wait_for_timeout(500)

    async def _solve_challenge(
        self,
        page,
        *,
        timeout_seconds: int = NAVIGATION_TIMEOUT_SECONDS,
    ) -> None:
        if await self._wait_for_challenge_clear(page):
            LOG.info("Cloudflare challenge cleared automatically")
            return
        from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

        LOG.info("Cloudflare challenge detected; starting browser solver")
        async with ClickSolver(
            framework=FrameworkType.PLAYWRIGHT,
            page=page,
            max_attempts=90,
            attempt_delay=1,
        ) as solver:
            await asyncio.wait_for(
                solver.solve_captcha(
                    captcha_container=page,
                    captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL,
                    wait_checkbox_attempts=1,
                    wait_checkbox_delay=0.5,
                ),
                timeout=timeout_seconds,
            )
        if not await self._wait_for_challenge_clear(page):
            raise RuntimeError("Cloudflare challenge did not clear")
        LOG.info("Cloudflare challenge solved")

    async def _navigate(
        self,
        page,
        url: str,
        *,
        timeout_seconds: int,
    ) -> str:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_seconds * 1000,
        )
        if response is not None and response.status >= 500:
            raise RuntimeError(f"upstream returned HTTP {response.status}")
        if await self._is_challenge(page):
            await self._solve_challenge(page, timeout_seconds=timeout_seconds)
        await page.wait_for_timeout(750)
        return await page.content()

    async def fetch(
        self,
        url: str,
        *,
        page_key: str = "1337x",
        timeout_seconds: int = NAVIGATION_TIMEOUT_SECONDS,
    ) -> str:
        await self.start_monitor()
        async with self._lock:
            reason = self._recycle_reason(time.monotonic())
            if reason is not None:
                await self._stop_browser(reason)
            page = await self._page_for(page_key)
            try:
                for attempt in range(2):
                    try:
                        return await self._navigate(
                            page,
                            url,
                            timeout_seconds=timeout_seconds,
                        )
                    except Exception:
                        if attempt:
                            raise
                        LOG.exception(
                            "Browser navigation failed for %s; restarting its context once",
                            page_key,
                        )
                        page = await self._restart_page(page_key)
                raise RuntimeError("browser navigation failed")
            finally:
                self._last_used_at = time.monotonic()
