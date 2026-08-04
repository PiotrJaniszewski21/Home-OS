import json
import gzip
import sys
import threading
import time
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import call, patch

import pytest
import httpx

from home_os.app import create_app
from home_os.extensions import db
from home_os import music_cache_maintenance
from home_os.models import MusicListen, MusicPlaybackMetric, User
from home_os.services.home_music import (
    HomeMusicError,
    HomeMusicService,
    StreamDetails,
)


@pytest.fixture()
def app(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
server:
  debug: true
  secret_key: test-secret
database:
  path: "{tmp_path / 'test.db'}"
storage:
  root: storage
  trash_path: trash
"""
    )
    application = create_app(config_path)
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with application.app_context():
        user = User(username="music-user", role="admin", is_active=True)
        user.set_password("secure-password")
        db.session.add(user)
        db.session.commit()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client):
    response = client.post(
        "/api/login",
        json={"username": "music-user", "password": "secure-password"},
    )
    return response.get_json()["data"]["token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_search_requires_authentication(client):
    response = client.get("/api/music/search?q=Massive+Attack")
    assert response.status_code == 401


def test_music_api_requires_media_permission(client, app):
    with app.app_context():
        user = User(username="no-media", role="user", is_active=True, permissions="dashboard")
        user.set_password("secure-password")
        db.session.add(user)
        db.session.commit()
    token = client.post(
        "/api/login",
        json={"username": "no-media", "password": "secure-password"},
    ).get_json()["data"]["token"]
    response = client.get("/api/music/history", headers=auth_headers(token))
    assert response.status_code == 403
    assert response.get_json()["error"] == "Media access required"


def test_music_page_renders_for_media_user(client):
    token = login(client)
    response = client.get("/music", headers=auth_headers(token))
    assert response.status_code == 200
    assert b"HomeMusic" in response.data
    assert b"music-search-form" in response.data
    assert b'hm-content' in response.data
    assert b'home-music.css' in response.data
    assert b'home-music.js' in response.data


def test_search_returns_track_shape(client):
    token = login(client)
    tracks = [{
        "id": "abcdefghijk",
        "title": "Teardrop",
        "artist": "Massive Attack",
        "thumbnail": "https://example.com/art.jpg",
    }]
    with patch("home_os.modules.music.routes.home_music_service.search", return_value=tracks):
        response = client.get(
            "/api/music/search?q=Teardrop",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "data": tracks}


def test_search_excludes_video_and_user_generated_results():
    results = [
        {
            "videoId": "abcdefghijk",
            "title": "Album Version",
            "artists": [{"name": "Artist"}],
            "videoType": "MUSIC_VIDEO_TYPE_ATV",
        },
        {
            "videoId": "lmnopqrstuv",
            "title": "Official Music Video",
            "artists": [{"name": "Artist"}],
            "videoType": "MUSIC_VIDEO_TYPE_OMV",
        },
        {
            "videoId": "ponmlkjihgf",
            "title": "User Upload",
            "artists": [{"name": "Artist"}],
            "videoType": "MUSIC_VIDEO_TYPE_UGC",
        },
        {
            "videoId": "zyxwvutsrqp",
            "title": "Legacy Album Version",
            "artists": [{"name": "Artist"}],
        },
    ]

    class YTMusicStub:
        def search(self, *args, **kwargs):
            return results

    service = HomeMusicService()
    service._ytmusic = YTMusicStub()

    assert [track["id"] for track in service.search("Artist", limit=2)] == [
        "abcdefghijk",
        "zyxwvutsrqp",
    ]


def test_genres_endpoint_returns_supported_genres(client):
    token = login(client)
    response = client.get(
        "/api/music/genres",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.get_json()["data"] == sorted(
        response.get_json()["data"]
    )
    assert "Drum and Bass" in response.get_json()["data"]
    assert "Bass House" in response.get_json()["data"]
    assert "Liquid Drum and Bass" in response.get_json()["data"]
    assert "UK Garage" in response.get_json()["data"]
    assert response.headers["Cache-Control"] == "private, max-age=86400"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("reaggeton", "Reggaeton"),
        ("popular drum & bass songs", "Drum and Bass"),
        ("bass house", "Bass House"),
        ("liquid dnb", "Liquid Drum and Bass"),
        ("uk garage", "UK Garage"),
        ("Pop Smoke", None),
        ("Rockstar", None),
    ],
)
def test_genre_resolution_handles_typos_without_artist_false_positives(
    query,
    expected,
):
    assert HomeMusicService.resolve_genre(query) == expected


def test_smart_search_uses_the_same_editorial_page_for_every_user(client, app):
    token = login(client)
    with app.app_context():
        current = User.query.filter_by(username="music-user").one()
        other = User(username="other-user", role="admin", is_active=True)
        other.set_password("secure-password")
        db.session.add(other)
        db.session.flush()
        db.session.add_all([
            MusicListen(
                user_id=current.id,
                track_id="abcdefghijk",
                title="Current Track",
                artist="Bad Bunny",
                liked=True,
                play_count=4,
                completed_count=3,
            ),
            MusicListen(
                user_id=other.id,
                track_id="lmnopqrstuv",
                title="Other Track",
                artist="Other Artist",
                liked=True,
                play_count=20,
                completed_count=20,
            ),
        ])
        db.session.commit()

    with patch(
        "home_os.modules.music.routes.home_music_service.genre_page",
        return_value={
            "popular": [],
            "recent_releases": [],
            "classics": [],
            "hot_artists": [],
        },
    ) as genre_page:
        response = client.get(
            "/api/music/search/smart?q=reaggeton",
            headers=auth_headers(token),
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == (
        "private, max-age=300, stale-if-error=86400"
    )
    assert response.get_json()["data"]["genre"] == "Reggaeton"
    assert genre_page.call_args.args == ("Reggaeton",)
    assert genre_page.call_args.kwargs == {"limit": 25}


def test_smart_search_preserves_ordinary_text_search(client):
    token = login(client)
    tracks = [{
        "id": "abcdefghijk",
        "title": "Teardrop",
        "artist": "Massive Attack",
        "thumbnail": "",
    }]
    with (
        patch(
            "home_os.modules.music.routes.home_music_service.search",
            return_value=tracks,
        ) as search,
        patch(
            "home_os.modules.music.routes.home_music_service.genre_search",
        ) as genre_search,
    ):
        response = client.get(
            "/api/music/search/smart?q=Massive+Attack",
            headers=auth_headers(token),
        )

    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "tracks": tracks,
        "genre": None,
        "recent_releases": [],
        "classics": [],
        "hot_artists": [],
    }
    search.assert_called_once_with("Massive Attack", limit=25)
    genre_search.assert_not_called()


def test_genre_search_rejects_mixes_and_one_query_false_positives():
    def result(video_id, title, artist, duration=210):
        return {
            "videoId": video_id,
            "title": title,
            "artists": [{"name": artist}],
            "thumbnails": [],
            "duration_seconds": duration,
        }

    valid = result("abcdefghijk", "Real Genre Track", "Genre Artist")
    compilation = result(
        "lmnopqrstuv",
        "Summer Vibes: Drum & Bass (Continuous DJ Mix)",
        "Various Artists",
        duration=4_300,
    )
    false_positive = result(
        "ponmlkjihgf",
        "I Bet You Look Good On The Dancefloor",
        "Arctic Monkeys",
    )

    class YTMusicStub:
        def search(self, query, **kwargs):
            if str(time.gmtime().tm_year) in query:
                return [valid]
            return [compilation, false_positive, valid]

    service = HomeMusicService()
    service._ytmusic = YTMusicStub()
    tracks = service.genre_search("Dancefloor Drum and Bass")

    assert [track["id"] for track in tracks] == ["abcdefghijk"]


def test_drum_and_bass_genre_page_builds_editorial_shelves():
    def song(video_id, title, artist):
        return {
            "videoId": video_id,
            "title": title,
            "artists": [{"name": artist}],
            "thumbnails": [],
        }

    def release(browse_id, title, artist, year):
        return {
            "browseId": browse_id,
            "title": title,
            "artists": [{"name": artist}],
            "year": year,
            "type": "Album",
            "thumbnails": [],
        }

    def artist(browse_id, name):
        return {
            "browseId": browse_id,
            "artist": name,
            "thumbnails": [],
        }

    class YTMusicStub:
        def search(self, query, filter, limit):
            if filter == "artists" and query == "Sub Focus":
                return [artist("UCr1uxon7aFztw_dnrau0TDA", "Sub Focus")]
            if filter == "albums" and query == "Sub Focus":
                return [
                    release(
                        "MPREb_jYXks8Oi1Ss",
                        "Contact",
                        "Sub Focus",
                        str(time.gmtime().tm_year),
                    ),
                    release(
                        "MPREb_g59z4MBeYs5",
                        "Old Album",
                        "Sub Focus",
                        "2009",
                    ),
                    release(
                        "MPREb_QL2yXLfWqEP",
                        "Unrelated",
                        "Other Artist",
                        str(time.gmtime().tm_year),
                    ),
                    release(
                        "MPREb_8yjYXksOi1S",
                        "Sub Focus Continuous DJ Mix",
                        "Sub Focus",
                        str(time.gmtime().tm_year),
                    ),
                ]
            if filter == "songs" and query == "Sub Focus":
                return [song("abcdefghijk", "Solar System", "Sub Focus")]
            if query == "Original Nuttah Shy FX":
                return [
                    song("lmnopqrstuv", "Original Nuttah", "Shy FX"),
                    song("ponmlkjihgf", "Wrong Song", "Other Artist"),
                ]
            return []

    service = HomeMusicService()
    service._ytmusic = YTMusicStub()
    page = service.genre_page("Drum and Bass")

    assert [track["title"] for track in page["popular"]] == ["Solar System"]
    assert [release["title"] for release in page["recent_releases"]] == [
        "Contact",
    ]
    assert [track["title"] for track in page["classics"]] == [
        "Original Nuttah",
    ]
    assert [artist["name"] for artist in page["hot_artists"]] == [
        "Sub Focus",
    ]


def test_genre_page_cache_is_shared_for_24_hours_without_personalization(
    tmp_path,
):
    cache_directory = tmp_path / "genres"
    neutral_page = {
        "popular": [
            {
                "id": "abcdefghijk",
                "title": "Provider First",
                "artist": "Artist One",
                "thumbnail": "",
            },
            {
                "id": "lmnopqrstuv",
                "title": "Personal Favourite",
                "artist": "Preferred Artist",
                "thumbnail": "",
            },
        ],
        "recent_releases": [],
        "classics": [],
        "hot_artists": [],
    }
    first_worker = HomeMusicService(
        genre_cache_directory=cache_directory,
    )
    second_worker = HomeMusicService(
        genre_cache_directory=cache_directory,
    )

    with patch.object(
        first_worker,
        "_generate_genre_page_lists",
        return_value=neutral_page,
    ) as first_generate:
        first = first_worker.genre_page("Bass House", limit=25)
    with patch.object(
        second_worker,
        "_generate_genre_page_lists",
    ) as second_generate:
        unpersonalized = second_worker.genre_page(
            "Bass House",
            limit=25,
        )

    assert first == unpersonalized == neutral_page
    first_generate.assert_called_once_with("Bass House", 25)
    second_generate.assert_not_called()

    cache_key = first_worker._genre_cache_key("Bass House", 25)
    cache_path = first_worker._shared_genre_path(cache_key)
    payload = json.loads(cache_path.read_text())
    assert payload["value"] == neutral_page
    assert payload["expires_at"] - payload["updated_at"] == 24 * 60 * 60
    assert (cache_directory.stat().st_mode & 0o777) == 0o700
    assert (cache_path.stat().st_mode & 0o777) == 0o600


def test_genre_maintenance_refreshes_accessed_page_before_expiry(tmp_path):
    original = {
        "popular": [{"id": "abcdefghijk"}],
        "recent_releases": [],
        "classics": [],
        "hot_artists": [],
    }
    refreshed = {
        "popular": [{"id": "lmnopqrstuv"}],
        "recent_releases": [],
        "classics": [],
        "hot_artists": [],
    }
    service = HomeMusicService(genre_cache_directory=tmp_path)
    with patch.object(
        service,
        "_generate_genre_page_lists",
        return_value=original,
    ):
        service.genre_page("Bass House", limit=25)

    cache_key = service._genre_cache_key("Bass House", 25)
    cache_path = service._shared_genre_path(cache_key)
    payload = json.loads(cache_path.read_text())
    payload["expires_at"] = time.time() + 60
    cache_path.write_text(json.dumps(payload))

    with patch.object(
        service,
        "_generate_genre_page_lists",
        return_value=refreshed,
    ) as generate:
        result = service.maintain_genre_cache(
            refresh_ahead_seconds=120,
            warm_genres=(),
        )

    assert result == {
        "configured": True,
        "eligible": 1,
        "refreshed": 1,
        "fresh": 0,
        "failed": 0,
    }
    assert json.loads(cache_path.read_text())["value"] == refreshed
    assert json.loads(cache_path.read_text())["cache_key"] == cache_key
    access_path = service._shared_genre_access_path(cache_key)
    assert (access_path.stat().st_mode & 0o777) == 0o600
    generate.assert_called_once_with("Bass House", 25)


def test_genre_page_derives_hot_artists_from_popular_tracks():
    popular = [{
        "id": "abcdefghijk",
        "title": "Acid House Track",
        "artist": "Hot Artist",
        "artist_id": "UCr1uxon7aFztw_dnrau0TDA",
        "thumbnail": "https://example.com/artist.jpg",
    }]
    service = HomeMusicService()

    with (
        patch.object(service, "genre_search", return_value=popular),
        patch.object(service, "search_albums", return_value=[]),
        patch.object(service, "_genre_classics", return_value=[]),
    ):
        page = service.genre_page("Acid House")

    assert page["hot_artists"] == [{
        "id": "UCr1uxon7aFztw_dnrau0TDA",
        "name": "Hot Artist",
        "thumbnail": "https://example.com/artist.jpg",
        "subscribers": None,
    }]


def test_generic_genre_page_rejects_compilation_releases():
    current_year = str(time.gmtime().tm_year)
    releases = [
        {
            "id": "MPREb_jYXks8Oi1Ss",
            "title": "New Artist Album",
            "artist": "Named Artist",
            "thumbnail": "",
            "year": current_year,
            "type": "Album",
        },
        {
            "id": "MPREb_g59z4MBeYs5",
            "title": "Top 100 Hits Compilation",
            "artist": "Various Artists",
            "thumbnail": "",
            "year": current_year,
            "type": "Album",
        },
    ]
    service = HomeMusicService()

    with (
        patch.object(service, "genre_search", return_value=[]),
        patch.object(service, "search_albums", return_value=releases),
        patch.object(service, "_genre_classics", return_value=[]),
        patch.object(
            service,
            "_editorial_genre_page",
            return_value={
                "popular": [],
                "recent_releases": [],
                "classics": [],
                "hot_artists": [],
            },
        ),
    ):
        page = service.genre_page("Acid House")

    assert page["recent_releases"] == [releases[0]]


@pytest.mark.parametrize("genre", ["Afro House", "Bass House"])
def test_live_broken_genres_use_curated_editorial_profiles(genre):
    expected = {
        "popular": [{"id": "abcdefghijk"}],
        "recent_releases": [{"id": "MPREb_jYXks8Oi1Ss"}],
        "classics": [{"id": "lmnopqrstuv"}],
        "hot_artists": [{"id": "UCr1uxon7aFztw_dnrau0TDA"}],
    }
    service = HomeMusicService()

    with patch.object(
        service,
        "_editorial_genre_page",
        return_value=expected,
    ) as editorial:
        page = service.genre_page(genre)

    assert page == expected
    profile, limit = editorial.call_args.args
    assert len(profile["artists"]) >= 8
    assert len(profile["classics"]) >= 8
    assert limit == 25


def test_generic_genre_page_uses_release_artists_when_consensus_is_empty():
    current_year = str(time.gmtime().tm_year)
    release = {
        "id": "MPREb_jYXks8Oi1Ss",
        "title": "Current Genre Album",
        "artist": "Genre Artist, Featured Artist",
        "thumbnail": "",
        "year": current_year,
        "type": "Album",
    }
    fallback = {
        "popular": [{
            "id": "abcdefghijk",
            "title": "Genre Track",
            "artist": "Genre Artist",
            "thumbnail": "",
        }],
        "recent_releases": [],
        "classics": [],
        "hot_artists": [{
            "id": "UCr1uxon7aFztw_dnrau0TDA",
            "name": "Genre Artist",
            "thumbnail": "",
        }],
    }
    service = HomeMusicService()

    with (
        patch.object(service, "genre_search", return_value=[]),
        patch.object(service, "search_albums", return_value=[release]),
        patch.object(service, "_genre_classics", return_value=[]),
        patch.object(
            service,
            "_editorial_genre_page",
            return_value=fallback,
        ) as editorial,
    ):
        page = service.genre_page("Acid House")

    assert page["popular"] == fallback["popular"]
    assert page["recent_releases"] == [release]
    assert page["classics"] == []
    assert page["hot_artists"] == fallback["hot_artists"]
    assert editorial.call_args.args == ({
        "artists": ("Genre Artist",),
        "classics": (),
    }, 25)
    assert service._genre_cache_key("Acid House", 25) == (
        "genre-page:v7:Acid House:25"
    )


def test_artist_search_and_detail_endpoints(client):
    token = login(client)
    artist = {"id": "UCr1uxon7aFztw_dnrau0TDA", "name": "Massive Attack", "thumbnail": ""}
    with patch(
        "home_os.modules.music.routes.home_music_service.search_artists",
        return_value=[artist],
    ):
        response = client.get(
            "/api/music/search/artists?q=Massive+Attack",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    assert response.get_json()["data"] == [artist]

    detail = {**artist, "essentials": [], "albums": [], "singles": [], "related": []}
    with patch(
        "home_os.modules.music.routes.home_music_service.artist",
        return_value=detail,
    ):
        response = client.get(
            f"/api/music/artists/{artist['id']}",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    assert response.get_json()["data"]["name"] == "Massive Attack"


def test_album_search_and_album_artwork_fallback(client):
    token = login(client)
    album = {
        "id": "MPREb_Ibts2W4TjGD",
        "title": "Mezzanine",
        "thumbnail": "https://example.com/album.jpg",
        "year": "1998",
        "type": "Album",
    }
    with patch(
        "home_os.modules.music.routes.home_music_service.search_albums",
        return_value=[album],
    ):
        response = client.get(
            "/api/music/search/albums?q=Mezzanine",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    assert response.get_json()["data"] == [album]

    service = HomeMusicService()
    service._ytmusic = type("YTMusicStub", (), {
        "get_album": lambda self, browse_id: {
            "title": "Mezzanine",
            "artists": [{"name": "Massive Attack"}],
            "thumbnails": [{"url": "https://example.com/album.jpg", "width": 800}],
            "tracks": [{
                "videoId": "abcdefghijk",
                "title": "Teardrop",
                "artists": [{"name": "Massive Attack"}],
                "thumbnails": [],
            }],
        },
    })()
    detail = service.album("MPREb_Ibts2W4TjGD")
    assert detail["tracks"][0]["thumbnail"] == detail["thumbnail"]


def test_album_uses_catalogue_audio_playlist_tracks():
    class YTMusicStub:
        def get_album(self, browse_id):
            return {
                "title": "Mayhem",
                "artists": [{"name": "Lady Gaga"}],
                "audioPlaylistId": "OLAK5uy_mlEEjSrehSGLBIqyqBoj1ZqORtJTd-tL8",
                "tracks": [{
                    "videoId": "abcdefghijk",
                    "title": "Official Video",
                    "artists": [{"name": "Lady Gaga"}],
                    "videoType": "MUSIC_VIDEO_TYPE_OMV",
                }],
            }

        def get_playlist(self, playlist_id, limit):
            assert playlist_id == "OLAK5uy_mlEEjSrehSGLBIqyqBoj1ZqORtJTd-tL8"
            assert limit == 100
            return {
                "tracks": [{
                    "videoId": "lmnopqrstuv",
                    "title": "Album Version",
                    "artists": [{"name": "Lady Gaga"}],
                    "videoType": "MUSIC_VIDEO_TYPE_ATV",
                }],
            }

    service = HomeMusicService()
    service._ytmusic = YTMusicStub()

    detail = service.album("MPREb_fdn3rDTkRH3")

    assert [track["id"] for track in detail["tracks"]] == ["lmnopqrstuv"]


def test_album_keeps_embedded_tracks_when_audio_playlist_is_unavailable():
    class YTMusicStub:
        def get_album(self, browse_id):
            return {
                "title": "Album",
                "artists": [{"name": "Artist"}],
                "audioPlaylistId": "OLAK5uy_mlEEjSrehSGLBIqyqBoj1ZqORtJTd-tL8",
                "tracks": [{
                    "videoId": "abcdefghijk",
                    "title": "Embedded Album Track",
                    "artists": [{"name": "Artist"}],
                    "videoType": "MUSIC_VIDEO_TYPE_OMV",
                }],
            }

        def get_playlist(self, playlist_id, limit):
            raise RuntimeError("playlist unavailable")

    service = HomeMusicService()
    service._ytmusic = YTMusicStub()

    detail = service.album("MPREb_Ibts2W4TjGD")

    assert [track["id"] for track in detail["tracks"]] == ["abcdefghijk"]


def test_service_normalizes_artist_sections():
    service = HomeMusicService()
    service._ytmusic = type("YTMusicStub", (), {
        "get_artist": lambda self, browse_id: {
            "name": "Massive Attack",
            "thumbnails": [{"url": "artist.jpg", "width": 500}],
            "songs": {"results": [{
                "videoId": "abcdefghijk",
                "title": "Teardrop",
                "artists": [{"name": "Massive Attack"}],
                "thumbnails": [],
            }]},
            "albums": {"results": [{
                "browseId": "MPREb_Ibts2W4TjGD",
                "title": "Mezzanine",
                "thumbnails": [],
                "year": "1998",
            }]},
            "singles": {"results": []},
            "related": {"results": []},
        },
    })()
    artist = service.artist("UCr1uxon7aFztw_dnrau0TDA")
    assert artist["essentials"][0]["title"] == "Teardrop"
    assert artist["albums"][0]["title"] == "Mezzanine"


def test_playback_ticket_streams_audio_and_forwards_range(client):
    token = login(client)
    with patch(
        "home_os.modules.music.routes.home_music_service.stream_details",
        return_value=SimpleNamespace(
            url="https://audio.googlevideo.com/videoplayback?expire=1",
            duration_seconds=213.25,
        ),
    ):
        ticket_response = client.get(
            "/api/music/playback-url?id=abcdefghijk",
            headers=auth_headers(token),
        )
    path = ticket_response.get_json()["data"]["path"]
    assert ticket_response.get_json()["data"]["duration_seconds"] == 213.25
    upstream = SimpleNamespace(
        status_code=206,
        headers={
            "Content-Type": "audio/mp4",
            "Content-Length": "4",
            "Content-Range": "bytes 0-3/100",
            "Accept-Ranges": "bytes",
        },
        iter_bytes=lambda chunk_size: iter([b"test"]),
        close=lambda: None,
    )
    http_client = SimpleNamespace(
        build_request=lambda *args, **kwargs: (args, kwargs),
        send=lambda *args, **kwargs: upstream,
        close=lambda: None,
    )
    with (
        patch(
            "home_os.modules.music.routes.home_music_service.stream_url",
            return_value="https://audio.googlevideo.com/videoplayback?expire=1",
        ),
        patch("home_os.modules.music.routes.httpx.Client", return_value=http_client),
    ):
        response = client.get(path, headers={"Range": "bytes=0-3"})
    assert response.status_code == 206
    assert response.data == b"test"
    assert response.headers["Content-Type"] == "audio/mp4"
    assert response.headers["Content-Range"] == "bytes 0-3/100"


def test_playback_ticket_is_bound_to_track(client):
    token = login(client)
    with patch(
        "home_os.modules.music.routes.home_music_service.stream_details",
        return_value=SimpleNamespace(url="https://audio.googlevideo.com/audio", duration_seconds=200),
    ):
        ticket_response = client.get(
            "/api/music/playback-url?id=abcdefghijk",
            headers=auth_headers(token),
        )
    path = ticket_response.get_json()["data"]["path"]
    ticket = parse_qs(urlsplit(path).query)["ticket"][0]
    response = client.get(f"/proxy-stream?id=lmnopqrstuv&ticket={ticket}")
    assert response.status_code == 401


def test_cached_playback_uses_persisted_audio_duration(client, app):
    token = login(client)
    with app.app_context():
        user = User.query.filter_by(username="music-user").one()
        db.session.add(MusicListen(
            user_id=user.id,
            track_id="abcdefghijk",
            title="The Door",
            artist="Teddy Swims",
            thumbnail="",
            duration_seconds=None,
        ))
        db.session.commit()

    with (
        patch(
            "home_os.modules.music.routes.home_music_service.cached_audio_path",
            return_value="/cache/abcdefghijk.m4a",
        ),
        patch(
            "home_os.modules.music.routes.home_music_service.cached_audio_duration",
            return_value=213,
        ) as cached_audio_duration,
    ):
        response = client.get(
            "/api/music/playback-url?id=abcdefghijk",
            headers=auth_headers(token),
        )

    assert response.status_code == 200
    assert response.get_json()["data"]["duration_seconds"] == 213
    cached_audio_duration.assert_called_once_with("abcdefghijk")
    with app.app_context():
        listen = MusicListen.query.filter_by(track_id="abcdefghijk").one()
        assert listen.duration_seconds == 213


def test_cache_prepare_queues_visible_tracks(client):
    token = login(client)
    expected = {"requested": 2, "cached": 1, "queued": 1}
    with patch(
        "home_os.modules.music.routes.home_music_service.schedule_audio_cache_many",
        return_value=expected,
    ) as schedule:
        response = client.post(
            "/api/music/cache/prepare",
            json={"track_ids": ["abcdefghijk", "lmnopqrstuv"]},
            headers=auth_headers(token),
        )

    assert response.status_code == 202
    assert response.get_json()["data"] == expected
    schedule.assert_called_once_with(
        ["abcdefghijk", "lmnopqrstuv"],
        limit=20,
    )


def test_large_music_json_responses_are_gzipped(client):
    token = login(client)
    tracks = [
        {
            "id": f"track{i:06d}"[-11:],
            "title": "A deliberately long track title " * 3,
            "artist": "A deliberately long artist name " * 3,
            "thumbnail": "https://example.com/artwork.jpg",
        }
        for i in range(25)
    ]
    with patch(
        "home_os.modules.music.routes.home_music_service.search",
        return_value=tracks,
    ):
        response = client.get(
            "/api/music/search?q=test",
            headers={**auth_headers(token), "Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(response.data))["data"] == tracks


def test_proxy_refreshes_a_rejected_cached_stream(client):
    token = login(client)
    with patch(
        "home_os.modules.music.routes.home_music_service.stream_details",
        return_value=SimpleNamespace(
            url="https://audio.googlevideo.com/old",
            duration_seconds=200,
            expires_at=int(time.time()) + 21_600,
        ),
    ):
        ticket_response = client.get(
            "/api/music/playback-url?id=abcdefghijk",
            headers=auth_headers(token),
        )
    path = ticket_response.get_json()["data"]["path"]

    rejected = SimpleNamespace(
        status_code=403,
        headers={},
        close=lambda: None,
    )
    accepted = SimpleNamespace(
        status_code=206,
        headers={
            "Content-Type": "audio/mp4",
            "Content-Length": "4",
            "Content-Range": "bytes 0-3/100",
        },
        iter_bytes=lambda chunk_size: iter([b"test"]),
        close=lambda: None,
    )
    clients = [
        SimpleNamespace(
            build_request=lambda *args, **kwargs: (args, kwargs),
            send=lambda *args, **kwargs: rejected,
            close=lambda: None,
        ),
        SimpleNamespace(
            build_request=lambda *args, **kwargs: (args, kwargs),
            send=lambda *args, **kwargs: accepted,
            close=lambda: None,
        ),
    ]
    with (
        patch(
            "home_os.modules.music.routes.home_music_service.stream_url",
            side_effect=[
                "https://audio.googlevideo.com/old",
                "https://audio.googlevideo.com/refreshed",
            ],
        ),
        patch(
            "home_os.modules.music.routes.home_music_service.invalidate_stream"
        ) as invalidate_stream,
        patch(
            "home_os.modules.music.routes.httpx.Client",
            side_effect=clients,
        ),
    ):
        response = client.get(path, headers={"Range": "bytes=0-3"})

    assert response.status_code == 206
    assert response.data == b"test"
    invalidate_stream.assert_called_once_with("abcdefghijk")


def test_playback_url_can_return_validated_direct_source(client):
    token = login(client)
    direct_url = "https://audio.googlevideo.com/videoplayback?expire=1"
    source_expires_at = int(time.time()) + 21_600
    with patch(
        "home_os.modules.music.routes.home_music_service.stream_details",
        return_value=SimpleNamespace(
            url=direct_url,
            duration_seconds=200,
            expires_at=source_expires_at,
        ),
    ):
        response = client.get(
            "/api/music/playback-url?id=abcdefghijk&direct=1",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["direct_url"] == direct_url
    assert payload["path"].startswith("/proxy-stream?")
    assert payload["source_expires_at"] == source_expires_at


def test_prefetch_playback_warms_the_next_server_audio_file(client):
    token = login(client)
    stream = SimpleNamespace(
        url="https://audio.googlevideo.com/videoplayback",
        duration_seconds=200,
        expires_at=int(time.time()) + 21_600,
    )
    with (
        patch(
            "home_os.modules.music.routes.home_music_service.cached_audio_path",
            return_value=None,
        ),
        patch(
            "home_os.modules.music.routes.home_music_service.stream_details",
            return_value=stream,
        ),
        patch(
            "home_os.modules.music.routes.home_music_service.schedule_audio_cache"
        ) as schedule_audio_cache,
    ):
        prefetch = client.get(
            "/api/music/playback-url?id=abcdefghijk&direct=1&prefetch=1",
            headers=auth_headers(token),
        )
        playback = client.get(
            "/api/music/playback-url?id=abcdefghijk&direct=1",
            headers=auth_headers(token),
        )

    assert prefetch.status_code == playback.status_code == 200
    assert schedule_audio_cache.call_args_list == [
        call("abcdefghijk"),
        call("abcdefghijk"),
    ]


def test_cached_playback_uses_authenticated_proxy_and_supports_range(
    client,
    tmp_path,
):
    token = login(client)
    audio_path = tmp_path / "abcdefghijk.m4a"
    audio_path.write_bytes(b"a" * (32 * 1024))
    with (
        patch(
            "home_os.modules.music.routes.home_music_service.cached_audio_path",
            return_value=audio_path,
        ),
        patch(
            "home_os.modules.music.routes.home_music_service.stream_details",
        ) as stream_details,
    ):
        ticket_response = client.get(
            "/api/music/playback-url?id=abcdefghijk&direct=1",
            headers=auth_headers(token),
        )
        payload = ticket_response.get_json()["data"]
        response = client.get(
            payload["path"],
            headers={"Range": "bytes=0-3"},
        )

    assert ticket_response.status_code == 200
    assert payload["cache_hit"] is True
    assert payload["direct_url"] is None
    assert response.status_code == 206
    assert response.data == b"aaaa"
    assert response.headers["Content-Range"] == "bytes 0-3/32768"
    assert response.headers["X-HomeMusic-Cache"] == "hit"
    stream_details.assert_not_called()


def test_playback_metrics_are_recorded_deduplicated_and_aggregated(client, app):
    token = login(client)
    payload = {
        "event_id": "3bda22ad-bd85-440d-b2d5-32e585c10aa8",
        "track_id": "abcdefghijk",
        "scenario": "previous",
        "source_kind": "starter_cache",
        "source_ready_ms": 12,
        "audible_ms": 180,
        "success": True,
        "fallback_used": False,
        "app_version": "1.0 (1)",
        "os_version": "iOS 27.0",
    }

    response = client.post(
        "/api/music/playback-metrics",
        json=payload,
        headers=auth_headers(token),
    )
    duplicate = client.post(
        "/api/music/playback-metrics",
        json=payload,
        headers=auth_headers(token),
    )
    aggregate = client.get(
        "/api/monitor/music-playback",
        headers=auth_headers(token),
    )
    dashboard = client.get(
        "/dashboard",
        headers=auth_headers(token),
    )

    assert response.status_code == 201
    assert response.get_json()["data"]["recorded"] is True
    assert duplicate.status_code == 200
    assert duplicate.get_json()["data"]["recorded"] is False
    assert aggregate.status_code == 200
    assert dashboard.status_code == 200
    assert b"HomeMusic startup" in dashboard.data
    metrics = aggregate.get_json()["data"]
    assert metrics["overall"]["samples"] == 1
    assert metrics["overall"]["avg_audible_ms"] == 180
    assert metrics["scenarios"][0]["name"] == "previous"
    assert metrics["sources"][0]["name"] == "starter_cache"
    with app.app_context():
        stored = MusicPlaybackMetric.query.one()
        assert stored.track_id == "abcdefghijk"
        assert stored.source_ready_ms == 12


def test_playback_metrics_reject_unknown_scenarios(client):
    token = login(client)
    response = client.post(
        "/api/music/playback-metrics",
        json={
            "event_id": "3bda22ad-bd85-440d-b2d5-32e585c10aa8",
            "track_id": "abcdefghijk",
            "scenario": "surprise",
            "source_kind": "server_cache",
            "audible_ms": 100,
            "success": True,
        },
        headers=auth_headers(token),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid playback scenario"


def test_download_serves_prepared_audio_file(client, tmp_path):
    token = login(client)
    audio_path = tmp_path / "track.m4a"
    audio_path.write_bytes(b"audio-data")
    with (
        patch(
            "home_os.modules.music.routes.home_music_service.download_audio",
            return_value=str(audio_path),
        ) as download_audio,
    ):
        response = client.get(
            "/api/music/download?id=abcdefghijk",
            headers=auth_headers(token),
        )

    assert response.status_code == 200
    assert response.data == b"audio-data"
    download_audio.assert_called_once()


def test_audio_cache_is_atomic_shared_and_prunable(tmp_path):
    cache_directory = tmp_path / "audio"
    service = HomeMusicService(audio_cache_directory=cache_directory)
    source = tmp_path / "source.m4a"
    source.write_bytes(b"a" * (32 * 1024))

    with patch.object(service, "download_audio", return_value=str(source)) as download:
        first = service.cache_audio("abcdefghijk")
        second = service.cache_audio("abcdefghijk")

    assert first == second
    assert first.read_bytes() == source.read_bytes()
    assert (first.stat().st_mode & 0o777) == 0o600
    assert (cache_directory.stat().st_mode & 0o777) == 0o700
    download.assert_called_once()

    old_time = time.time() - (31 * 24 * 60 * 60)
    os.utime(first, (old_time, old_time))
    result = service.maintain_audio_cache(
        retained_track_ids=[],
        max_idle_seconds=30 * 24 * 60 * 60,
    )
    assert result["removed"] == 1
    assert result["files"] == 0


def test_audio_cache_persists_duration_metadata(tmp_path):
    cache_directory = tmp_path / "audio"
    service = HomeMusicService(
        audio_cache_directory=cache_directory,
        stream_cache_directory=tmp_path / "streams",
    )
    source = tmp_path / "source.m4a"
    source.write_bytes(b"a" * (32 * 1024))
    details = StreamDetails(
        url="https://audio.googlevideo.com/audio",
        duration_seconds=213.5,
        expires_at=int(time.time()) + 3600,
    )

    with (
        patch.object(service, "download_audio", return_value=str(source)),
        patch.object(service, "_read_shared_stream", return_value=details),
    ):
        service.cache_audio("abcdefghijk")

    assert service.cached_audio_duration("abcdefghijk") == 213.5


def test_catalog_metadata_cache_is_shared_between_workers(tmp_path):
    first = HomeMusicService(metadata_cache_directory=tmp_path)
    first._ytmusic = type("YTMusicStub", (), {
        "search": lambda self, *args, **kwargs: [{
            "videoId": "abcdefghijk",
            "title": "Teardrop",
            "artists": [{"name": "Massive Attack"}],
            "thumbnails": [],
        }],
    })()
    assert first.search("Teardrop")[0]["id"] == "abcdefghijk"

    second = HomeMusicService(metadata_cache_directory=tmp_path)
    second._ytmusic = type("FailingYTMusic", (), {
        "search": lambda self, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("shared cache was not used")
        ),
    })()
    assert second.search("Teardrop")[0]["id"] == "abcdefghijk"


def test_service_filters_invalid_song_results():
    service = HomeMusicService()
    service._ytmusic = type("YTMusicStub", (), {
        "search": lambda self, *args, **kwargs: [
            {"videoId": None, "title": "Video", "artists": [{"name": "Artist"}]},
            {"videoId": "abcdefghijk", "title": "Song", "artists": [{"name": "Artist"}], "thumbnails": []},
        ]
    })()
    assert service.search("Song") == [{
        "id": "abcdefghijk",
        "title": "Song",
        "artist": "Artist",
        "artist_id": None,
        "thumbnail": "",
        "duration": None,
        "duration_seconds": None,
        "explicit": False,
    }]


def test_service_includes_primary_artist_id_in_tracks():
    service = HomeMusicService()
    service._ytmusic = type("YTMusicStub", (), {
        "search": lambda self, *args, **kwargs: [{
            "videoId": "abcdefghijk",
            "title": "Song",
            "artists": [
                {"name": "Primary Artist", "id": "UCr1uxon7aFztw_dnrau0TDA"},
                {"name": "Featured Artist", "id": "UC2L77F4BKHQfhcYTdGcQXgQ"},
            ],
            "thumbnails": [],
        }]
    })()

    track = service.search("Song")[0]

    assert track["artist"] == "Primary Artist, Featured Artist"
    assert track["artist_id"] == "UCr1uxon7aFztw_dnrau0TDA"


def test_service_rejects_non_google_stream_redirect():
    service = HomeMusicService()
    downloader = type("DownloaderStub", (), {
        "__init__": lambda self, options: None,
        "__enter__": lambda self: self,
        "__exit__": lambda self, *args: None,
        "extract_info": lambda self, *args, **kwargs: {"url": "https://attacker.example/audio"},
    })
    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=downloader)}):
        with pytest.raises(HomeMusicError):
            service.stream_url("abcdefghijk")


def test_service_prefers_ios_compatible_m4a_streams(monkeypatch, tmp_path):
    captured_options = {}
    yt_dlp_cache = tmp_path / "yt-dlp"
    monkeypatch.setenv("HOME_OS_YTDLP_CACHE_DIR", str(yt_dlp_cache))

    class DownloaderStub:
        def __init__(self, options):
            captured_options.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, *args, **kwargs):
            return {"url": "https://audio.googlevideo.com/videoplayback?mime=audio%2Fmp4"}

    service = HomeMusicService()
    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=DownloaderStub)}):
        service.stream_url("abcdefghijk")

    assert captured_options["format"].startswith("bestaudio[ext=m4a]")
    assert captured_options["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}
    assert captured_options["remote_components"] == ["ejs:github"]
    assert captured_options["extractor_retries"] == 3
    assert captured_options["retries"] == 3
    assert captured_options["cachedir"] == str(yt_dlp_cache)


def test_service_uses_configured_youtube_cookie_file(monkeypatch, tmp_path):
    cookie_file = tmp_path / "youtube-cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setenv("HOME_OS_YTDLP_COOKIE_FILE", str(cookie_file))

    options = HomeMusicService._youtube_dl_options()

    assert options["cookiefile"] == str(cookie_file)


def test_service_classifies_provider_failures():
    assert HomeMusicService._classify_extraction_failure(
        RuntimeError("LOGIN_REQUIRED: Sign in to confirm you're not a bot")
    ) == "anti_bot"
    assert HomeMusicService._classify_extraction_failure(
        RuntimeError("Private video")
    ) == "private"
    assert HomeMusicService._classify_extraction_failure(
        RuntimeError("network is unreachable")
    ) == "network"


def test_audio_download_reuses_resolved_stream(tmp_path):
    service = HomeMusicService()
    stream = StreamDetails(
        url="https://audio.googlevideo.com/videoplayback",
        duration_seconds=213,
    )
    payload = b"a" * (32 * 1024)

    class ResponseStub:
        url = stream.url
        headers = {
            "Content-Length": str(len(payload)),
            "Content-Type": "audio/mp4",
        }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            return iter([payload])

    with (
        patch.object(service, "stream_details", return_value=stream) as resolve,
        patch(
            "home_os.services.home_music.httpx.stream",
            return_value=ResponseStub(),
        ) as download,
    ):
        path = service.download_audio("abcdefghijk", tmp_path)

    assert Path(path).read_bytes() == payload
    assert (Path(path).stat().st_mode & 0o777) == 0o600
    resolve.assert_called_once_with("abcdefghijk")
    download.assert_called_once()


def test_audio_download_refreshes_rejected_stream_once(tmp_path):
    service = HomeMusicService()
    stale_stream = StreamDetails(
        url="https://audio.googlevideo.com/stale",
        duration_seconds=213,
    )
    fresh_stream = StreamDetails(
        url="https://audio.googlevideo.com/fresh",
        duration_seconds=213,
    )
    payload = b"a" * (32 * 1024)

    class RejectedResponse:
        url = stale_stream.url
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            request = httpx.Request("GET", self.url)
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError(
                "Rejected",
                request=request,
                response=response,
            )

    class SuccessfulResponse:
        url = fresh_stream.url
        headers = {
            "Content-Length": str(len(payload)),
            "Content-Type": "audio/mp4",
        }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            return iter([payload])

    with (
        patch.object(
            service,
            "stream_details",
            side_effect=[stale_stream, fresh_stream],
        ) as resolve,
        patch.object(service, "invalidate_stream") as invalidate,
        patch(
            "home_os.services.home_music.httpx.stream",
            side_effect=[RejectedResponse(), SuccessfulResponse()],
        ) as download,
    ):
        path = service.download_audio("abcdefghijk", tmp_path)

    assert Path(path).read_bytes() == payload
    assert resolve.call_count == 2
    invalidate.assert_called_once_with("abcdefghijk")
    assert download.call_count == 2


def test_stream_extractions_are_paced_across_service_instances(tmp_path):
    starts = []
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    class DownloaderStub:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, download):
            nonlocal active, maximum_active
            with lock:
                starts.append(time.monotonic())
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.12)
            with lock:
                active -= 1
            return {
                "url": "https://audio.googlevideo.com/videoplayback",
                "duration": 213,
            }

    services = [
        HomeMusicService(
            stream_cache_directory=tmp_path,
            extraction_interval=0.05,
        )
        for _ in range(2)
    ]
    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=DownloaderStub)}):
        threads = [
            threading.Thread(
                target=service.stream_details,
                args=(track_id,),
            )
            for service, track_id in zip(
                services,
                ("abcdefghijk", "lmnopqrstuv"),
            )
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 2
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.045


def test_stream_cache_is_shared_across_service_instances(tmp_path):
    extraction_count = 0
    source_expires_at = int(time.time()) + 21_600
    source_url = (
        "https://audio.googlevideo.com/videoplayback"
        f"?mime=audio%2Fmp4&expire={source_expires_at}"
    )

    class DownloaderStub:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, *args, **kwargs):
            nonlocal extraction_count
            extraction_count += 1
            return {
                "url": source_url,
                "duration": 213,
            }

    first_worker = HomeMusicService(stream_cache_directory=tmp_path)
    second_worker = HomeMusicService(stream_cache_directory=tmp_path)
    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=DownloaderStub)}):
        first = first_worker.stream_details("abcdefghijk")
        second = second_worker.stream_details("abcdefghijk")

    assert extraction_count == 1
    assert second == first
    assert first.expires_at == source_expires_at
    assert first.duration_seconds == 213
    assert (tmp_path.stat().st_mode & 0o777) == 0o700
    assert ((tmp_path / "abcdefghijk.json").stat().st_mode & 0o777) == 0o600


def test_failed_stream_is_suppressed_across_service_instances(tmp_path):
    unavailable_cache = tmp_path / "unavailable"
    extraction_count = 0

    class DownloaderStub:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, *args, **kwargs):
            nonlocal extraction_count
            extraction_count += 1
            raise RuntimeError("provider rejected extraction")

    first_worker = HomeMusicService(
        unavailable_cache_directory=unavailable_cache,
    )
    second_worker = HomeMusicService(
        unavailable_cache_directory=unavailable_cache,
    )
    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=DownloaderStub)}):
        with pytest.raises(HomeMusicError):
            first_worker.stream_details("abcdefghijk")
        with pytest.raises(HomeMusicError):
            second_worker.stream_details("abcdefghijk")

    assert second_worker.is_track_unavailable("abcdefghijk") is True
    assert extraction_count == 1
    marker = unavailable_cache / "abcdefghijk.json"
    assert (unavailable_cache.stat().st_mode & 0o777) == 0o700
    assert (marker.stat().st_mode & 0o777) == 0o600


def test_cached_audio_clears_unavailable_suppression(tmp_path):
    unavailable_cache = tmp_path / "unavailable"
    audio_cache = tmp_path / "audio"
    service = HomeMusicService(
        unavailable_cache_directory=unavailable_cache,
        audio_cache_directory=audio_cache,
    )
    second_worker = HomeMusicService(
        unavailable_cache_directory=unavailable_cache,
    )
    service.mark_track_unavailable("abcdefghijk")
    audio_cache.mkdir()
    (audio_cache / "abcdefghijk.m4a").write_bytes(b"a" * (32 * 1024))

    assert service.cached_audio_path("abcdefghijk") is not None
    assert second_worker.is_track_unavailable("abcdefghijk") is False


def test_history_drives_recommendations(client):
    token = login(client)
    track = {
        "id": "abcdefghijk",
        "title": "Teardrop",
        "artist": "Massive Attack",
        "thumbnail": "https://example.com/art.jpg",
        "played_seconds": 220,
        "completed": True,
    }
    response = client.post(
        "/api/music/history",
        json=track,
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    suggested = [{"id": "lmnopqrstuv", "title": "Angel", "artist": "Massive Attack", "thumbnail": ""}]
    with patch("home_os.modules.music.routes.home_music_service.recommendations", return_value=suggested) as recommend:
        response = client.get("/api/music/recommendations", headers=auth_headers(token))
    assert response.get_json()["data"] == suggested
    assert recommend.call_args.args[0] == ["abcdefghijk"]


def test_history_completion_updates_one_play_without_double_counting(client, app):
    token = login(client)
    headers = auth_headers(token)
    track = {
        "id": "abcdefghijk",
        "title": "Teardrop",
        "artist": "Massive Attack",
        "thumbnail": "",
        "duration_seconds": 210,
    }

    first = client.post(
        "/api/music/history",
        json={**track, "played_seconds": 30, "completed": False},
        headers=headers,
    )
    completed = client.post(
        "/api/music/history",
        json={**track, "played_seconds": 210, "completed": True},
        headers=headers,
    )

    assert first.status_code == completed.status_code == 200
    with app.app_context():
        listen = MusicListen.query.filter_by(track_id="abcdefghijk").one()
        assert listen.play_count == 1
        assert listen.completed_count == 1
        assert listen.total_play_seconds == 210


def test_history_does_not_erase_known_duration(client, app):
    token = login(client)
    headers = auth_headers(token)
    track = {
        "id": "abcdefghijk",
        "title": "The Door",
        "artist": "Teddy Swims",
        "thumbnail": "",
    }
    client.post(
        "/api/music/history",
        json={**track, "duration_seconds": 213, "played_seconds": 30},
        headers=headers,
    )
    response = client.post(
        "/api/music/history",
        json={**track, "duration_seconds": None, "played_seconds": 30},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["duration_seconds"] == 213
    with app.app_context():
        listen = MusicListen.query.filter_by(track_id="abcdefghijk").one()
        assert listen.duration_seconds == 213


def test_cache_candidates_prioritize_recent_repeated_and_liked_tracks(
    client,
    app,
):
    token = login(client)
    headers = auth_headers(token)
    tracks = [
        ("abcdefghijk", False),
        ("lmnopqrstuv", True),
    ]
    for track_id, liked in tracks:
        client.post(
            "/api/music/history",
            json={
                "id": track_id,
                "title": "Track",
                "artist": "Artist",
                "thumbnail": "",
                "played_seconds": 30,
            },
            headers=headers,
        )
        if liked:
            client.put(
                f"/api/music/library/{track_id}",
                json={
                    "liked": True,
                    "title": "Track",
                    "artist": "Artist",
                    "thumbnail": "",
                },
                headers=headers,
            )

    response = client.get(
        "/api/music/cache/candidates",
        headers=headers,
    )

    assert response.status_code == 200
    assert [track["id"] for track in response.get_json()["data"]] == [
        "lmnopqrstuv",
        "abcdefghijk",
    ]


def test_history_drives_personalized_home(client):
    token = login(client)
    headers = auth_headers(token)
    client.post(
        "/api/music/history",
        json={
            "id": "abcdefghijk",
            "title": "Teardrop",
            "artist": "Massive Attack",
            "thumbnail": "https://example.com/art.jpg",
            "duration_seconds": 330,
            "played_seconds": 120,
            "completed": True,
        },
        headers=headers,
    )
    feed = {
        "suggested_songs": [],
        "suggested_albums": [{
            "id": "MPREb_example",
            "title": "Mezzanine",
            "artist": "Massive Attack",
            "thumbnail": "https://example.com/album.jpg",
            "year": "1998",
            "type": "Album",
        }],
        "new_releases": [],
    }
    with patch(
        "home_os.modules.music.routes.home_music_service.personalized_home",
        return_value=feed,
    ) as personalized:
        response = client.get("/api/music/home", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["data"] == feed
    assert personalized.call_args.args[0] == ["abcdefghijk"]
    assert personalized.call_args.args[1] == ["Massive Attack"]
    assert personalized.call_args.kwargs["exclude_ids"] == ["abcdefghijk"]
    assert personalized.call_args.kwargs["cache_key"] == "user:1"
    assert personalized.call_args.kwargs["force_refresh"] is False


def test_personalized_home_route_can_force_refresh(client):
    token = login(client)
    headers = auth_headers(token)
    client.post(
        "/api/music/history",
        json={
            "id": "abcdefghijk",
            "title": "Teardrop",
            "artist": "Massive Attack",
            "thumbnail": "",
            "played_seconds": 30,
        },
        headers=headers,
    )
    with patch(
        "home_os.modules.music.routes.home_music_service.personalized_home",
        return_value={
            "suggested_songs": [],
            "suggested_albums": [],
            "new_releases": [],
        },
    ) as personalized:
        response = client.get("/api/music/home?refresh=1", headers=headers)

    assert response.status_code == 200
    assert personalized.call_args.kwargs["force_refresh"] is True


def test_personalized_feed_cache_is_shared_and_protected(tmp_path):
    feed = {
        "suggested_songs": [{"id": "lmnopqrstuv"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    first_worker = HomeMusicService(feed_cache_directory=tmp_path)
    second_worker = HomeMusicService(feed_cache_directory=tmp_path)

    with patch.object(
        first_worker,
        "_generate_personalized_home",
        return_value=feed,
    ) as first_generate:
        first = first_worker.personalized_home(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
        )
    with patch.object(
        second_worker,
        "_generate_personalized_home",
    ) as second_generate:
        second = second_worker.personalized_home(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
        )

    assert first == second == feed
    first_generate.assert_called_once()
    second_generate.assert_not_called()
    cache_path = first_worker._shared_feed_path("user:1")
    assert (tmp_path.stat().st_mode & 0o777) == 0o700
    assert (cache_path.stat().st_mode & 0o777) == 0o600
    assert "user:1" not in cache_path.name


def test_personalized_feed_filters_suppressed_tracks_from_existing_cache(tmp_path):
    feed_cache = tmp_path / "feeds"
    unavailable_cache = tmp_path / "unavailable"
    service = HomeMusicService(
        feed_cache_directory=feed_cache,
        unavailable_cache_directory=unavailable_cache,
    )
    service._write_shared_feed(
        "user:1",
        {
            "suggested_songs": [
                {"id": "abcdefghijk"},
                {"id": "lmnopqrstuv"},
            ],
            "suggested_albums": [],
            "new_releases": [],
        },
    )
    service.mark_track_unavailable("abcdefghijk")

    result = service.personalized_home(
        ["zyxwvutsrqp"],
        [],
        cache_key="user:1",
    )

    assert result["suggested_songs"] == [{"id": "lmnopqrstuv"}]


def test_contextual_recommendations_filter_suppressed_tracks(tmp_path):
    service = HomeMusicService(
        unavailable_cache_directory=tmp_path / "unavailable",
    )
    service.mark_track_unavailable("abcdefghijk")
    service._ytmusic = type("YTMusicStub", (), {
        "get_watch_playlist": lambda self, **kwargs: {
            "tracks": [
                {
                    "videoId": "abcdefghijk",
                    "title": "Unavailable",
                    "artists": [{"name": "Artist"}],
                },
                {
                    "videoId": "lmnopqrstuv",
                    "title": "Playable",
                    "artists": [{"name": "Artist"}],
                },
            ],
        },
    })()

    result = service.recommendations(["zyxwvutsrqp"])

    assert [track["id"] for track in result] == ["lmnopqrstuv"]


def test_personalized_feed_cold_requests_share_one_generation(tmp_path):
    feed = {
        "suggested_songs": [],
        "suggested_albums": [],
        "new_releases": [],
    }
    first_worker = HomeMusicService(feed_cache_directory=tmp_path)
    second_worker = HomeMusicService(feed_cache_directory=tmp_path)
    generation_started = threading.Event()
    allow_generation = threading.Event()
    results = []

    def generate(*args):
        generation_started.set()
        assert allow_generation.wait(timeout=2)
        return feed

    def load(service):
        results.append(service.personalized_home(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
        ))

    with (
        patch.object(
            first_worker,
            "_generate_personalized_home",
            side_effect=generate,
        ) as first_generate,
        patch.object(
            second_worker,
            "_generate_personalized_home",
            return_value=feed,
        ) as second_generate,
    ):
        first_thread = threading.Thread(target=load, args=(first_worker,))
        second_thread = threading.Thread(target=load, args=(second_worker,))
        first_thread.start()
        assert generation_started.wait(timeout=1)
        second_thread.start()
        time.sleep(0.05)
        allow_generation.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert results == [feed, feed]
    first_generate.assert_called_once()
    second_generate.assert_not_called()


def test_personalized_feed_returns_stale_while_refreshing(tmp_path):
    stale_feed = {
        "suggested_songs": [{"id": "abcdefghijk"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    refreshed_feed = {
        "suggested_songs": [{"id": "lmnopqrstuv"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    service = HomeMusicService(
        feed_cache_directory=tmp_path,
        feed_fresh_ttl=0,
        feed_stale_ttl=60,
    )
    service._write_shared_feed("user:1", stale_feed)
    refresh_started = threading.Event()
    allow_refresh = threading.Event()

    def generate(*args):
        refresh_started.set()
        assert allow_refresh.wait(timeout=2)
        return refreshed_feed

    with patch.object(
        service,
        "_generate_personalized_home",
        side_effect=generate,
    ):
        started_at = time.monotonic()
        result = service.personalized_home(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
        )
        elapsed = time.monotonic() - started_at
        assert result == stale_feed
        assert elapsed < 0.2
        assert refresh_started.wait(timeout=1)
        allow_refresh.set()
        deadline = time.monotonic() + 2
        while (
            service._read_shared_feed("user:1").value != refreshed_feed
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

    assert service._read_shared_feed("user:1").value == refreshed_feed


def test_personalized_feed_force_refresh_replaces_fresh_cache(tmp_path):
    original_feed = {
        "suggested_songs": [{"id": "abcdefghijk"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    refreshed_feed = {
        "suggested_songs": [{"id": "lmnopqrstuv"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    service = HomeMusicService(feed_cache_directory=tmp_path)
    service._write_shared_feed("user:1", original_feed)

    with patch.object(
        service,
        "_generate_personalized_home",
        return_value=refreshed_feed,
    ) as generate:
        result = service.personalized_home(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
            force_refresh=True,
        )

    assert result == refreshed_feed
    assert service._read_shared_feed("user:1").value == refreshed_feed
    generate.assert_called_once()


def test_personalized_feed_background_refreshes_only_when_due(tmp_path):
    original_feed = {
        "suggested_songs": [{"id": "abcdefghijk"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    refreshed_feed = {
        "suggested_songs": [{"id": "lmnopqrstuv"}],
        "suggested_albums": [],
        "new_releases": [],
    }
    service = HomeMusicService(feed_cache_directory=tmp_path)
    service._write_shared_feed("user:1", original_feed)

    with patch.object(
        service,
        "_generate_personalized_home",
        return_value=refreshed_feed,
    ) as generate:
        assert service.refresh_personalized_home_if_due(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
        ) is False
        generate.assert_not_called()

        path = service._shared_feed_path("user:1")
        payload = json.loads(path.read_text())
        payload["fresh_until"] = time.time() - 1
        path.write_text(json.dumps(payload))

        assert service.refresh_personalized_home_if_due(
            ["abcdefghijk"],
            ["Massive Attack"],
            cache_key="user:1",
        ) is True

    assert service._read_shared_feed("user:1").value == refreshed_feed
    generate.assert_called_once()


def test_background_maintenance_combines_audio_genres_and_feeds():
    audio_result = {"files": 2, "bytes": 1024, "removed": 0, "warmed": 1}
    genre_result = {
        "configured": True,
        "eligible": 2,
        "refreshed": 1,
        "fresh": 1,
        "failed": 0,
    }
    feeds = [{
        "cache_key": "user:1",
        "seed_ids": ["abcdefghijk"],
        "preferred_artists": ["Massive Attack"],
        "exclude_ids": ["abcdefghijk"],
    }]
    with (
        patch.object(
            music_cache_maintenance,
            "select_cache_tracks",
            return_value=(["abcdefghijk"], ["abcdefghijk"]),
        ) as select_tracks,
        patch.object(
            music_cache_maintenance,
            "select_personalized_feeds",
            return_value=feeds,
        ),
        patch.object(
            music_cache_maintenance.home_music_service,
            "maintain_audio_cache",
            return_value=audio_result,
        ) as maintain_audio,
        patch.object(
            music_cache_maintenance.home_music_service,
            "maintain_genre_cache",
            return_value=genre_result,
        ),
        patch.object(
            music_cache_maintenance.home_music_service,
            "refresh_personalized_home_if_due",
            return_value=True,
        ) as refresh_feed,
        patch.object(
            music_cache_maintenance.home_music_service,
            "personalized_home",
            return_value={
                "suggested_songs": [
                    {"id": "lmnopqrstuv"},
                    {"id": "abcdefghijk"},
                ],
            },
        ) as load_feed,
    ):
        result = music_cache_maintenance.run_maintenance()

    assert result["genres"] == genre_result
    assert result["feeds"] == {
        "eligible": 1,
        "refreshed": 1,
        "fresh": 0,
        "failed": 0,
    }
    assert result["retained_candidates"] == 1
    assert result["warm_candidates"] == 1
    assert result["feed_warm_candidates"] == 1
    select_tracks.assert_called_once_with(warm_limit=40)
    refresh_feed.assert_called_once_with(
        ["abcdefghijk"],
        ["Massive Attack"],
        exclude_ids=["abcdefghijk"],
        cache_key="user:1",
    )
    load_feed.assert_called_once_with(
        ["abcdefghijk"],
        ["Massive Attack"],
        exclude_ids=["abcdefghijk"],
        cache_key="user:1",
    )
    maintain_audio.assert_called_once()
    assert maintain_audio.call_args.args == (
        ["abcdefghijk", "lmnopqrstuv"],
    )
    assert maintain_audio.call_args.kwargs["warm_track_ids"] == [
        "abcdefghijk",
        "lmnopqrstuv",
    ]


def test_cache_track_selection_warms_only_recent_missing_audio(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        user = User.query.filter_by(username="music-user").one()
        db.session.add_all([
            MusicListen(
                user_id=user.id,
                track_id="abcdefghijk",
                title="Newest Missing",
                artist="Artist",
                thumbnail="",
                last_played_at=now,
            ),
            MusicListen(
                user_id=user.id,
                track_id="lmnopqrstuv",
                title="Already Cached",
                artist="Artist",
                thumbnail="",
                last_played_at=now - timedelta(minutes=1),
            ),
            MusicListen(
                user_id=user.id,
                track_id="zyxwvutsrqp",
                title="Older Missing",
                artist="Artist",
                thumbnail="",
                last_played_at=now - timedelta(minutes=2),
            ),
        ])
        db.session.commit()

        with patch.object(
            music_cache_maintenance.home_music_service,
            "cached_audio_path",
            side_effect=lambda track_id, touch=False: (
                "/cache/track.m4a"
                if track_id == "lmnopqrstuv"
                else None
            ),
        ):
            retained, warm = music_cache_maintenance.select_cache_tracks(
                warm_limit=2
            )

    assert retained == [
        "abcdefghijk",
        "lmnopqrstuv",
        "zyxwvutsrqp",
    ]
    assert warm == ["abcdefghijk", "zyxwvutsrqp"]


def test_personalized_home_uses_bounded_parallel_requests():
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    watch_calls = 0
    search_calls = 0

    class YTMusicStub:
        @staticmethod
        def _request(result):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return result

        def get_watch_playlist(self, **kwargs):
            nonlocal watch_calls
            with lock:
                watch_calls += 1
            return self._request({"tracks": []})

        def search(self, *args, **kwargs):
            nonlocal search_calls
            with lock:
                search_calls += 1
            return self._request([])

    service = HomeMusicService()
    service._ytmusic = YTMusicStub()
    started_at = time.monotonic()
    result = service.personalized_home(
        ["abcdefghijk", "lmnopqrstuv", "zyxwvutsrqp"],
        ["Massive Attack", "Portishead"],
        force_refresh=True,
    )
    elapsed = time.monotonic() - started_at

    assert result == {
        "suggested_songs": [],
        "suggested_albums": [],
        "new_releases": [],
    }
    assert watch_calls == 3
    assert search_calls == 2
    assert maximum_active == 3
    assert elapsed < 0.32


def test_artist_release_searches_outlive_forced_feed_refreshes():
    watch_calls = 0
    search_calls = 0

    class YTMusicStub:
        def get_watch_playlist(self, **kwargs):
            nonlocal watch_calls
            watch_calls += 1
            return {"tracks": []}

        def search(self, *args, **kwargs):
            nonlocal search_calls
            search_calls += 1
            return []

    service = HomeMusicService(release_ttl=3600)
    service._ytmusic = YTMusicStub()
    for _ in range(2):
        service.personalized_home(
            ["abcdefghijk"],
            ["Massive Attack"],
            force_refresh=True,
        )

    assert watch_calls == 2
    assert search_calls == 1


def test_playlist_crud_and_track_management(client):
    token = login(client)
    headers = auth_headers(token)
    created = client.post(
        "/api/music/playlists",
        json={"name": "Night Drive", "description": "Late-night favourites"},
        headers=headers,
    )
    assert created.status_code == 201
    playlist = created.get_json()["data"]
    assert playlist["name"] == "Night Drive"
    assert playlist["tracks"] == []

    added = client.post(
        f"/api/music/playlists/{playlist['id']}/tracks",
        json={
            "id": "abcdefghijk",
            "title": "Teardrop",
            "artist": "Massive Attack",
            "thumbnail": "https://example.com/art.jpg",
            "duration_seconds": 330,
        },
        headers=headers,
    )
    assert added.status_code == 200
    assert added.get_json()["data"]["track_count"] == 1

    listed = client.get("/api/music/playlists", headers=headers).get_json()["data"]
    assert listed[0]["tracks"][0]["id"] == "abcdefghijk"

    removed = client.delete(
        f"/api/music/playlists/{playlist['id']}/tracks/abcdefghijk",
        headers=headers,
    )
    assert removed.status_code == 200
    assert removed.get_json()["data"]["track_count"] == 0


def test_playlist_suggestions_use_playlist_seeds_and_exclusions(client):
    token = login(client)
    headers = auth_headers(token)
    playlist = client.post(
        "/api/music/playlists",
        json={"name": "Night Drive"},
        headers=headers,
    ).get_json()["data"]
    for index, track_id in enumerate(("abcdefghijk", "lmnopqrstuv")):
        client.post(
            f"/api/music/playlists/{playlist['id']}/tracks",
            json={
                "id": track_id,
                "title": f"Song {index}",
                "artist": "Artist",
                "thumbnail": "",
            },
            headers=headers,
        )
    suggested = [{"id": "zyxwvutsrqp", "title": "Suggestion", "artist": "Artist", "thumbnail": ""}]
    with patch(
        "home_os.modules.music.routes.home_music_service.recommendations",
        return_value=suggested,
    ) as recommend:
        response = client.get(
            f"/api/music/playlists/{playlist['id']}/suggestions",
            headers=headers,
        )
    assert response.status_code == 200
    assert response.get_json()["data"] == suggested
    assert recommend.call_args.args[0] == ["abcdefghijk", "lmnopqrstuv"]
    assert recommend.call_args.kwargs["exclude_ids"] == ["abcdefghijk", "lmnopqrstuv"]


def test_contextual_recommendations_accept_queue_context(client):
    token = login(client)
    with patch(
        "home_os.modules.music.routes.home_music_service.recommendations",
        return_value=[],
    ) as recommend:
        response = client.post(
            "/api/music/recommendations/context",
            json={
                "seed_ids": ["abcdefghijk", "lmnopqrstuv"],
                "exclude_ids": ["zyxwvutsrqp"],
                "limit": 8,
            },
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    assert recommend.call_args.args[0] == ["abcdefghijk", "lmnopqrstuv"]
    assert recommend.call_args.kwargs == {"exclude_ids": ["zyxwvutsrqp"], "limit": 8}


def test_live_radio_directory_endpoint(client):
    token = login(client)
    stations = [{
        "id": "station-id",
        "name": "BBC Radio 4",
        "stream_url": "https://example.com/live.mp3",
        "artwork": "",
        "tags": ["news"],
        "is_hls": False,
    }, {
        "id": "hls-station",
        "name": "HLS Station",
        "stream_url": "https://example.com/live.m3u8",
        "artwork": "",
        "tags": ["news"],
        "is_hls": True,
    }]
    with patch(
        "home_os.modules.music.routes.live_radio_service.featured",
        return_value=stations,
    ) as featured:
        response = client.get(
            "/api/music/radio/stations?limit=20",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert len(payload) == 1
    assert payload[0]["id"] == "station-id"
    assert payload[0]["stream_url"].startswith(
        "http://localhost/api/music/radio/stream?ticket="
    )
    featured.assert_called_once_with(limit="20")


def test_live_radio_search_endpoint(client):
    token = login(client)
    with patch(
        "home_os.modules.music.routes.live_radio_service.search",
        return_value=[],
    ) as search:
        response = client.get(
            "/api/music/radio/stations?q=Jazz",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    search.assert_called_once_with("Jazz", limit=40)


def test_live_radio_stream_rejects_invalid_ticket(client):
    response = client.get("/api/music/radio/stream?ticket=invalid")
    assert response.status_code == 401


def test_live_radio_stream_proxies_signed_station(client):
    token = login(client)
    station = {
        "id": "station-id",
        "name": "Capital",
        "stream_url": "https://media.example.com/live.aac",
        "artwork": "",
        "tags": ["pop"],
        "is_hls": False,
    }
    with patch(
        "home_os.modules.music.routes.live_radio_service.featured",
        return_value=[station],
    ):
        directory_response = client.get(
            "/api/music/radio/stations",
            headers=auth_headers(token),
        )
    stream_path = urlsplit(
        directory_response.get_json()["data"][0]["stream_url"]
    ).path + "?" + urlsplit(
        directory_response.get_json()["data"][0]["stream_url"]
    ).query
    upstream = SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "audio/aac"},
        iter_bytes=lambda chunk_size: iter([b"radio"]),
        close=lambda: None,
    )
    http_client = SimpleNamespace(
        build_request=lambda *args, **kwargs: (args, kwargs),
        send=lambda *args, **kwargs: upstream,
        close=lambda: None,
    )
    with (
        patch(
            "home_os.modules.music.routes.live_radio_service.is_public_stream_url",
            return_value=True,
        ),
        patch("home_os.modules.music.routes.httpx.Client", return_value=http_client),
    ):
        response = client.get(stream_path)
    assert response.status_code == 200
    assert response.data == b"radio"
    assert response.headers["Content-Type"] == "audio/aac"
    assert response.headers["X-Accel-Buffering"] == "no"


def test_playlists_are_isolated_between_users(client, app):
    first_token = login(client)
    created = client.post(
        "/api/music/playlists",
        json={"name": "Private"},
        headers=auth_headers(first_token),
    ).get_json()["data"]

    with app.app_context():
        other = User(username="other-user", role="admin", is_active=True)
        other.set_password("secure-password")
        db.session.add(other)
        db.session.commit()
    other_token = client.post(
        "/api/login",
        json={"username": "other-user", "password": "secure-password"},
    ).get_json()["data"]["token"]

    assert client.get(
        f"/api/music/playlists/{created['id']}",
        headers=auth_headers(other_token),
    ).status_code == 404
    assert client.get(
        "/api/music/playlists",
        headers=auth_headers(other_token),
    ).get_json()["data"] == []


def test_saved_albums_are_isolated_between_users(client, app):
    first_token = login(client)
    album_id = "MPREb_Ibts2W4TjGD"
    saved = client.put(
        f"/api/music/albums/library/{album_id}",
        json={
            "title": "Mezzanine",
            "artist": "Massive Attack",
            "thumbnail": "https://example.com/album.jpg",
            "year": "1998",
            "type": "Album",
        },
        headers=auth_headers(first_token),
    )
    assert saved.status_code == 200
    assert client.get(
        "/api/music/albums/library",
        headers=auth_headers(first_token),
    ).get_json()["data"][0]["id"] == album_id

    with app.app_context():
        other = User(username="album-user", role="admin", is_active=True)
        other.set_password("secure-password")
        db.session.add(other)
        db.session.commit()
    other_token = client.post(
        "/api/login",
        json={"username": "album-user", "password": "secure-password"},
    ).get_json()["data"]["token"]
    assert client.get(
        "/api/music/albums/library",
        headers=auth_headers(other_token),
    ).get_json()["data"] == []
    assert client.delete(
        f"/api/music/albums/library/{album_id}",
        headers=auth_headers(other_token),
    ).status_code == 200
    assert client.get(
        "/api/music/albums/library",
        headers=auth_headers(first_token),
    ).get_json()["data"][0]["id"] == album_id
