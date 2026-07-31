from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core import (
    DETAIL_PATH_RE,
    SearchResult,
    build_caps_xml,
    build_search_query,
    build_search_xml,
    extract_info_hash,
    parse_search_results,
    search_path,
)
from fastapi import FastAPI, HTTPException, Query
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, Response
from tgx_core import (
    DETAIL_PATH_RE as TGX_DETAIL_PATH_RE,
    TorrentGalaxyResult,
    build_torrentgalaxy_caps_xml,
    build_torrentgalaxy_search_xml,
    parse_torrentgalaxy_results,
    torrentgalaxy_category_path,
    torrentgalaxy_search_path,
)
from uindex_core import (
    UindexResult,
    build_uindex_caps_xml,
    build_uindex_search_xml,
    parse_uindex_results,
    uindex_search_path,
)

LOG = logging.getLogger("homeos.1337x_bridge")
UPSTREAM = os.getenv("UPSTREAM", "https://x1337x.ws").rstrip("/")
UINDEX_UPSTREAM = os.getenv("UINDEX_UPSTREAM", "https://uindex.org").rstrip("/")
TORRENTGALAXY_UPSTREAM = os.getenv(
    "TORRENTGALAXY_UPSTREAM",
    "https://torrentgalaxy.one",
).rstrip("/")
API_KEY = os.getenv("BRIDGE_API_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
SEARCH_CACHE_SECONDS = int(os.getenv("SEARCH_CACHE_SECONDS", "900"))
DETAIL_CACHE_SECONDS = int(os.getenv("DETAIL_CACHE_SECONDS", "86400"))
NAVIGATION_TIMEOUT_SECONDS = int(os.getenv("NAVIGATION_TIMEOUT_SECONDS", "120"))
MAX_SEARCH_PAGES = int(os.getenv("MAX_SEARCH_PAGES", "3"))
DEFAULT_BROWSE_QUERY = os.getenv("DEFAULT_BROWSE_QUERY", "ubuntu").strip()
CHALLENGE_TITLES = {"Just a moment...", "Attention Required! | Cloudflare"}
MAGNET_RE = re.compile(r'href=["\'](magnet:\?[^"\']+)', re.IGNORECASE)


@dataclass
class CacheEntry:
    expires: float
    value: object


class BrowserWorker:
    def __init__(self) -> None:
        self._manager = None
        self._browser = None
        self._context = None
        self._page = None
        self._contexts: dict[str, object] = {}
        self._pages: dict[str, object] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._page is not None:
            return
        from invisible_playwright.async_api import InvisiblePlaywright

        self._manager = InvisiblePlaywright(
            headless=True,
            humanize=True,
            locale="en-GB",
            timezone="Europe/London",
            extra_prefs={"network.dns.disableIPv6": True},
        )
        self._browser = await self._manager.__aenter__()
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._contexts["1337x"] = self._context
        self._pages["1337x"] = self._page
        LOG.info("Invisible Playwright browser started")

    async def stop(self) -> None:
        manager = self._manager
        self._manager = None
        self._browser = None
        self._context = None
        self._page = None
        self._contexts = {}
        self._pages = {}
        if manager is not None:
            await manager.__aexit__(None, None, None)

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

    async def _solve_challenge(self, page) -> None:
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
                timeout=NAVIGATION_TIMEOUT_SECONDS,
            )
        if await self._is_challenge(page):
            raise RuntimeError("Cloudflare challenge did not clear")
        LOG.info("Cloudflare challenge solved")

    async def _navigate(self, page, url: str) -> str:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_SECONDS * 1000,
        )
        if response is not None and response.status >= 500:
            raise RuntimeError(f"upstream returned HTTP {response.status}")
        if await self._is_challenge(page):
            await self._solve_challenge(page)
        await page.wait_for_timeout(750)
        return await page.content()

    async def fetch(self, url: str, *, page_key: str = "1337x") -> str:
        async with self._lock:
            page = await self._page_for(page_key)
            for attempt in range(2):
                try:
                    return await self._navigate(page, url)
                except Exception:
                    if attempt:
                        raise
                    LOG.exception(
                        "Browser navigation failed for %s; restarting its context once",
                        page_key,
                    )
                    page = await self._restart_page(page_key)
            raise RuntimeError("browser navigation failed")


