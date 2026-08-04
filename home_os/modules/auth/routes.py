from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import hashlib
import ipaddress
import os
import secrets

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import confirm_login, current_user, login_fresh, login_required, login_user, logout_user

from home_os.extensions import csrf, db
from home_os.models import APIToken, User
from home_os.modules.auth import auth_bp
from home_os.modules.auth.forms import CreateUserForm, LoginForm, SetupForm

SESSION_FRESHNESS_SECONDS = 300  # 5 minutes
API_TOKEN_LIFETIME_DAYS = 365


def _local_redirect_target(value):
    """Return a same-site path, rejecting scheme-relative and absolute URLs."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return None
    return value


def _request_path_with_query():
    path = request.full_path.rstrip("?")
    return _local_redirect_target(path) or url_for("monitor.dashboard")


def _rate_limit_keys():
    remote_address = request.remote_addr or "unknown"
    try:
        remote_ip = ipaddress.ip_address(remote_address)
    except ValueError:
        remote_ip = None
    cloudflare_address = request.headers.get("CF-Connecting-IP", "")
    if remote_ip and remote_ip.is_loopback and cloudflare_address:
        try:
            remote_address = str(ipaddress.ip_address(cloudflare_address))
        except ValueError:
            pass
    return f"ip:{remote_address}"


def _account_rate_limit_key(username):
    return f"account:{username.casefold()}"


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Admin access required"}), 403
            flash("Admin access required.", "error")
            return redirect(url_for("monitor.dashboard"))
        return f(*args, **kwargs)

    return decorated


def fresh_session_required(f):
    """Require that the user logged in within the last 5 minutes.
    Used for high-risk system administration routes."""
    @wraps(f)
    @admin_required
    def decorated(*args, **kwargs):
        if not login_fresh():
            session["next_after_reauth"] = _request_path_with_query()
            if request.path.startswith("/api/"):
                return jsonify({
                    "ok": False,
                    "error": "Recent password confirmation required",
                    "reauth_url": url_for("auth.reauth"),
                }), 401
            flash("Please re-enter your password to access this feature.", "warning")
            return redirect(url_for("auth.reauth"))
        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    lock_file = Path(current_app.instance_path) / "setup.lock"
    if lock_file.exists() or User.query.first() is not None:
        return redirect(url_for("auth.login"))

    # Never allow the first administrator to be claimed through Cloudflare.
    if request.headers.get("CF-Connecting-IP"):
        abort(403)

    form = SetupForm()
    if form.validate_on_submit():
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_descriptor = os.open(
                lock_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return redirect(url_for("auth.login"))
        try:
            with os.fdopen(lock_descriptor, "w") as setup_lock:
                setup_lock.write(datetime.now(timezone.utc).isoformat())
                setup_lock.flush()
                os.fsync(setup_lock.fileno())
            if User.query.first() is not None:
                return redirect(url_for("auth.login"))
            user = User(
                username=form.username.data,
                email=form.email.data or None,
                role="admin",
                home_directory="/",
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
        except Exception:
            db.session.rollback()
            lock_file.unlink(missing_ok=True)
            raise
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

    client_ip = _rate_limit_keys()
    if login_limiter.is_limited(client_ip):
        flash("Too many login attempts. Try again later.", "error")
        return render_template("auth/login.html", form=LoginForm())

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        account_key = _account_rate_limit_key(username)
        if login_limiter.is_limited(account_key, max_attempts=login_limiter.per_account_max):
            flash("This account is temporarily locked. Try again later.", "error")
            return render_template("auth/login.html", form=form)

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(form.password.data) and user.is_active:
            login_limiter.reset(client_ip)
            login_limiter.reset(account_key)
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user, remember=form.remember_me.data)
            next_page = _local_redirect_target(request.args.get("next"))
            return redirect(next_page or url_for("monitor.dashboard"))
        login_limiter.record(client_ip)
        login_limiter.record(account_key)
        flash("Invalid username or password.", "error")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/api/login", methods=["POST"])
@csrf.exempt
def api_login():
    """Token-based login for native apps (Mac app, API clients)."""
    from home_os.services.rate_limiter import login_limiter

    client_ip = _rate_limit_keys()
    if login_limiter.is_limited(client_ip):
        return jsonify({"ok": False, "error": "Too many attempts"}), 429

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    username = data.get("username", "")
    password = data.get("password", "")

    account_key = _account_rate_limit_key(username)
    if login_limiter.is_limited(account_key, max_attempts=login_limiter.per_account_max):
        return jsonify({"ok": False, "error": "Account temporarily locked"}), 429

    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password) and user.is_active:
        login_limiter.reset(client_ip)
        login_limiter.reset(account_key)
        user.last_login = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.api_token_hash = token_hash
        db.session.add(APIToken(
            user=user,
            token_hash=token_hash,
            name="native-app",
            user_agent=(request.headers.get("User-Agent") or "")[:255],
            expires_at=datetime.now(timezone.utc) + timedelta(days=API_TOKEN_LIFETIME_DAYS),
        ))
        db.session.commit()
        return jsonify({
            "ok": True,
            "data": {
                "token": token,
                "user": {"username": user.username, "role": user.role},
            }
        })

    login_limiter.record(client_ip)
    login_limiter.record(account_key)
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@auth_bp.route("/reauth", methods=["GET", "POST"])
@login_required
def reauth():
    """Re-authentication gate for sensitive actions."""
    from home_os.services.rate_limiter import login_limiter

    client_ip = _rate_limit_keys()
    if login_limiter.is_limited(client_ip):
        flash("Too many attempts. Try again later.", "error")
        return render_template("auth/reauth.html", form=LoginForm())

    form = LoginForm()
    if form.validate_on_submit():
        if current_user.check_password(form.password.data):
            login_limiter.reset(client_ip)
            confirm_login()
            next_url = _local_redirect_target(session.pop("next_after_reauth", None))
            return redirect(next_url or url_for("monitor.dashboard"))
        login_limiter.record(client_ip)
        flash("Incorrect password.", "error")

    return render_template("auth/reauth.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/api/logout", methods=["POST"])
@login_required
def api_logout():
    """Revoke the bearer token used for this request."""
    from flask import g

    api_token = getattr(g, "api_token", None)
    if api_token is None:
        return jsonify({"ok": False, "error": "Bearer token required"}), 400
    api_token.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True})


@auth_bp.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template("auth/users.html", users=all_users)


@auth_bp.route("/users/create", methods=["GET", "POST"])
@fresh_session_required
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
                home_directory=f"/users/{form.username.data}",
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash(f"User '{user.username}' created.", "success")
            return redirect(url_for("auth.users"))

    return render_template("auth/create_user.html", form=form)


@auth_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@fresh_session_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        requested_role = request.form.get("role", "user")
        if user.id == current_user.id and requested_role != "admin":
            flash("You cannot remove your own administrator role.", "error")
            return redirect(url_for("auth.edit_user", user_id=user.id))
        user.role = requested_role
        user.default_page = request.form.get("default_page", "dashboard")
        perms = request.form.getlist("permissions")
        user.permissions = ",".join(perms)
        db.session.commit()
        flash(f"User '{user.username}' updated.", "success")
        return redirect(url_for("auth.users"))

    return render_template("auth/edit_user.html", user=user)


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@fresh_session_required
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
@fresh_session_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate yourself.", "error")
    else:
        user.is_active = not user.is_active
        user.auth_version += 1
        db.session.commit()
        status = "activated" if user.is_active else "deactivated"
        flash(f"User '{user.username}' {status}.", "success")
    return redirect(url_for("auth.users"))
