import fcntl
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

from home_os.config import ROOT_DIR, create_flask_config, get_config_path, load_config
from home_os.extensions import csrf, db, login_manager


def _ensure_user_columns():
    """Add newer user columns when running against an older SQLite database."""
    from sqlalchemy import inspect, text
    from sqlalchemy.sql.compiler import IdentifierPreparer

    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    preparer = IdentifierPreparer(db.engine.dialect)
    table_name = preparer.quote("users")
    user_columns = db.metadata.tables["users"].columns
    column_defaults = {
        "role": "'user'",
        "home_directory": "''",
        "is_active": "1",
        "auth_version": "1",
        "monthly_income": "0",
        "default_page": "'dashboard'",
        "permissions": "'dashboard,files,storage,media,ai,calendar,budget'",
        "created_at": "'1970-01-01 00:00:00'",
    }

    for column in user_columns:
        if column.name in existing_columns:
            continue
        column_name = preparer.quote(column.name)
        column_type = column.type.compile(dialect=db.engine.dialect)
        ddl = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        default = column_defaults.get(column.name)
        if not column.nullable and default is not None:
            ddl += f" NOT NULL DEFAULT {default}"
        db.session.execute(text(ddl))
    db.session.commit()


def _ensure_security_columns():
    """Add security-related columns when upgrading an existing database."""
    from sqlalchemy import inspect, text
    from sqlalchemy.sql.compiler import IdentifierPreparer

    inspector = inspect(db.engine)
    preparer = IdentifierPreparer(db.engine.dialect)
    migrations = {
        "api_tokens": {
            "expires_at": "DATETIME",
            "revoked_at": "DATETIME",
        },
        "trash": {
            "user_id": "INTEGER REFERENCES users(id) ON DELETE SET NULL",
        },
    }
    table_names = set(inspector.get_table_names())
    for raw_table_name, columns in migrations.items():
        if raw_table_name not in table_names:
            continue
        existing = {
            column["name"] for column in inspector.get_columns(raw_table_name)
        }
        table_name = preparer.quote(raw_table_name)
        for raw_column_name, column_type in columns.items():
            if raw_column_name in existing:
                continue
            column_name = preparer.quote(raw_column_name)
            db.session.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            )
    db.session.commit()