class Bridge:
    def __init__(self) -> None:
        self.browser = BrowserWorker()
        self.search_cache: dict[tuple[str, int], CacheEntry] = {}
        self.magnet_cache: dict[str, CacheEntry] = {}

    async def start(self) -> None:
        await self.browser.start()

    async def stop(self) -> None:
        await self.browser.stop()

    @staticmethod
    def _cached(cache: dict[object, CacheEntry], key: object) -> object | None:
        entry = cache.get(key)
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            cache.pop(key, None)
            return None
        return entry.value

    async def search_page(self, query: str, page: int) -> list[SearchResult]:
        key = (query.casefold(), page)
        cached = self._cached(self.search_cache, key)
        if cached is not None:
            return list(cached)
        path = search_path(query, page)
        document = await self.browser.fetch(f"{UPSTREAM}{path}")
        results = parse_search_results(document)
        if not results and "no result" not in document.casefold():
            raise RuntimeError("1337x returned no parseable result rows")
        self.search_cache[key] = CacheEntry(
            time.monotonic() + SEARCH_CACHE_SECONDS,
            results,
        )
        LOG.info("Parsed %d results from %s", len(results), path)
        return results

    async def search(
        self,
        query: str,
        *,
        offset: int,
        limit: int,
    ) -> list[SearchResult]:
        if not query:
            return []
        first_page = (offset // 20) + 1
        requested = min(max(1, limit), MAX_SEARCH_PAGES * 20)
        pages = min(
            MAX_SEARCH_PAGES,
            max(1, ((offset % 20) + requested + 19) // 20),
        )
        results: list[SearchResult] = []
        for page in range(first_page, first_page + pages):
            page_results = await self.search_page(query, page)
            results.extend(page_results)
            if len(page_results) < 20:
                break
        start = offset % 20
        return results[start : start + requested]

    async def magnet(self, detail_path: str) -> str:
        if not DETAIL_PATH_RE.fullmatch(detail_path):
            raise ValueError("invalid 1337x detail path")
        cached = self._cached(self.magnet_cache, detail_path)
        if cached is not None:
            return str(cached)
        document = await self.browser.fetch(f"{UPSTREAM}{detail_path}")
        match = MAGNET_RE.search(document)
        if match is None:
            raise RuntimeError("torrent detail page did not contain a magnet link")
        magnet = html.unescape(match.group(1))
        self.magnet_cache[detail_path] = CacheEntry(
            time.monotonic() + DETAIL_CACHE_SECONDS,
            magnet,
        )
        return magnet


BRIDGE = Bridge()


class UindexBridge:
    def __init__(self, browser: BrowserWorker) -> None:
        self.browser = browser
        self.search_cache: dict[str, CacheEntry] = {}

    async def search(
        self,
        query: str,
        *,
        offset: int,
        limit: int,
    ) -> list[UindexResult]:
        key = query.casefold()
        cached = Bridge._cached(self.search_cache, key)
        if cached is None:
            path = uindex_search_path(query)
            document = await self.browser.fetch(
                f"{UINDEX_UPSTREAM}{path}",
                page_key="uindex",
            )
            results = parse_uindex_results(document)
            if not results and "no result" not in document.casefold():
                raise RuntimeError("Uindex returned no parseable result rows")
            self.search_cache[key] = CacheEntry(
                time.monotonic() + SEARCH_CACHE_SECONDS,
                results,
            )
            LOG.info("Parsed %d Uindex results from %s", len(results), path)
        else:
            results = list(cached)
        return results[offset : offset + min(limit, 100)]


UINDEX_BRIDGE = UindexBridge(BRIDGE.browser)


class TorrentGalaxyBridge:
    PAGE_SIZE = 50

    def __init__(self, browser: BrowserWorker) -> None:
        self.browser = browser
        self.search_cache: dict[tuple[str, int], CacheEntry] = {}
        self.magnet_cache: dict[str, CacheEntry] = {}

    async def search(
        self,
        query: str,
        *,
        category: int = 0,
        offset: int,
        limit: int,
    ) -> list[TorrentGalaxyResult]:
        page = offset // self.PAGE_SIZE
        source = f"query:{query.casefold()}" if query else f"category:{category}"
        key = (source, page)
        cached = Bridge._cached(self.search_cache, key)
        if cached is None:
            path = (
                torrentgalaxy_search_path(query, page)
                if query
                else torrentgalaxy_category_path(category, page)
            )
            document = await self.browser.fetch(
                f"{TORRENTGALAXY_UPSTREAM}{path}",
                page_key="torrentgalaxy",
            )
            results = parse_torrentgalaxy_results(document)
            if not results and "no result" not in document.casefold():
                raise RuntimeError("TorrentGalaxy returned no parseable result rows")
            self.search_cache[key] = CacheEntry(
                time.monotonic() + SEARCH_CACHE_SECONDS,
                results,
            )
            LOG.info("Parsed %d TorrentGalaxy results from %s", len(results), path)
        else:
            results = list(cached)
        start = offset % self.PAGE_SIZE
        return results[start : start + min(limit, self.PAGE_SIZE)]

    async def magnet(self, detail_path: str) -> str:
        if not TGX_DETAIL_PATH_RE.fullmatch(detail_path):
            raise ValueError("invalid TorrentGalaxy detail path")
        cached = Bridge._cached(self.magnet_cache, detail_path)
        if cached is not None:
            return str(cached)
        document = await self.browser.fetch(
            f"{TORRENTGALAXY_UPSTREAM}{detail_path}",
            page_key="torrentgalaxy",
        )
        match = MAGNET_RE.search(document)
        if match is None:
            raise RuntimeError(
                "TorrentGalaxy detail page did not contain a magnet link"
            )
        magnet = html.unescape(match.group(1))
        self.magnet_cache[detail_path] = CacheEntry(
            time.monotonic() + DETAIL_CACHE_SECONDS,
            magnet,
        )
        return magnet


TORRENTGALAXY_BRIDGE = TorrentGalaxyBridge(BRIDGE.browser)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await BRIDGE.start()
    try:
        yield
    finally:
        await BRIDGE.stop()


app = FastAPI(
    title="Home OS 1337x Torznab Bridge",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


def _authorize(api_key: str) -> None:
    if API_KEY and not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(status_code=401, detail="invalid API key")


def _xml_response(content: bytes) -> Response:
    return Response(content=content, media_type="application/xml; charset=utf-8")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "browserReady": BRIDGE.browser._page is not None,
            "upstream": UPSTREAM,
            "torrentGalaxyUpstream": TORRENTGALAXY_UPSTREAM,
        }
    )


@app.get("/api")
async def torznab_api(
    request: FastAPIRequest,
    t: str = Query(default="search"),
    q: str = Query(default=""),
    apikey: str = Query(default=""),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    season: str = Query(default=""),
    ep: str = Query(default=""),
    imdbid: str = Query(default=""),
) -> Response:
    _authorize(apikey)
    if t.casefold() == "caps":
        return _xml_response(build_caps_xml())
    query = build_search_query(
        {
            "q": q,
            "season": season,
            "ep": ep,
            "imdbid": imdbid,
        }
    )
    if not query:
        query = DEFAULT_BROWSE_QUERY
    try:
        results = await BRIDGE.search(query, offset=offset, limit=limit)
    except Exception as exc:
        LOG.exception("Search failed for query %r", query)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    base_url = str(request.base_url).rstrip("/")
    return _xml_response(
        build_search_xml(
            results,
            base_url=base_url or PUBLIC_BASE_URL,
            api_key=API_KEY,
            site_url=UPSTREAM,
            offset=offset,
            total=offset + len(results),
        )
    )


@app.get("/uindex/api")
async def uindex_torznab_api(
    t: str = Query(default="search"),
    q: str = Query(default=""),
    apikey: str = Query(default=""),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    season: str = Query(default=""),
    ep: str = Query(default=""),
) -> Response:
    _authorize(apikey)
    if t.casefold() == "caps":
        return _xml_response(build_uindex_caps_xml())
    query = build_search_query({"q": q, "season": season, "ep": ep})
    if not query:
        query = DEFAULT_BROWSE_QUERY
    try:
        results = await UINDEX_BRIDGE.search(query, offset=offset, limit=limit)
    except Exception as exc:
        LOG.exception("Uindex search failed for query %r", query)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _xml_response(
        build_uindex_search_xml(
            results,
            site_url=UINDEX_UPSTREAM,
            offset=offset,
        )
    )


@app.get("/torrentgalaxy/api")
async def torrentgalaxy_torznab_api(
    request: FastAPIRequest,
    t: str = Query(default="search"),
    q: str = Query(default=""),
    apikey: str = Query(default=""),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    season: str = Query(default=""),
    ep: str = Query(default=""),
    imdbid: str = Query(default=""),
    cat: str = Query(default=""),
) -> Response:
    _authorize(apikey)
    if t.casefold() == "caps":
        return _xml_response(build_torrentgalaxy_caps_xml())
    query = build_search_query(
        {
            "q": q,
            "season": season,
            "ep": ep,
            "imdbid": imdbid,
        }
    )
    categories = [
        int(value)
        for value in cat.split(",")
        if value.strip().isdigit()
    ]
    category = categories[0] if categories else 0
    if not query and not category:
        query = DEFAULT_BROWSE_QUERY
    try:
        results = await TORRENTGALAXY_BRIDGE.search(
            query,
            category=category,
            offset=offset,
            limit=limit,
        )
    except Exception as exc:
        LOG.exception("TorrentGalaxy search failed for query %r", query)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    base_url = str(request.base_url).rstrip("/")
    return _xml_response(
        build_torrentgalaxy_search_xml(
            results,
            base_url=base_url or PUBLIC_BASE_URL,
            api_key=API_KEY,
            site_url=TORRENTGALAXY_UPSTREAM,
            offset=offset,
        )
    )


def _fetch_torrent(info_hash: str) -> bytes:
    url = f"https://itorrents.org/torrent/{info_hash}.torrent"
    request = Request(url, headers={"User-Agent": "HomeOS-1337x-Bridge/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read(10 * 1024 * 1024)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"iTorrents download failed: {exc}") from exc
    if not content.startswith(b"d"):
        raise RuntimeError("iTorrents returned an invalid torrent payload")
    return content


@app.get("/download")
async def download(
    path: str = Query(...),
    apikey: str = Query(default=""),
) -> Response:
    _authorize(apikey)
    try:
        magnet = await BRIDGE.magnet(path)
        info_hash = extract_info_hash(magnet)
        torrent = await asyncio.to_thread(_fetch_torrent, info_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("Download resolution failed for %s", path)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    filename = f"{info_hash}.torrent"
    return Response(
        content=torrent,
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/torrentgalaxy/download")
async def torrentgalaxy_download(
    path: str = Query(...),
    apikey: str = Query(default=""),
) -> Response:
    _authorize(apikey)
    try:
        magnet = await TORRENTGALAXY_BRIDGE.magnet(path)
        info_hash = extract_info_hash(magnet)
        torrent = await asyncio.to_thread(_fetch_torrent, info_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("TorrentGalaxy download resolution failed for %s", path)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    filename = f"{info_hash}.torrent"
    return Response(
        content=torrent,
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
