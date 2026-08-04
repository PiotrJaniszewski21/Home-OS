import tempfile
import textwrap
import unittest
import re
from io import BytesIO
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from home_os.app import create_app
from home_os.extensions import db
from home_os.models import APIToken, CalendarEvent, TrashEntry, User


class BackendHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.storage = self.root / "storage"
        config_path = self.root / "config.yaml"
        config_path.write_text(
            textwrap.dedent(
                f"""
                server:
                  debug: true
                  secret_key: test-secret
                database:
                  path: {self.root / 'home_os.db'}
                storage:
                  root: {self.storage}
                  trash_path: {self.root / 'trash'}
                  trash_retention_days: 30
                adguard:
                  url: http://localhost:3000
                  username: ""
                  password: ""
                ai:
                  provider: ""
                """
            )
        )
        self.app = create_app(config_path)
        with self.app.app_context():
            self.admin = self._create_user("Admin", "admin")
            self.alice = self._create_user("Alice", "user")
            self.bob = self._create_user("Bob", "user")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _create_user(self, username, role):
        user = User(
            username=username,
            role=role,
            home_directory="/" if role == "admin" else f"/users/{username}",
        )
        user.set_password("test-password")
        db.session.add(user)
        db.session.flush()
        user_id = user.id
        db.session.commit()
        return user_id

    def _token_for(self, user_id, expires_at=None):
        import hashlib

        raw_token = f"token-{user_id}-{datetime.now(timezone.utc).timestamp()}"
        token = APIToken(
            user_id=user_id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            name="test",
            expires_at=expires_at,
        )
        db.session.add(token)
        db.session.commit()
        return raw_token

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def test_login_rejects_scheme_relative_redirect(self):
        client = self.app.test_client()
        login_page = client.get("/login")
        csrf_token = re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            login_page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            "/login?next=//evil.example/path",
            data={
                "username": "Admin",
                "password": "test-password",
                "csrf_token": csrf_token,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")

    def test_expired_and_revoked_tokens_are_rejected(self):
        with self.app.app_context():
            expired = self._token_for(
                self.admin,
                datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            active = self._token_for(
                self.admin,
                datetime.now(timezone.utc) + timedelta(days=1),
            )

        client = self.app.test_client()
        self.assertEqual(client.get("/api/monitor/metrics", headers=self._headers(expired)).status_code, 401)
        logout = client.post("/api/logout", headers=self._headers(active))
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(client.get("/api/monitor/metrics", headers=self._headers(active)).status_code, 401)

    def test_non_admin_files_and_trash_are_isolated(self):
        alice_root = self.storage / "users" / "Alice"
        bob_root = self.storage / "users" / "Bob"
        alice_root.mkdir(parents=True)
        bob_root.mkdir(parents=True)
        (alice_root / "alice.txt").write_text("alice")
        (bob_root / "bob.txt").write_text("bob")

        with self.app.app_context():
            alice_token = self._token_for(self.alice)
            bob_token = self._token_for(self.bob)

        client = self.app.test_client()
        alice_listing = client.get("/files?format=json", headers=self._headers(alice_token))
        names = {entry["name"] for entry in alice_listing.get_json()["data"]["entries"]}
        self.assertEqual(names, {"alice.txt"})
        self.assertEqual(
            client.get("/files/%2E%2E/Bob/bob.txt", headers=self._headers(alice_token)).status_code,
            403,
        )

        deleted = client.post(
            "/api/files/delete",
            json={"path": "/alice.txt"},
            headers=self._headers(alice_token),
        )
        trash_id = deleted.get_json()["data"]["id"]
        bob_trash = client.get("/trash?format=json", headers=self._headers(bob_token))
        self.assertEqual(bob_trash.get_json()["data"], [])
        restore = client.post(
            f"/api/files/trash/{trash_id}/restore",
            headers=self._headers(bob_token),
        )
        self.assertEqual(restore.status_code, 400)
        with self.app.app_context():
            self.assertEqual(db.session.get(TrashEntry, trash_id).user_id, self.alice)

    def test_upload_quota_rejects_without_leaving_partial_file(self):
        with self.app.app_context():
            alice = db.session.get(User, self.alice)
            alice.quota_bytes = 4
            db.session.commit()
            alice_token = self._token_for(self.alice)

        client = self.app.test_client()
        rejected = client.post(
            "/api/files/upload",
            data={"path": "/", "file": (BytesIO(b"12345"), "too-large.txt")},
            headers=self._headers(alice_token),
            content_type="multipart/form-data",
        )
        self.assertEqual(rejected.status_code, 507)
        alice_root = self.storage / "users" / "Alice"
        self.assertFalse((alice_root / "too-large.txt").exists())
        self.assertEqual(list(alice_root.glob(".homeos-upload-*")), [])

        accepted = client.post(
            "/api/files/upload",
            data={"path": "/", "file": (BytesIO(b"1234"), "fits.txt")},
            headers=self._headers(alice_token),
            content_type="multipart/form-data",
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual((alice_root / "fits.txt").read_bytes(), b"1234")

    def test_storage_root_and_recursive_copy_are_rejected(self):
        alice_root = self.storage / "users" / "Alice"
        (alice_root / "folder" / "child").mkdir(parents=True)
        with self.app.app_context():
            alice_token = self._token_for(self.alice)

        client = self.app.test_client()
        delete_root = client.post(
            "/api/files/delete",
            json={"path": "/"},
            headers=self._headers(alice_token),
        )
        self.assertEqual(delete_root.status_code, 403)
        recursive_copy = client.post(
            "/api/files/copy",
            json={"src": "/folder", "dest": "/folder/child"},
            headers=self._headers(alice_token),
        )
        self.assertEqual(recursive_copy.status_code, 400)
        self.assertTrue(alice_root.exists())

    def test_users_cannot_modify_another_users_calendar_or_income(self):
        with self.app.app_context():
            event = CalendarEvent(
                title="Alice event",
                event_type="event",
                date=date.today(),
                created_by=self.alice,
            )
            db.session.add(event)
            db.session.commit()
            event_id = event.id
            alice_token = self._token_for(self.alice)
            bob_token = self._token_for(self.bob)

        client = self.app.test_client()
        denied_delete = client.delete(
            f"/api/calendar/events/{event_id}",
            headers=self._headers(bob_token),
        )
        self.assertEqual(denied_delete.status_code, 403)
        denied_income = client.post(
            "/api/budget/income",
            json={"username": "Alice", "monthly_income": 1234},
            headers=self._headers(bob_token),
        )
        self.assertEqual(denied_income.status_code, 403)
        own_delete = client.delete(
            f"/api/calendar/events/{event_id}",
            headers=self._headers(alice_token),
        )
        self.assertEqual(own_delete.status_code, 200)

    def test_web_terminal_routes_are_removed(self):
        with self.app.app_context():
            admin_token = self._token_for(self.admin)

        client = self.app.test_client()
        headers = self._headers(admin_token)
        self.assertEqual(client.get("/terminal", headers=headers).status_code, 404)
        self.assertEqual(client.post("/api/terminal/exec", headers=headers).status_code, 404)
        self.assertNotIn("terminal", self.app.blueprints)

    def test_network_status_matches_dashboard_contract(self):
        with self.app.app_context():
            admin_token = self._token_for(self.admin)

        response = self.app.test_client().get(
            "/api/network/status",
            headers=self._headers(admin_token),
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertIn("active_interfaces", data)
        self.assertIn("total", data)
        for interface in data["interfaces"]:
            self.assertIn("kind", interface)
            self.assertIn("primary", interface)

    def test_browser_files_request_renders_html(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin)
            session["_fresh"] = True

        response = client.get("/files", headers={"Accept": "*/*"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/html"))
        self.assertIn(b"<title>Files", response.data)


if __name__ == "__main__":
    unittest.main()
