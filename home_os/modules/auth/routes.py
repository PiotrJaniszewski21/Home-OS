import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import hashlib
import secrets

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from home_os.extensions import csrf, db
from home_os.models import User
from home_os.modules.auth import auth_bp
from home_os.modules.auth.forms import CreateUserForm, LoginForm, SetupForm

SESSION_FRESHNESS_SECONDS = 300  # 5 minutes


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("monitor.dashboard"))
        return f(*args, **kwargs)

    return decorated


def fresh_session_required(f):
    """Require that the user logged in within the last 5 minutes.
    Used for high-risk routes like terminal access."""
    @wraps(f)
    @admin_required
    def decorated(*args, **kwargs):
        login_ts = session.get("login_ts", 0)
        if time.time() - login_ts > SESSION_FRESHNESS_SECONDS:
            session["next_after_reauth"] = request.url
            flash("Please re-enter your password to access this feature.", "warning")
            return redirect(url_for("auth.reauth"))
        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    lock_file = Path(current_app.instance_path) / "setup.lock"
    if lock_file.exists() or User.query.first() is not None:
        return redirect(url_for("auth.login"))

    form = SetupForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data or None,
            role="admin",
            home_directory="/",
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(datetime.now(timezone.utc).isoformat())
        login_user(user)
        flash("Admin account created. Welcome to Home OS!", "success")
        return redirect(url_for("monitor.dashboard"))

    return render_template("auth/setup.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if User.query.first() is None:
        return redirect(url_for("auth.setup"))

    if current_user.is_authenticated:
        return redirect(url_for("monitor.dashboard"))

    from home_os.services.rate_limiter import login_limiter

    client_ip = request.remote_addr
    if login_limiter.is_limited(client_ip):
        flash("Too many login attempts. Try again later.", "error")
        return render_template("auth/login.html", form=LoginForm())

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        if login_limiter.is_limited(username, max_attempts=login_limiter.per_account_max):
            flash("This account is temporarily locked. Try again later.", "error")
            return render_template("auth/login.html", form=form)

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(form.password.data) and user.is_active:
            login_limiter.reset(client_ip)
            login_limiter.reset(username)
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user, remember=form.remember_me.data)
            session["login_ts"] = time.time()
            next_page = request.args.get("next")
            if next_page and not next_page.startswith("/"):
                next_page = None
            return redirect(next_page or url_for("monitor.dashboard"))
        login_limiter.record(client_ip)
        login_limiter.record(username)
        flash("Invalid username or password.", "error")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/api/login", methods=["POST"])
@csrf.exempt
def api_login():
    """Token-based login for native apps (Mac app, API clients)."""
    from home_os.services.rate_limiter import login_limiter

    client_ip = request.remote_addr
    if login_limiter.is_limited(client_ip):
        return jsonify({"ok": False, "error": "Too many attempts"}), 429

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    username = data.get("username", "")
    password = data.get("password", "")

    if login_limiter.is_limited(username, max_attempts=login_limiter.per_account_max):
        return jsonify({"ok": False, "error": "Account temporarily locked"}), 429

    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password) and user.is_active:
        login_limiter.reset(client_ip)
        login_limiter.reset(username)
        user.last_login = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(32)
        user.api_token_hash = hashlib.sha256(token.encode()).hexdigest()
        db.session.commit()
        return jsonify({
            "ok": True,
            "data": {
                "token": token,
                "user": {"username": user.username, "role": user.role},
            }
        })

    login_limiter.record(client_ip)
    login_limiter.record(username)
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@auth_bp.route("/reauth", methods=["GET", "POST"])
@login_required
def reauth():
    """Re-authentication gate for sensitive actions."""
    from home_os.services.rate_limiter import login_limiter

    client_ip = request.remote_addr
    if login_limiter.is_limited(client_ip):
        flash("Too many attempts. Try again later.", "error")
        return render_template("auth/reauth.html", form=LoginForm())

    form = LoginForm()
    if form.validate_on_submit():
        if current_user.check_password(form.password.data):
            login_limiter.reset(client_ip)
            session["login_ts"] = time.time()
            next_url = session.pop("next_after_reauth", None)
            return redirect(next_url or url_for("monitor.dashboard"))
        login_limiter.record(client_ip)
        flash("Incorrect password.", "error")

    return render_template("auth/reauth.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template("auth/users.html", users=all_users)


@auth_bp.route("/users/create", methods=["GET", "POST"])
@admin_required
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already exists.", "error")
        else:
            user = User(
                username=form.username.data,
                email=form.email.data or None,
                role=form.role.data,
                home_directory="/",
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash(f"User '{user.username}' created.", "success")
            return redirect(url_for("auth.users"))

    return render_template("auth/create_user.html", form=form)


@auth_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        user.role = request.form.get("role", "user")
        user.default_page = request.form.get("default_page", "dashboard")
        perms = request.form.getlist("permissions")
        user.permissions = ",".join(perms)
        db.session.commit()
        flash(f"User '{user.username}' updated.", "success")
        return redirect(url_for("auth.users"))

    return render_template("auth/edit_user.html", user=user)


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete yourself.", "error")
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f"User '{user.username}' deleted.", "success")
    return redirect(url_for("auth.users"))


@auth_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate yourself.", "error")
    else:
        user.is_active = not user.is_active
        db.session.commit()
        status = "activated" if user.is_active else "deactivated"
        flash(f"User '{user.username}' {status}.", "success")
    return redirect(url_for("auth.users"))
