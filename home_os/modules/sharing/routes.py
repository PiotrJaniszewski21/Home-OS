from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from home_os.modules.auth.routes import admin_required
from home_os.modules.sharing import sharing_bp


def get_share_service():
    from home_os.services.share_service import ShareService

    config = current_app.config["_raw_config"]
    root = config["storage"]["root"]
    return ShareService(root)


@sharing_bp.route("/sharing")
@admin_required
def shares():
    svc = get_share_service()
    all_shares = svc.list_shares()
    return render_template("sharing/shares.html", shares=all_shares)


@sharing_bp.route("/sharing/create", methods=["POST"])
@admin_required
def create_share():
    svc = get_share_service()
    name = request.form.get("name", "").strip()
    path = request.form.get("path", "").strip()
    read_only = request.form.get("read_only") == "on"
    guest_access = request.form.get("guest_access") == "on"

    if not name or not path:
        flash("Name and path are required.", "error")
        return redirect(url_for("sharing.shares"))

    try:
        svc.create_share(name, path, read_only, guest_access)
        flash(f"Share '{name}' created.", "success")
    except (PermissionError, FileNotFoundError, ValueError) as e:
        flash(str(e), "error")

    return redirect(url_for("sharing.shares"))


@sharing_bp.route("/sharing/<int:share_id>/delete", methods=["POST"])
@admin_required
def delete_share(share_id):
    svc = get_share_service()
    try:
        svc.delete_share(share_id)
        flash("Share deleted.", "success")
    except FileNotFoundError:
        flash("Share not found.", "error")
    return redirect(url_for("sharing.shares"))


@sharing_bp.route("/sharing/<int:share_id>/toggle", methods=["POST"])
@admin_required
def toggle_share(share_id):
    svc = get_share_service()
    try:
        share = svc.toggle_share(share_id)
        status = "enabled" if share.is_active else "disabled"
        flash(f"Share '{share.name}' {status}.", "success")
    except FileNotFoundError:
        flash("Share not found.", "error")
    return redirect(url_for("sharing.shares"))
