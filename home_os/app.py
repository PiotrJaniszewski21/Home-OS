import os
import secrets
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from home_os.config import ROOT_DIR, create_flask_config, load_config
from home_os.extensions import csrf, db, login_manager


def create_app(config_path=None):
    app = Flask(
        __name__,
        instance_path=str(ROOT_DIR / "data"),
        template_folder="templates",
    )

    config_dict = load_config(config_path)
    flask_config = create_flask_config(config_dict)

    # Generate a real secret key if still using the default
    if flask_config["SECRET_KEY"] == "change-me-in-production":
        flask_config["SECRET_KEY"] = secrets.token_hex(32)

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

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

    @app.before_request
    def _csrf_check():
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        # Skip CSRF for Bearer token authenticated API requests
        if request.path.startswith("/api/") and request.headers.get("Authorization", "").startswith("Bearer "):
            return
        # Skip CSRF for the login endpoint (no session yet)
        if request.path == "/api/login":
            return
        csrf.protect()

    @login_manager.user_loader
    def load_user(user_id):
        from home_os.models import User

        return User.query.get(int(user_id))

    @login_manager.request_loader
    def load_user_from_token(req):
        import hashlib
        from home_os.models import User

        auth = req.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            if token:
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                return User.query.filter_by(api_token_hash=token_hash, is_active=True).first()
        return None

    app.config["_raw_config"] = config_dict

    from home_os.modules.ai import ai_bp
    from home_os.modules.auth import auth_bp
    from home_os.modules.budget import budget_bp
    from home_os.modules.calendar import calendar_bp
    from home_os.modules.dns import dns_bp
    from home_os.modules.files import files_bp
    from home_os.modules.monitor import monitor_bp
    from home_os.modules.network import network_bp
    from home_os.modules.settings import settings_bp
    from home_os.modules.sharing import sharing_bp
    from home_os.modules.storage import storage_bp
    from home_os.modules.terminal import terminal_bp

    app.register_blueprint(ai_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(dns_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(network_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(sharing_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(terminal_bp)

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
        return {"status": "healthy", "version": "0.1.0"}

    # Security headers
    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com"
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

    with app.app_context():
        db_path = Path(config_dict["database"]["path"])
        if not db_path.is_absolute():
            db_path = ROOT_DIR / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            db.create_all()
            # Dev-only test account (only when debug=True)
            if app.debug:
                from home_os.models import User
                if not User.query.filter_by(username="123").first():
                    user = User(username="123", role="admin", home_directory="/")
                    user.set_password("123")
                    db.session.add(user)
                    db.session.commit()

    return app
