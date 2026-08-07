import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
BROWSE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,80}$")
THUMBNAIL_SIZE_PATTERN = re.compile(r"=w(\d+)-h(\d+)([^?]*)$")
MUSIC_AUDIO_VIDEO_TYPES = frozenset({"MUSIC_VIDEO_TYPE_ATV"})
STREAM_EXPIRY_SAFETY_SECONDS = 300
STREAM_FALLBACK_TTL_SECONDS = 180
DEFAULT_STREAM_TTL_SECONDS = 5 * 60 * 60
DEFAULT_FEED_FRESH_TTL_SECONDS = 30 * 60
DEFAULT_FEED_STALE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_RELEASE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_UNAVAILABLE_TRACK_TTL_SECONDS = 6 * 60 * 60
DEFAULT_ANTI_BOT_TRACK_TTL_SECONDS = 30 * 60
DEFAULT_EXTRACTION_INTERVAL_SECONDS = 1.25
DEFAULT_GENRE_REFRESH_AHEAD_SECONDS = 2 * 60 * 60
DEFAULT_GENRE_ACTIVE_SECONDS = 30 * 24 * 60 * 60
DEFAULT_GENRE_MAINTENANCE_LIMIT = 24
DEFAULT_AUDIO_CACHE_MAX_BYTES = 100 * 1024 * 1024 * 1024
DEFAULT_AUDIO_CACHE_TARGET_BYTES = 90 * 1024 * 1024 * 1024
DEFAULT_AUDIO_CACHE_MAX_IDLE_SECONDS = 90 * 24 * 60 * 60
MINIMUM_AUDIO_FILE_BYTES = 16 * 1024
MAXIMUM_AUDIO_FILE_BYTES = 128 * 1024 * 1024
PERSONALIZED_HOME_WORKERS = 3
GENRE_SEARCH_WORKERS = 4
GENRE_PAGE_WORKERS = 8
GENRE_PAGE_TTL_SECONDS = 24 * 60 * 60
GENRE_MAX_TRACK_SECONDS = 12 * 60
GENRE_NON_SINGLE_PATTERN = re.compile(
    r"\b(?:"
    r"compilation|continuous(?:\s+dj)?\s+mix|dj\s+mix|full\s+album|"
    r"megamix|non[\s-]?stop|top\s+\d+\s+hits|workout\s+music"
    r")\b",
    re.IGNORECASE,
)
logger = logging.getLogger("gunicorn.error")

GENRE_ALIASES = {
    "Acid House": ("acid house",),
    "Afrobeats": ("afrobeats", "afrobeat"),
    "Afro House": ("afro house",),
    "Amapiano": ("amapiano",),
    "Alternative": ("alternative", "alt rock"),
    "Bass House": ("bass house",),
    "Blues": ("blues",),
    "Breakbeat": ("breakbeat", "breaks"),
    "Classical": ("classical",),
    "Country": ("country",),
    "Dance": ("dance", "dance music"),
    "Dancefloor Drum and Bass": (
        "dancefloor drum and bass",
        "dancefloor dnb",
        "dancefloor drum & bass",
    ),
    "Dancehall": ("dancehall",),
    "Deep House": ("deep house",),
    "Disco": ("disco",),
    "Drill": ("drill", "uk drill"),
    "Drum and Bass": ("drum and bass", "dnb", "drum & bass"),
    "Dubstep": ("dubstep",),
    "EDM": ("edm", "electronic dance music"),
    "Electro House": ("electro house",),
    "Folk": ("folk",),
    "Funk": ("funk",),
    "Future Bass": ("future bass",),
    "Garage": ("garage",),
    "Gospel": ("gospel",),
    "Grime": ("grime",),
    "Hardstyle": ("hardstyle",),
    "Hip-Hop": ("hip hop", "hip-hop", "rap"),
    "House": ("house", "house music"),
    "Indie": ("indie", "indie rock", "indie pop"),
    "Jazz": ("jazz",),
    "Jump Up": ("jump up", "jump up dnb", "jump up drum and bass"),
    "Jungle": ("jungle", "jungle music"),
    "K-Pop": ("kpop", "k-pop", "k pop"),
    "Latin": ("latin", "latin music"),
    "Latin House": ("latin house",),
    "Liquid Drum and Bass": (
        "liquid drum and bass",
        "liquid dnb",
        "liquid drum & bass",
    ),
    "Melodic House and Techno": (
        "melodic house and techno",
        "melodic house",
        "melodic techno",
    ),
    "Metal": ("metal", "heavy metal"),
    "Minimal House": ("minimal house",),
    "Neurofunk": ("neurofunk",),
    "Phonk": ("phonk",),
    "Pop": ("pop", "pop music"),
    "Progressive House": ("progressive house",),
    "Punk": ("punk", "punk rock"),
    "R&B": ("r&b", "rnb", "rhythm and blues"),
    "Reggae": ("reggae",),
    "Reggaeton": ("reggaeton",),
    "Rock": ("rock", "rock music"),
    "Salsa": ("salsa",),
    "Soul": ("soul", "soul music"),
    "Speed Garage": ("speed garage",),
    "Tech House": ("tech house",),
    "Techno": ("techno",),
    "Trance": ("trance",),
    "Trap": ("trap", "trap music"),
    "UK Garage": ("uk garage", "ukg"),
}
GENRE_QUERY_FILLER = {
    "best",
    "genre",
    "hits",
    "music",
    "playlist",
    "popular",
    "songs",
    "top",
}
GENRE_EDITORIAL_PROFILES = {
    "Afro House": {
        "artists": (
            "Black Coffee",
            "Adam Port",
            "HUGEL",
            "Francis Mercier",
            "Shimza",
            "Caiiro",
            "Da Capo",
            "Zakes Bantwini",
        ),
        "classics": (
            ("Jerusalema", "Master KG"),
            ("Yamore", "MoBlack"),
            ("Osama", "Zakes Bantwini"),
            ("Anchor Point", "Ahmed Spins"),
            ("Move", "Adam Port"),
            ("The Rapture Pt.III", "&ME"),
            ("Premier Gaou", "Francis Mercier"),
            ("Wish You Were Here", "Black Coffee"),
        ),
    },
    "Bass House": {
        "artists": (
            "JOYRYDE",
            "Knock2",
            "Habstrakt",
            "AC Slater",
            "Malaa",
            "Wax Motif",
            "BIJOU",
            "Taiki Nulight",
        ),
        "classics": (
            ("HOT DRUM", "JOYRYDE"),
            ("Chicken Soup", "Skrillex"),
            ("Notorious", "Malaa"),
            ("Bass Inside", "AC Slater"),
            ("Feel the Volume", "Jauz"),
            ("Rock the Party", "Ephwurd"),
            ("Prophecy", "Tchami"),
            ("Discharge", "Curbi"),
        ),
    },
    "Drum and Bass": {
        "artists": (
            "Chase & Status",
            "Sub Focus",
            "Wilkinson",
            "Hybrid Minds",
            "Hedex",
            "K Motionz",
        ),
        "classics": (
            ("Original Nuttah", "Shy FX"),
            ("Inner City Life", "Goldie"),
            ("Brown Paper Bag", "Roni Size"),
            ("Circles", "Adam F"),
            ("Slam", "Pendulum"),
            ("Gold Dust", "DJ Fresh"),
            ("If We Ever", "High Contrast"),
            ("Timewarp", "Sub Focus"),
        ),
    },
    "Dancefloor Drum and Bass": {
        "artists": (
            "Sub Focus",
            "Dimension",
            "Wilkinson",
            "Culture Shock",
            "1991",
            "Metrik",
            "Grafix",
            "Andromedik",
        ),
        "classics": (
            ("Tidal Wave", "Sub Focus"),
            ("Desire", "Sub Focus"),
            ("Afterglow", "Wilkinson"),
            ("Devotion", "Dimension"),
            ("There For You", "Culture Shock"),
            ("We Got It", "Metrik"),
        ),
    },
    "Liquid Drum and Bass": {
        "artists": (
            "Hybrid Minds",
            "Calibre",
            "LSB",
            "High Contrast",
            "Etherwood",
            "Technimatic",
            "Pola & Bryson",
        ),
        "classics": (
            ("If We Ever", "High Contrast"),
            ("Even If", "Calibre"),
            ("Open Page", "LSB"),
            ("Lighter Than Air", "Etherwood"),
            ("Beneath the Skies", "Hybrid Minds"),
        ),
    },
    "Jump Up": {
        "artists": (
            "Hedex",
            "Bou",
            "K Motionz",
            "Simula",
            "Sota",
            "Mozey",
            "Turno",
        ),
        "classics": (
            ("Mr Happy", "DJ Hazard"),
            ("Bricks Don't Roll", "DJ Hazard"),
            ("No Problem", "Chase & Status"),
            ("Original Nuttah", "Shy FX"),
        ),
    },
    "Jungle": {
        "artists": (
            "Shy FX",
            "Nia Archives",
            "Tim Reaper",
            "Coco Bryce",
            "Sully",
            "Dwarde",
        ),
        "classics": (
            ("Original Nuttah", "Shy FX"),
            ("Incredible", "M-Beat"),
            ("Valley of the Shadows", "Origin Unknown"),
            ("Super Sharp Shooter", "The Ganja Kru"),
            ("Inner City Life", "Goldie"),
        ),
    },
    "Neurofunk": {
        "artists": (
            "Noisia",
            "Black Sun Empire",
            "Mefjus",
            "Teddy Killerz",
            "Burr Oak",
            "Pythius",
        ),
        "classics": (
            ("Stigma", "Noisia"),
            ("Arrakis", "Black Sun Empire"),
            ("Suicide Bassline", "Mefjus"),
            ("Dead Limit", "Noisia"),
        ),
    },
}


class HomeMusicError(RuntimeError):
    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class CacheEntry:
    value: object
    expires_at: float


@dataclass(frozen=True)
class StreamDetails:
    url: str
    duration_seconds: float | None
    expires_at: int | None = None


@dataclass(frozen=True)
class FeedCacheEntry:
    value: dict
    fresh_until: float
    stale_until: float