def _persistent_secret_key(instance_path):
    """Load or atomically create the installation's session signing key."""
    key_path = Path(instance_path) / ".secret_key"
    try:
        key = key_path.read_text().strip()
        if key:
            key_path.chmod(0o600)
            return key
    except FileNotFoundError:
        pass

    key_path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_hex(32)
    try:
        file_descriptor = os.open(
            key_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        key = key_path.read_text().strip()
        if not key:
            raise RuntimeError("Home OS secret key file is empty")
        return key
    with os.fdopen(file_descriptor, "w") as secret_file:
        secret_file.write(generated)
        secret_file.flush()
        os.fsync(secret_file.fileno())
    return generated


def _same_origin(value, host):
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and parsed.netloc == host


@contextmanager
def _schema_lock(instance_path):
    lock_path = Path(instance_path) / "schema.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def create_app(config_path=None):
    resolved_config_path = get_config_path(config_path)
    instance_path = (
        ROOT_DIR / "data"
        if config_path is None
        else resolved_config_path.parent / ".home_os_instance"
    )
    app = Flask(
        __name__,
        instance_path=str(instance_path),
        template_folder="templates",
    )

    config_dict = load_config(resolved_config_path)
    flask_config = create_flask_config(config_dict)

    os.makedirs(app.instance_path, exist_ok=True)

    # Keep sessions valid across restarts and multiple Gunicorn workers.
    if flask_config["SECRET_KEY"] == "change-me-in-production":
        flask_config["SECRET_KEY"] = _persistent_secret_key(app.instance_path)

    app.config.update(flask_config)

    # Secure session cookies
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not app.debug
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = not app.debug
    app.config["REMEMBER_COOKIE_DURATION"] = 86400  # 1 day
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600  # 1 hour idle

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

    @login_manager.unauthorized_handler
    def _unauthorized():
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Authentication required"}), 401
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.before_request
    def _tunnel_guard():
        """Reject requests not from Cloudflare Tunnel or the local network."""
        if app.debug:
            return
        if request.path == "/health":
            return
        if (
            request.path.startswith("/api/media/instant-play/embedarr/")
            and request.headers.get("X-Api-Key")
        ):
            return
        # Allow local/private network access
        import ipaddress
        try:
            client = ipaddress.ip_address(request.remote_addr)
            if client.is_private or client.is_loopback:
                return
        except (TypeError, ValueError):
            pass
        from flask import abort
        abort(403)

    @app.before_request
    def _enforce_permissions():
        """Block access to modules the user doesn't have permission for."""
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        path = request.path
        path_to_perm = {
            "/dashboard": "dashboard",
            "/api/monitor": "dashboard",
            "/files": "files",
            "/api/files": "files",
            "/trash": "files",
            "/storage": "storage",
            "/media": "media",
            "/api/media": "media",
            "/svc/": "media",
            "/qbt": "media",
            "/ai": "ai",
            "/api/ai": "ai",
            "/calendar": "calendar",
            "/api/calendar": "calendar",
            "/budget": "budget",
            "/api/budget": "budget",
            "/network": "network",
            "/api/network": "network",
            "/settings": "settings",
        }
        for prefix, perm in path_to_perm.items():
            if path.startswith(prefix):
                if not current_user.has_permission(perm):
                    from flask import abort
                    abort(403)
                break

        if current_user.role == "guest" and request.method not in ("GET", "HEAD", "OPTIONS"):
            read_only_prefixes = ("/api/files", "/api/calendar", "/api/budget")
            if path.startswith(read_only_prefixes):
                from flask import abort
                abort(403)

    @app.before_request
    def _generate_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.before_request
    def _csrf_check():
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        # Skip CSRF for Bearer token authenticated API requests
        if request.path.startswith("/api/") and request.headers.get("Authorization", "").startswith("Bearer "):
            return
        if (
            request.path.startswith("/api/media/instant-play/embedarr/")
            and request.headers.get("X-Api-Key")
        ):
            return
        # Skip CSRF for WebSocket upgrades
        if request.path.startswith("/ws/"):
            return
        # Skip CSRF for the login endpoint (no session yet)
        if request.path == "/api/login":
            return
        if request.path == "/svc/jellyfin" or request.path.startswith("/svc/jellyfin/"):
            return
        # Skip CSRF token for proxied service UIs (their own JS makes POSTs)
        # but enforce same-origin check to prevent cross-site forgery
        if (
            request.path.startswith("/qbt/")
            or request.path.startswith("/svc/")
            or request.path.startswith("/network/adguard/")
        ):
            origin = request.headers.get("Origin") or ""
            referer = request.headers.get("Referer") or ""
            if not _same_origin(origin or referer, request.host):
                from flask import abort
                abort(403)
            return
        csrf.protect()

    @login_manager.user_loader
    def load_user(user_id):
        from home_os.models import User
        try:
            raw_id, raw_version = str(user_id).split(":", 1)
            user = db.session.get(User, int(raw_id))
            version = int(raw_version)
        except (TypeError, ValueError):
            return None
        if user is None or not user.is_active or user.auth_version != version:
            return None
        return user

    @login_manager.request_loader
    def load_user_from_token(req):
        import hashlib

        from home_os.models import APIToken, User

        auth = req.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            if token:
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                api_token = (
                    APIToken.query
                    .join(User)
                    .filter(
                        APIToken.token_hash == token_hash,
                        APIToken.revoked_at.is_(None),
                        User.is_active.is_(True),
                    )
                    .first()
                )
                if api_token:
                    now = datetime.now(timezone.utc)
                    expires_at = api_token.expires_at
                    if expires_at and expires_at.replace(tzinfo=timezone.utc) <= now:
                        return None
                    last_used_at = api_token.last_used_at
                    if (
                        last_used_at is None
                        or last_used_at.replace(tzinfo=timezone.utc) < now - timedelta(hours=1)
                    ):
                        api_token.last_used_at = now
                        db.session.commit()
                    g.api_token = api_token
                    return api_token.user
        return None

    app.config["_raw_config"] = config_dict
    app.config["_config_path"] = str(resolved_config_path)

    from home_os.modules.ai import ai_bp
    from home_os.modules.auth import auth_bp
    from home_os.modules.budget import budget_bp
    from home_os.modules.calendar import calendar_bp
    from home_os.modules.dns import dns_bp
    from home_os.modules.files import files_bp
    from home_os.modules.media import media_bp
    from home_os.modules.monitor import monitor_bp
    from home_os.modules.music import music_bp
    from home_os.modules.network import network_bp
    from home_os.modules.settings import settings_bp
    from home_os.modules.storage import storage_bp

    from home_os.services.rate_limiter import login_limiter, music_limiter
    login_limiter.configure(Path(app.instance_path) / "rate_limit.db")
    music_limiter.configure(Path(app.instance_path) / "rate_limit.db")

    app.register_blueprint(ai_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(dns_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(music_bp)
    app.register_blueprint(network_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(storage_bp)

    @app.route("/")
    def index():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.default_page:
            page_map = {
                "dashboard": "monitor.dashboard",
                "files": "files.browse",
                "storage": "storage.overview",
                "ai": "ai.chat",
                "calendar": "calendar.calendar_view",
                "budget": "budget.budget_view",
            }
            target = page_map.get(current_user.default_page, "monitor.dashboard")
            return redirect(url_for(target))
        return redirect(url_for("monitor.dashboard"))

    @app.route("/health")
    def health():
        return {"status": "healthy", "version": "0.3.1"}

    # Security headers
    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith("/static/"):
            if app.debug:
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            else:
                response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        if request.path.startswith("/qbt/") or request.path.startswith("/svc/"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://yt3.googleusercontent.com; "
            "media-src 'self' https://*.googlevideo.com; "
            "font-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
            "frame-src 'self'; "
            "connect-src 'self' wss: ws:"
        )
        return response

    # Error pages
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    with app.app_context(), _schema_lock(app.instance_path):
        db_path = Path(config_dict["database"]["path"])
        if not db_path.is_absolute():
            db_path = ROOT_DIR / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            db.create_all()
        else:
            # Create any new tables that don't exist yet
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            existing = set(inspector.get_table_names())
            for table in db.metadata.tables.values():
                if table.name not in existing:
                    table.create(db.engine)
        _ensure_user_columns()
        _ensure_security_columns()
        from sqlalchemy import text
        db.session.execute(
            text("CREATE INDEX IF NOT EXISTS ix_trash_user_id ON trash (user_id)")
        )
        from home_os.models import APIToken, User
        for user in User.query.filter(User.api_token_hash.isnot(None)).all():
            if not APIToken.query.filter_by(token_hash=user.api_token_hash).first():
                db.session.add(APIToken(
                    user=user,
                    token_hash=user.api_token_hash,
                    name="legacy-native-app",
                ))
        db.session.commit()
    return app
