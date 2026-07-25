import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask
from home_os.app import create_app
from home_os.modules.media import routes as media_routes
from home_os.services.system_service import privileged_command


ROOT_DIR = Path(__file__).resolve().parents[1]


class SystemPermissionTests(unittest.TestCase):
    def test_privileged_commands_run_directly_as_root(self):
        with patch.object(os, "geteuid", return_value=0):
            command = privileged_command(["systemctl", "restart", "jellyfin"])

        self.assertEqual(command, ["systemctl", "restart", "jellyfin"])

    def test_privileged_commands_reject_non_root_backend(self):
        with patch.object(os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(PermissionError, "must run as root"):
                privileged_command(["systemctl", "restart", "jellyfin"])

    def test_installer_uses_root_administration_service(self):
        installer = (ROOT_DIR / "install.sh").read_text()
        home_os_unit = installer.split(
            "cat > /etc/systemd/system/home-os.service << EOF", 1
        )[1].split("EOF", 1)[0]

        self.assertIn("User=root", home_os_unit)
        self.assertIn("Group=root", home_os_unit)
        self.assertIn("NoNewPrivileges=false", home_os_unit)
        self.assertIn("ProtectSystem=false", home_os_unit)
        self.assertIn("ProtectHome=false", home_os_unit)
        self.assertIn("--bind [::]:443", home_os_unit)
        self.assertNotIn(":4443", home_os_unit)
        self.assertNotIn("CapabilityBoundingSet=", home_os_unit)
        self.assertNotIn("ReadWritePaths=", home_os_unit)

    def test_installer_provisions_shared_media_group(self):
        installer = (ROOT_DIR / "install.sh").read_text()

        self.assertIn('MEDIA_GROUP="homeos-media"', installer)
        self.assertIn('groupadd --force "$MEDIA_GROUP"', installer)
        self.assertIn(
            'chown root:"$MEDIA_GROUP" "$INSTALL_DIR/storage"',
            installer,
        )
        self.assertIn('chmod 2750 "$INSTALL_DIR/storage"', installer)
        self.assertIn('chmod 2770 "$INSTALL_DIR/storage/HomeOS"', installer)

    def test_autodelete_timer_uses_root_backend_model(self):
        completed = MagicMock(returncode=0, stderr="")
        with (
            Flask(__name__).app_context(),
            patch.object(media_routes, "run_privileged", return_value=completed) as run,
        ):
            self.assertTrue(media_routes._manage_autodelete_timer(True))

        script = run.call_args.args[0][2]
        self.assertIn("User=root", script)
        self.assertNotIn("User=homeos", script)
        self.assertNotIn("SupplementaryGroups=", script)

    def test_installer_migrates_existing_autodelete_unit(self):
        installer = (ROOT_DIR / "install.sh").read_text()

        self.assertIn(
            "if [ -f /etc/systemd/system/home-os-autodelete.service ]; then",
            installer,
        )
        self.assertIn("User=root", installer)
        self.assertIn(
            "systemctl reset-failed home-os-autodelete.service",
            installer,
        )

    def test_upgrade_migration_matches_root_service_model(self):
        migration = (
            ROOT_DIR / "scripts" / "configure-root-backend.sh"
        ).read_text()

        self.assertIn("User=root", migration)
        self.assertIn("NoNewPrivileges=false", migration)
        self.assertIn("--bind [::]:443 --bind [::]:4443", migration)
        self.assertIn('for folder_name in Movies Series Downloads', migration)
        self.assertIn(
            'chown root:"$MEDIA_GROUP" "$INSTALL_DIR/storage"',
            migration,
        )
        self.assertIn('chmod 2750 "$INSTALL_DIR/storage"', migration)
        self.assertIn('chgrp -R "$MEDIA_GROUP" "$media_path"', migration)
        self.assertNotIn("chmod 777", migration)

    def test_explicit_config_uses_isolated_instance_directory(self):
        with patch("home_os.app.Flask") as flask:
            flask.return_value.instance_path = "/tmp/unused"
            flask.return_value.config = {}
            with patch("home_os.app.load_config", side_effect=RuntimeError("stop")):
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    create_app("/tmp/home-os-test/config.yaml")

        self.assertEqual(
            flask.call_args.kwargs["instance_path"],
            "/tmp/home-os-test/.home_os_instance",
        )

    def test_sharing_feature_is_not_installed_or_registered(self):
        application = (ROOT_DIR / "home_os" / "app.py").read_text()
        installer = (ROOT_DIR / "install.sh").read_text()

        self.assertNotIn("home_os.modules.sharing", application)
        self.assertNotIn("register_blueprint(sharing_bp)", application)
        self.assertNotIn("\n    samba \\", installer)
        self.assertFalse((ROOT_DIR / "home_os" / "modules" / "sharing").exists())
        self.assertFalse((ROOT_DIR / "home_os" / "services" / "share_service.py").exists())
        self.assertFalse((ROOT_DIR / "home_os" / "models" / "share.py").exists())

    def test_tunnel_uses_standard_https_port(self):
        setup = (ROOT_DIR / "setup-tunnel.sh").read_text()
        network = (
            ROOT_DIR / "home_os" / "modules" / "network" / "routes.py"
        ).read_text()

        self.assertIn("service: https://localhost", setup)
        self.assertNotIn("localhost:4443", setup)
        self.assertIn("tunnel --url https://localhost --no-tls-verify", network)


if __name__ == "__main__":
    unittest.main()
