import os
import tempfile
from urllib.parse import urlsplit

import yaml
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from home_os.extensions import db
from home_os.modules.auth.routes import admin_required, fresh_session_required
from home_os.modules.settings import settings_bp


def _save_config(config):
    config_path = current_app.config["_config_path"]
    dir_name = os.path.dirname(config_path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_path)
    except Exception:
        os.unlink(tmp_path)
        raise


@settings_bp.route("/settings")
@login_required
def settings_view():
    from home_os.models.settings import Setting

    config = current_app.config["_raw_config"]
    tunnel_url = Setting.get("cloudflare_tunnel_url", "")
    return render_template("settings/settings.html", config=config, user=current_user, tunnel_url=tunnel_url)


@settings_bp.route("/settings/profile", methods=["POST"])
@login_required
def save_profile():
    email = request.form.get("email", "").strip()
    new_password = request.form.get("new_password", "").strip()
    default_page = request.form.get("default_page", "dashboard")

    current_user.email = email or None
    allowed_default_pages = {
        page for page in ("dashboard", "files", "storage", "calendar", "budget")
        if current_user.has_permission(page)
    }
    current_user.default_page = (
        default_page if default_page in allowed_default_pages else "dashboard"
    )

    if new_password:
        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("settings.settings_view"))
        current_password = request.form.get("current_password", "")
        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("settings.settings_view"))
        current_user.set_password(new_password)
        current_user.auth_version += 1
        current_user.api_token_hash = None
        for token in current_user.api_tokens.all():
            db.session.delete(token)

    db.session.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/dock", methods=["POST"])
@login_required
def save_dock():
    valid_tabs = {
        "dashboard", "files", "storage", "media", "calendar",
        "budget", "network", "settings",
    }
    tabs = [tab for tab in request.form.getlist("dock_tabs") if tab in valid_tabs]
    current_user.dock_tabs = ",".join(tabs) if tabs else None
    db.session.commit()
    flash("Dock updated.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/server", methods=["POST"])
@fresh_session_required
def save_server():
    config = current_app.config["_raw_config"]

    try:
        trash_days = int(request.form.get("trash_days", 30))
    except (ValueError, TypeError):
        flash("Invalid number value.", "error")
        return redirect(url_for("settings.settings_view"))

    if not 1 <= trash_days <= 3650:
        flash("Trash retention is outside the allowed range.", "error")
        return redirect(url_for("settings.settings_view"))

    config["server"]["port"] = 443
    config["storage"]["trash_retention_days"] = trash_days

    _save_config(config)

    flash("Server settings saved.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/adguard", methods=["POST"])
@fresh_session_required
def save_adguard():
    config = current_app.config["_raw_config"]

    if "adguard" not in config:
        config["adguard"] = {}

    adguard_url = request.form.get("adguard_url", "http://localhost:3000").strip()
    parsed_url = urlsplit(adguard_url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
        flash("AdGuard URL must be a valid HTTP or HTTPS URL.", "error")
        return redirect(url_for("settings.settings_view"))
    config["adguard"]["url"] = adguard_url.rstrip("/")
    config["adguard"]["username"] = request.form.get("adguard_username", "").strip()
    config["adguard"]["password"] = request.form.get("adguard_password", "").strip()

    _save_config(config)

    flash("AdGuard Home settings saved.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/media", methods=["POST"])
@fresh_session_required
def save_media():
    config = current_app.config["_raw_config"]

    if "media" not in config:
        config["media"] = {}

    try:
        config["media"]["plex_port"] = int(request.form.get("plex_port", 32400))
        config["media"]["jellyfin_port"] = int(request.form.get("jellyfin_port", 8096))
        config["media"]["sonarr_port"] = int(request.form.get("sonarr_port", 8989))
        config["media"]["radarr_port"] = int(request.form.get("radarr_port", 7878))
        config["media"]["prowlarr_port"] = int(request.form.get("prowlarr_port", 9696))
        config["media"]["overseerr_port"] = int(request.form.get("overseerr_port", 5055))
    except (ValueError, TypeError):
        flash("Invalid port number.", "error")
        return redirect(url_for("settings.settings_view"))

    if any(
        not 1 <= config["media"][key] <= 65535
        for key in (
            "plex_port",
            "jellyfin_port",
            "sonarr_port",
            "radarr_port",
            "prowlarr_port",
            "overseerr_port",
        )
    ):
        flash("Ports must be between 1 and 65535.", "error")
        return redirect(url_for("settings.settings_view"))

    _save_config(config)

    flash("Media service ports saved.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/cloudflare")
@admin_required
def cloudflare_setup():
    return redirect(url_for("network.network_view") + "#tunnel")
