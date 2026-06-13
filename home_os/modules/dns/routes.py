from flask import current_app, jsonify, render_template, request
from flask_login import login_required

from home_os.modules.auth.routes import admin_required
from home_os.modules.dns import dns_bp


def get_adguard():
    from home_os.services.adguard_service import AdGuardService

    config = current_app.config["_raw_config"]
    ag = config.get("adguard", {})
    return AdGuardService(
        url=ag.get("url", "http://localhost:3000"),
        username=ag.get("username", ""),
        password=ag.get("password", ""),
    )


@dns_bp.route("/dns")
@admin_required
def dns_view():
    return render_template("dns/dns.html")


@dns_bp.route("/api/dns/status")
@admin_required
def dns_status():
    try:
        ag = get_adguard()
        status = ag.get_status()
        stats = ag.get_top_clients()
        return jsonify({"ok": True, "data": {**status, **stats}})
    except Exception as e:
        return jsonify({"ok": False, "error": "Cannot connect to AdGuard Home. Is it running?"}), 503


@dns_bp.route("/api/dns/toggle", methods=["POST"])
@admin_required
def dns_toggle():
    try:
        ag = get_adguard()
        data = request.get_json()
        ag.set_protection(data.get("enabled", True))
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Failed to toggle protection"}), 500


@dns_bp.route("/api/dns/querylog")
@admin_required
def dns_querylog():
    try:
        ag = get_adguard()
        log = ag.get_query_log(limit=100)
        entries = []
        for entry in log.get("data", []):
            entries.append({
                "domain": entry.get("QH", ""),
                "type": entry.get("QT", ""),
                "client": entry.get("CP", "") or entry.get("client", ""),
                "status": entry.get("reason", ""),
                "time": entry.get("time", ""),
                "blocked": entry.get("reason", "") != "NotFilteredNotFound",
            })
        return jsonify({"ok": True, "data": entries})
    except Exception:
        return jsonify({"ok": False, "error": "Cannot fetch query log"}), 503


@dns_bp.route("/api/dns/rewrites")
@admin_required
def dns_rewrites():
    try:
        ag = get_adguard()
        rewrites = ag.get_rewrites()
        return jsonify({"ok": True, "data": rewrites})
    except Exception:
        return jsonify({"ok": False, "error": "Cannot fetch DNS records"}), 503


@dns_bp.route("/api/dns/rewrites", methods=["POST"])
@admin_required
def add_rewrite():
    try:
        ag = get_adguard()
        data = request.get_json()
        domain = data.get("domain", "").strip()
        answer = data.get("answer", "").strip()
        if not domain or not answer:
            return jsonify({"ok": False, "error": "Domain and answer required"}), 400
        ag.add_rewrite(domain, answer)
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Failed to add record"}), 500


@dns_bp.route("/api/dns/rewrites/delete", methods=["POST"])
@admin_required
def delete_rewrite():
    try:
        ag = get_adguard()
        data = request.get_json()
        ag.delete_rewrite(data["domain"], data["answer"])
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Failed to delete record"}), 500


@dns_bp.route("/api/dns/filters")
@admin_required
def dns_filters():
    try:
        ag = get_adguard()
        status = ag.get_filtering_status()
        return jsonify({"ok": True, "data": status})
    except Exception:
        return jsonify({"ok": False, "error": "Cannot fetch filters"}), 503
