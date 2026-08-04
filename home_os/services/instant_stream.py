"""Create temporary Jellyfin stream entries for active Arr downloads."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import yaml


LOGGER = logging.getLogger("home_os.instant_stream")
DEFAULT_CONFIG_PATH = Path("/opt/home-os/config/config.yaml")
DEFAULT_STATE_PATH = Path("/opt/home-os/data/instant-stream/instant_streams.json")
DEFAULT_ACCESS_TOKEN_PATH = Path("/opt/home-os/data/instant-stream/access-token")
DEFAULT_SEERR_SETTINGS_PATH = Path("/opt/seerr/config/settings.json")
TORRSERVER_URL = "http://127.0.0.1:8090"
MANAGED_CATEGORIES = {"radarr": "Movies", "tv-sonarr": "Series"}
VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".ts", ".vob", ".webm", ".wmv",
}
MANAGED_NAME_PREFIX = "HomeOS Instant - "
IMDB_ID_PATTERN = re.compile(r"^tt[0-9]{7,12}$", re.IGNORECASE)
TORRENT_HASH_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


class InstantPlayUnavailable(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_component(value: Any, fallback: str = "Untitled") -> str:
    text = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:160]


def select_stream_file(file_stats: Any) -> dict[str, Any] | None:
    if not isinstance(file_stats, list):
        return None
    candidates = []
    for item in file_stats:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        lowered_parts = {part.lower() for part in Path(path).parts}
        if "sample" in lowered_parts or re.search(r"(^|[._ -])sample([._ -]|$)", Path(path).stem, re.I):
            continue
        try:
            length = int(item.get("length") or 0)
            file_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if length > 0:
            candidates.append((length, file_id, path))
    if not candidates:
        return None
    length, file_id, path = max(candidates)
    return {"id": file_id, "path": path, "length": length}


def _atomic_write_text(path: Path, content: str, mode: int = 0o664) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"version": 1, "streams": {}, "last_run": None, "last_error": None}
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), dict):
        return {"version": 1, "streams": {}, "last_run": None, "last_error": None}
    return payload


def save_state(path: Path, state: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o660)


@contextmanager
def state_lock(path: Path):
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o660)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_access_token(path: Path = DEFAULT_ACCESS_TOKEN_PATH) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _future_timestamp(value: Any) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


def mark_stream_active(
    state_path: Path,
    torrent_hash: str,
    *,
    lifetime: timedelta = timedelta(hours=4),
) -> None:
    with state_lock(state_path):
        state = load_state(state_path)
        entry = state.get("streams", {}).get(torrent_hash.lower())
        if not isinstance(entry, dict):
            return
        entry["active_until"] = (datetime.now(timezone.utc) + lifetime).isoformat()
        entry["last_accessed_at"] = utc_now()
        save_state(state_path, state)


def rewrite_hls_playlist(
    content: str,
    base_path: str,
    authorization: dict[str, str],
) -> str:
    base_path = base_path.rstrip("/") + "/"

    def proxied_uri(uri: str) -> str:
        if not uri or "://" in uri or uri.startswith("/"):
            return uri
        path, separator, query = uri.partition("?")
        parameters = query
        parameters += ("&" if parameters else "") + urlencode(authorization)
        return f"{base_path}{quote(path, safe='/')}?{parameters}"

    rewritten = []
    map_pattern = re.compile(r'URI="([^"]+)"')
    for line in content.splitlines():
        if line.startswith("#EXT-X-MAP:"):
            line = map_pattern.sub(lambda match: f'URI="{proxied_uri(match.group(1))}"', line)
        elif line and not line.startswith("#"):
            line = proxied_uri(line)
        rewritten.append(line)
    return "\n".join(rewritten) + ("\n" if content.endswith("\n") else "")


def _read_arr_config(path: Path) -> tuple[str, str]:
    root = ET.parse(path).getroot()
    api_key = (root.findtext("ApiKey") or "").strip()
    if not api_key:
        raise RuntimeError(f"API key is missing from {path}")
    raw_base = (root.findtext("UrlBase") or "").strip("/")
    return api_key, f"/{raw_base}" if raw_base else ""


def _storage_root(config_path: Path, config: dict[str, Any]) -> Path:
    configured = Path(str(config.get("storage", {}).get("root") or "/opt/home-os/storage"))
    if configured.is_absolute():
        return configured
    if config_path.parent.name == "config":
        return config_path.parent.parent / configured
    return Path.cwd() / configured


def _torrent_hash(item: dict[str, Any]) -> str:
    return str(item.get("hash") or "").lower()


def _queue_download_id(item: dict[str, Any]) -> str:
    return str(item.get("downloadId") or "").lower()


def _queue_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("records", [])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def stream_destination(
    media_root: Path,
    category: str,
    torrent: dict[str, Any],
    queue_item: dict[str, Any] | None,
    stream_file: dict[str, Any],
) -> Path:
    torrent_hash = _torrent_hash(torrent)
    selected_stem = safe_component(Path(stream_file["path"]).stem)
    queue_item = queue_item or {}

    if category == "radarr":
        movie = queue_item.get("movie") if isinstance(queue_item.get("movie"), dict) else {}
        title = safe_component(movie.get("title") or torrent.get("name"))
        year = movie.get("year")
        folder_name = f"{title} ({year})" if year else title
        folder = media_root / "Movies" / safe_component(folder_name)
        display_name = title
    else:
        series = queue_item.get("series") if isinstance(queue_item.get("series"), dict) else {}
        title = safe_component(series.get("title") or torrent.get("name"))
        episode = queue_item.get("episode")
        if not isinstance(episode, dict):
            episodes = queue_item.get("episodes")
            episode = episodes[0] if isinstance(episodes, list) and episodes else {}
        try:
            season_number = int((episode or {}).get("seasonNumber"))
        except (TypeError, ValueError):
            season_number = None
        folder = media_root / "Series" / title
        if season_number is not None:
            folder /= f"Season {season_number:02d}"
        display_name = selected_stem

    filename = (
        f"{MANAGED_NAME_PREFIX}{torrent_hash[:12]} - {safe_component(display_name)}.strm"
    )
    destination = (folder / filename).resolve()
    expected_root = (media_root / MANAGED_CATEGORIES[category]).resolve()
    if destination != expected_root and expected_root not in destination.parents:
        raise ValueError("refusing to create an instant stream outside the media library")
    return destination


class InstantStreamWorker:
    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        state_path: Path = DEFAULT_STATE_PATH,
        client: httpx.Client | None = None,
    ):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.client = client or httpx.Client(timeout=10)
        self._owns_client = client is None

        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        media = config.get("media", {})
        self.media_root = _storage_root(self.config_path, config) / "HomeOS"
        self.qbit_url = f"http://127.0.0.1:{int(media.get('qbittorrent_port', 8080))}"
        self.arr_ports = {
            "radarr": int(media.get("radarr_port", 7878)),
            "tv-sonarr": int(media.get("sonarr_port", 8989)),
        }

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _get_qbit_torrents(self) -> list[dict[str, Any]]:
        response = self.client.get(f"{self.qbit_url}/api/v2/torrents/info")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("qBittorrent returned an invalid torrent list")
        return [item for item in payload if isinstance(item, dict)]

    def _arr_queue(self, category: str) -> list[dict[str, Any]]:
        service = "radarr" if category == "radarr" else "sonarr"
        config_path = Path(
            "/opt/Radarr/data/config.xml" if service == "radarr" else "/opt/Sonarr/data/config.xml"
        )
        api_key, url_base = _read_arr_config(config_path)
        endpoint = (
            f"http://127.0.0.1:{self.arr_ports[category]}{url_base}"
            "/api/v3/queue?page=1&pageSize=1000&includeUnknownMovieItems=true"
            "&includeMovie=true&includeSeries=true&includeEpisode=true"
        )
        response = self.client.get(endpoint, headers={"X-Api-Key": api_key, "Accept": "application/json"})
        response.raise_for_status()
        return _queue_records(response.json())

    def _torrserver(self, action: str, **payload: Any) -> Any:
        response = self.client.post(
            f"{TORRSERVER_URL}/torrents",
            json={"action": action, **payload},
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def _remove_managed_stream(
        self,
        entry: dict[str, Any],
        *,
        remove_torrent: bool = True,
    ) -> None:
        raw_path = entry.get("strm_path")
        if raw_path:
            path = Path(raw_path)
            expected_url = str(entry.get("stream_url") or "")
            try:
                if (
                    path.name.startswith(MANAGED_NAME_PREFIX)
                    and path.suffix == ".strm"
                    and path.read_text(encoding="utf-8").strip() == expected_url
                ):
                    path.unlink(missing_ok=True)
                    self._remove_empty_parents(path.parent)
            except FileNotFoundError:
                pass
            entry["strm_path"] = None
        if not remove_torrent:
            return
        torrent_hash = str(entry.get("hash") or "")
        if torrent_hash:
            try:
                self._torrserver("rem", hash=torrent_hash)
            except (httpx.HTTPError, ValueError):
                LOGGER.warning("Could not remove TorrServer entry %s", torrent_hash)

    def _remove_empty_parents(self, path: Path) -> None:
        library_roots = {
            (self.media_root / "Movies").resolve(),
            (self.media_root / "Series").resolve(),
        }
        current = path.resolve()
        while current not in library_roots and any(root in current.parents for root in library_roots):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def cleanup_all(self) -> None:
        with state_lock(self.state_path):
            state = load_state(self.state_path)
            for entry in state["streams"].values():
                if isinstance(entry, dict):
                    self._remove_managed_stream(entry)
            state["streams"] = {}
            state["last_run"] = utc_now()
            state["last_error"] = None
            save_state(self.state_path, state)

    def run_once(self) -> dict[str, Any]:
        with state_lock(self.state_path):
            return self._run_once_locked()

    def _run_once_locked(self) -> dict[str, Any]:
        state = load_state(self.state_path)
        streams = state["streams"]
        try:
            torrents = self._get_qbit_torrents()
            managed = {
                _torrent_hash(item): item
                for item in torrents
                if item.get("category") in MANAGED_CATEGORIES and _torrent_hash(item)
            }
            queues: dict[str, dict[str, dict[str, Any]] | None] = {}
            for category in MANAGED_CATEGORIES:
                try:
                    queues[category] = {
                        _queue_download_id(item): item
                        for item in self._arr_queue(category)
                        if _queue_download_id(item)
                    }
                except (ET.ParseError, OSError, RuntimeError, httpx.HTTPError, ValueError):
                    LOGGER.exception("Could not read %s queue", category)
                    queues[category] = None

            for torrent_hash, entry in list(streams.items()):
                torrent = managed.get(torrent_hash)
                queue = queues.get(torrent.get("category")) if torrent else None
                queue_known = queue is not None
                queue_present = torrent and queue_known and torrent_hash in queue
                complete = torrent and float(torrent.get("progress") or 0) >= 0.9999
                if torrent is None or complete or (queue_known and not queue_present):
                    if _future_timestamp(entry.get("active_until")):
                        self._remove_managed_stream(entry, remove_torrent=False)
                        entry.setdefault("completed_at", utc_now())
                        continue
                    self._remove_managed_stream(entry)
                    streams.pop(torrent_hash, None)

            for torrent_hash, torrent in managed.items():
                if torrent_hash in streams or float(torrent.get("progress") or 0) >= 0.9999:
                    continue
                category = str(torrent.get("category"))
                queue = queues.get(category)
                queue_item = queue.get(torrent_hash) if queue else None
                if not queue_item:
                    continue
                magnet_uri = str(torrent.get("magnet_uri") or "")
                if not magnet_uri.startswith("magnet:"):
                    LOGGER.warning("Torrent %s has no usable magnet URI yet", torrent_hash)
                    continue

                metadata = self._torrserver(
                    "add",
                    link=magnet_uri,
                    title=str(torrent.get("name") or torrent_hash),
                    category=category,
                    save_to_db=True,
                )
                if not isinstance(metadata, dict) or not metadata.get("file_stats"):
                    metadata = self._torrserver("get", hash=torrent_hash)
                selected = select_stream_file(metadata.get("file_stats") if isinstance(metadata, dict) else None)
                if not selected:
                    LOGGER.info("TorrServer metadata is not ready for %s", torrent_hash)
                    continue

                stream_url = f"{TORRSERVER_URL}/play/{torrent_hash}/{selected['id']}"
                destination = stream_destination(
                    self.media_root, category, torrent, queue_item, selected
                )
                _atomic_write_text(destination, stream_url + "\n")
                streams[torrent_hash] = {
                    "hash": torrent_hash,
                    "category": category,
                    "name": str(torrent.get("name") or ""),
                    "file_id": selected["id"],
                    "source_path": selected["path"],
                    "stream_url": stream_url,
                    "strm_path": str(destination),
                    "created_at": utc_now(),
                }

            state["last_run"] = utc_now()
            state["last_error"] = None
            save_state(self.state_path, state)
            return state
        except Exception as error:
            state["last_run"] = utc_now()
            state["last_error"] = f"{type(error).__name__}: {error}"
            save_state(self.state_path, state)
            raise


class InstantPlayResolver(InstantStreamWorker):
    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        state_path: Path = DEFAULT_STATE_PATH,
        client: httpx.Client | None = None,
        seerr_settings_path: Path = DEFAULT_SEERR_SETTINGS_PATH,
        sleeper=time.sleep,
        clock=time.monotonic,
    ):
        super().__init__(config_path, state_path, client)
        self.seerr_settings_path = Path(seerr_settings_path)
        self.sleeper = sleeper
        self.clock = clock
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        seerr_port = int(config.get("media", {}).get("overseerr_port", 5055))
        self.seerr_url = f"http://127.0.0.1:{seerr_port}"

    def _radarr_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        api_key, url_base = _read_arr_config(Path("/opt/Radarr/data/config.xml"))
        url = f"http://127.0.0.1:{self.arr_ports['radarr']}{url_base}/api/v3/{path.lstrip('/')}"
        headers = {"X-Api-Key": api_key, "Accept": "application/json"}
        response = self.client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    def _lookup_movie(self, imdb_id: str) -> dict[str, Any]:
        response = self._radarr_request("GET", "movie/lookup", params={"term": f"imdb:{imdb_id}"})
        payload = response.json()
        if not isinstance(payload, list):
            raise InstantPlayUnavailable("Radarr returned an invalid movie lookup response")
        for movie in payload:
            if isinstance(movie, dict) and str(movie.get("imdbId") or "").lower() == imdb_id:
                return movie
        raise InstantPlayUnavailable("Radarr could not identify this movie")

    def _radarr_movie(self, tmdb_id: int) -> dict[str, Any] | None:
        response = self._radarr_request("GET", "movie", params={"tmdbId": tmdb_id})
        payload = response.json()
        if isinstance(payload, list):
            return next((item for item in payload if isinstance(item, dict)), None)
        return payload if isinstance(payload, dict) and payload.get("id") else None

    def _seerr_api_key(self) -> str:
        try:
            settings = json.loads(self.seerr_settings_path.read_text(encoding="utf-8"))
            api_key = str(settings.get("main", {}).get("apiKey") or "").strip()
        except (OSError, json.JSONDecodeError, AttributeError):
            api_key = ""
        if not api_key:
            raise InstantPlayUnavailable("Seerr is not configured")
        return api_key

    def _request_movie(self, tmdb_id: int) -> None:
        response = self.client.post(
            f"{self.seerr_url}/api/v1/request",
            headers={"X-Api-Key": self._seerr_api_key(), "Accept": "application/json"},
            json={"mediaType": "movie", "mediaId": tmdb_id},
        )
        if response.status_code in (200, 201, 409):
            return
        try:
            message = str(response.json().get("message") or "")
        except (ValueError, AttributeError):
            message = ""
        if response.status_code == 403 and "already" in message.lower():
            return
        raise InstantPlayUnavailable(message or f"Seerr rejected the request ({response.status_code})")

    def _trigger_movie_search(self, movie_id: int) -> None:
        self._radarr_request(
            "POST",
            "command",
            json={"name": "MoviesSearch", "movieIds": [movie_id]},
        )

    def _history_download_id(self, movie_id: int) -> str:
        response = self._radarr_request(
            "GET",
            "history/movie",
            params={"movieId": movie_id},
        )
        payload = response.json()
        records = payload.get("records", []) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            return ""
        for record in records:
            if not isinstance(record, dict):
                continue
            download_id = _queue_download_id(record)
            if download_id and record.get("eventType") in {"downloadFolderImported", "grabbed"}:
                return download_id
        return ""

    @staticmethod
    def _queue_for_movie(records: list[dict[str, Any]], tmdb_id: int) -> dict[str, Any] | None:
        for record in records:
            movie = record.get("movie")
            if isinstance(movie, dict):
                try:
                    if int(movie.get("tmdbId")) == tmdb_id:
                        return record
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _torrent_for_queue(
        torrents: list[dict[str, Any]],
        queue_item: dict[str, Any],
    ) -> dict[str, Any] | None:
        download_id = _queue_download_id(queue_item)
        if not download_id:
            return None
        return next((item for item in torrents if _torrent_hash(item) == download_id), None)

    def _existing_stream(self, imdb_id: str) -> dict[str, Any] | None:
        with state_lock(self.state_path):
            state = load_state(self.state_path)
            for entry in state.get("streams", {}).values():
                if not isinstance(entry, dict):
                    continue
                provider_ids = entry.get("provider_ids")
                if (
                    isinstance(provider_ids, dict)
                    and str(provider_ids.get("imdb") or "").lower() == imdb_id
                    and TORRENT_HASH_PATTERN.fullmatch(str(entry.get("hash") or ""))
                ):
                    return dict(entry)
        return None

    def _remember_stream(
        self,
        imdb_id: str,
        tmdb_id: int,
        torrent: dict[str, Any],
        selected: dict[str, Any],
    ) -> dict[str, Any]:
        torrent_hash = _torrent_hash(torrent)
        entry = {
            "hash": torrent_hash,
            "category": "radarr",
            "name": str(torrent.get("name") or ""),
            "file_id": selected["id"],
            "source_path": selected["path"],
            "stream_url": f"{TORRSERVER_URL}/play/{torrent_hash}/{selected['id']}",
            "strm_path": None,
            "provider_ids": {"imdb": imdb_id, "tmdb": tmdb_id},
            "created_at": utc_now(),
            "last_accessed_at": utc_now(),
            "active_until": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        }
        with state_lock(self.state_path):
            state = load_state(self.state_path)
            previous = state["streams"].get(torrent_hash)
            if isinstance(previous, dict):
                entry = {**previous, **entry}
                if previous.get("strm_path"):
                    entry["strm_path"] = previous["strm_path"]
            state["streams"][torrent_hash] = entry
            state["last_run"] = utc_now()
            state["last_error"] = None
            save_state(self.state_path, state)
        return entry

    def prepare_movie(self, imdb_id: str) -> tuple[str, int, dict[str, Any] | None]:
        imdb_id = str(imdb_id or "").lower()
        if not IMDB_ID_PATTERN.fullmatch(imdb_id):
            raise InstantPlayUnavailable("The movie identifier is invalid")

        lookup = self._lookup_movie(imdb_id)
        try:
            tmdb_id = int(lookup["tmdbId"])
        except (KeyError, TypeError, ValueError) as error:
            raise InstantPlayUnavailable("Radarr did not return a TMDB identifier") from error

        movie = self._radarr_movie(tmdb_id)
        if movie is None:
            self._request_movie(tmdb_id)
        elif not movie.get("hasFile") and movie.get("id"):
            self._trigger_movie_search(int(movie["id"]))
        return imdb_id, tmdb_id, movie

    def resolve_movie(self, imdb_id: str, *, max_wait: float = 85) -> dict[str, Any]:
        imdb_id = str(imdb_id or "").lower()
        if not IMDB_ID_PATTERN.fullmatch(imdb_id):
            raise InstantPlayUnavailable("The movie identifier is invalid")

        existing = self._existing_stream(imdb_id)
        if existing:
            try:
                metadata = self._torrserver("get", hash=str(existing["hash"]))
            except (httpx.HTTPError, ValueError):
                metadata = None
            if isinstance(metadata, dict) and metadata.get("file_stats"):
                mark_stream_active(self.state_path, str(existing["hash"]))
                return existing
            with state_lock(self.state_path):
                state = load_state(self.state_path)
                state.get("streams", {}).pop(str(existing["hash"]), None)
                save_state(self.state_path, state)

        imdb_id, tmdb_id, movie = self.prepare_movie(imdb_id)
        history_download_id = ""
        if isinstance(movie, dict) and movie.get("id"):
            history_download_id = self._history_download_id(int(movie["id"]))

        deadline = self.clock() + max(0, max_wait)
        while True:
            queue_item = self._queue_for_movie(self._arr_queue("radarr"), tmdb_id)
            torrents = self._get_qbit_torrents()
            torrent = self._torrent_for_queue(torrents, queue_item) if queue_item else None
            if torrent is None and history_download_id:
                torrent = next(
                    (
                        item
                        for item in torrents
                        if _torrent_hash(item) == history_download_id
                    ),
                    None,
                )
            if torrent:
                magnet_uri = str(torrent.get("magnet_uri") or "")
                if magnet_uri.startswith("magnet:"):
                    metadata = self._torrserver(
                        "add",
                        link=magnet_uri,
                        title=str(torrent.get("name") or imdb_id),
                        category="radarr",
                        save_to_db=True,
                    )
                    if not isinstance(metadata, dict) or not metadata.get("file_stats"):
                        metadata = self._torrserver("get", hash=_torrent_hash(torrent))
                    selected = select_stream_file(
                        metadata.get("file_stats") if isinstance(metadata, dict) else None
                    )
                    if selected:
                        return self._remember_stream(imdb_id, tmdb_id, torrent, selected)

            if self.clock() >= deadline:
                raise InstantPlayUnavailable(
                    "The movie was requested, but a playable release is not ready yet"
                )
            self.sleeper(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("HOME_OS_CONFIG", DEFAULT_CONFIG_PATH)))
    parser.add_argument("--state", type=Path, default=Path(os.environ.get("HOME_OS_INSTANT_STREAM_STATE", DEFAULT_STATE_PATH)))
    parser.add_argument("--cleanup-all", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    worker = InstantStreamWorker(arguments.config, arguments.state)
    try:
        if arguments.cleanup_all:
            worker.cleanup_all()
        else:
            worker.run_once()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