class HomeMusicService:
    def __init__(
        self,
        search_ttl=120,
        stream_ttl=DEFAULT_STREAM_TTL_SECONDS,
        stream_cache_directory=None,
        feed_cache_directory=None,
        genre_cache_directory=None,
        metadata_cache_directory=None,
        audio_cache_directory=None,
        feed_fresh_ttl=DEFAULT_FEED_FRESH_TTL_SECONDS,
        feed_stale_ttl=DEFAULT_FEED_STALE_TTL_SECONDS,
        release_ttl=DEFAULT_RELEASE_TTL_SECONDS,
        unavailable_track_ttl=DEFAULT_UNAVAILABLE_TRACK_TTL_SECONDS,
        unavailable_cache_directory=None,
        extraction_interval=DEFAULT_EXTRACTION_INTERVAL_SECONDS,
    ):
        self.search_ttl = search_ttl
        self.stream_ttl = stream_ttl
        self.feed_fresh_ttl = feed_fresh_ttl
        self.feed_stale_ttl = max(feed_stale_ttl, feed_fresh_ttl)
        self.release_ttl = release_ttl
        self.unavailable_track_ttl = max(60, int(unavailable_track_ttl))
        self.extraction_interval = max(0, float(extraction_interval))
        self._search_cache = {}
        self._stream_cache = {}
        self._unavailable_tracks = {}
        self._lock = threading.RLock()
        self._extraction_lock = threading.Lock()
        self._last_extraction_at = 0.0
        self._ytmusic = None
        self._ytmusic_local = threading.local()
        self._feed_refreshes = set()
        self._audio_warmups = set()
        self._audio_warm_queue = deque()
        self._audio_warm_queued = set()
        self._audio_warm_worker = None
        configured_cache = (
            stream_cache_directory
            if stream_cache_directory is not None
            else os.environ.get("HOME_OS_MUSIC_CACHE_DIR")
        )
        self._stream_cache_directory = (
            Path(configured_cache).expanduser()
            if configured_cache
            else None
        )
        configured_unavailable_cache = (
            unavailable_cache_directory
            if unavailable_cache_directory is not None
            else os.environ.get("HOME_OS_MUSIC_UNAVAILABLE_CACHE_DIR")
        )
        if configured_unavailable_cache is None and self._stream_cache_directory:
            configured_unavailable_cache = (
                self._stream_cache_directory.parent / "music-unavailable"
            )
        self._unavailable_cache_directory = (
            Path(configured_unavailable_cache).expanduser()
            if configured_unavailable_cache
            else None
        )
        configured_feed_cache = (
            feed_cache_directory
            if feed_cache_directory is not None
            else os.environ.get("HOME_OS_MUSIC_FEED_CACHE_DIR")
        )
        self._feed_cache_directory = (
            Path(configured_feed_cache).expanduser()
            if configured_feed_cache
            else None
        )
        configured_genre_cache = (
            genre_cache_directory
            if genre_cache_directory is not None
            else os.environ.get("HOME_OS_MUSIC_GENRE_CACHE_DIR")
        )
        self._genre_cache_directory = (
            Path(configured_genre_cache).expanduser()
            if configured_genre_cache
            else None
        )
        configured_metadata_cache = (
            metadata_cache_directory
            if metadata_cache_directory is not None
            else os.environ.get("HOME_OS_MUSIC_METADATA_CACHE_DIR")
        )
        self._metadata_cache_directory = (
            Path(configured_metadata_cache).expanduser()
            if configured_metadata_cache
            else None
        )
        configured_audio_cache = (
            audio_cache_directory
            if audio_cache_directory is not None
            else os.environ.get("HOME_OS_MUSIC_AUDIO_CACHE_DIR")
        )
        self._audio_cache_directory = (
            Path(configured_audio_cache).expanduser()
            if configured_audio_cache
            else None
        )

    @staticmethod
    def validate_video_id(video_id):
        value = (video_id or "").strip()
        if not VIDEO_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid track identifier")
        return value

    @staticmethod
    def validate_browse_id(browse_id):
        value = (browse_id or "").strip()
        if not BROWSE_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid music identifier")
        return value

    def mark_track_unavailable(self, video_id, reason=None):
        video_id = self.validate_video_id(video_id)
        ttl = (
            DEFAULT_ANTI_BOT_TRACK_TTL_SECONDS
            if reason == "anti_bot"
            else self.unavailable_track_ttl
        )
        unavailable_until = time.time() + ttl
        with self._lock:
            self._unavailable_tracks[video_id] = unavailable_until
        directory = self._ensure_unavailable_cache_directory()
        if directory is not None:
            payload = {
                "unavailable_until": unavailable_until,
                "reason": reason,
            }
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{video_id}.",
                suffix=".tmp",
                dir=directory,
            )
            temporary_path = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w") as cache_file:
                    json.dump(payload, cache_file, separators=(",", ":"))
                    cache_file.flush()
                    os.fsync(cache_file.fileno())
                os.replace(
                    temporary_path,
                    self._unavailable_track_path(video_id),
                )
            finally:
                temporary_path.unlink(missing_ok=True)

    def is_track_unavailable(self, video_id):
        video_id = self.validate_video_id(video_id)
        now = time.time()
        if self._unavailable_cache_directory is None:
            with self._lock:
                unavailable_until = self._unavailable_tracks.get(video_id)
                if unavailable_until is None:
                    return False
                if unavailable_until > now:
                    return True
                self._unavailable_tracks.pop(video_id, None)
                return False
        path = self._unavailable_track_path(video_id)
        try:
            unavailable_until = float(
                json.loads(path.read_text())["unavailable_until"]
            )
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            path.unlink(missing_ok=True)
            with self._lock:
                self._unavailable_tracks.pop(video_id, None)
            return False
        if unavailable_until <= now:
            path.unlink(missing_ok=True)
            with self._lock:
                self._unavailable_tracks.pop(video_id, None)
            return False
        with self._lock:
            self._unavailable_tracks[video_id] = unavailable_until
        return True

    def clear_track_unavailable(self, video_id):
        video_id = self.validate_video_id(video_id)
        with self._lock:
            self._unavailable_tracks.pop(video_id, None)
        if self._unavailable_cache_directory is not None:
            self._unavailable_track_path(video_id).unlink(missing_ok=True)

    def _ensure_unavailable_cache_directory(self):
        if self._unavailable_cache_directory is None:
            return None
        self._unavailable_cache_directory.mkdir(
            mode=0o700,
            parents=True,
            exist_ok=True,
        )
        self._unavailable_cache_directory.chmod(0o700)
        return self._unavailable_cache_directory

    def _unavailable_track_path(self, video_id):
        return self._unavailable_cache_directory / f"{video_id}.json"

    def search(self, query, limit=20):
        normalized_query = " ".join((query or "").split())
        if not normalized_query:
            raise ValueError("Search query is required")
        if len(normalized_query) > 120:
            raise ValueError("Search query is too long")
        limit = max(1, min(int(limit), 25))
        cache_key = (normalized_query.casefold(), limit)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        cached = self._get_shared_metadata("search", cache_key)
        if cached is not None:
            self._store_cached(self._search_cache, cache_key, cached, self.search_ttl)
            return cached

        try:
            results = self._get_ytmusic().search(
                normalized_query,
                filter="songs",
                limit=limit,
            )
        except Exception as error:
            raise HomeMusicError("YouTube Music search is unavailable") from error

        tracks = self._normalize_tracks(results, limit=limit)

        self._store_cached(self._search_cache, cache_key, tracks, self.search_ttl)
        self._store_shared_metadata("search", cache_key, tracks, 10 * 60)
        return tracks

    def track_duration(self, video_id):
        video_id = self.validate_video_id(video_id)
        try:
            song = self._get_ytmusic().get_song(video_id)
            raw_duration = (song.get("videoDetails") or {}).get("lengthSeconds")
            duration = int(raw_duration)
        except (AttributeError, TypeError, ValueError):
            return None
        except Exception as error:
            raise HomeMusicError(
                "YouTube Music track metadata is unavailable"
            ) from error
        return duration if 0 < duration <= 24 * 60 * 60 else None

    @staticmethod
    def resolve_genre(query):
        normalized = " ".join(str(query or "").casefold().replace("-", " ").split())
        if not normalized:
            return None
        stripped = " ".join(
            word
            for word in normalized.split()
            if word not in GENRE_QUERY_FILLER
        )
        candidate = stripped or normalized
        best = None
        best_ratio = 0.0
        for genre, aliases in GENRE_ALIASES.items():
            for alias in aliases:
                comparable_alias = alias.casefold().replace("-", " ")
                if candidate == comparable_alias:
                    return genre
                ratio = SequenceMatcher(None, candidate, comparable_alias).ratio()
                if ratio > best_ratio:
                    best = genre
                    best_ratio = ratio
        return best if best_ratio >= 0.78 else None

    @staticmethod
    def genres():
        return sorted(GENRE_ALIASES)

    def genre_search(self, genre, limit=25):
        if genre not in GENRE_ALIASES:
            raise ValueError("Unknown music genre")
        limit = max(1, min(int(limit), 25))
        cache_key = ("genre-search-v2", genre, limit)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        cached = self._get_shared_metadata("search", cache_key)
        if cached is not None:
            self._store_cached(self._search_cache, cache_key, cached, self.search_ttl)
            return cached

        current_year = time.gmtime().tm_year
        jobs = [
            (f"{genre} hits", 110),
            (f"best {genre} songs", 100),
            (f"{genre} {current_year}", 105),
        ]
        tracks = self._genre_consensus_tracks(jobs, limit)
        self._store_cached(
            self._search_cache,
            cache_key,
            tracks,
            self.search_ttl,
        )
        return tracks

    def _genre_classics(self, genre, limit):
        jobs = [
            (f"{genre} classics", 110),
            (f"best {genre} songs of all time", 100),
            (f"iconic {genre} songs", 105),
        ]
        return self._genre_consensus_tracks(jobs, limit)

    def _genre_consensus_tracks(self, jobs, limit):
        try:
            with ThreadPoolExecutor(
                max_workers=min(GENRE_SEARCH_WORKERS, len(jobs))
            ) as executor:
                groups = list(executor.map(
                    lambda job: self._get_ytmusic().search(
                        job[0],
                        filter="songs",
                        limit=limit,
                    ),
                    jobs,
                ))
        except Exception as error:
            raise HomeMusicError("Genre search is unavailable") from error

        tracks_by_id = {}
        scores = {}
        sources = {}
        for group_index, ((_, source_weight), results) in enumerate(
            zip(jobs, groups)
        ):
            for rank, track in enumerate(
                self._normalize_tracks(results, limit=limit)
            ):
                if not self._is_standalone_genre_track(track):
                    continue
                track_id = track["id"]
                tracks_by_id.setdefault(track_id, track)
                sources.setdefault(track_id, set()).add(group_index)
                scores[track_id] = scores.get(track_id, 0) + max(
                    1,
                    source_weight - (rank * 3),
                )

        tracks = sorted(
            (
                track
                for track_id, track in tracks_by_id.items()
                if len(sources.get(track_id, ())) == len(jobs)
            ),
            key=lambda track: (-scores[track["id"]], track["title"].casefold()),
        )[:limit]
        return tracks

    def genre_page(self, genre, limit=25):
        if genre not in GENRE_ALIASES:
            raise ValueError("Unknown music genre")
        limit = max(1, min(int(limit), 25))
        self._record_genre_access(self._genre_cache_key(genre, limit))
        return self._genre_page_lists(genre, limit)

    def maintain_genre_cache(
        self,
        refresh_ahead_seconds=DEFAULT_GENRE_REFRESH_AHEAD_SECONDS,
        active_seconds=DEFAULT_GENRE_ACTIVE_SECONDS,
        max_entries=DEFAULT_GENRE_MAINTENANCE_LIMIT,
        warm_genres=None,
    ):
        if self._genre_cache_directory is None:
            return {
                "configured": False,
                "eligible": 0,
                "refreshed": 0,
                "fresh": 0,
                "failed": 0,
            }
        now = time.time()
        refresh_ahead_seconds = max(0, int(refresh_ahead_seconds))
        active_seconds = max(0, int(active_seconds))
        max_entries = max(1, int(max_entries))
        warm = tuple(
            GENRE_EDITORIAL_PROFILES
            if warm_genres is None
            else (
                genre
                for genre in warm_genres
                if genre in GENRE_ALIASES
            )
        )
        warm_keys = {
            self._genre_cache_key(genre, 25)
            for genre in warm
        }
        candidates = {}
        for genre in GENRE_ALIASES:
            for limit in range(1, 26):
                cache_key = self._genre_cache_key(genre, limit)
                cache_path = self._shared_genre_path(cache_key)
                access_path = self._shared_genre_access_path(cache_key)
                try:
                    accessed_at = access_path.stat().st_mtime
                except FileNotFoundError:
                    accessed_at = None
                if accessed_at is None and cache_path.exists():
                    try:
                        payload = json.loads(cache_path.read_text())
                    except (OSError, ValueError, json.JSONDecodeError):
                        payload = {}
                    if not payload.get("cache_key"):
                        self._record_genre_access(cache_key)
                        accessed_at = now
                if cache_key in warm_keys:
                    candidates[cache_key] = (genre, limit, float("inf"))
                elif (
                    accessed_at is not None
                    and now - accessed_at <= active_seconds
                ):
                    candidates[cache_key] = (genre, limit, accessed_at)
                elif accessed_at is not None:
                    access_path.unlink(missing_ok=True)

        ordered = sorted(
            candidates.items(),
            key=lambda item: (
                item[0] not in warm_keys,
                -item[1][2],
                item[1][0],
                item[1][1],
            ),
        )[:max_entries]
        result = {
            "configured": True,
            "eligible": len(ordered),
            "refreshed": 0,
            "fresh": 0,
            "failed": 0,
        }
        for cache_key, (genre, limit, _) in ordered:
            try:
                cached = self._read_shared_genre(cache_key)
                if (
                    cached is not None
                    and cached.expires_at > now + refresh_ahead_seconds
                ):
                    result["fresh"] += 1
                    continue
                with self._shared_genre_lock(cache_key):
                    cached = self._read_shared_genre(cache_key)
                    if (
                        cached is not None
                        and cached.expires_at > time.time() + refresh_ahead_seconds
                    ):
                        result["fresh"] += 1
                        continue
                    page = self._generate_genre_page_lists(genre, limit)
                    self._write_shared_genre(cache_key, page)
                    result["refreshed"] += 1
            except Exception:
                result["failed"] += 1
                logger.warning(
                    "HomeMusic genre maintenance failed for %s",
                    genre,
                    exc_info=True,
                )
        return result

    def _genre_page_lists(self, genre, limit):
        shared_cache_key = self._genre_cache_key(genre, limit)
        if self._genre_cache_directory is not None:
            try:
                cached = self._read_shared_genre(shared_cache_key)
                if cached is not None:
                    return cached.value
                with self._shared_genre_lock(shared_cache_key):
                    cached = self._read_shared_genre(shared_cache_key)
                    if cached is not None:
                        return cached.value
                    page = self._generate_genre_page_lists(genre, limit)
                    self._write_shared_genre(shared_cache_key, page)
                    return page
            except OSError:
                logger.warning(
                    "HomeMusic shared genre cache is unavailable",
                    exc_info=True,
                )

        memory_cache_key = ("genre-page-lists-v7", genre, limit)
        cached = self._get_cached(self._search_cache, memory_cache_key)
        if cached is not None:
            return cached
        page = self._generate_genre_page_lists(genre, limit)
        self._store_cached(
            self._search_cache,
            memory_cache_key,
            page,
            GENRE_PAGE_TTL_SECONDS,
        )
        return page

    def _generate_genre_page_lists(self, genre, limit):
        profile = GENRE_EDITORIAL_PROFILES.get(genre)
        if profile is None:
            popular = self.genre_search(
                genre,
                limit=limit,
            )
            recent_cutoff = time.gmtime().tm_year - 2
            recent_releases = [
                release
                for release in self.search_albums(
                    f"{genre} new releases",
                    limit=20,
                )
                if (
                    self._is_genre_artist_release(release)
                    and self._release_year(release) >= recent_cutoff
                )
            ][:10]
            classics = self._genre_classics(genre, limit=10)
            hot_artists = self._unique_artists(
                self._artists_from_tracks(popular),
                limit=10,
            )
            if recent_releases and (not popular or not hot_artists):
                release_artists = self._genre_release_artists(
                    recent_releases,
                    limit=8,
                )
                if release_artists:
                    fallback = self._editorial_genre_page(
                        {
                            "artists": release_artists,
                            "classics": (),
                        },
                        limit,
                    )
                    if not popular:
                        popular = fallback["popular"]
                    if not hot_artists:
                        hot_artists = fallback["hot_artists"]
            page = {
                "popular": popular,
                "recent_releases": recent_releases,
                "classics": classics,
                "hot_artists": hot_artists,
            }
        else:
            page = self._editorial_genre_page(profile, limit)
        return page

    def _editorial_genre_page(self, profile, limit):
        jobs = []
        for artist in profile["artists"]:
            jobs.append(("popular", artist, artist))
            jobs.append(("release", artist, artist))
            jobs.append(("artist", artist, artist))
        for title, artist in profile["classics"]:
            jobs.append(("classic", f"{title} {artist}", (title, artist)))

        def perform(job):
            kind, query, _ = job
            if kind == "artist":
                result_filter = "artists"
            elif kind == "release":
                result_filter = "albums"
            else:
                result_filter = "songs"
            return self._get_ytmusic().search(
                query,
                filter=result_filter,
                limit=8,
            )

        try:
            with ThreadPoolExecutor(
                max_workers=min(GENRE_PAGE_WORKERS, len(jobs))
            ) as executor:
                groups = list(executor.map(perform, jobs))
        except Exception as error:
            raise HomeMusicError("Genre page is unavailable") from error

        popular_groups = []
        recent_releases = []
        classics = []
        hot_artists = []
        recent_cutoff = time.gmtime().tm_year - 2
        for (kind, _, expected), results in zip(jobs, groups):
            if kind == "popular":
                tracks = [
                    track
                    for track in self._normalize_tracks(results, limit=8)
                    if (
                        self._artist_matches(track["artist"], expected)
                        and self._is_standalone_genre_track(track)
                    )
                ]
                popular_groups.append(tracks[:3])
            elif kind == "release":
                releases = [
                    release
                    for release in self._normalize_releases(results)
                    if (
                        self._artist_matches(release["artist"], expected)
                        and self._is_genre_artist_release(release)
                        and self._release_year(release) >= recent_cutoff
                    )
                ]
                recent_releases.extend(releases[:2])
            elif kind == "artist":
                match = next(
                    (
                        artist
                        for artist in (
                            self._normalize_artist(result)
                            for result in results
                        )
                        if (
                            artist is not None
                            and self._comparison_text(artist["name"])
                            == self._comparison_text(expected)
                        )
                    ),
                    None,
                )
                if match is not None:
                    hot_artists.append(match)
            else:
                title, artist = expected
                match = self._best_editorial_track(results, title, artist)
                if match is not None:
                    classics.append(match)

        popular = []
        for index in range(3):
            for tracks in popular_groups:
                if index < len(tracks):
                    popular.append(tracks[index])
        page = {
            "popular": self._unique_tracks(popular, limit=limit),
            "recent_releases": self._unique_releases(
                sorted(
                    recent_releases,
                    key=self._release_year,
                    reverse=True,
                ),
                limit=12,
            ),
            "classics": self._unique_tracks(classics, limit=12),
            "hot_artists": self._unique_artists(hot_artists, limit=10),
        }
        return page

    def _best_editorial_track(self, results, title, artist):
        tracks = [
            track
            for track in self._normalize_tracks(results, limit=8)
            if self._artist_matches(track["artist"], artist)
        ]
        if not tracks:
            return None
        expected = self._comparison_text(title)
        match = max(
            tracks,
            key=lambda track: SequenceMatcher(
                None,
                expected,
                self._comparison_text(track["title"]),
            ).ratio(),
        )
        ratio = SequenceMatcher(
            None,
            expected,
            self._comparison_text(match["title"]),
        ).ratio()
        return match if ratio >= 0.55 else None

    @staticmethod
    def _artist_matches(value, expected):
        expected_value = HomeMusicService._comparison_text(expected)
        return expected_value in {
            HomeMusicService._comparison_text(artist)
            for artist in str(value or "").split(",")
        }

    def _genre_release_artists(self, releases, limit):
        artists = []
        seen = set()
        for release in releases:
            name = str(release.get("artist") or "").split(",", 1)[0].strip()
            identity = self._comparison_text(name)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            artists.append(name)
            if len(artists) >= limit:
                break
        return tuple(artists)

    @staticmethod
    def _comparison_text(value):
        return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split())

    @staticmethod
    def _release_year(release):
        try:
            return int(release.get("year") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_standalone_genre_track(track):
        artist = str(track.get("artist") or "")
        if "various artists" in artist.casefold():
            return False
        title = str(track.get("title") or "")
        if GENRE_NON_SINGLE_PATTERN.search(title):
            return False
        try:
            duration = int(track.get("duration_seconds"))
        except (TypeError, ValueError):
            duration = 0
        return not duration or duration <= GENRE_MAX_TRACK_SECONDS

    @staticmethod
    def _is_genre_artist_release(release):
        artist = str(release.get("artist") or "")
        if not artist or "various artists" in artist.casefold():
            return False
        title = str(release.get("title") or "")
        return GENRE_NON_SINGLE_PATTERN.search(title) is None

    def search_artists(self, query, limit=8):
        normalized_query = " ".join((query or "").split())
        if not normalized_query:
            raise ValueError("Search query is required")
        if len(normalized_query) > 120:
            raise ValueError("Search query is too long")
        limit = max(1, min(int(limit), 12))
        cache_key = ("artists", normalized_query.casefold(), limit)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        cached = self._get_shared_metadata("search", cache_key)
        if cached is not None:
            self._store_cached(self._search_cache, cache_key, cached, self.search_ttl)
            return cached
        try:
            results = self._get_ytmusic().search(normalized_query, filter="artists", limit=limit)
        except Exception as error:
            raise HomeMusicError("Artist search is unavailable") from error
        artists = [self._normalize_artist(item) for item in results]
        artists = [artist for artist in artists if artist is not None][:limit]
        self._store_cached(self._search_cache, cache_key, artists, self.search_ttl)
        self._store_shared_metadata("search", cache_key, artists, 15 * 60)
        return artists

    def search_albums(self, query, limit=12):
        normalized_query = " ".join((query or "").split())
        if not normalized_query:
            raise ValueError("Search query is required")
        if len(normalized_query) > 120:
            raise ValueError("Search query is too long")
        limit = max(1, min(int(limit), 20))
        cache_key = ("albums", normalized_query.casefold(), limit)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        cached = self._get_shared_metadata("search", cache_key)
        if cached is not None:
            self._store_cached(self._search_cache, cache_key, cached, self.search_ttl)
            return cached
        try:
            results = self._get_ytmusic().search(normalized_query, filter="albums", limit=limit)
        except Exception as error:
            raise HomeMusicError("Album search is unavailable") from error
        albums = self._normalize_releases(results)[:limit]
        self._store_cached(self._search_cache, cache_key, albums, self.search_ttl)
        self._store_shared_metadata("search", cache_key, albums, 15 * 60)
        return albums

    def artist(self, browse_id):
        browse_id = self.validate_browse_id(browse_id)
        cache_key = ("artist", browse_id)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        cached = self._get_shared_metadata("catalog", cache_key)
        if cached is not None:
            self._store_cached(self._search_cache, cache_key, cached, self.search_ttl)
            return cached
        try:
            result = self._get_ytmusic().get_artist(browse_id)
        except Exception as error:
            raise HomeMusicError("Artist details are unavailable") from error
        artist = {
            "id": browse_id,
            "name": str(result.get("name") or "Artist").strip(),
            "description": str(result.get("description") or "").strip(),
            "subscribers": result.get("subscribers"),
            "monthly_listeners": result.get("monthlyListeners"),
            "thumbnail": self._largest_thumbnail(result.get("thumbnails")),
            "essentials": self._normalize_tracks(
                (result.get("songs") or {}).get("results") or [], limit=20
            ),
            "albums": self._normalize_releases((result.get("albums") or {}).get("results") or []),
            "singles": self._normalize_releases((result.get("singles") or {}).get("results") or []),
            "related": [
                artist
                for artist in (
                    self._normalize_artist(item)
                    for item in ((result.get("related") or {}).get("results") or [])
                )
                if artist is not None
            ],
        }
        self._store_cached(self._search_cache, cache_key, artist, self.search_ttl)
        self._store_shared_metadata("catalog", cache_key, artist, 24 * 60 * 60)
        return artist

    def album(self, browse_id):
        browse_id = self.validate_browse_id(browse_id)
        cache_key = ("album", browse_id)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        cached = self._get_shared_metadata("catalog", cache_key)
        if cached is not None:
            self._store_cached(self._search_cache, cache_key, cached, self.search_ttl)
            return cached
        try:
            result = self._get_ytmusic().get_album(browse_id)
        except Exception as error:
            raise HomeMusicError("Album details are unavailable") from error
        thumbnail = self._largest_thumbnail(result.get("thumbnails"))
        track_results = result.get("tracks") or []
        filter_non_audio = False
        audio_playlist_id = str(result.get("audioPlaylistId") or "").strip()
        if audio_playlist_id:
            try:
                audio_playlist = self._get_ytmusic().get_playlist(
                    audio_playlist_id,
                    limit=100,
                )
                audio_tracks = audio_playlist.get("tracks") or []
                if audio_tracks:
                    track_results = audio_tracks
                    filter_non_audio = True
            except Exception:
                logger.warning(
                    "HomeMusic album audio playlist failed for %s",
                    browse_id,
                    exc_info=True,
                )
        tracks = self._normalize_tracks(
            track_results,
            limit=100,
            filter_non_audio=filter_non_audio,
        )
        if thumbnail:
            tracks = [
                {**track, "thumbnail": track.get("thumbnail") or thumbnail}
                for track in tracks
            ]
        album = {
            "id": browse_id,
            "title": str(result.get("title") or "Album").strip(),
            "artist": ", ".join(
                str(item.get("name") or "").strip()
                for item in (result.get("artists") or [])
                if item.get("name")
            ),
            "year": str(result.get("year") or ""),
            "type": str(result.get("type") or "Album"),
            "thumbnail": thumbnail,
            "tracks": tracks,
        }
        self._store_cached(self._search_cache, cache_key, album, self.search_ttl)
        self._store_shared_metadata("catalog", cache_key, album, 24 * 60 * 60)
        return album

    def recommendations(self, seed_ids, exclude_ids=None, limit=20):
        limit = max(1, min(int(limit), 25))
        seeds = []
        for value in seed_ids:
            try:
                video_id = self.validate_video_id(value)
            except ValueError:
                continue
            if video_id not in seeds:
                seeds.append(video_id)
            if len(seeds) == 3:
                break
        if not seeds:
            return []

        excluded = set(exclude_ids or ()) | set(seeds)
        cache_key = (tuple(seeds), tuple(sorted(excluded)), limit)
        cached = self._get_cached(self._search_cache, ("recommendations", cache_key))
        if cached is not None:
            return self._filter_unavailable_tracks(cached)[:limit]

        candidates = self._recommendation_candidates(seeds, limit)

        tracks = [
            track
            for track in self._normalize_tracks(candidates, limit=limit + len(excluded))
            if (
                track["id"] not in excluded
                and not self.is_track_unavailable(track["id"])
            )
        ][:limit]
        self._store_cached(
            self._search_cache,
            ("recommendations", cache_key),
            tracks,
            self.search_ttl,
        )
        return tracks

    def personalized_home(
        self,
        seed_ids,
        preferred_artists,
        exclude_ids=None,
        cache_key=None,
        force_refresh=False,
    ):
        seeds = []
        for value in seed_ids:
            try:
                video_id = self.validate_video_id(value)
            except ValueError:
                continue
            if video_id not in seeds:
                seeds.append(video_id)
            if len(seeds) == 3:
                break
        if not seeds:
            return {"suggested_songs": [], "suggested_albums": [], "new_releases": []}

        artists = []
        for value in preferred_artists:
            artist = " ".join(str(value or "").split())
            if artist and artist.casefold() not in {item.casefold() for item in artists}:
                artists.append(artist)
            if len(artists) == 2:
                break
        excluded = set(exclude_ids or ()) | set(seeds)
        memory_inputs_key = (
            tuple(seeds),
            tuple(artists),
            tuple(sorted(excluded)),
        )
        shared_cache_key = str(cache_key) if cache_key is not None else None
        if shared_cache_key and self._feed_cache_directory is not None:
            cached = self._read_shared_feed(shared_cache_key)
            if cached is not None and not force_refresh:
                if cached.fresh_until <= time.time():
                    self._schedule_feed_refresh(
                        shared_cache_key,
                        seeds,
                        artists,
                        excluded,
                    )
                return self._sanitize_personalized_home(cached.value)
            return self._refresh_shared_feed(
                shared_cache_key,
                seeds,
                artists,
                excluded,
                force_refresh=force_refresh,
            )

        memory_cache_key = ("personalized-home", memory_inputs_key)
        if not force_refresh:
            cached = self._get_cached(self._search_cache, memory_cache_key)
            if cached is not None:
                return self._sanitize_personalized_home(cached)
        payload = self._generate_personalized_home(seeds, artists, excluded)
        self._store_cached(
            self._search_cache,
            memory_cache_key,
            payload,
            max(self.search_ttl, 300),
        )
        return payload

    def refresh_personalized_home_if_due(
        self,
        seed_ids,
        preferred_artists,
        exclude_ids=None,
        cache_key=None,
        refresh_ahead_seconds=5 * 60,
    ):
        if cache_key is None or self._feed_cache_directory is None:
            return False
        shared_cache_key = str(cache_key)
        cached = self._read_shared_feed(shared_cache_key)
        if (
            cached is not None
            and cached.fresh_until
            > time.time() + max(0, int(refresh_ahead_seconds))
        ):
            return False
        self.personalized_home(
            seed_ids,
            preferred_artists,
            exclude_ids=exclude_ids,
            cache_key=shared_cache_key,
            force_refresh=True,
        )
        return True

    def _generate_personalized_home(self, seeds, artists, excluded):
        jobs = [
            ("recommendation", seed)
            for seed in seeds
        ] + [
            ("releases", artist)
            for artist in artists
        ]
        candidates = []
        release_groups = []
        try:
            with ThreadPoolExecutor(
                max_workers=min(PERSONALIZED_HOME_WORKERS, len(jobs))
            ) as executor:
                futures = [
                    executor.submit(self._personalized_home_job, kind, value)
                    for kind, value in jobs
                ]
                for (kind, _), future in zip(jobs, futures):
                    try:
                        result = future.result()
                    except Exception as error:
                        if kind == "recommendation":
                            raise HomeMusicError(
                                "Music recommendations are unavailable"
                            ) from error
                        result = []
                    if kind == "recommendation":
                        candidates.extend(result)
                    else:
                        release_groups.append(result)
        except HomeMusicError:
            raise
        except Exception as error:
            raise HomeMusicError("Music recommendations are unavailable") from error

        suggested_songs = [
            track
            for track in self._normalize_tracks(candidates, limit=40)
            if (
                track["id"] not in excluded
                and not self.is_track_unavailable(track["id"])
            )
        ][:16]
        suggested_albums = self._albums_from_tracks(candidates, limit=10)
        new_releases = []
        for artist, releases in zip(artists, release_groups):
            for release in releases:
                release = dict(release)
                if release:
                    release["artist"] = artist
                    new_releases.append(release)
        new_releases = self._unique_releases(
            sorted(new_releases, key=lambda item: item.get("year") or "", reverse=True),
            limit=12,
        )
        new_release_ids = {item["id"] for item in new_releases}
        suggested_albums = [
            item for item in suggested_albums if item["id"] not in new_release_ids
        ][:10]
        payload = {
            "suggested_songs": suggested_songs,
            "suggested_albums": suggested_albums,
            "new_releases": new_releases,
        }
        return payload

    def _personalized_home_job(self, kind, value):
        if kind == "recommendation":
            radio = self._get_ytmusic().get_watch_playlist(
                videoId=value,
                radio=True,
                limit=30,
            )
            return radio.get("tracks") or []
        cache_key = ("artist-releases", value.casefold())
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        releases = self._get_ytmusic().search(value, filter="albums", limit=6)
        normalized = self._normalize_releases(releases)
        self._store_cached(
            self._search_cache,
            cache_key,
            normalized,
            self.release_ttl,
        )
        return normalized

    def _recommendation_candidates(self, seeds, limit):
        try:
            with ThreadPoolExecutor(
                max_workers=min(PERSONALIZED_HOME_WORKERS, len(seeds))
            ) as executor:
                groups = list(executor.map(
                    lambda seed: (
                        self._get_ytmusic().get_watch_playlist(
                            videoId=seed,
                            radio=True,
                            limit=max(limit, 12),
                        ).get("tracks")
                        or []
                    ),
                    seeds,
                ))
                return [
                    candidate
                    for group in groups
                    for candidate in group
                ]
        except Exception as error:
            raise HomeMusicError("Music recommendations are unavailable") from error

    def _albums_from_tracks(self, tracks, limit):
        releases = []
        for track in tracks:
            album = track.get("album") or {}
            browse_id = album.get("id") or album.get("browseId")
            title = str(album.get("name") or album.get("title") or "").strip()
            try:
                browse_id = self.validate_browse_id(browse_id)
            except ValueError:
                continue
            artists = [
                str(item.get("name") or "").strip()
                for item in (track.get("artists") or [])
                if item.get("name")
            ]
            thumbnail = self._largest_thumbnail(
                track.get("thumbnails") or track.get("thumbnail")
            )
            releases.append({
                "id": browse_id,
                "title": title or "Album",
                "artist": ", ".join(artists),
                "thumbnail": thumbnail,
                "year": "",
                "type": "Album",
            })
        return self._unique_releases(releases, limit)

    @staticmethod
    def _unique_releases(releases, limit):
        unique = []
        seen = set()
        for release in releases:
            if release["id"] in seen:
                continue
            seen.add(release["id"])
            unique.append(release)
            if len(unique) >= limit:
                break
        return unique

    @staticmethod
    def _unique_tracks(tracks, limit):
        unique = []
        seen = set()
        for track in tracks:
            if track["id"] in seen:
                continue
            seen.add(track["id"])
            unique.append(track)
            if len(unique) >= limit:
                break
        return unique

    @staticmethod
    def _unique_artists(artists, limit):
        unique = []
        seen = set()
        for artist in artists:
            if artist["id"] in seen:
                continue
            seen.add(artist["id"])
            unique.append(artist)
            if len(unique) >= limit:
                break
        return unique

    def _artists_from_tracks(self, tracks):
        artists = []
        for track in tracks:
            try:
                artist_id = self.validate_browse_id(track.get("artist_id"))
            except (AttributeError, ValueError):
                continue
            name = str(track.get("artist") or "").split(",", 1)[0].strip()
            if not name:
                continue
            artists.append({
                "id": artist_id,
                "name": name,
                "thumbnail": str(track.get("thumbnail") or ""),
                "subscribers": None,
            })
        return self._unique_artists(artists, limit=10)

    def stream_url(self, video_id):
        return self.stream_details(video_id).url

    def _fast_resolve_innertube(self, video_id):
        """Attempts high-speed direct stream URL resolution using InnerTube API (~150ms)."""
        import json
        import urllib.request
        start = time.monotonic()
        url = "https://www.youtube.com/youtubei/v1/player"
        payload = {
            "videoId": video_id,
            "context": {
                "client": {
                    "clientName": "ANDROID_VR",
                    "clientVersion": "1.54.1",
                    "hl": "en",
                    "gl": "US"
                }
            }
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "com.google.android.apps.youtube.vr.oculus/1.54.1 (Linux; U; Android 12; en_US)"
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))

            streaming_data = data.get("streamingData", {})
            formats = streaming_data.get("adaptiveFormats", []) + streaming_data.get("formats", [])
            audio_formats = [f for f in formats if "audio" in f.get("mimeType", "") and "url" in f]

            if audio_formats:
                best = max(audio_formats, key=lambda f: f.get("bitrate", 0))
                stream_url = best["url"]
                raw_duration = data.get("videoDetails", {}).get("lengthSeconds")
                duration = float(raw_duration) if raw_duration else None
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.info("[FAST-INNERTUBE] Resolved %s stream in %.1f ms", video_id, elapsed_ms)
                return stream_url, duration
        except Exception as exc:
            logger.debug("[FAST-INNERTUBE] Fast resolution for %s bypassed: %s", video_id, exc)
        return None, None

    def stream_details(self, video_id):
        video_id = self.validate_video_id(video_id)
        if self.is_track_unavailable(video_id):
            raise HomeMusicError("Audio stream is currently unavailable")
        started_at = time.monotonic()
        cached = self._get_cached(self._stream_cache, video_id)
        if cached is not None:
            self._log_stream_resolution(video_id, "memory", started_at)
            return cached

        cached = self._read_shared_stream(video_id)
        if cached is not None:
            self._store_stream_in_memory(video_id, cached)
            self._log_stream_resolution(video_id, "shared", started_at)
            return cached

        with self._stream_resolution_lock(video_id):
            cached = self._get_cached(self._stream_cache, video_id)
            if cached is not None:
                self._log_stream_resolution(video_id, "memory-after-lock", started_at)
                return cached
            cached = self._read_shared_stream(video_id)
            if cached is not None:
                self._store_stream_in_memory(video_id, cached)
                self._log_stream_resolution(video_id, "shared-after-lock", started_at)
                return cached

            # Attempt ultra-fast InnerTube resolution (~150ms)
            fast_url, fast_duration = self._fast_resolve_innertube(video_id)
            if fast_url and self._is_allowed_stream_url(fast_url):
                details = StreamDetails(
                    url=fast_url,
                    duration_seconds=fast_duration if fast_duration and fast_duration > 0 else None,
                    expires_at=self._stream_expiry(fast_url),
                )
                self._store_stream_in_memory(video_id, details)
                self._write_shared_stream(video_id, details)
                self.clear_track_unavailable(video_id)
                self._log_stream_resolution(video_id, "innertube-fast", started_at)
                return details

            try:
                from yt_dlp import YoutubeDL

                options = self._youtube_dl_options()
                options["skip_download"] = True
                with self._extraction_slot():
                    with YoutubeDL(options) as downloader:
                        info = downloader.extract_info(
                            f"https://music.youtube.com/watch?v={video_id}",
                            download=False,
                        )
                stream_url = str(info.get("url") or "")
                raw_duration = info.get("duration")
                duration_seconds = float(raw_duration) if raw_duration is not None else None
            except Exception as error:
                reason = self._classify_extraction_failure(error)
                self.mark_track_unavailable(video_id, reason=reason)
                self._log_extraction_failure(video_id, reason, error)
                message = (
                    "Audio provider temporarily requires authentication"
                    if reason == "anti_bot"
                    else "Audio stream is currently unavailable"
                )
                raise HomeMusicError(message, reason=reason) from error

            if not self._is_allowed_stream_url(stream_url):
                self.mark_track_unavailable(video_id, reason="invalid_source")
                raise HomeMusicError(
                    "Audio provider returned an invalid stream URL",
                    reason="invalid_source",
                )
            details = StreamDetails(
                url=stream_url,
                duration_seconds=duration_seconds,
                expires_at=self._stream_expiry(stream_url),
            )
            self._store_stream_in_memory(video_id, details)
            self._write_shared_stream(video_id, details)
            self.clear_track_unavailable(video_id)
            self._log_stream_resolution(video_id, "yt-dlp", started_at)
            return details

    def prefetch_stream_details(self, video_ids):
        if not video_ids:
            return
        def _prefetch_job():
            for vid in video_ids:
                try:
                    if self._get_cached(self._stream_cache, vid) is None and not self.is_track_unavailable(vid):
                        self.stream_details(vid)
                except Exception:
                    pass
        threading.Thread(target=_prefetch_job, daemon=True).start()

    def download_audio(self, video_id, output_directory):
        video_id = self.validate_video_id(video_id)
        output_directory = os.path.realpath(output_directory)
        os.makedirs(output_directory, exist_ok=True)
        destination = Path(output_directory) / f"{video_id}.m4a"
        try:
            for attempt in range(2):
                stream = self.stream_details(video_id)
                total = 0
                try:
                    with httpx.stream(
                        "GET",
                        stream.url,
                        headers={
                            "Accept": "audio/*,*/*;q=0.8",
                            "User-Agent": "HomeMusic/1.0",
                        },
                        follow_redirects=True,
                        timeout=httpx.Timeout(30, read=180),
                    ) as response:
                        response.raise_for_status()
                        if not self._is_allowed_stream_url(str(response.url)):
                            raise HomeMusicError(
                                "Audio provider returned an invalid stream URL",
                                reason="invalid_source",
                            )
                        raw_length = response.headers.get("Content-Length")
                        if raw_length and int(raw_length) > MAXIMUM_AUDIO_FILE_BYTES:
                            raise HomeMusicError(
                                "Audio provider returned an invalid file"
                            )
                        content_type = response.headers.get(
                            "Content-Type",
                            "",
                        ).lower()
                        if "html" in content_type or "json" in content_type:
                            raise HomeMusicError(
                                "Audio provider returned an invalid file"
                            )
                        with destination.open("wb") as audio_file:
                            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                                total += len(chunk)
                                if total > MAXIMUM_AUDIO_FILE_BYTES:
                                    raise HomeMusicError(
                                        "Audio provider returned an invalid file"
                                    )
                                audio_file.write(chunk)
                            audio_file.flush()
                            os.fsync(audio_file.fileno())
                except httpx.HTTPStatusError as error:
                    destination.unlink(missing_ok=True)
                    if (
                        attempt == 0
                        and error.response.status_code in {401, 403, 410}
                    ):
                        self.invalidate_stream(video_id)
                        continue
                    raise
                break
        except HomeMusicError:
            destination.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError, TypeError, ValueError) as error:
            destination.unlink(missing_ok=True)
            raise HomeMusicError(
                "Audio download is currently unavailable",
                reason="network",
            ) from error

        if not MINIMUM_AUDIO_FILE_BYTES <= total <= MAXIMUM_AUDIO_FILE_BYTES:
            destination.unlink(missing_ok=True)
            raise HomeMusicError("Audio provider returned an invalid file")
        destination.chmod(0o600)
        self.clear_track_unavailable(video_id)
        return str(destination)

    @property
    def audio_cache_enabled(self):
        return self._audio_cache_directory is not None

    def _ensure_audio_cache_directory(self):
        if self._audio_cache_directory is None:
            return None
        self._audio_cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._audio_cache_directory.chmod(0o700)
        return self._audio_cache_directory

    def _audio_cache_path(self, video_id):
        return self._audio_cache_directory / f"{video_id}.m4a"

    def _audio_metadata_path(self, video_id):
        return self._audio_cache_directory / f"{video_id}.json"

    def _audio_cache_lock_path(self, video_id):
        return self._audio_cache_directory / f"{video_id}.audio.lock"

    @contextmanager
    def _audio_cache_lock(self, video_id, blocking=True):
        directory = self._ensure_audio_cache_directory()
        if directory is None:
            yield False
            return
        lock_path = self._audio_cache_lock_path(video_id)
        with lock_path.open("a") as lock_file:
            lock_path.chmod(0o600)
            operation = fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(lock_file.fileno(), operation)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def cached_audio_path(self, video_id, touch=True):
        video_id = self.validate_video_id(video_id)
        if self._audio_cache_directory is None:
            return None
        path = self._audio_cache_path(video_id)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return None
        if not MINIMUM_AUDIO_FILE_BYTES <= size <= MAXIMUM_AUDIO_FILE_BYTES:
            path.unlink(missing_ok=True)
            return None
        if touch:
            try:
                os.utime(path, None)
            except OSError:
                pass
        self.clear_track_unavailable(video_id)
        return path

    def cached_audio_duration(self, video_id):
        video_id = self.validate_video_id(video_id)
        if self.cached_audio_path(video_id, touch=False) is None:
            return None
        try:
            value = float(
                json.loads(self._audio_metadata_path(video_id).read_text()).get(
                    "duration_seconds"
                )
            )
        except (
            AttributeError,
            FileNotFoundError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None
        return value if 0 < value <= 24 * 60 * 60 else None

    def _write_audio_metadata(self, video_id, duration_seconds, size):
        if self._audio_cache_directory is None:
            return
        payload = {
            "duration_seconds": duration_seconds,
            "size": int(size),
            "cached_at": time.time(),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{video_id}.",
            suffix=".json.tmp",
            dir=self._audio_cache_directory,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as metadata_file:
                json.dump(payload, metadata_file, separators=(",", ":"))
                metadata_file.flush()
                os.fsync(metadata_file.fileno())
            os.replace(temporary_path, self._audio_metadata_path(video_id))
        finally:
            temporary_path.unlink(missing_ok=True)

    def cache_audio(self, video_id):
        video_id = self.validate_video_id(video_id)
        cached = self.cached_audio_path(video_id)
        if cached is not None:
            return cached
        if self._audio_cache_directory is None:
            raise HomeMusicError("Audio cache is not configured")

        with self._audio_cache_lock(video_id) as acquired:
            if not acquired:
                raise HomeMusicError("Audio cache is busy")
            cached = self.cached_audio_path(video_id)
            if cached is not None:
                return cached

            directory = self._ensure_audio_cache_directory()
            work_directory = Path(tempfile.mkdtemp(
                prefix=f".{video_id}.",
                dir=directory,
            ))
            staging_path = directory / f".{video_id}.{os.getpid()}.m4a.tmp"
            try:
                downloaded_path = Path(self.download_audio(video_id, work_directory))
                if downloaded_path.parent == work_directory:
                    os.replace(downloaded_path, staging_path)
                else:
                    shutil.copyfile(downloaded_path, staging_path)
                staging_path.chmod(0o600)
                with staging_path.open("rb") as audio_file:
                    os.fsync(audio_file.fileno())
                size = staging_path.stat().st_size
                if not MINIMUM_AUDIO_FILE_BYTES <= size <= MAXIMUM_AUDIO_FILE_BYTES:
                    raise HomeMusicError("Audio provider returned an invalid file")
                destination = self._audio_cache_path(video_id)
                os.replace(staging_path, destination)
                details = self._read_shared_stream(video_id)
                self._write_audio_metadata(
                    video_id,
                    details.duration_seconds if details is not None else None,
                    size,
                )
                return destination
            finally:
                staging_path.unlink(missing_ok=True)
                shutil.rmtree(work_directory, ignore_errors=True)

    def schedule_audio_cache(self, video_id):
        if self._audio_cache_directory is None:
            return False
        video_id = self.validate_video_id(video_id)
        if self.cached_audio_path(video_id, touch=False) is not None:
            return False
        with self._lock:
            if video_id in self._audio_warmups or video_id in self._audio_warm_queued:
                return False
            self._audio_warm_queue.append(video_id)
            self._audio_warm_queued.add(video_id)
            if self._audio_warm_worker is None or not self._audio_warm_worker.is_alive():
                self._audio_warm_worker = threading.Thread(
                    target=self._run_audio_warm_queue,
                    name="music-audio-warmer",
                    daemon=True,
                )
                self._audio_warm_worker.start()
        return True

    def schedule_audio_cache_many(self, video_ids, limit=20):
        queued = 0
        cached = 0
        seen = set()
        for value in video_ids:
            if len(seen) >= max(0, min(int(limit), 50)):
                break
            try:
                video_id = self.validate_video_id(value)
            except ValueError:
                continue
            if video_id in seen:
                continue
            seen.add(video_id)
            if self.cached_audio_path(video_id, touch=False) is not None:
                cached += 1
            elif self.schedule_audio_cache(video_id):
                queued += 1
        return {"requested": len(seen), "cached": cached, "queued": queued}

    def _run_audio_warm_queue(self):
        while True:
            with self._lock:
                if not self._audio_warm_queue:
                    self._audio_warm_worker = None
                    return
                video_id = self._audio_warm_queue.popleft()
                self._audio_warm_queued.discard(video_id)
                self._audio_warmups.add(video_id)
            try:
                self.cache_audio(video_id)
            except HomeMusicError:
                logger.warning(
                    "HomeMusic audio cache warm failed track=%s",
                    hashlib.sha256(video_id.encode()).hexdigest()[:12],
                )
            finally:
                with self._lock:
                    self._audio_warmups.discard(video_id)

    def maintain_audio_cache(
        self,
        retained_track_ids,
        warm_track_ids=(),
        max_bytes=DEFAULT_AUDIO_CACHE_MAX_BYTES,
        target_bytes=DEFAULT_AUDIO_CACHE_TARGET_BYTES,
        max_idle_seconds=DEFAULT_AUDIO_CACHE_MAX_IDLE_SECONDS,
    ):
        directory = self._ensure_audio_cache_directory()
        if directory is None:
            return {
                "files": 0,
                "bytes": 0,
                "removed": 0,
                "warmed": 0,
            }

        retained = {
            self.validate_video_id(video_id)
            for video_id in retained_track_ids
        }
        removed = 0
        now = time.time()
        files = []
        for path in directory.glob("*.m4a"):
            try:
                stat = path.stat()
                video_id = path.stem
                self.validate_video_id(video_id)
            except (FileNotFoundError, ValueError):
                path.unlink(missing_ok=True)
                removed += 1
                continue
            if not MINIMUM_AUDIO_FILE_BYTES <= stat.st_size <= MAXIMUM_AUDIO_FILE_BYTES:
                path.unlink(missing_ok=True)
                self._audio_metadata_path(path.stem).unlink(missing_ok=True)
                removed += 1
                continue
            if (
                video_id not in retained
                and now - stat.st_mtime > max_idle_seconds
            ):
                path.unlink(missing_ok=True)
                self._audio_metadata_path(video_id).unlink(missing_ok=True)
                removed += 1
                continue
            files.append((path, video_id, stat.st_size, stat.st_mtime))

        total_bytes = sum(item[2] for item in files)
        if total_bytes > max_bytes:
            eviction_order = sorted(
                files,
                key=lambda item: (item[1] in retained, item[3]),
            )
            for path, _, size, _ in eviction_order:
                if total_bytes <= target_bytes:
                    break
                path.unlink(missing_ok=True)
                self._audio_metadata_path(path.stem).unlink(missing_ok=True)
                total_bytes -= size
                removed += 1

        def warm_audio(video_id):
            try:
                video_id = self.validate_video_id(video_id)
                if self.cached_audio_path(video_id, touch=False) is not None:
                    return 0
                self.cache_audio(video_id)
                return 1
            except (HomeMusicError, ValueError):
                logger.warning(
                    "HomeMusic scheduled cache warm failed",
                    exc_info=True,
                )
                return 0

        warm_candidates = list(dict.fromkeys(warm_track_ids))
        if warm_candidates:
            with ThreadPoolExecutor(
                max_workers=min(5, len(warm_candidates)),
                thread_name_prefix="music-cache-fill",
            ) as executor:
                warmed = sum(executor.map(warm_audio, warm_candidates))
        else:
            warmed = 0

        final_files = []
        for path in directory.glob("*.m4a"):
            try:
                final_files.append(path.stat().st_size)
            except FileNotFoundError:
                pass
        return {
            "files": len(final_files),
            "bytes": sum(final_files),
            "removed": removed,
            "warmed": warmed,
        }

    def cached_genre_track_ids(self, max_entries=24, per_entry_limit=12):
        if self._genre_cache_directory is None:
            return []
        candidates = []
        for access_path in self._genre_cache_directory.glob("*.access"):
            try:
                candidates.append((access_path.stat().st_mtime, access_path.stem))
            except FileNotFoundError:
                continue
        track_ids = []
        seen = set()
        for _, digest in sorted(candidates, reverse=True)[:max(0, int(max_entries))]:
            try:
                payload = json.loads(
                    (self._genre_cache_directory / f"{digest}.json").read_text()
                )["value"]
            except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError):
                continue
            tracks = list(payload.get("popular") or []) + list(
                payload.get("classics") or []
            )
            added = 0
            for track in tracks:
                try:
                    video_id = self.validate_video_id(track.get("id"))
                except (AttributeError, ValueError):
                    continue
                if video_id in seen:
                    continue
                seen.add(video_id)
                track_ids.append(video_id)
                added += 1
                if added >= max(0, int(per_entry_limit)):
                    break
        return track_ids

    def invalidate_stream(self, video_id):
        video_id = self.validate_video_id(video_id)
        with self._lock:
            self._stream_cache.pop(video_id, None)
        if self._stream_cache_directory is not None:
            self._shared_stream_path(video_id).unlink(missing_ok=True)

    @staticmethod
    def _youtube_dl_options():
        cache_directory = os.environ.get("HOME_OS_YTDLP_CACHE_DIR")
        options = {
            "cachedir": cache_directory or False,
            "extract_flat": False,
            "extractor_retries": 3,
            "format": (
                "bestaudio[ext=m4a][protocol^=http]/"
                "bestaudio[acodec^=mp4a][protocol^=http]/"
                "bestaudio[protocol^=http]/bestaudio/best"
            ),
            "js_runtimes": {"node": {"path": "/usr/bin/node"}},
            "noplaylist": True,
            "noprogress": True,
            "no_warnings": True,
            "quiet": True,
            "remote_components": ["ejs:github"],
            "retries": 3,
            "socket_timeout": 20,
        }
        cookie_file = os.environ.get("HOME_OS_YTDLP_COOKIE_FILE")
        if cookie_file and Path(cookie_file).is_file():
            options["cookiefile"] = cookie_file
        return options

    @staticmethod
    def _stream_expiry(stream_url):
        raw_expiry = parse_qs(urlsplit(stream_url).query).get("expire", [None])[0]
        try:
            expires_at = int(raw_expiry)
        except (TypeError, ValueError):
            return None
        return expires_at if expires_at > int(time.time()) else None

    def _stream_cache_ttl(self, details):
        if details.expires_at is None:
            return min(self.stream_ttl, STREAM_FALLBACK_TTL_SECONDS)
        usable_for = details.expires_at - time.time() - STREAM_EXPIRY_SAFETY_SECONDS
        return max(0, min(self.stream_ttl, usable_for))

    def _store_stream_in_memory(self, video_id, details):
        ttl = self._stream_cache_ttl(details)
        if ttl > 0:
            self._store_cached(self._stream_cache, video_id, details, ttl)

    def _ensure_stream_cache_directory(self):
        if self._stream_cache_directory is None:
            return None
        self._stream_cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._stream_cache_directory.chmod(0o700)
        return self._stream_cache_directory

    def _shared_stream_path(self, video_id):
        return self._stream_cache_directory / f"{video_id}.json"

    def _shared_lock_path(self, video_id):
        return self._stream_cache_directory / f"{video_id}.lock"

    @contextmanager
    def _extraction_slot(self):
        directory = self._ensure_stream_cache_directory()
        if directory is None:
            with self._extraction_lock:
                wait_for = (
                    self.extraction_interval
                    - (time.monotonic() - self._last_extraction_at)
                )
                if wait_for > 0:
                    time.sleep(wait_for)
                self._last_extraction_at = time.monotonic()
            yield
            return

        lock_path = directory / ".extractor.lock"
        with lock_path.open("a+") as lock_file:
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                lock_file.seek(0)
                try:
                    last_started_at = float(lock_file.read() or 0)
                except ValueError:
                    last_started_at = 0
                wait_for = (
                    self.extraction_interval
                    - (time.time() - last_started_at)
                )
                if wait_for > 0:
                    time.sleep(wait_for)
                lock_file.seek(0)
                lock_file.truncate()
                lock_file.write(str(time.time()))
                lock_file.flush()
                os.fsync(lock_file.fileno())
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        yield

    @contextmanager
    def _stream_resolution_lock(self, video_id):
        directory = self._ensure_stream_cache_directory()
        if directory is None:
            yield
            return
        lock_path = self._shared_lock_path(video_id)
        with lock_path.open("a") as lock_file:
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_shared_stream(self, video_id):
        if self._stream_cache_directory is None:
            return None
        path = self._shared_stream_path(video_id)
        try:
            payload = json.loads(path.read_text())
            raw_duration = payload.get("duration_seconds")
            raw_expiry = payload.get("expires_at")
            details = StreamDetails(
                url=str(payload["url"]),
                duration_seconds=float(raw_duration) if raw_duration is not None else None,
                expires_at=int(raw_expiry) if raw_expiry is not None else None,
            )
            cache_expires_at = float(payload["cache_expires_at"])
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            path.unlink(missing_ok=True)
            return None
        if cache_expires_at <= time.time() or not self._is_allowed_stream_url(details.url):
            path.unlink(missing_ok=True)
            return None
        return details

    def _write_shared_stream(self, video_id, details):
        directory = self._ensure_stream_cache_directory()
        if directory is None:
            return
        ttl = self._stream_cache_ttl(details)
        if ttl <= 0:
            return
        payload = {
            "url": details.url,
            "duration_seconds": details.duration_seconds,
            "expires_at": details.expires_at,
            "cache_expires_at": time.time() + ttl,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{video_id}.",
            suffix=".tmp",
            dir=directory,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as cache_file:
                json.dump(payload, cache_file, separators=(",", ":"))
                cache_file.flush()
                os.fsync(cache_file.fileno())
            os.replace(temporary_path, self._shared_stream_path(video_id))
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _log_stream_resolution(video_id, cache_status, started_at):
        track_hash = hashlib.sha256(video_id.encode()).hexdigest()[:12]
        elapsed_ms = round((time.monotonic() - started_at) * 1000)
        logger.info(
            "HomeMusic source track=%s cache=%s resolution_ms=%d",
            track_hash,
            cache_status,
            elapsed_ms,
        )

    @staticmethod
    def _classify_extraction_failure(error):
        message = str(error).casefold()
        if "not a bot" in message or "login_required" in message:
            return "anti_bot"
        if "private video" in message:
            return "private"
        if "members-only" in message or "members only" in message:
            return "members_only"
        if "age-restricted" in message or "age restricted" in message:
            return "age_restricted"
        if "not available in your country" in message or "geo" in message:
            return "geo_restricted"
        if "video unavailable" in message or "has been removed" in message:
            return "unavailable"
        if "no video formats" in message or "requested format" in message:
            return "no_format"
        if (
            "timed out" in message
            or "temporary failure in name resolution" in message
            or "network is unreachable" in message
        ):
            return "network"
        return "provider"

    @staticmethod
    def _log_extraction_failure(video_id, reason, error):
        track_hash = hashlib.sha256(video_id.encode()).hexdigest()[:12]
        logger.warning(
            "HomeMusic source failed track=%s reason=%s error_type=%s",
            track_hash,
            reason,
            type(error).__name__,
        )

    def _ensure_feed_cache_directory(self):
        if self._feed_cache_directory is None:
            return None
        self._feed_cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._feed_cache_directory.chmod(0o700)
        return self._feed_cache_directory

    def _feed_cache_digest(self, cache_key):
        return hashlib.sha256(f"feed:v2:{cache_key}".encode()).hexdigest()

    def _shared_feed_path(self, cache_key):
        return self._feed_cache_directory / f"{self._feed_cache_digest(cache_key)}.json"

    def _shared_feed_lock_path(self, cache_key):
        return self._feed_cache_directory / f"{self._feed_cache_digest(cache_key)}.lock"

    @contextmanager
    def _shared_feed_lock(self, cache_key, blocking=True):
        directory = self._ensure_feed_cache_directory()
        if directory is None:
            yield True
            return
        lock_path = self._shared_feed_lock_path(cache_key)
        with lock_path.open("a") as lock_file:
            lock_path.chmod(0o600)
            operation = fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(lock_file.fileno(), operation)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_shared_feed(self, cache_key):
        if self._feed_cache_directory is None:
            return None
        path = self._shared_feed_path(cache_key)
        try:
            payload = json.loads(path.read_text())
            entry = FeedCacheEntry(
                value=dict(payload["value"]),
                fresh_until=float(payload["fresh_until"]),
                stale_until=float(payload["stale_until"]),
            )
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            path.unlink(missing_ok=True)
            return None
        if entry.stale_until <= time.time():
            path.unlink(missing_ok=True)
            return None
        if not all(
            isinstance(entry.value.get(key), list)
            for key in ("suggested_songs", "suggested_albums", "new_releases")
        ):
            path.unlink(missing_ok=True)
            return None
        return FeedCacheEntry(
            value=self._sanitize_personalized_home(entry.value),
            fresh_until=entry.fresh_until,
            stale_until=entry.stale_until,
        )

    def _write_shared_feed(self, cache_key, value):
        directory = self._ensure_feed_cache_directory()
        if directory is None:
            return
        now = time.time()
        payload = {
            "value": value,
            "fresh_until": now + self.feed_fresh_ttl,
            "stale_until": now + self.feed_stale_ttl,
            "updated_at": now,
        }
        digest = self._feed_cache_digest(cache_key)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=directory,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as cache_file:
                json.dump(payload, cache_file, separators=(",", ":"))
                cache_file.flush()
                os.fsync(cache_file.fileno())
            os.replace(temporary_path, self._shared_feed_path(cache_key))
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _genre_cache_key(genre, limit):
        return f"genre-page:v7:{genre}:{limit}"

    def _ensure_genre_cache_directory(self):
        if self._genre_cache_directory is None:
            return None
        self._genre_cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._genre_cache_directory.chmod(0o700)
        return self._genre_cache_directory

    @staticmethod
    def _genre_cache_digest(cache_key):
        return hashlib.sha256(cache_key.encode()).hexdigest()

    def _shared_genre_path(self, cache_key):
        digest = self._genre_cache_digest(cache_key)
        return self._genre_cache_directory / f"{digest}.json"

    def _shared_genre_lock_path(self, cache_key):
        digest = self._genre_cache_digest(cache_key)
        return self._genre_cache_directory / f"{digest}.lock"

    def _shared_genre_access_path(self, cache_key):
        digest = self._genre_cache_digest(cache_key)
        return self._genre_cache_directory / f"{digest}.access"

    def _record_genre_access(self, cache_key):
        directory = self._ensure_genre_cache_directory()
        if directory is None:
            return
        access_path = self._shared_genre_access_path(cache_key)
        access_path.touch(mode=0o600, exist_ok=True)
        access_path.chmod(0o600)

    @contextmanager
    def _shared_genre_lock(self, cache_key):
        directory = self._ensure_genre_cache_directory()
        if directory is None:
            yield
            return
        lock_path = self._shared_genre_lock_path(cache_key)
        with lock_path.open("a") as lock_file:
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_shared_genre(self, cache_key):
        if self._genre_cache_directory is None:
            return None
        path = self._shared_genre_path(cache_key)
        try:
            payload = json.loads(path.read_text())
            value = dict(payload["value"])
            expires_at = float(payload["expires_at"])
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            path.unlink(missing_ok=True)
            return None
        if expires_at <= time.time():
            path.unlink(missing_ok=True)
            return None
        if not all(
            isinstance(value.get(key), list)
            for key in (
                "popular",
                "recent_releases",
                "classics",
                "hot_artists",
            )
        ):
            path.unlink(missing_ok=True)
            return None
        return CacheEntry(value=value, expires_at=expires_at)

    def _write_shared_genre(self, cache_key, value):
        directory = self._ensure_genre_cache_directory()
        if directory is None:
            return
        now = time.time()
        identity = cache_key.removeprefix("genre-page:v7:")
        genre, raw_limit = identity.rsplit(":", 1)
        payload = {
            "cache_key": cache_key,
            "genre": genre,
            "limit": int(raw_limit),
            "value": value,
            "expires_at": now + GENRE_PAGE_TTL_SECONDS,
            "updated_at": now,
        }
        digest = self._genre_cache_digest(cache_key)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=directory,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as cache_file:
                json.dump(payload, cache_file, separators=(",", ":"))
                cache_file.flush()
                os.fsync(cache_file.fileno())
            os.replace(temporary_path, self._shared_genre_path(cache_key))
        finally:
            temporary_path.unlink(missing_ok=True)

    def _refresh_shared_feed(
        self,
        cache_key,
        seeds,
        artists,
        excluded,
        force_refresh=False,
    ):
        with self._shared_feed_lock(cache_key) as acquired:
            if not acquired:
                raise HomeMusicError("Listen Now refresh is unavailable")
            if not force_refresh:
                cached = self._read_shared_feed(cache_key)
                if cached is not None and cached.fresh_until > time.time():
                    return cached.value
            payload = self._generate_personalized_home(seeds, artists, excluded)
            self._write_shared_feed(cache_key, payload)
            return payload

    def _schedule_feed_refresh(self, cache_key, seeds, artists, excluded):
        digest = self._feed_cache_digest(cache_key)
        with self._lock:
            if digest in self._feed_refreshes:
                return
            self._feed_refreshes.add(digest)

        def refresh():
            try:
                with self._shared_feed_lock(cache_key, blocking=False) as acquired:
                    if not acquired:
                        return
                    cached = self._read_shared_feed(cache_key)
                    if cached is not None and cached.fresh_until > time.time():
                        return
                    payload = self._generate_personalized_home(
                        seeds,
                        artists,
                        excluded,
                    )
                    self._write_shared_feed(cache_key, payload)
            except Exception:
                logger.warning(
                    "HomeMusic background feed refresh failed",
                    exc_info=True,
                )
            finally:
                with self._lock:
                    self._feed_refreshes.discard(digest)

        threading.Thread(
            target=refresh,
            name=f"music-feed-{digest[:8]}",
            daemon=True,
        ).start()

    def _get_ytmusic(self):
        with self._lock:
            if self._ytmusic is not None:
                return self._ytmusic
        client = getattr(self._ytmusic_local, "client", None)
        if client is None:
            from ytmusicapi import YTMusic

            client = YTMusic()
            self._ytmusic_local.client = client
        return client

    def _filter_unavailable_tracks(self, tracks):
        filtered = []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            try:
                unavailable = self.is_track_unavailable(track.get("id"))
            except ValueError:
                continue
            if not unavailable:
                filtered.append(track)
        return filtered

    def _sanitize_personalized_home(self, payload):
        sanitized = dict(payload)
        sanitized["suggested_songs"] = self._filter_unavailable_tracks(
            payload.get("suggested_songs") or []
        )
        return sanitized

    def _normalize_tracks(self, results, limit, filter_non_audio=True):
        tracks = []
        seen_ids = set()
        for result in results:
            video_type = str(result.get("videoType") or "").strip()
            if (
                filter_non_audio
                and video_type
                and video_type not in MUSIC_AUDIO_VIDEO_TYPES
            ):
                continue
            video_id = result.get("videoId")
            if not video_id or video_id in seen_ids:
                continue
            try:
                video_id = self.validate_video_id(video_id)
            except ValueError:
                continue
            title = str(result.get("title") or "").strip()
            artist_names = [
                str(artist.get("name") or "").strip()
                for artist in (result.get("artists") or [])
                if artist.get("name")
            ]
            artist_id = None
            for artist in result.get("artists") or []:
                try:
                    artist_id = self.validate_browse_id(
                        artist.get("id") or artist.get("browseId")
                    )
                except ValueError:
                    continue
                break
            if not title or not artist_names:
                continue
            thumbnails = result.get("thumbnails") or result.get("thumbnail") or []
            thumbnail = ""
            if thumbnails:
                thumbnail = max(
                    thumbnails,
                    key=lambda item: int(item.get("width") or 0),
                ).get("url", "")
                thumbnail = self._high_resolution_thumbnail(thumbnail)
            dur_str = result.get("duration")
            dur_sec = self._parse_duration_seconds(dur_str, result.get("duration_seconds"))
            tracks.append({
                "id": video_id,
                "title": title,
                "artist": ", ".join(artist_names),
                "artist_id": artist_id,
                "thumbnail": thumbnail,
                "duration": dur_str,
                "duration_seconds": dur_sec,
                "explicit": bool(result.get("isExplicit")),
            })
            seen_ids.add(video_id)
            if len(tracks) >= limit:
                break
        return tracks

    @staticmethod
    def _parse_duration_seconds(duration_str, duration_seconds_val):
        if isinstance(duration_seconds_val, (int, float)) and duration_seconds_val > 0:
            return int(duration_seconds_val)
        if not duration_str or not isinstance(duration_str, str):
            return None
        try:
            parts = [int(p) for p in duration_str.strip().split(":")]
            if len(parts) == 1:
                return parts[0]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        except Exception:
            pass
        return None

    def _normalize_artist(self, result):
        try:
            browse_id = self.validate_browse_id(result.get("browseId"))
        except ValueError:
            return None
        name = str(result.get("artist") or result.get("title") or "").strip()
        if not name:
            return None
        return {
            "id": browse_id,
            "name": name,
            "thumbnail": self._largest_thumbnail(result.get("thumbnails")),
            "subscribers": result.get("subscribers"),
        }

    def _normalize_releases(self, results):
        releases = []
        for result in results:
            try:
                browse_id = self.validate_browse_id(result.get("browseId"))
            except ValueError:
                continue
            title = str(result.get("title") or "").strip()
            if not title:
                continue
            releases.append({
                "id": browse_id,
                "title": title,
                "artist": ", ".join(
                    str(artist.get("name") or "").strip()
                    for artist in (result.get("artists") or [])
                    if artist.get("name")
                ),
                "thumbnail": self._largest_thumbnail(result.get("thumbnails")),
                "year": str(result.get("year") or ""),
                "type": str(result.get("type") or "Album"),
            })
        return releases

    @staticmethod
    def _largest_thumbnail(thumbnails):
        values = thumbnails or []
        if not values:
            return ""
        item = max(values, key=lambda value: int(value.get("width") or 0))
        return HomeMusicService._high_resolution_thumbnail(str(item.get("url") or ""))

    @staticmethod
    def _high_resolution_thumbnail(value, maximum_dimension=1200):
        url = str(value or "")
        match = THUMBNAIL_SIZE_PATTERN.search(url)
        if match is None:
            return url
        width, height = int(match.group(1)), int(match.group(2))
        largest = max(width, height)
        if largest <= 0 or largest >= maximum_dimension:
            return url
        scale = maximum_dimension / largest
        upgraded_width = max(1, round(width * scale))
        upgraded_height = max(1, round(height * scale))
        return THUMBNAIL_SIZE_PATTERN.sub(
            f"=w{upgraded_width}-h{upgraded_height}{match.group(3)}",
            url,
        )

    @staticmethod
    def _is_allowed_stream_url(value):
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and bool(hostname)
            and (hostname == "googlevideo.com" or hostname.endswith(".googlevideo.com"))
        )

    def _get_cached(self, cache, key):
        now = time.monotonic()
        with self._lock:
            entry = cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                cache.pop(key, None)
                return None
            return entry.value

    def _get_shared_metadata(self, namespace, key):
        if self._metadata_cache_directory is None:
            return None
        digest = hashlib.sha256(
            f"{namespace}:{json.dumps(key, sort_keys=True, default=str)}".encode()
        ).hexdigest()
        path = self._metadata_cache_directory / f"{digest}.json"
        try:
            payload = json.loads(path.read_text())
            if float(payload["expires_at"]) <= time.time():
                path.unlink(missing_ok=True)
                return None
            return payload["value"]
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            path.unlink(missing_ok=True)
            return None

    def _store_shared_metadata(self, namespace, key, value, ttl):
        if self._metadata_cache_directory is None:
            return
        self._metadata_cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._metadata_cache_directory.chmod(0o700)
        digest = hashlib.sha256(
            f"{namespace}:{json.dumps(key, sort_keys=True, default=str)}".encode()
        ).hexdigest()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=self._metadata_cache_directory,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as cache_file:
                json.dump(
                    {"value": value, "expires_at": time.time() + ttl},
                    cache_file,
                    separators=(",", ":"),
                )
                cache_file.flush()
                os.fsync(cache_file.fileno())
            os.replace(
                temporary_path,
                self._metadata_cache_directory / f"{digest}.json",
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    def _store_cached(self, cache, key, value, ttl):
        with self._lock:
            cache[key] = CacheEntry(value=value, expires_at=time.monotonic() + ttl)


home_music_service = HomeMusicService()
