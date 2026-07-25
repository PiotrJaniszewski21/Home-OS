import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from home_os.modules.media import routes


ROOT_DIR = Path(__file__).resolve().parents[1]


class MediaStatisticsTests(unittest.TestCase):
    def test_registers_jellyfin_as_a_managed_media_server(self):
        jellyfin = routes.SERVICES["jellyfin"]

        self.assertEqual(jellyfin["service"], "jellyfin")
        self.assertEqual(jellyfin["package"], "jellyfin")
        self.assertEqual(jellyfin["port"], 8096)
        self.assertEqual(jellyfin["web_path"], "/web/")

    def test_reads_jellyfin_public_server_info(self):
        response = unittest.mock.Mock()
        response.json.return_value = {
            "ServerName": "Living Room",
            "Version": "10.10.7",
            "OperatingSystem": "Linux",
        }

        with patch.object(routes, "_get_port", return_value=8096), patch.object(
            routes.httpx, "get", return_value=response
        ) as get:
            info = routes._jellyfin_public_info()

        get.assert_called_once_with(
            "http://127.0.0.1:8096/svc/jellyfin/System/Info/Public",
            headers={"Accept": "application/json"},
            timeout=5,
        )
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            info,
            {
                "server_name": "Living Room",
                "version": "10.10.7",
                "operating_system": "Linux",
            },
        )

    def test_runs_jellyfin_actions_through_allowlisted_systemd_unit(self):
        with patch.object(routes.subprocess, "run") as run:
            routes._run_media_helper("install-jellyfin", timeout=600)

        run.assert_called_once_with(
            [
                "/usr/bin/systemctl",
                "start",
                "home-os-media-helper@install-jellyfin.service",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )

    def test_rejects_arguments_to_privileged_media_actions(self):
        with self.assertRaises(ValueError):
            routes._run_media_helper("set-jellyfin-port", 8096)

    def test_starts_media_updates_without_blocking_request(self):
        status = unittest.mock.Mock(
            returncode=0,
            stdout="ActiveState=inactive\nSubState=dead\nResult=success\nExecMainStatus=0\n",
            stderr="",
        )
        reset = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        start = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        with patch.object(routes.subprocess, "run", side_effect=[status, reset, start]) as run:
            started = routes._start_media_helper("update-sonarr")

        self.assertTrue(started)
        self.assertEqual(
            run.call_args_list[-1].args[0],
            [
                "/usr/bin/systemctl",
                "start",
                "--no-block",
                "home-os-media-helper@update-sonarr.service",
            ],
        )

    def test_does_not_start_duplicate_media_update(self):
        active = unittest.mock.Mock(
            returncode=0,
            stdout="ActiveState=activating\nSubState=start\nResult=success\nExecMainStatus=0\n",
            stderr="",
        )
        with patch.object(routes.subprocess, "run", return_value=active) as run:
            started = routes._start_media_helper("update-seerr")

        self.assertFalse(started)
        self.assertEqual(run.call_count, 1)

    def test_media_helper_dispatches_seerr_update_action(self):
        helper = runpy.run_path(ROOT_DIR / "scripts" / "home-os-media-helper")
        update_seerr = unittest.mock.Mock()
        with patch.object(sys, "argv", ["home-os-media-helper", "update-seerr"]), patch.dict(
            helper["main"].__globals__,
            {"update_seerr": update_seerr},
        ), patch.object(
            helper["main"].__globals__["os"],
            "geteuid",
            return_value=0,
        ):
            helper["main"]()

        update_seerr.assert_called_once_with()

    def test_seerr_update_uses_writable_build_home(self):
        helper = runpy.run_path(ROOT_DIR / "scripts" / "home-os-media-helper")
        update_seerr = helper["update_seerr"]
        release = {"tarball_url": "https://example.test/seerr.tar.gz"}

        def extract_release(_archive, extracted):
            source = extracted / "seerr-source"
            source.mkdir()
            (source / "package.json").write_text("{}")
            (source / "dist").mkdir()
            (source / "dist" / "index.js").write_text("")

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            helper["tempfile"],
            "TemporaryDirectory",
            return_value=unittest.mock.MagicMock(
                __enter__=unittest.mock.Mock(return_value=temp_dir),
                __exit__=unittest.mock.Mock(return_value=False),
            ),
        ), patch.object(
            helper["urllib"].request,
            "urlopen",
        ) as urlopen, patch.dict(
            update_seerr.__globals__,
            {
                "json": unittest.mock.Mock(load=unittest.mock.Mock(return_value=release)),
                "download": unittest.mock.Mock(),
                "extract_archive": unittest.mock.Mock(side_effect=extract_release),
                "run": unittest.mock.Mock(),
                "replace_application": unittest.mock.Mock(),
            },
        ):
            urlopen.return_value.__enter__.return_value = unittest.mock.Mock()
            with patch.object(helper["shutil"], "which", return_value="/usr/local/bin/pnpm"):
                update_seerr()

            install_call = update_seerr.__globals__["run"].call_args_list[0]
            environment = install_call.kwargs["env"]
            self.assertTrue(Path(environment["HOME"]).is_dir())
            self.assertEqual(environment["XDG_CACHE_HOME"], f"{environment['HOME']}/cache")
            self.assertEqual(environment["CYPRESS_INSTALL_BINARY"], "0")

    def test_seerr_update_skips_installed_release(self):
        helper = runpy.run_path(ROOT_DIR / "scripts" / "home-os-media-helper")
        update_seerr = helper["update_seerr"]
        release = {"tag_name": "v3.3.0"}
        package = unittest.mock.Mock()
        package.is_file.return_value = True
        package.read_text.return_value = '{"version":"3.3.0"}'
        download = unittest.mock.Mock()

        with patch.object(
            helper["urllib"].request,
            "urlopen",
        ) as urlopen, patch.dict(
            update_seerr.__globals__,
            {
                "json": unittest.mock.Mock(
                    load=unittest.mock.Mock(return_value=release),
                    loads=helper["json"].loads,
                ),
                "Path": unittest.mock.Mock(return_value=package),
                "download": download,
            },
        ):
            urlopen.return_value.__enter__.return_value = unittest.mock.Mock()
            update_seerr()

        download.assert_not_called()

    def test_application_update_stages_on_destination_filesystem(self):
        helper = runpy.run_path(ROOT_DIR / "scripts" / "home-os-media-helper")
        replace_application = helper["replace_application"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            application = root / "opt" / "Sonarr"
            replacement = root / "download" / "Sonarr"
            (application / "data").mkdir(parents=True)
            replacement.mkdir(parents=True)
            (application / "Sonarr").write_text("old")
            (application / "data" / "sonarr.db").write_text("library")
            (replacement / "Sonarr").write_text("new")
            original_replace = Path.replace

            def reject_download_rename(path, target):
                if path == replacement:
                    raise OSError(18, "Invalid cross-device link")
                return original_replace(path, target)

            with patch.object(Path, "replace", reject_download_rename), patch.dict(
                replace_application.__globals__,
                {
                    "run": unittest.mock.Mock(),
                    "service_is_active": lambda service: True,
                },
            ):
                replace_application(
                    application,
                    replacement,
                    "sonarr",
                    "sonarr",
                    ("data",),
                )

            self.assertEqual((application / "Sonarr").read_text(), "new")
            self.assertEqual(
                (application / "data" / "sonarr.db").read_text(),
                "library",
            )
            self.assertFalse(any((root / "opt").glob(".Sonarr-*")))

    def test_media_folders_use_shared_group_without_world_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            application = Flask(__name__)
            application.config["_raw_config"] = {
                "storage": {"root": temp_dir}
            }
            with application.app_context(), patch.object(
                routes, "run_privileged"
            ) as run:
                routes._setup_media_folders("radarr")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["groupadd", "--force", "homeos-media"], commands)
        self.assertIn(
            ["usermod", "-aG", "homeos-media", "radarr"],
            commands,
        )
        self.assertIn(["chgrp", "homeos-media", temp_dir], commands)
        self.assertIn(["chmod", "g+rx", temp_dir], commands)
        self.assertTrue(
            any(command[:3] == ["chgrp", "-R", "homeos-media"] for command in commands)
        )
        self.assertFalse(any("777" in command for command in commands))
        self.assertFalse(any(command[0] == "chown" for command in commands))

    def test_reads_arr_api_config_and_normalizes_url_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.xml"
            config_path.write_text(
                "<Config><ApiKey>secret</ApiKey><UrlBase>/svc/sonarr/</UrlBase></Config>"
            )
            with patch.dict(routes.ARR_CONFIG_PATHS, {"sonarr": str(config_path)}):
                api_key, url_base = routes._read_arr_api_config("sonarr")

        self.assertEqual(api_key, "secret")
        self.assertEqual(url_base, "/svc/sonarr")

    def test_summarizes_sonarr_statistics(self):
        metrics = routes._summarize_arr_stats(
            "sonarr",
            {
                "library": [
                    {"statistics": {"episodeFileCount": 12}},
                    {"statistics": {"episodeFileCount": 8}},
                ],
                "missing": {"totalRecords": 3},
                "queue": {"totalRecords": 1},
            },
        )

        self.assertEqual(
            metrics,
            [
                {"label": "Series", "value": 2},
                {"label": "Episodes", "value": 20},
                {"label": "Missing", "value": 3},
                {"label": "Queue", "value": 1},
            ],
        )

    def test_summarizes_radarr_statistics(self):
        metrics = routes._summarize_arr_stats(
            "radarr",
            {
                "library": [
                    {"hasFile": True, "monitored": True},
                    {"hasFile": False, "monitored": True},
                ],
                "queue": {"totalRecords": 4},
            },
        )

        self.assertEqual([metric["value"] for metric in metrics], [2, 1, 2, 4])

    def test_summarizes_prowlarr_statistics(self):
        metrics = routes._summarize_arr_stats(
            "prowlarr",
            {
                "indexers": [{"enable": True}, {"enable": False}],
                "applications": [{"enable": True}, {"enable": True}],
                "history": {"totalRecords": 1250},
                "health": [{"type": "warning"}],
            },
        )

        self.assertEqual([metric["value"] for metric in metrics], [1, 2, 1250, 1])


if __name__ == "__main__":
    unittest.main()
