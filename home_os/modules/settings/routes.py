import yaml
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from home_os.config import ROOT_DIR
from home_os.extensions import db
from home_os.models.user import User
from home_os.modules.auth.routes import admin_required
from home_os.modules.settings import settings_bp


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


@settings_bp.route("/settings/server", methods=["POST"])
@admin_required
def save_server():
    config = current_app.config["_raw_config"]

    config["server"]["port"] = int(request.form.get("port", 5000))
    config["storage"]["trash_retention_days"] = int(request.form.get("trash_days", 30))

    config_path = ROOT_DIR / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    flash("Server settings saved. Restart to apply port changes.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/cloudflare")
@admin_required
def cloudflare_setup():
    from home_os.models.settings import Setting

    tunnel_url = Setting.get("cloudflare_tunnel_url", "")
    tunnel_domain = Setting.get("cloudflare_domain", "")
    tunnel_configured = Setting.get("cloudflare_configured", "false") == "true"
    return render_template("settings/cloudflare.html",
                           tunnel_url=tunnel_url,
                           tunnel_domain=tunnel_domain,
                           tunnel_configured=tunnel_configured)


@settings_bp.route("/settings/cloudflare", methods=["POST"])
@admin_required
def save_cloudflare():
    from home_os.models.settings import Setting

    tunnel_url = request.form.get("tunnel_url", "").strip()
    tunnel_domain = request.form.get("tunnel_domain", "").strip()

    Setting.set("cloudflare_tunnel_url", tunnel_url)
    Setting.set("cloudflare_domain", tunnel_domain)
    Setting.set("cloudflare_configured", "true" if tunnel_url else "false")

    flash("Cloudflare Tunnel configured.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/cloudflare/reset", methods=["POST"])
@admin_required
def reset_cloudflare():
    from home_os.models.settings import Setting

    Setting.set("cloudflare_tunnel_url", "")
    Setting.set("cloudflare_domain", "")
    Setting.set("cloudflare_configured", "false")

    flash("Cloudflare Tunnel configuration removed.", "success")
    return redirect(url_for("settings.settings_view"))
