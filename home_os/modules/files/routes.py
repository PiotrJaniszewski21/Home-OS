from flask import (
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)
from flask_login import current_user, login_required

from home_os.extensions import csrf
from home_os.modules.files import files_bp


def get_file_service():
    from home_os.services.file_service import FileService

    config = current_app.config["_raw_config"]
    root = config["storage"]["root"]
    trash = config["storage"]["trash_path"]
    retention = config["storage"].get("trash_retention_days", 30)
    return FileService(root, trash, retention)


@files_bp.route("/files")
@files_bp.route("/files/<path:filepath>")
@login_required
def browse(filepath=""):
    svc = get_file_service()
    path = "/" + filepath

    try:
        info = svc.get_file_info(path)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Not found"}), 404
    except PermissionError:
        return jsonify({"ok": False, "error": "Access denied"}), 403

    if not info["is_dir"]:
        resolved = svc._resolve_and_validate(path)
        return send_file(resolved, as_attachment=("download" in request.args))

    entries = svc.list_directory(
        path,
        sort_by=request.args.get("sort", "name"),
        reverse=request.args.get("reverse", "").lower() == "true",
    )

    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": True, "data": {"path": path, "entries": entries}})

    parts = [p for p in path.split("/") if p]
    breadcrumbs = [{"name": "Root", "path": "/"}]
    for i, part in enumerate(parts):
        breadcrumbs.append({"name": part, "path": "/" + "/".join(parts[: i + 1])})

    return render_template(
        "files/browse.html", entries=entries, path=path, breadcrumbs=breadcrumbs
    )


@files_bp.route("/api/files/upload", methods=["POST"])
@login_required
def upload():
    from flask import redirect, url_for

    svc = get_file_service()
    dest = request.form.get("path", "/")

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    for f in request.files.getlist("file"):
        if f.filename:
            try:
                svc.save_upload(dest, f)
            except (PermissionError, NotADirectoryError) as e:
                return jsonify({"ok": False, "error": str(e)}), 400

    filepath = dest.strip("/")
    if filepath:
        return redirect(url_for("files.browse", filepath=filepath))
    return redirect(url_for("files.browse"))


@files_bp.route("/api/files/mkdir", methods=["POST"])
@login_required
def mkdir():
    svc = get_file_service()
    data = request.get_json()
    path = data.get("path", "")

    if not path:
        return jsonify({"ok": False, "error": "Path required"}), 400

    try:
        info = svc.create_directory(path)
        return jsonify({"ok": True, "data": info})
    except FileExistsError:
        return jsonify({"ok": False, "error": "Already exists"}), 409
    except PermissionError:
        return jsonify({"ok": False, "error": "Access denied"}), 403


@files_bp.route("/api/files/rename", methods=["POST"])
@login_required
def rename():
    svc = get_file_service()
    data = request.get_json()
    path = data.get("path", "")
    new_name = data.get("new_name", "")

    if not path or not new_name:
        return jsonify({"ok": False, "error": "path and new_name required"}), 400

    try:
        new_path = svc.rename(path, new_name)
        return jsonify({"ok": True, "data": {"path": new_path}})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "File not found"}), 404
    except FileExistsError:
        return jsonify({"ok": False, "error": "Name already taken"}), 409
    except (ValueError, PermissionError):
        return jsonify({"ok": False, "error": "Invalid operation"}), 400


@files_bp.route("/api/files/move", methods=["POST"])
@login_required
def move():
    svc = get_file_service()
    data = request.get_json()
    src = data.get("src", "")
    dest = data.get("dest", "")

    if not src or not dest:
        return jsonify({"ok": False, "error": "src and dest required"}), 400

    try:
        new_path = svc.move(src, dest)
        return jsonify({"ok": True, "data": {"path": new_path}})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "File not found"}), 404
    except FileExistsError:
        return jsonify({"ok": False, "error": "Already exists at destination"}), 409
    except (NotADirectoryError, PermissionError):
        return jsonify({"ok": False, "error": "Invalid operation"}), 400


@files_bp.route("/api/files/copy", methods=["POST"])
@login_required
def copy():
    svc = get_file_service()
    data = request.get_json()
    src = data.get("src", "")
    dest = data.get("dest", "")

    if not src or not dest:
        return jsonify({"ok": False, "error": "src and dest required"}), 400

    try:
        new_path = svc.copy(src, dest)
        return jsonify({"ok": True, "data": {"path": new_path}})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "File not found"}), 404
    except FileExistsError:
        return jsonify({"ok": False, "error": "Already exists at destination"}), 409
    except (NotADirectoryError, PermissionError):
        return jsonify({"ok": False, "error": "Invalid operation"}), 400


@files_bp.route("/api/files/delete", methods=["POST"])
@login_required
def delete():
    svc = get_file_service()
    data = request.get_json()
    path = data.get("path", "")

    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400

    try:
        entry = svc.delete(path)
        return jsonify({"ok": True, "data": {"id": entry.id, "original_path": entry.original_path}})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "File not found"}), 404
    except PermissionError:
        return jsonify({"ok": False, "error": "Access denied"}), 403


@files_bp.route("/api/files/search")
@login_required
def search():
    svc = get_file_service()
    query = request.args.get("q", "")
    path = request.args.get("path", "/")
    extensions = request.args.get("ext", "")

    if not query:
        return jsonify({"ok": False, "error": "q parameter required"}), 400

    ext_list = [e.strip() for e in extensions.split(",") if e.strip()] if extensions else None
    results = svc.search(query, path, ext_list)
    return jsonify({"ok": True, "data": results})


@files_bp.route("/files/search")
@login_required
def search_page():
    svc = get_file_service()
    query = request.args.get("q", "")
    ext = request.args.get("ext", "")
    results = []

    if query:
        ext_list = [e.strip() for e in ext.split(",") if e.strip()] if ext else None
        results = svc.search(query, "/", ext_list)

    return render_template("files/search.html", query=query, ext=ext, results=results)


@files_bp.route("/trash")
@login_required
def trash():
    svc = get_file_service()
    entries = svc.list_trash()

    if request.headers.get("Accept") == "application/json":
        return jsonify({"ok": True, "data": [
            {"id": e.id, "original_path": e.original_path, "size": e.size_bytes, "deleted_at": e.deleted_at.isoformat()}
            for e in entries
        ]})

    return render_template("files/trash.html", entries=entries)


@files_bp.route("/api/files/trash/<int:trash_id>/restore", methods=["POST"])
@login_required
def restore(trash_id):
    svc = get_file_service()
    try:
        svc.restore_from_trash(trash_id)
        return jsonify({"ok": True})
    except (FileNotFoundError, FileExistsError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@files_bp.route("/api/files/trash/<int:trash_id>", methods=["DELETE"])
@login_required
def permanent_delete(trash_id):
    svc = get_file_service()
    try:
        svc.permanent_delete(trash_id)
        return jsonify({"ok": True})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@files_bp.route("/api/files/trash/empty", methods=["POST"])
@login_required
def empty_trash():
    svc = get_file_service()
    svc.empty_trash()
    return jsonify({"ok": True})
