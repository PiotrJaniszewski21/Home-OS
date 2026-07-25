import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from home_os.services import media_cleanup


class MediaCleanupSafetyTests(unittest.TestCase):
    def test_arr_url_base_is_discovered(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.xml"
            config.write_text("<Config><ApiKey>secret</ApiKey><UrlBase>/svc/sonarr/</UrlBase></Config>")
            self.assertEqual(media_cleanup._read_arr_api_key(str(config)), "secret")
            self.assertEqual(media_cleanup._read_arr_url_base(str(config)), "/svc/sonarr")

    def test_delete_rejects_path_outside_media_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            media_root = base / "media"
            media_root.mkdir()
            outside = base / "outside.mkv"
            outside.write_text("video")

            deleted = media_cleanup._delete_file(
                str(outside), [str(media_root)], media_cleanup._file_identity(str(outside)),
            )

            self.assertFalse(deleted)
            self.assertTrue(outside.exists())

    def test_delete_rejects_replaced_file(self):
        with tempfile.TemporaryDirectory() as folder:
            media_root = Path(folder)
            media = media_root / "movie.mkv"
            media.write_text("original")
            identity = media_cleanup._file_identity(str(media))
            media.unlink()
            media.write_text("replacement")

            deleted = media_cleanup._delete_file(str(media), [str(media_root)], identity)

            self.assertFalse(deleted)
            self.assertTrue(media.exists())

    def test_blank_series_title_never_matches_sonarr(self):
        config = {"sonarr_api_key": "key", "sonarr_url": "http://sonarr"}
        with patch.object(media_cleanup, "_arr_request") as request:
            result = media_cleanup._unmonitor_episode(config, "", 1, 1)
        self.assertIsNone(result)
        request.assert_not_called()

    def test_failed_sonarr_update_is_not_success(self):
        config = {"sonarr_api_key": "key", "sonarr_url": "http://sonarr"}

        def fake_request(method, base_url, api_key, path, json_data=None):
            if method == "GET" and path == "/api/v3/series":
                return [{"id": 10, "title": "Exact Show"}]
            if method == "GET":
                return [{"id": 20, "seasonNumber": 1, "episodeNumber": 2}]
            return None

        with patch.object(media_cleanup, "_arr_request", side_effect=fake_request):
            result = media_cleanup._unmonitor_episode(config, "Exact Show", 1, 2)
        self.assertIsNone(result)

    def test_cleanup_does_not_delete_when_arr_unmonitor_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            media_root = base / "media"
            media_root.mkdir()
            media = media_root / "movie.mkv"
            media.write_text("video")
            state_file = base / "state.json"
            state_file.write_text(json.dumps({
                "watched": {
                    "1": {
                        "title": "Movie",
                        "file_path": str(media),
                        "file_identity": media_cleanup._file_identity(str(media)),
                        "watched_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                        "type": "movie",
                        "tmdb_id": "123",
                    }
                },
                "deleted": [],
                "processed": {},
            }))
            config = {
                "enabled": True,
                "state_file": str(state_file),
                "delay_hours": 1,
                "threshold": 90,
                "media_roots": [str(media_root)],
                "max_deletions_per_run": 3,
            }
            with (
                patch.object(media_cleanup, "_poll_watched_items", return_value=[]),
                patch.object(media_cleanup, "_revalidate_entry", return_value="ready"),
                patch.object(media_cleanup, "_unmonitor_entry", return_value=None),
            ):
                result = media_cleanup.run_cleanup(config)

            self.assertEqual(result["deleted"], 0)
            self.assertEqual(result["errors"], 1)
            self.assertTrue(media.exists())

    def test_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            state_file = base / "state.json"
            state_file.write_text("not-json")
            result = media_cleanup.run_cleanup({
                "enabled": True,
                "state_file": str(state_file),
                "delay_hours": 24,
                "threshold": 90,
                "media_roots": [str(base)],
            })
            self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
