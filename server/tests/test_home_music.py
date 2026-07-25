import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from home_os.app import create_app
from home_os.extensions import db
from home_os.models import User
from home_os.services.home_music import HomeMusicError, HomeMusicService


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


def test_playback_url_can_return_validated_direct_source(client):
    token = login(client)
    direct_url = "https://audio.googlevideo.com/videoplayback?expire=1"
    with patch(
        "home_os.modules.music.routes.home_music_service.stream_details",
        return_value=SimpleNamespace(url=direct_url, duration_seconds=200),
    ):
        response = client.get(
            "/api/music/playback-url?id=abcdefghijk&direct=1",
            headers=auth_headers(token),
        )
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["direct_url"] == direct_url
    assert payload["path"].startswith("/proxy-stream?")


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


def test_service_prefers_ios_compatible_m4a_streams():
    captured_options = {}

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
    assert captured_options["extractor_retries"] == 3
    assert captured_options["retries"] == 3


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
