"""Media auto-delete cleanup service.

Polls Plex for watched media and auto-deletes files after unmonitoring
them in Sonarr/Radarr. Designed to be called periodically by a systemd timer.
"""

import json
import logging
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import fcntl

import httpx

logger = logging.getLogger(__name__)

STATE_VERSION = 2
DEFAULT_STATE = {"version": STATE_VERSION, "watched": {}, "deleted": [], "processed": {}}


class CleanupStateError(RuntimeError):
    pass


class CleanupAlreadyRunning(RuntimeError):
    pass


# --- State Management ---


def _load_state(state_file: str) -> dict:
    path = Path(state_file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        return {"version": STATE_VERSION, "watched": {}, "deleted": [], "processed": {}}
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise CleanupStateError(f"Failed to load state file {state_file}: {e}") from e

    if not isinstance(state, dict):
        raise CleanupStateError(f"Invalid cleanup state in {state_file}")
    if not isinstance(state.get("watched", {}), dict):
        raise CleanupStateError(f"Invalid watched entries in {state_file}")
    if not isinstance(state.get("deleted", []), list):
        raise CleanupStateError(f"Invalid deletion history in {state_file}")
    if not isinstance(state.get("processed", {}), dict):
        raise CleanupStateError(f"Invalid processed entries in {state_file}")

    state.setdefault("version", 1)
    state.setdefault("watched", {})
    state.setdefault("deleted", [])
    state.setdefault("processed", {})
    return state


def _save_state(state: dict, state_file: str) -> None:
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = STATE_VERSION
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as temp_file:
            json.dump(state, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_name = temp_file.name
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except OSError as e:
        try:
            os.unlink(temp_name)
        except (OSError, UnboundLocalError):
            pass
        raise CleanupStateError(f"Failed to save state file {state_file}: {e}") from e


@contextmanager
def _state_lock(state_file: str):
    lock_path = Path(f"{state_file}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CleanupAlreadyRunning("A media cleanup cycle is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# --- Plex API ---


def _plex_get(url: str, token: str, path: str) -> dict | None:
    try:
        resp = httpx.get(
            f"{url.rstrip('/')}{path}",
            headers={"X-Plex-Token": token, "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.error("Plex API error [%s]: %s", path, e)
        return None


def _get_library_sections(config: dict) -> list[dict]:
    """Get all library sections from Plex, filtered to movie/show types."""
    data = _plex_get(config["plex_url"], config["plex_token"], "/library/sections")
    if not data:
        return []
    sections = data.get("MediaContainer", {}).get("Directory", [])
    result = []
    for s in sections:
        stype = s.get("type")
        if stype == "movie" and config.get("movies", True):
            result.append({"id": s["key"], "type": "movie"})
        elif stype == "show" and config.get("tv", True):
            result.append({"id": s["key"], "type": "show"})
    return result


def _extract_tmdb_id(guids: list) -> str | None:
    """Extract TMDB ID from Plex guid list (e.g. tmdb://12345)."""
    for g in guids:
        guid_id = g.get("id", "")
        match = re.match(r"tmdb://(\d+)", guid_id)
        if match:
            return match.group(1)
    return None


def _get_file_path(item: dict) -> str | None:
    """Extract file path from a Plex media item."""
    media_list = item.get("Media", [])
    if not media_list:
        return None
    parts = media_list[0].get("Part", [])
    if not parts:
        return None
    return parts[0].get("file")


def _is_watched(item: dict, threshold: int) -> bool:
    """Check if item is considered watched based on threshold or viewCount."""
    if item.get("viewCount", 0) > 0:
        return True
    view_offset = item.get("viewOffset", 0)
    duration = item.get("duration", 1)
    if duration > 0 and (view_offset / duration * 100) >= threshold:
        return True
    return False


def _get_plex_item(config: dict, rating_key: str) -> dict | None:
    if not rating_key.isdigit():
        logger.error("Rejected invalid Plex rating key: %r", rating_key)
        return None
    data = _plex_get(
        config["plex_url"], config["plex_token"],
        f"/library/metadata/{rating_key}",
    )
    if not data:
        return None
    items = data.get("MediaContainer", {}).get("Metadata", [])
    return items[0] if len(items) == 1 else None


def _poll_watched_items(config: dict) -> list[dict]:
    """Poll Plex for all watched items across configured libraries."""
    sections = _get_library_sections(config)
    watched_items = []

    for section in sections:
        sid = section["id"]
        if section["type"] == "movie":
            path = (
                f"/library/sections/{sid}/all?type=1"
                "&X-Plex-Container-Start=0&X-Plex-Container-Size=10000"
            )
            data = _plex_get(config["plex_url"], config["plex_token"], path)
            if not data:
                continue
            items = data.get("MediaContainer", {}).get("Metadata", [])
            for item in items:
                if not _is_watched(item, config.get("threshold", 85)):
                    continue
                file_path = _get_file_path(item)
                if not file_path:
                    continue
                guids = item.get("Guid", [])
                watched_items.append({
                    "rating_key": str(item["ratingKey"]),
                    "title": item.get("title", "Unknown"),
                    "file_path": file_path,
                    "type": "movie",
                    "tmdb_id": _extract_tmdb_id(guids),
                })

        elif section["type"] == "show":
            path = (
                f"/library/sections/{sid}/all?type=4"
                "&X-Plex-Container-Start=0&X-Plex-Container-Size=10000"
            )
            data = _plex_get(config["plex_url"], config["plex_token"], path)
            if not data:
                continue
            items = data.get("MediaContainer", {}).get("Metadata", [])
            for item in items:
                if not _is_watched(item, config.get("threshold", 85)):
                    continue
                file_path = _get_file_path(item)
                if not file_path:
                    continue
                watched_items.append({
                    "rating_key": str(item["ratingKey"]),
                    "title": item.get("title", "Unknown"),
                    "file_path": file_path,
                    "type": "episode",
                    "series_title": item.get("grandparentTitle", ""),
                    "season": item.get("parentIndex", 0),
                    "episode": item.get("index", 0),
                })

    return watched_items


# --- Sonarr / Radarr API ---


def _arr_request(method: str, base_url: str, api_key: str, path: str,
                 json_data: dict | None = None) -> dict | list | None:
    try:
        resp = httpx.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            headers={"X-Api-Key": api_key},
            json=json_data,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.warning("Arr API error [%s %s]: %s", method, path, e)
        return None


def _unmonitor_movie(config: dict, tmdb_id: str) -> int | None:
    """Unmonitor movie in Radarr by TMDB ID. Returns movie ID or None."""
    if not config.get("radarr_api_key"):
        return None
    movies = _arr_request("GET", config["radarr_url"], config["radarr_api_key"],
                          f"/api/v3/movie?tmdbId={tmdb_id}")
    if not movies or not isinstance(movies, list) or len(movies) == 0:
        return None
    movie = movies[0]
    movie["monitored"] = False
    result = _arr_request("PUT", config["radarr_url"], config["radarr_api_key"],
                          f"/api/v3/movie/{movie['id']}", json_data=movie)
    if result is not None:
        logger.info("Unmonitored movie in Radarr: %s (id=%d)", movie.get("title"), movie["id"])
        return movie["id"]
    return None


def _unmonitor_episode(config: dict, series_title: str, season: int, episode: int) -> int | None:
    """Unmonitor episode in Sonarr. Returns series ID or None."""
    if not config.get("sonarr_api_key") or not series_title.strip():
        return None
    series_list = _arr_request("GET", config["sonarr_url"], config["sonarr_api_key"],
                               "/api/v3/series")
    if not series_list or not isinstance(series_list, list):
        return None

    normalized_title = series_title.casefold().strip()
    matches = [
        series for series in series_list
        if series.get("title", "").casefold().strip() == normalized_title
    ]
    if len(matches) != 1:
        logger.warning(
            "Expected one exact Sonarr match for %s, found %d",
            series_title, len(matches),
        )
        return None
    series = matches[0]

    # Get episodes for the series
    episodes = _arr_request("GET", config["sonarr_url"], config["sonarr_api_key"],
                            f"/api/v3/episode?seriesId={series['id']}")
    if not episodes or not isinstance(episodes, list):
        return None

    # Find matching episode
    for ep in episodes:
        if ep.get("seasonNumber") == season and ep.get("episodeNumber") == episode:
            ep["monitored"] = False
            result = _arr_request(
                "PUT", config["sonarr_url"], config["sonarr_api_key"],
                f"/api/v3/episode/{ep['id']}", json_data=ep,
            )
            if result is not None:
                logger.info(
                    "Unmonitored episode in Sonarr: %s S%02dE%02d",
                    series_title, season, episode,
                )
                return series["id"]
            return None

    return None


def _rescan_movie(config: dict, movie_id: int) -> None:
    _arr_request("POST", config["radarr_url"], config["radarr_api_key"],
                 "/api/v3/command", json_data={"name": "RescanMovie", "movieId": movie_id})


def _rescan_series(config: dict, series_id: int) -> None:
    _arr_request("POST", config["sonarr_url"], config["sonarr_api_key"],
                 "/api/v3/command", json_data={"name": "RescanSeries", "seriesId": series_id})


def _read_arr_config_value(config_path: str, field: str) -> str:
    try:
        root = ElementTree.parse(config_path).getroot()
        return (root.findtext(field) or "").strip()
    except (ElementTree.ParseError, OSError):
        return ""


def _read_arr_api_key(config_path: str) -> str:
    return _read_arr_config_value(config_path, "ApiKey")


def _read_arr_url_base(config_path: str) -> str:
    url_base = _read_arr_config_value(config_path, "UrlBase").strip("/")
    return f"/{url_base}" if url_base else ""


# --- File Deletion ---


def _file_identity(file_path: str) -> dict | None:
    path = Path(file_path)
    try:
        file_stat = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        return None
    return {
        "device": file_stat.st_dev,
        "inode": file_stat.st_ino,
        "size": file_stat.st_size,
        "mtime_ns": file_stat.st_mtime_ns,
    }


def _path_within_roots(file_path: str, allowed_roots: list[str]) -> bool:
    try:
        resolved_path = Path(file_path).resolve(strict=True)
    except OSError:
        return False
    for root in allowed_roots:
        try:
            resolved_path.relative_to(Path(root).resolve(strict=True))
            return True
        except (OSError, ValueError):
            continue
    return False


def _delete_file(file_path: str, allowed_roots: list[str], expected_identity: dict) -> bool:
    """Delete one validated media file and optionally its empty directory."""
    path = Path(file_path)
    if not _path_within_roots(file_path, allowed_roots):
        logger.error("Refusing to delete path outside configured media roots: %s", file_path)
        return False

    current_identity = _file_identity(file_path)
    if current_identity != expected_identity:
        logger.error("Refusing to delete changed or unsafe file: %s", file_path)
        return False

    try:
        os.unlink(path)
        logger.info("Deleted file: %s", file_path)
        parent = path.parent
        resolved_roots = {Path(root).resolve() for root in allowed_roots}
        if parent.resolve() not in resolved_roots and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            logger.info("Removed empty directory: %s", parent)
        return True
    except OSError as e:
        logger.error("Failed to delete %s: %s", file_path, e)
        return False


# --- Main Cleanup Logic ---


def _validate_config(config: dict) -> None:
    delay_hours = int(config.get("delay_hours", 24))
    threshold = int(config.get("threshold", 85))
    if not 1 <= delay_hours <= 8760:
        raise ValueError("Cleanup delay must be between 1 and 8760 hours")
    if not 50 <= threshold <= 100:
        raise ValueError("Watched threshold must be between 50 and 100 percent")
    if not config.get("media_roots"):
        raise ValueError("At least one media root is required")


def _revalidate_entry(config: dict, rating_key: str, entry: dict, now: datetime) -> str:
    item = _get_plex_item(config, rating_key)
    if item is None:
        return "retry"
    if not _is_watched(item, config.get("threshold", 85)):
        return "cancel"

    current_path = _get_file_path(item)
    if not current_path or current_path != entry.get("file_path"):
        logger.error("Plex path changed for %s; refusing deletion", entry.get("title"))
        return "retry"
    if not _path_within_roots(current_path, config["media_roots"]):
        logger.error("Plex path is outside configured roots: %s", current_path)
        return "retry"

    identity = _file_identity(current_path)
    if identity is None:
        return "gone"
    if not entry.get("file_identity"):
        entry["file_identity"] = identity
        entry["watched_at"] = now.isoformat()
        entry["migrated_at"] = now.isoformat()
        logger.info("Migrated legacy cleanup entry with a fresh safety delay: %s", entry.get("title"))
        return "defer"
    if identity != entry["file_identity"]:
        logger.error("File changed after it was queued; refusing deletion: %s", current_path)
        return "retry"

    if entry.get("type") == "movie" and not entry.get("tmdb_id"):
        entry["tmdb_id"] = _extract_tmdb_id(item.get("Guid", []))
    return "ready"


def _unmonitor_entry(config: dict, entry: dict) -> int | None:
    if entry["type"] == "movie":
        tmdb_id = entry.get("tmdb_id")
        if not tmdb_id or not config.get("radarr_api_key"):
            logger.error("Cannot safely delete movie without a TMDB ID and Radarr API key: %s", entry["title"])
            return None
        return _unmonitor_movie(config, tmdb_id)
    if entry["type"] == "episode":
        if not config.get("sonarr_api_key"):
            logger.error("Cannot safely delete episode without a Sonarr API key: %s", entry["title"])
            return None
        return _unmonitor_episode(
            config, entry.get("series_title", ""),
            entry.get("season", 0), entry.get("episode", 0),
        )
    return None


def run_cleanup(config: dict) -> dict:
    """Run one fail-closed cleanup cycle."""
    if not config.get("enabled", False):
        return {"status": "disabled"}

    try:
        _validate_config(config)
        with _state_lock(config["state_file"]):
            return _run_cleanup_locked(config)
    except CleanupAlreadyRunning as exc:
        logger.warning("Media cleanup skipped: %s", exc)
        return {"status": "busy", "message": str(exc)}
    except (CleanupStateError, ValueError) as exc:
        logger.error("Media cleanup stopped safely: %s", exc)
        return {"status": "error", "message": str(exc)}


def _run_cleanup_locked(config: dict) -> dict:
    state = _load_state(config["state_file"])
    now = datetime.now(timezone.utc)
    delay_hours = int(config.get("delay_hours", 24))
    max_deletions = int(config.get("max_deletions_per_run", 3))
    summary = {
        "new_watched": 0, "deleted": 0, "cancelled": 0,
        "deferred": 0, "errors": 0,
    }

    watched_items = _poll_watched_items(config)
    for item in watched_items:
        key = item["rating_key"]
        if key in state["watched"] or key in state["processed"]:
            continue
        identity = _file_identity(item["file_path"])
        if identity is None or not _path_within_roots(item["file_path"], config["media_roots"]):
            logger.error("Rejected unsafe Plex media path: %s", item["file_path"])
            summary["errors"] += 1
            continue
        entry = {
            "title": item["title"],
            "file_path": item["file_path"],
            "file_identity": identity,
            "watched_at": now.isoformat(),
            "type": item["type"],
        }
        if item["type"] == "episode":
            entry["series_title"] = item.get("series_title", "")
            entry["season"] = item.get("season", 0)
            entry["episode"] = item.get("episode", 0)
        elif item["type"] == "movie":
            entry["tmdb_id"] = item.get("tmdb_id")
        state["watched"][key] = entry
        summary["new_watched"] += 1
        logger.info("Tracking watched item: %s", item["title"])

    _save_state(state, config["state_file"])

    keys_to_delete = []
    for key, entry in list(state["watched"].items()):
        if summary["deleted"] >= max_deletions:
            summary["deferred"] += 1
            continue
        try:
            watched_at = datetime.fromisoformat(entry["watched_at"])
            if watched_at.tzinfo is None:
                watched_at = watched_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            logger.error("Invalid watched timestamp for cleanup entry %s", key)
            summary["errors"] += 1
            continue
        hours_elapsed = (now - watched_at).total_seconds() / 3600
        if hours_elapsed < delay_hours:
            continue

        validation = _revalidate_entry(config, key, entry, now)
        if validation == "cancel":
            keys_to_delete.append(key)
            summary["cancelled"] += 1
            continue
        if validation == "gone":
            keys_to_delete.append(key)
            state["processed"][key] = {"status": "gone", "processed_at": now.isoformat()}
            summary["cancelled"] += 1
            continue
        if validation == "defer":
            summary["deferred"] += 1
            continue
        if validation != "ready":
            summary["errors"] += 1
            continue

        logger.info(
            "Processing deletion for: %s (watched %.1fh ago)",
            entry["title"], hours_elapsed,
        )
        arr_id = entry.get("arr_id")
        if not arr_id:
            arr_id = _unmonitor_entry(config, entry)
            if not arr_id:
                summary["errors"] += 1
                continue
            entry["arr_id"] = arr_id
            _save_state(state, config["state_file"])

        if _delete_file(
            entry["file_path"], config["media_roots"], entry["file_identity"],
        ):
            keys_to_delete.append(key)
            state["deleted"].append({
                "title": entry["title"],
                "file_path": entry["file_path"],
                "deleted_at": now.isoformat(),
                "type": entry["type"],
            })
            state["processed"][key] = {
                "status": "deleted", "processed_at": now.isoformat(),
            }
            summary["deleted"] += 1
            if entry["type"] == "movie":
                _rescan_movie(config, arr_id)
            else:
                _rescan_series(config, arr_id)
        else:
            summary["errors"] += 1

    for key in keys_to_delete:
        del state["watched"][key]

    state["deleted"] = state["deleted"][-20:]
    state["processed"] = dict(list(state["processed"].items())[-5000:])
    _save_state(state, config["state_file"])
    logger.info("Cleanup cycle complete: %s", summary)
    return summary


# --- Status / UI ---


def get_cleanup_status(config: dict) -> dict:
    """Return current cleanup status for the UI."""
    enabled = config.get("enabled", False)
    state = _load_state(config["state_file"])
    now = datetime.now(timezone.utc)
    delay_hours = config.get("delay_hours", 24)

    pending = []
    for key, entry in state["watched"].items():
        try:
            watched_at = datetime.fromisoformat(entry["watched_at"])
            if watched_at.tzinfo is None:
                watched_at = watched_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            logger.error("Skipping invalid cleanup status entry: %s", key)
            continue
        hours_elapsed = (now - watched_at).total_seconds() / 3600
        hours_remaining = max(0, delay_hours - hours_elapsed)
        pending.append({
            "title": entry["title"],
            "type": entry["type"],
            "file_path": entry["file_path"],
            "watched_at": entry["watched_at"],
            "hours_remaining": round(hours_remaining, 1),
            "series_title": entry.get("series_title"),
            "season": entry.get("season"),
            "episode": entry.get("episode"),
        })

    # Sort by hours remaining (soonest first)
    pending.sort(key=lambda x: x["hours_remaining"])

    return {
        "enabled": enabled,
        "delay_hours": delay_hours,
        "pending": pending,
        "pending_count": len(pending),
        "recent_deletions": state.get("deleted", []),
    }


# --- Entry Point for Systemd Timer ---


def run_cleanup_cycle() -> dict:
    """Load config from Home OS settings database and run cleanup.

    This is the entry point called by the systemd timer.
    Creates its own Flask app context when running outside of a request.
    """
    from flask import current_app
    try:
        current_app._get_current_object()
        has_context = True
    except RuntimeError:
        has_context = False

    if not has_context:
        from home_os.app import create_app
        app = create_app()
        ctx = app.app_context()
        ctx.push()
    else:
        ctx = None

    try:
        return _run_cleanup_inner()
    finally:
        if ctx:
            ctx.pop()


def _run_cleanup_inner() -> dict:
    try:
        from flask import current_app
        from home_os.models.settings import Setting
        plex_port = Setting.get("plex_port", "32400")
        sonarr_port = Setting.get("sonarr_port", "8989")
        radarr_port = Setting.get("radarr_port", "7878")
        raw_config = current_app.config.get("_raw_config", {})
        storage_root = Path(raw_config.get("storage", {}).get("root", "/opt/home-os/storage"))
        media_root = storage_root / "HomeOS"
        sonarr_config = "/opt/Sonarr/data/config.xml"
        radarr_config = "/opt/Radarr/data/config.xml"
        sonarr_key = Setting.get("autodelete_sonarr_key", "") or _read_arr_api_key(sonarr_config)
        radarr_key = Setting.get("autodelete_radarr_key", "") or _read_arr_api_key(radarr_config)
        config = {
            "enabled": Setting.get("autodelete_enabled", "false").lower() == "true",
            "plex_url": f"http://localhost:{plex_port}",
            "plex_token": Setting.get("autodelete_plex_token", ""),
            "delay_hours": int(Setting.get("autodelete_delay_hours", "24")),
            "threshold": int(Setting.get("autodelete_threshold", "85")),
            "movies": Setting.get("autodelete_movies", "true").lower() == "true",
            "tv": Setting.get("autodelete_tv", "true").lower() == "true",
            "sonarr_url": f"http://localhost:{sonarr_port}{_read_arr_url_base(sonarr_config)}",
            "sonarr_api_key": sonarr_key,
            "radarr_url": f"http://localhost:{radarr_port}{_read_arr_url_base(radarr_config)}",
            "radarr_api_key": radarr_key,
            "media_roots": [
                str(media_root / "Movies"),
                str(media_root / "Series"),
            ],
            "max_deletions_per_run": 3,
            "state_file": Setting.get(
                "autodelete_state_file", "/opt/home-os/data/autodelete_state.json"
            ),
        }
    except Exception as e:
        logger.error("Failed to load config from database: %s", e)
        return {"status": "error", "message": str(e)}

    if not config["plex_token"]:
        logger.warning("Media cleanup: no Plex token configured, skipping")
        return {"status": "skipped", "message": "No Plex token configured"}

    return run_cleanup(config)
