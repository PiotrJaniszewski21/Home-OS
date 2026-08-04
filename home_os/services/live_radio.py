import ipaddress
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx


RADIO_BROWSER_URL = "https://de1.api.radio-browser.info/json"


class LiveRadioError(RuntimeError):
    pass


class LiveRadioService:
    def __init__(self, ttl=300):
        self.ttl = ttl
        self._cache = {}
        self._lock = threading.RLock()

    def featured(self, country_code="GB", limit=40):
        return self._stations(
            "/stations/search",
            {
                "countrycode": country_code,
                "hidebroken": "true",
                "order": "clickcount",
                "reverse": "true",
                "limit": self._limit(limit),
            },
        )

    def search(self, query, country_code="GB", limit=40):
        term = " ".join((query or "").split())
        if not term:
            raise ValueError("Station search is required")
        if len(term) > 100:
            raise ValueError("Station search is too long")
        return self._stations(
            "/stations/search",
            {
                "name": term,
                "countrycode": country_code,
                "hidebroken": "true",
                "order": "clickcount",
                "reverse": "true",
                "limit": self._limit(limit),
            },
        )

    def _stations(self, path, params):
        key = (path, tuple(sorted(params.items())))
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            response = httpx.get(
                f"{RADIO_BROWSER_URL}{path}",
                params=params,
                headers={"User-Agent": "HomeMusic/1.0"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LiveRadioError("The live radio directory is unavailable") from error
        stations = []
        seen = set()
        for item in payload:
            station = self._normalize(item)
            if station is None or station["id"] in seen:
                continue
            stations.append(station)
            seen.add(station["id"])
        self._store_cached(key, stations)
        return stations

    def _normalize(self, item):
        station_id = str(item.get("stationuuid") or "").strip()
        name = " ".join(str(item.get("name") or "").split())[:200]
        stream_url = str(item.get("url_resolved") or item.get("url") or "").strip()
        if not station_id or not name or not self._is_public_stream_url(stream_url):
            return None
        tags = [value.strip() for value in str(item.get("tags") or "").split(",") if value.strip()]
        return {
            "id": station_id,
            "name": name,
            "stream_url": stream_url,
            "artwork": str(item.get("favicon") or "")[:2048],
            "country": str(item.get("country") or ""),
            "country_code": str(item.get("countrycode") or ""),
            "language": str(item.get("language") or ""),
            "tags": tags[:8],
            "codec": str(item.get("codec") or ""),
            "bitrate": max(0, int(item.get("bitrate") or 0)),
            "is_hls": bool(item.get("hls")),
        }

    @staticmethod
    def _is_public_stream_url(value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return False
        try:
            addresses = {
                entry[4][0]
                for entry in socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror:
            return False
        if not addresses:
            return False
        return all(ipaddress.ip_address(address).is_global for address in addresses)

    def is_public_stream_url(self, value):
        return self._is_public_stream_url(value)

    @staticmethod
    def _limit(value):
        return max(1, min(int(value), 100))

    def _get_cached(self, key):
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry[0] <= time.monotonic():
                self._cache.pop(key, None)
                return None
            return entry[1]

    def _store_cached(self, key, value):
        with self._lock:
            self._cache[key] = (time.monotonic() + self.ttl, value)


live_radio_service = LiveRadioService()
