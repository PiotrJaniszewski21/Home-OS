"""Media auto-delete cleanup service.

Polls Plex for watched media and auto-deletes files after unmonitoring
them in Sonarr/Radarr. Designed to be called periodically by a systemd timer.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_STATE = {"watched": {}, "deleted": []}


# --- State Management ---


def _load_state(state_file: str) -> dict:
    path = Path(state_file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        return {"watched": {}, "deleted": []}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load state file %s: %s", state_file, e)
        return {"watched": {}, "deleted": []}


def _save_state(state: dict, state_file: str) -> None:
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state, indent=2))
    except OSError as e:
        logger.error("Failed to save state file %s: %s", state_file, e)


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


def _poll_watched_items(config: dict) -> list[dict]:
    """Poll Plex for all watched items across configured libraries."""
    sections = _get_library_sections(config)
    watched_items = []

    for section in sections:
        sid = section["id"]
        if section["type"] == "movie":
            # Movies: type=1, unwatched=0 returns watched movies
            path = f"/library/sections/{sid}/all?type=1&unwatched=0"
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
            # TV Episodes: type=4, unwatched=0 returns watched episodes
            path = f"/library/sections/{sid}/all?type=4&unwatched=0"
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
    if not config.get("sonarr_api_key"):
        return None
    series_list = _arr_request("GET", config["sonarr_url"], config["sonarr_api_key"],
                               "/api/v3/series")
    if not series_list or not isinstance(series_list, list):
        return None

    # Find matching series by title
    series = None
    for s in series_list:
        if s.get("title", "").lower() == series_title.lower():
            series = s
            break
    if not series:
        # Try partial match
        for s in series_list:
            if series_title.lower() in s.get("title", "").lower():
                series = s
                break
    if not series:
        logger.warning("Series not found in Sonarr: %s", series_title)
        return None

    # Get episodes for the series
    episodes = _arr_request("GET", config["sonarr_url"], config["sonarr_api_key"],
                            f"/api/v3/episode?seriesId={series['id']}")
    if not episodes or not isinstance(episodes, list):
        return None

    # Find matching episode
    for ep in episodes:
        if ep.get("seasonNumber") == season and ep.get("episodeNumber") == episode:
            ep["monitored"] = False
            _arr_request("PUT", config["sonarr_url"], config["sonarr_api_key"],
                         f"/api/v3/episode/{ep['id']}", json_data=ep)
            logger.info("Unmonitored episode in Sonarr: %s S%02dE%02d",
                        series_title, season, episode)
            return series["id"]

    return None


def _rescan_movie(config: dict, movie_id: int) -> None:
    _arr_request("POST", config["radarr_url"], config["radarr_api_key"],
                 "/api/v3/command", json_data={"name": "RescanMovie", "movieId": movie_id})


def _rescan_series(config: dict, series_id: int) -> None:
    _arr_request("POST", config["sonarr_url"], config["sonarr_api_key"],
                 "/api/v3/command", json_data={"name": "RescanSeries", "seriesId": series_id})


# --- File Deletion ---


def _delete_file(file_path: str) -> bool:
    """Delete a file and remove empty parent directory."""
    path = Path(file_path)
    if not path.exists():
        logger.warning("File already gone: %s", file_path)
        return True
    try:
        os.unlink(path)
        logger.info("Deleted file: %s", file_path)
        # Remove empty parent directory
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            logger.info("Removed empty directory: %s", parent)
        return True
    except OSError as e:
        logger.error("Failed to delete %s: %s", file_path, e)
        return False


# --- Main Cleanup Logic ---


def run_cleanup(config: dict) -> dict:
    """Run a single cleanup cycle. Returns summary of actions taken."""
    if not config.get("enabled", False):
        return {"status": "disabled"}

    state = _load_state(config["state_file"])
    now = datetime.now(timezone.utc)
    delay_hours = config.get("delay_hours", 24)
    summary = {"new_watched": 0, "deleted": 0, "errors": 0}

    # Step 1: Poll Plex for watched items, add new ones to state
    watched_items = _poll_watched_items(config)
    for item in watched_items:
        key = item["rating_key"]
        if key not in state["watched"]:
            entry = {
                "title": item["title"],
                "file_path": item["file_path"],
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

    # Step 2: Process items past their delay
    keys_to_delete = []
    for key, entry in list(state["watched"].items()):
        watched_at = datetime.fromisoformat(entry["watched_at"])
        hours_elapsed = (now - watched_at).total_seconds() / 3600
        if hours_elapsed < delay_hours:
            continue

        logger.info("Processing deletion for: %s (watched %.1fh ago)",
                    entry["title"], hours_elapsed)

        # Unmonitor in Sonarr/Radarr
        if entry["type"] == "movie":
            tmdb_id = entry.get("tmdb_id")
            if tmdb_id:
                movie_id = _unmonitor_movie(config, tmdb_id)
                if movie_id:
                    _rescan_movie(config, movie_id)
            else:
                logger.warning("No TMDB ID for movie: %s", entry["title"])
        elif entry["type"] == "episode":
            series_id = _unmonitor_episode(
                config, entry.get("series_title", ""),
                entry.get("season", 0), entry.get("episode", 0)
            )
            if series_id:
                _rescan_series(config, series_id)

        # Delete the file
        if _delete_file(entry["file_path"]):
            keys_to_delete.append(key)
            state["deleted"].append({
                "title": entry["title"],
                "file_path": entry["file_path"],
                "deleted_at": now.isoformat(),
                "type": entry["type"],
            })
            summary["deleted"] += 1
        else:
            summary["errors"] += 1

    # Remove processed entries from watched
    for key in keys_to_delete:
        del state["watched"][key]

    # Keep only last 20 deleted entries
    state["deleted"] = state["deleted"][-20:]

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
        watched_at = datetime.fromisoformat(entry["watched_at"])
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
        from home_os.models.settings import Setting
        plex_port = Setting.get("plex_port", "32400")
        sonarr_port = Setting.get("sonarr_port", "8989")
        radarr_port = Setting.get("radarr_port", "7878")
        config = {
            "enabled": Setting.get("autodelete_enabled", "false").lower() == "true",
            "plex_url": f"http://localhost:{plex_port}",
            "plex_token": Setting.get("autodelete_plex_token", ""),
            "delay_hours": int(Setting.get("autodelete_delay_hours", "24")),
            "threshold": int(Setting.get("autodelete_threshold", "85")),
            "movies": Setting.get("autodelete_movies", "true").lower() == "true",
            "tv": Setting.get("autodelete_tv", "true").lower() == "true",
            "sonarr_url": f"http://localhost:{sonarr_port}",
            "sonarr_api_key": Setting.get("autodelete_sonarr_key", ""),
            "radarr_url": f"http://localhost:{radarr_port}",
            "radarr_api_key": Setting.get("autodelete_radarr_key", ""),
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
