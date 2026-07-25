import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
BROWSE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,80}$")
THUMBNAIL_SIZE_PATTERN = re.compile(r"=w(\d+)-h(\d+)([^?]*)$")


class HomeMusicError(RuntimeError):
    pass


@dataclass(frozen=True)
class CacheEntry:
    value: object
    expires_at: float


@dataclass(frozen=True)
class StreamDetails:
    url: str
    duration_seconds: float | None


class HomeMusicService:
    def __init__(self, search_ttl=120, stream_ttl=180):
        self.search_ttl = search_ttl
        self.stream_ttl = stream_ttl
        self._search_cache = {}
        self._stream_cache = {}
        self._lock = threading.RLock()
        self._ytmusic = None

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
        return tracks

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
        try:
            results = self._get_ytmusic().search(normalized_query, filter="artists", limit=limit)
        except Exception as error:
            raise HomeMusicError("Artist search is unavailable") from error
        artists = [self._normalize_artist(item) for item in results]
        artists = [artist for artist in artists if artist is not None][:limit]
        self._store_cached(self._search_cache, cache_key, artists, self.search_ttl)
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
        try:
            results = self._get_ytmusic().search(normalized_query, filter="albums", limit=limit)
        except Exception as error:
            raise HomeMusicError("Album search is unavailable") from error
        albums = self._normalize_releases(results)[:limit]
        self._store_cached(self._search_cache, cache_key, albums, self.search_ttl)
        return albums

    def artist(self, browse_id):
        browse_id = self.validate_browse_id(browse_id)
        cache_key = ("artist", browse_id)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
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
        return artist

    def album(self, browse_id):
        browse_id = self.validate_browse_id(browse_id)
        cache_key = ("album", browse_id)
        cached = self._get_cached(self._search_cache, cache_key)
        if cached is not None:
            return cached
        try:
            result = self._get_ytmusic().get_album(browse_id)
        except Exception as error:
            raise HomeMusicError("Album details are unavailable") from error
        thumbnail = self._largest_thumbnail(result.get("thumbnails"))
        tracks = self._normalize_tracks(result.get("tracks") or [], limit=100)
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
            return cached

        candidates = self._recommendation_candidates(seeds, limit)

        tracks = [
            track
            for track in self._normalize_tracks(candidates, limit=limit + len(excluded))
            if track["id"] not in excluded
        ][:limit]
        self._store_cached(
            self._search_cache,
            ("recommendations", cache_key),
            tracks,
            self.search_ttl,
        )
        return tracks

    def personalized_home(self, seed_ids, preferred_artists, exclude_ids=None):
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
        cache_key = (tuple(seeds), tuple(artists), tuple(sorted(excluded)))
        cached = self._get_cached(self._search_cache, ("personalized-home", cache_key))
        if cached is not None:
            return cached

        candidates = self._recommendation_candidates(seeds, 30)
        suggested_songs = [
            track
            for track in self._normalize_tracks(candidates, limit=40)
            if track["id"] not in excluded
        ][:16]
        suggested_albums = self._albums_from_tracks(candidates, limit=10)
        new_releases = []
        try:
            for artist in artists:
                releases = self._get_ytmusic().search(artist, filter="albums", limit=6)
                for release in self._normalize_releases(releases):
                    release["artist"] = artist
                    new_releases.append(release)
        except Exception:
            new_releases = []
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
        self._store_cached(
            self._search_cache,
            ("personalized-home", cache_key),
            payload,
            max(self.search_ttl, 300),
        )
        return payload

    def _recommendation_candidates(self, seeds, limit):
        candidates = []
        try:
            for seed in seeds:
                radio = self._get_ytmusic().get_watch_playlist(
                    videoId=seed,
                    radio=True,
                    limit=max(limit, 12),
                )
                candidates.extend(radio.get("tracks") or [])
        except Exception as error:
            raise HomeMusicError("Music recommendations are unavailable") from error
        return candidates

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

    def stream_url(self, video_id):
        return self.stream_details(video_id).url

    def stream_details(self, video_id):
        video_id = self.validate_video_id(video_id)
        cached = self._get_cached(self._stream_cache, video_id)
        if cached is not None:
            return cached

        try:
            from yt_dlp import YoutubeDL

            options = self._youtube_dl_options()
            options["skip_download"] = True
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(
                    f"https://music.youtube.com/watch?v={video_id}",
                    download=False,
                )
            stream_url = str(info.get("url") or "")
            raw_duration = info.get("duration")
            duration_seconds = float(raw_duration) if raw_duration is not None else None
        except Exception as error:
            raise HomeMusicError("Audio stream is currently unavailable") from error

        if not self._is_allowed_stream_url(stream_url):
            raise HomeMusicError("Audio provider returned an invalid stream URL")
        details = StreamDetails(
            url=stream_url,
            duration_seconds=duration_seconds if duration_seconds and duration_seconds > 0 else None,
        )
        self._store_cached(
            self._stream_cache,
            video_id,
            details,
            self.stream_ttl,
        )
        return details

    def download_audio(self, video_id, output_directory):
        video_id = self.validate_video_id(video_id)
        output_directory = os.path.realpath(output_directory)
        os.makedirs(output_directory, exist_ok=True)
        try:
            from yt_dlp import YoutubeDL

            options = self._youtube_dl_options()
            options.update({
                "max_filesize": 128 * 1024 * 1024,
                "outtmpl": os.path.join(output_directory, f"{video_id}.%(ext)s"),
                "overwrites": True,
            })
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(
                    f"https://music.youtube.com/watch?v={video_id}",
                    download=True,
                )
                requested = info.get("requested_downloads") or []
                candidates = [
                    item.get("filepath")
                    for item in requested
                    if item.get("filepath")
                ]
                candidates.append(downloader.prepare_filename(info))
        except Exception as error:
            raise HomeMusicError("Audio download is currently unavailable") from error

        for candidate in candidates:
            path = os.path.realpath(candidate)
            if os.path.commonpath((output_directory, path)) != output_directory:
                continue
            if os.path.isfile(path) and 0 < os.path.getsize(path) <= 128 * 1024 * 1024:
                return path
        raise HomeMusicError("Audio provider returned an invalid file")

    def invalidate_stream(self, video_id):
        video_id = self.validate_video_id(video_id)
        with self._lock:
            self._stream_cache.pop(video_id, None)

    @staticmethod
    def _youtube_dl_options():
        return {
            "cachedir": False,
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
            "retries": 3,
            "socket_timeout": 20,
        }

    def _get_ytmusic(self):
        with self._lock:
            if self._ytmusic is None:
                from ytmusicapi import YTMusic

                self._ytmusic = YTMusic()
            return self._ytmusic

    def _normalize_tracks(self, results, limit):
        tracks = []
        seen_ids = set()
        for result in results:
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
            tracks.append({
                "id": video_id,
                "title": title,
                "artist": ", ".join(artist_names),
                "artist_id": artist_id,
                "thumbnail": thumbnail,
                "duration": result.get("duration"),
                "duration_seconds": result.get("duration_seconds"),
                "explicit": bool(result.get("isExplicit")),
            })
            seen_ids.add(video_id)
            if len(tracks) >= limit:
                break
        return tracks

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

    def _store_cached(self, cache, key, value, ttl):
        with self._lock:
            cache[key] = CacheEntry(value=value, expires_at=time.monotonic() + ttl)


home_music_service = HomeMusicService()
