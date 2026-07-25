import tempfile
import textwrap
import unittest
import re
from io import BytesIO
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
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

    def test_api_reauthentication_returns_to_requested_local_page(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = False

        response = client.get(
            "/api/media/autodelete/config",
            headers={"X-Reauth-Return-To": "/media"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["reauth_url"], "/reauth")
        with client.session_transaction() as session:
            self.assertEqual(session["next_after_reauth"], "/media")

    def test_api_reauthentication_rejects_external_return_target(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = False

        response = client.get(
            "/api/media/autodelete/config",
            headers={"X-Reauth-Return-To": "//attacker.example"},
        )

        self.assertEqual(response.status_code, 401)
        with client.session_transaction() as session:
            self.assertEqual(session["next_after_reauth"], "/dashboard")

    def test_media_policy_allows_jellyfin_blob_playback(self):
        response = self.app.test_client().get("/health")

        self.assertIn(
            "media-src 'self' blob:",
            response.headers["Content-Security-Policy"],
        )

    def test_retired_ai_feature_is_not_routable_or_permitted(self):
        self.assertEqual(self.app.test_client().get("/ai").status_code, 404)
        with self.app.app_context():
            user = db.session.get(User, self.alice)
            user.permissions = "dashboard,ai"
            self.assertEqual(user.allowed_pages, ["dashboard"])

    def test_seerr_policy_allows_metadata_artwork_only_on_seerr_pages(self):
        client = self.app.test_client()
        health_policy = client.get("/health").headers["Content-Security-Policy"]
        seerr_policy = client.get("/svc/seerr/").headers["Content-Security-Policy"]

        self.assertNotIn("https://image.tmdb.org", health_policy)
        self.assertIn("https://image.tmdb.org", seerr_policy)
        self.assertIn("https://artworks.thetvdb.com", seerr_policy)
        self.assertIn("https://*.plex.tv", seerr_policy)

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
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True

        response = client.get("/files", headers={"Accept": "*/*"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/html"))
        self.assertIn(b"<title>Files", response.data)

    def test_inactive_user_session_is_rejected(self):
        client = self.app.test_client()
        with self.app.app_context():
            user = db.session.get(User, self.alice)
            session_identifier = user.get_id()
            user.is_active = False
            user.auth_version += 1
            db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True
        self.assertEqual(client.get("/api/monitor/metrics").status_code, 401)

    def test_uploaded_html_is_forced_to_download(self):
        alice_root = self.storage / "users" / "Alice"
        alice_root.mkdir(parents=True, exist_ok=True)
        (alice_root / "payload.html").write_text("<script>alert(1)</script>")
        with self.app.app_context():
            alice_token = self._token_for(self.alice)
        response = self.app.test_client().get(
            "/files/payload.html",
            headers=self._headers(alice_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))

    def test_guest_cannot_mutate_files_calendar_or_budget(self):
        with self.app.app_context():
            guest_id = self._create_user("Guest", "guest")
            guest_token = self._token_for(guest_id)
        client = self.app.test_client()
        headers = self._headers(guest_token)
        self.assertEqual(client.post("/api/files/mkdir", json={"path": "/new"}, headers=headers).status_code, 403)
        self.assertEqual(client.post("/api/calendar/events", json={}, headers=headers).status_code, 403)
        self.assertEqual(client.post("/api/budget/income", json={"monthly_income": 1}, headers=headers).status_code, 403)

    def test_jellyfin_proxy_uses_jellyfin_authentication(self):
        client = self.app.test_client()
        with patch(
            "home_os.modules.media.routes.httpx.Client",
            side_effect=httpx.ConnectError("offline"),
        ):
            public_info = client.get("/svc/jellyfin/System/Info/Public")
            login = client.post(
                "/svc/jellyfin/Users/AuthenticateByName",
                json={"Username": "test", "Pw": "test"},
            )

        self.assertEqual(public_info.status_code, 502)
        self.assertEqual(login.status_code, 502)
        self.assertEqual(client.get("/svc/sonarr/").status_code, 302)

    def test_seerr_proxy_round_trips_only_seerr_cookies(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True
        client.set_cookie("connect.sid", "signed-seerr-session")
        client.set_cookie("_csrf", "seerr-csrf-cookie")
        client.set_cookie("XSRF-TOKEN", "seerr-csrf-token")

        upstream_response = MagicMock()
        upstream_response.status_code = 200
        upstream_response.content = b'{"ok":true}'
        upstream_response.headers = httpx.Headers([
            ("Content-Type", "application/json"),
            (
                "Set-Cookie",
                "connect.sid=refreshed-session; Path=/; HttpOnly; SameSite=Lax",
            ),
        ])
        upstream_client = MagicMock()
        upstream_client.__enter__.return_value = upstream_client
        upstream_client.request.return_value = upstream_response

        with patch(
            "home_os.modules.media.routes.httpx.Client",
            return_value=upstream_client,
        ):
            response = client.get(
                "/svc/seerr/api/v1/auth/me",
                headers={
                    "Origin": "http://localhost",
                    "Referer": "http://localhost/svc/seerr/",
                },
            )

        self.assertEqual(response.status_code, 200)
        forwarded_headers = upstream_client.request.call_args.kwargs["headers"]
        self.assertEqual(
            forwarded_headers["Cookie"],
            "connect.sid=signed-seerr-session; "
            "_csrf=seerr-csrf-cookie; "
            "XSRF-TOKEN=seerr-csrf-token",
        )
        self.assertNotIn("session=", forwarded_headers["Cookie"])
        self.assertEqual(forwarded_headers["Host"], "localhost")
        self.assertEqual(forwarded_headers["X-Forwarded-Host"], "localhost")
        self.assertEqual(forwarded_headers["X-Forwarded-Proto"], "http")
        self.assertEqual(forwarded_headers["X-Forwarded-Prefix"], "/svc/seerr")
        self.assertEqual(forwarded_headers["Origin"], "http://localhost")
        self.assertEqual(
            forwarded_headers["Referer"],
            "http://localhost/svc/seerr/",
        )
        self.assertIn(
            "Path=/",
            response.headers.get("Set-Cookie"),
        )

    def test_seerr_proxy_does_not_forward_cross_origin_headers(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True

        upstream_response = MagicMock()
        upstream_response.status_code = 200
        upstream_response.content = b"ok"
        upstream_response.headers = httpx.Headers({"Content-Type": "text/plain"})
        upstream_client = MagicMock()
        upstream_client.__enter__.return_value = upstream_client
        upstream_client.request.return_value = upstream_response

        with patch(
            "home_os.modules.media.routes.httpx.Client",
            return_value=upstream_client,
        ):
            response = client.get(
                "/svc/seerr/",
                headers={
                    "Origin": "https://evil.example",
                    "Referer": "https://evil.example/login",
                },
            )

        self.assertEqual(response.status_code, 200)
        forwarded_headers = upstream_client.request.call_args.kwargs["headers"]
        self.assertNotIn("Cookie", forwarded_headers)
        self.assertNotIn("Origin", forwarded_headers)
        self.assertNotIn("Referer", forwarded_headers)

    def test_seerr_proxy_rewrites_template_paths_and_disables_stale_validators(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True

        upstream_response = MagicMock()
        upstream_response.status_code = 200
        upstream_response.content = (
            b"fetch(`/api/v1/movie/${id}`);"
            b'router.push("/");'
            b'const image="/imageproxy/tmdb/poster.jpg";'
            b'const requests="/requests?filter=all";'
            b'const user="/users/1";'
            b'navigator.serviceWorker.register("/sw.js");'
        )
        upstream_response.headers = httpx.Headers({
            "Content-Type": "application/javascript",
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": '"upstream-etag"',
            "Last-Modified": "Wed, 15 Jul 2026 18:00:00 GMT",
        })
        upstream_client = MagicMock()
        upstream_client.__enter__.return_value = upstream_client
        upstream_client.request.return_value = upstream_response

        with patch(
            "home_os.modules.media.routes.httpx.Client",
            return_value=upstream_client,
        ):
            response = client.get(
                "/svc/seerr/_next/static/chunks/app.js",
                headers={
                    "If-None-Match": '"upstream-etag"',
                    "If-Modified-Since": "Wed, 15 Jul 2026 18:00:00 GMT",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"`/svc/seerr/api/v1/movie/${id}`", response.data)
        self.assertIn(b'router.push("/svc/seerr/")', response.data)
        self.assertIn(
            b'"/svc/seerr/imageproxy/tmdb/poster.jpg"',
            response.data,
        )
        self.assertIn(b'"/svc/seerr/requests?filter=all"', response.data)
        self.assertIn(b'"/svc/seerr/users/1"', response.data)
        self.assertIn(
            b'navigator.serviceWorker.register("/svc/seerr/sw.js")',
            response.data,
        )
        forwarded_headers = upstream_client.request.call_args.kwargs["headers"]
        self.assertNotIn("If-None-Match", forwarded_headers)
        self.assertNotIn("If-Modified-Since", forwarded_headers)
        self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
        self.assertNotIn("ETag", response.headers)
        self.assertNotIn("Last-Modified", response.headers)

    def test_seerr_html_promotes_session_and_busts_old_asset_cache(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True
        client.set_cookie("connect.sid", "signed-seerr-session")

        upstream_response = MagicMock()
        upstream_response.status_code = 200
        upstream_response.content = (
            b'<script src="/_next/static/chunks/app.js"></script>'
        )
        upstream_response.headers = httpx.Headers({
            "Content-Type": "text/html; charset=utf-8",
        })
        upstream_client = MagicMock()
        upstream_client.__enter__.return_value = upstream_client
        upstream_client.request.return_value = upstream_response

        with patch(
            "home_os.modules.media.routes.httpx.Client",
            return_value=upstream_client,
        ):
            response = client.get("/svc/seerr/")

        self.assertIn(
            b'/svc/seerr/_next/static/chunks/app.js'
            b'?homeos_proxy=20260715-3',
            response.data,
        )
        response_cookies = response.headers.getlist("Set-Cookie")
        self.assertTrue(any(
            cookie.startswith("connect.sid=signed-seerr-session")
            and "Path=/" in cookie
            for cookie in response_cookies
        ))
        self.assertTrue(any(
            "connect.sid=" in cookie
            and "Path=/svc/seerr/" in cookie
            and "Max-Age=0" in cookie
            for cookie in response_cookies
        ))

    def test_seerr_root_api_compatibility_route_uses_seerr_proxy(self):
        client = self.app.test_client()
        with self.app.app_context():
            admin = db.session.get(User, self.admin)
            session_identifier = admin.get_id()
        with client.session_transaction() as session:
            session["_user_id"] = session_identifier
            session["_fresh"] = True
        client.set_cookie("connect.sid", "signed-seerr-session")

        upstream_response = MagicMock()
        upstream_response.status_code = 200
        upstream_response.content = b'{"title":"Movie"}'
        upstream_response.headers = httpx.Headers({
            "Content-Type": "application/json",
        })
        upstream_client = MagicMock()
        upstream_client.__enter__.return_value = upstream_client
        upstream_client.request.return_value = upstream_response

        with patch(
            "home_os.modules.media.routes.httpx.Client",
            return_value=upstream_client,
        ):
            response = client.get("/api/v1/movie/240?language=en")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"title": "Movie"})
        self.assertEqual(
            upstream_client.request.call_args.kwargs["url"],
            "http://localhost:5055/api/v1/movie/240?language=en",
        )
        self.assertEqual(
            upstream_client.request.call_args.kwargs["headers"]["Cookie"],
            "connect.sid=signed-seerr-session",
        )


if __name__ == "__main__":
    unittest.main()
