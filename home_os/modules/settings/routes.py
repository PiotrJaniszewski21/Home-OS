import os
import tempfile

import yaml
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from home_os.config import ROOT_DIR, get_config_path
from home_os.extensions import db
from home_os.models.user import User
from home_os.modules.auth.routes import admin_required
from home_os.modules.settings import settings_bp


def _save_config(config):
    config_path = get_config_path()
    dir_name = os.path.dirname(config_path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
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
    current_user.default_page = default_page

    if new_password:
        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("settings.settings_view"))
        current_user.set_password(new_password)

    db.session.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/dock", methods=["POST"])
@login_required
def save_dock():
    tabs = request.form.getlist("dock_tabs")
    current_user.dock_tabs = ",".join(tabs) if tabs else None
    db.session.commit()
    flash("Dock updated.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/server", methods=["POST"])
@admin_required
def save_server():
    config = current_app.config["_raw_config"]

    try:
        port = int(request.form.get("port", 5000))
        trash_days = int(request.form.get("trash_days", 30))
    except (ValueError, TypeError):
        flash("Invalid number value.", "error")
        return redirect(url_for("settings.settings_view"))

    config["server"]["port"] = port
    config["storage"]["trash_retention_days"] = trash_days

    _save_config(config)

    flash("Server settings saved. Restart to apply port changes.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/adguard", methods=["POST"])
@admin_required
def save_adguard():
    config = current_app.config["_raw_config"]

    if "adguard" not in config:
        config["adguard"] = {}

    config["adguard"]["url"] = request.form.get("adguard_url", "http://localhost:3000").strip()
    config["adguard"]["username"] = request.form.get("adguard_username", "").strip()
    config["adguard"]["password"] = request.form.get("adguard_password", "").strip()

    _save_config(config)

    flash("AdGuard Home settings saved.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/media", methods=["POST"])
@admin_required
def save_media():
    config = current_app.config["_raw_config"]

    if "media" not in config:
        config["media"] = {}

    try:
        config["media"]["plex_port"] = int(request.form.get("plex_port", 32400))
        config["media"]["sonarr_port"] = int(request.form.get("sonarr_port", 8989))
        config["media"]["radarr_port"] = int(request.form.get("radarr_port", 7878))
        config["media"]["prowlarr_port"] = int(request.form.get("prowlarr_port", 9696))
        config["media"]["overseerr_port"] = int(request.form.get("overseerr_port", 5055))
    except (ValueError, TypeError):
        flash("Invalid port number.", "error")
        return redirect(url_for("settings.settings_view"))

    _save_config(config)

    flash("Media service ports saved.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/cloudflare")
@admin_required
def cloudflare_setup():
    return redirect(url_for("network.network_view") + "#tunnel")
