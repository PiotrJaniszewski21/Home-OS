import os
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, jsonify, render_template, request, send_file
from flask_login import login_required

from home_os.modules.storage import storage_bp


def get_storage_service():
    from home_os.services.storage_service import StorageService

    config = current_app.config["_raw_config"]
    root = config["storage"]["root"]
    return StorageService(root)


@storage_bp.route("/storage")
@login_required
def overview():
    svc = get_storage_service()
    main_usage = svc.get_main_storage_usage()
    drives = svc.detect_external_drives()

    if request.headers.get("Accept") == "application/json":
        return jsonify({
            "ok": True,
            "data": {
                "main": main_usage,
                "drives": [d.to_dict() for d in drives],
            }
        })

    return render_template("storage/overview.html", main=main_usage, drives=drives)


@storage_bp.route("/files/drive/<name>")
@storage_bp.route("/files/drive/<name>/<path:filepath>")
@login_required
def browse_drive(name, filepath=""):
    svc = get_storage_service()
    drive = svc.get_drive_by_name(name)

    if not drive:
        return render_template("storage/drive_not_found.html", name=name), 404

    base = Path(drive.mount_point)
    resolved = (base / filepath).resolve()

    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "Access denied"}), 403

    if not resolved.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404

    if resolved.is_file():
        return send_file(resolved, as_attachment=("download" in request.args))

    entries = []
    for item in sorted(resolved.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            stat = item.stat()
            entries.append({
                "name": item.name,
                "path": str(item.relative_to(base)),
                "is_dir": item.is_dir(),
                "size": stat.st_size if item.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        except (PermissionError, OSError):
            continue

    path = "/" + filepath if filepath else "/"
    parts = [p for p in filepath.split("/") if p]
    breadcrumbs = [{"name": drive.name, "path": ""}]
    for i, part in enumerate(parts):
        breadcrumbs.append({"name": part, "path": "/".join(parts[: i + 1])})

    return render_template(
        "storage/browse_drive.html",
        drive=drive,
        entries=entries,
        path=path,
        filepath=filepath,
        breadcrumbs=breadcrumbs,
    )
