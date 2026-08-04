import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from home_os.services.instant_stream import (
    InstantPlayResolver,
    InstantPlayUnavailable,
    InstantStreamWorker,
    load_state,
    rewrite_hls_playlist,
    safe_component,
    save_state,
    select_stream_file,
    stream_destination,
)


TORRENT_HASH = "08ada5a7a6183aae1e09d831df6748d566095a10"
MAGNET = f"magnet:?xt=urn:btih:{TORRENT_HASH}&dn=Sintel"


class InstantStreamTests(unittest.TestCase):
    def test_selects_largest_non_sample_video(self):
        selected = select_stream_file([
            {"id": 0, "path": "sample.mkv", "length": 5000},
            {"id": 1, "path": "movie.mp4", "length": 3000},
            {"id": 2, "path": "notes.txt", "length": 9000},
            {"id": 3, "path": "feature.mkv", "length": 4000},
        ])

        self.assertEqual(selected, {"id": 3, "path": "feature.mkv", "length": 4000})

    def test_sanitizes_stream_destination_inside_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media_root = Path(temp_dir) / "HomeOS"
            destination = stream_destination(
                media_root,
                "radarr",
                {"hash": TORRENT_HASH, "name": "../../Unsafe"},
                {"movie": {"title": "../Sintel", "year": 2010}},
                {"id": 0, "path": "Sintel.mp4", "length": 100},
            )

        self.assertIn("Movies", destination.parts)
        self.assertNotIn("..", destination.parts)
        self.assertTrue(destination.name.startswith("HomeOS Instant - 08ada5a7a618"))

    def test_creates_then_removes_stream_when_download_completes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"storage:\n  root: {root / 'storage'}\nmedia:\n  qbittorrent_port: 8080\n",
                encoding="utf-8",
            )
            state_path = root / "state" / "instant_streams.json"
            client = Mock()
            worker = InstantStreamWorker(config_path, state_path, client=client)
            torrent = {
                "hash": TORRENT_HASH,
                "name": "Sintel",
                "category": "radarr",
                "magnet_uri": MAGNET,
                "progress": 0.25,
            }
            worker._get_qbit_torrents = Mock(return_value=[torrent])
            worker._arr_queue = Mock(return_value=[
                {
                    "downloadId": TORRENT_HASH.upper(),
                    "movie": {"title": "Sintel", "year": 2010},
                }
            ])
            worker._torrserver = Mock(return_value={
                "file_stats": [{"id": 7, "path": "Sintel.mp4", "length": 1000}]
            })

            state = worker.run_once()
            entry = state["streams"][TORRENT_HASH]
            stream_path = Path(entry["strm_path"])
            self.assertTrue(stream_path.is_file())
            self.assertEqual(
                stream_path.read_text(encoding="utf-8").strip(),
                f"http://127.0.0.1:8090/play/{TORRENT_HASH}/7",
            )

            torrent["progress"] = 1
            worker.run_once()
            self.assertFalse(stream_path.exists())
            self.assertEqual(load_state(state_path)["streams"], {})
            worker._torrserver.assert_called_with("rem", hash=TORRENT_HASH)

    def test_keeps_existing_stream_when_arr_queue_is_temporarily_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"storage:\n  root: {root / 'storage'}\n",
                encoding="utf-8",
            )
            worker = InstantStreamWorker(config_path, root / "state.json", client=Mock())
            torrent = {
                "hash": TORRENT_HASH,
                "name": "Sintel",
                "category": "radarr",
                "magnet_uri": MAGNET,
                "progress": 0.2,
            }
            worker._get_qbit_torrents = Mock(return_value=[torrent])
            worker._arr_queue = Mock(return_value=[
                {"downloadId": TORRENT_HASH, "movie": {"title": "Sintel", "year": 2010}}
            ])
            worker._torrserver = Mock(return_value={
                "file_stats": [{"id": 0, "path": "Sintel.mp4", "length": 1000}]
            })
            first_state = worker.run_once()
            stream_path = Path(first_state["streams"][TORRENT_HASH]["strm_path"])

            worker._arr_queue = Mock(side_effect=OSError("temporary failure"))
            with self.assertLogs("home_os.instant_stream", level="ERROR"):
                second_state = worker.run_once()

            self.assertIn(TORRENT_HASH, second_state["streams"])
            self.assertTrue(stream_path.exists())

    def test_safe_component_removes_path_separators(self):
        self.assertEqual(safe_component("../A/B:C"), "A B C")

    def test_resolves_movie_request_to_torrserver_hls_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"storage:\n  root: {root / 'storage'}\nmedia:\n  qbittorrent_port: 8080\n",
                encoding="utf-8",
            )
            state_path = root / "state.json"
            previous_stream_path = root / "Sintel.strm"
            save_state(state_path, {
                "version": 1,
                "streams": {
                    TORRENT_HASH: {
                        "hash": TORRENT_HASH,
                        "strm_path": str(previous_stream_path),
                    }
                },
                "last_run": None,
                "last_error": None,
            })
            resolver = InstantPlayResolver(
                config_path,
                state_path,
                client=Mock(),
                seerr_settings_path=root / "seerr.json",
                sleeper=lambda _: None,
            )
            resolver._lookup_movie = Mock(return_value={"tmdbId": 45745, "imdbId": "tt1727587"})
            resolver._radarr_movie = Mock(return_value=None)
            resolver._request_movie = Mock()
            resolver._arr_queue = Mock(return_value=[{
                "downloadId": TORRENT_HASH.upper(),
                "movie": {"tmdbId": 45745, "title": "Sintel"},
            }])
            resolver._get_qbit_torrents = Mock(return_value=[{
                "hash": TORRENT_HASH,
                "name": "Sintel",
                "category": "radarr",
                "magnet_uri": MAGNET,
                "progress": 0.1,
            }])
            resolver._torrserver = Mock(return_value={
                "file_stats": [{"id": 4, "path": "Sintel.mkv", "length": 1000}]
            })

            stream = resolver.resolve_movie("TT1727587", max_wait=0)

            self.assertEqual(stream["hash"], TORRENT_HASH)
            self.assertEqual(stream["file_id"], 4)
            self.assertEqual(stream["provider_ids"]["tmdb"], 45745)
            self.assertEqual(stream["strm_path"], str(previous_stream_path))
            resolver._request_movie.assert_called_once_with(45745)
            resolver._torrserver.assert_called_once()
            self.assertIn(TORRENT_HASH, load_state(state_path)["streams"])

    def test_reuses_existing_provider_stream_without_duplicate_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"storage:\n  root: {root / 'storage'}\n",
                encoding="utf-8",
            )
            state_path = root / "state.json"
            save_state(state_path, {
                "version": 1,
                "streams": {
                    TORRENT_HASH: {
                        "hash": TORRENT_HASH,
                        "file_id": 7,
                        "provider_ids": {"imdb": "tt1727587", "tmdb": 45745},
                    }
                },
                "last_run": None,
                "last_error": None,
            })
            resolver = InstantPlayResolver(config_path, state_path, client=Mock())
            resolver._lookup_movie = Mock()
            resolver._torrserver = Mock(return_value={
                "file_stats": [{"id": 7, "path": "Sintel.mkv", "length": 1000}]
            })

            stream = resolver.resolve_movie("tt1727587", max_wait=0)

            self.assertEqual(stream["file_id"], 7)
            resolver._lookup_movie.assert_not_called()
            self.assertTrue(load_state(state_path)["streams"][TORRENT_HASH]["active_until"])

    def test_reports_when_requested_movie_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"storage:\n  root: {root / 'storage'}\n",
                encoding="utf-8",
            )
            resolver = InstantPlayResolver(
                config_path,
                root / "state.json",
                client=Mock(),
                sleeper=lambda _: None,
            )
            resolver._lookup_movie = Mock(return_value={"tmdbId": 45745})
            resolver._radarr_movie = Mock(return_value=None)
            resolver._request_movie = Mock()
            resolver._arr_queue = Mock(return_value=[])
            resolver._get_qbit_torrents = Mock(return_value=[])

            with self.assertRaisesRegex(InstantPlayUnavailable, "not ready"):
                resolver.resolve_movie("tt1727587", max_wait=0)

    def test_uses_radarr_history_after_completed_item_leaves_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"storage:\n  root: {root / 'storage'}\n",
                encoding="utf-8",
            )
            resolver = InstantPlayResolver(config_path, root / "state.json", client=Mock())
            resolver._lookup_movie = Mock(return_value={"tmdbId": 45745})
            resolver._radarr_movie = Mock(return_value={"id": 45, "hasFile": True})
            resolver._history_download_id = Mock(return_value=TORRENT_HASH)
            resolver._arr_queue = Mock(return_value=[])
            resolver._get_qbit_torrents = Mock(return_value=[{
                "hash": TORRENT_HASH,
                "name": "Sintel",
                "category": "radarr",
                "magnet_uri": MAGNET,
                "progress": 1,
            }])
            resolver._torrserver = Mock(return_value={
                "file_stats": [{"id": 6, "path": "Sintel.mkv", "length": 1000}]
            })

            stream = resolver.resolve_movie("tt1727587", max_wait=0)

            self.assertEqual(stream["file_id"], 6)
            resolver._history_download_id.assert_called_once_with(45)

    def test_rewrites_hls_resources_through_authenticated_proxy(self):
        playlist = (
            "#EXTM3U\n"
            '#EXT-X-MAP:URI="init.mp4?audio=0"\n'
            "#EXTINF:6.00,\n"
            "seg/0.m4s\n"
        )

        rewritten = rewrite_hls_playlist(
            playlist,
            "/api/media/instant-play/hls/" + TORRENT_HASH,
            {"expires": "123", "signature": "signed value"},
        )

        self.assertIn(
            f"/api/media/instant-play/hls/{TORRENT_HASH}/init.mp4?"
            "audio=0&expires=123&signature=signed+value",
            rewritten,
        )
        self.assertIn(
            f"/api/media/instant-play/hls/{TORRENT_HASH}/seg/0.m4s?"
            "expires=123&signature=signed+value",
            rewritten,
        )


if __name__ == "__main__":
    unittest.main()
