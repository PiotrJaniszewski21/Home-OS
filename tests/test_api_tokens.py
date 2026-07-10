import tempfile
import textwrap
import unittest
import sqlite3
from pathlib import Path

from flask import jsonify
from flask_login import current_user, login_required
from sqlalchemy import inspect

from home_os.app import create_app
from home_os.extensions import db
from home_os.models import APIToken, User


class APITokenTests(unittest.TestCase):
    def test_api_logins_do_not_invalidate_existing_native_app_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config_path = tmp_path / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    f"""
                    server:
                      debug: true
                      secret_key: test-secret
                    database:
                      path: {tmp_path / "home_os.db"}
                    storage:
                      root: {tmp_path / "storage"}
                      trash_path: {tmp_path / "trash"}
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
            app = create_app(config_path)

            @app.get("/api/test-auth")
            @login_required
            def test_auth():
                return jsonify({"ok": True, "username": current_user.username})

            with app.app_context():
                user = User(username="Peter", role="admin", home_directory="/")
                user.set_password("test-password")
                db.session.add(user)
                db.session.commit()

            client = app.test_client()

            first_login = client.post(
                "/api/login", json={"username": "Peter", "password": "test-password"}
            )
            second_login = client.post(
                "/api/login", json={"username": "Peter", "password": "test-password"}
            )

            self.assertEqual(first_login.status_code, 200)
            self.assertEqual(second_login.status_code, 200)

            first_token = first_login.get_json()["data"]["token"]
            second_token = second_login.get_json()["data"]["token"]
            self.assertNotEqual(first_token, second_token)

            for token in (first_token, second_token):
                response = client.get(
                    "/api/test-auth", headers={"Authorization": f"Bearer {token}"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["username"], "Peter")

            with app.app_context():
                self.assertEqual(APIToken.query.count(), 2)

    def test_existing_database_gets_missing_user_columns_before_token_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            database_path = tmp_path / "home_os.db"
            config_path = tmp_path / "config.yaml"
            legacy_token_hash = "a" * 64

            config_path.write_text(
                textwrap.dedent(
                    f"""
                    server:
                      debug: true
                      secret_key: test-secret
                    database:
                      path: {database_path}
                    storage:
                      root: {tmp_path / "storage"}
                      trash_path: {tmp_path / "trash"}
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

            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        username VARCHAR(80) UNIQUE NOT NULL,
                        email VARCHAR(120),
                        password_hash VARCHAR(256) NOT NULL,
                        role VARCHAR(20) NOT NULL DEFAULT 'user',
                        quota_bytes BIGINT,
                        home_directory VARCHAR(512) NOT NULL DEFAULT '',
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        api_token_hash VARCHAR(64),
                        monthly_income FLOAT,
                        default_page VARCHAR(30) NOT NULL DEFAULT 'dashboard',
                        permissions TEXT NOT NULL DEFAULT 'dashboard,files,storage,media,ai,calendar,budget',
                        created_at DATETIME NOT NULL,
                        last_login DATETIME
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, home_directory, is_active,
                        api_token_hash, default_page, permissions, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "Peter",
                        "legacy-password-hash",
                        "admin",
                        "/",
                        1,
                        legacy_token_hash,
                        "dashboard",
                        "dashboard,files",
                        "2026-01-01 00:00:00",
                    ),
                )

            app = create_app(config_path)

            with app.app_context():
                columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
                self.assertIn("dock_tabs", columns)
                self.assertEqual(APIToken.query.count(), 1)
                self.assertEqual(APIToken.query.first().token_hash, legacy_token_hash)


if __name__ == "__main__":
    unittest.main()
