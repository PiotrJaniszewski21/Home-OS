import json
import os
import threading
import uuid
import time
from pathlib import Path

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

_TRANSFERS_DIR = Path("/tmp/home_os_transfers")
_transfer_lock = threading.Lock()


def _get_transfers_dir():
    """Return the directory used for transfer state files, creating it if needed."""
    _TRANSFERS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return _TRANSFERS_DIR


def _write_transfer(transfer_id, data):
    """Atomically write transfer state to a JSON file."""
    import tempfile
    dest = _get_transfers_dir() / f"{transfer_id}.json"
    fd, tmp_path = tempfile.mkstemp(dir=_get_transfers_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_transfer(transfer_id):
    """Read transfer state from a JSON file, or return None if missing."""
    path = _get_transfers_dir() / f"{transfer_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _update_transfer(transfer_id, updates):
    """Atomically read, update, and write back transfer state."""
    with _transfer_lock:
        data = _read_transfer(transfer_id)
        if data is None:
            return
        data.update(updates)
        _write_transfer(transfer_id, data)


def _schedule_transfer_cleanup(transfer_id, delay=60):
    """Delete the transfer state file after a delay (seconds)."""
    def _cleanup():
        time.sleep(delay)
        path = _get_transfers_dir() / f"{transfer_id}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    t = threading.Thread(target=_cleanup, daemon=True)
    t.start()


def get_file_service(system=False):
    from home_os.services.file_service import FileService

    config = current_app.config["_raw_config"]
    if system:
        trash = config["storage"]["trash_path"]
        retention = config["storage"].get("trash_retention_days", 30)
        return FileService("/", trash, retention)
    root = config["storage"]["root"]
    trash = config["storage"]["trash_path"]
    retention = config["storage"].get("trash_retention_days", 30)
    return FileService(root, trash, retention)


def _get_file_service_from_request():
    system = request.form.get("system", request.args.get("system", "")).lower() in ("1", "true")
    if not system:
        data = request.get_json(silent=True)
        if data and str(data.get("system", "")).lower() in ("1", "true"):
            system = True
    if system and not current_user.is_admin:
        return None, (jsonify({"ok": False, "error": "Access denied"}), 403)
    return get_file_service(system=system), None


@files_bp.route("/files")
@files_bp.route("/files/<path:filepath>")
@login_required
def browse(filepath=""):
    system_mode = request.args.get("system", "").lower() in ("1", "true")
    if system_mode and not current_user.is_admin:
        return jsonify({"ok": False, "error": "Access denied"}), 403

    svc = get_file_service(system=system_mode)
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
    breadcrumbs = [{"name": "System /" if system_mode else "Root", "path": "/"}]
    for i, part in enumerate(parts):
        breadcrumbs.append({"name": part, "path": "/" + "/".join(parts[: i + 1])})

    storage_info = None
    locations = None
    if path == "/" and not system_mode:
        from home_os.services.storage_service import StorageService
        from home_os.models.user import User
        from pathlib import Path as P
        config = current_app.config["_raw_config"]
        storage_root = config["storage"]["root"]
        storage_svc = StorageService(storage_root)

        # Ensure user folder exists
        user_dir = P(storage_root) / "users" / current_user.username
        user_dir.mkdir(parents=True, exist_ok=True)
        # Ensure HomeOS shared folder exists
        (P(storage_root) / "HomeOS").mkdir(exist_ok=True)

        import shutil

        def folder_size(p):
            total = 0
            try:
                for item in p.iterdir():
                    if item.is_file():
                        total += item.stat().st_size
                    elif item.is_dir():
                        try:
                            for f in item.iterdir():
                                if f.is_file():
                                    total += f.stat().st_size
                        except (PermissionError, OSError):
                            pass
            except (PermissionError, OSError):
                pass
            return total

        # Get all users for folder listing
        all_users = User.query.all()
        user_folders = []
        for u in all_users:
            udir = P(storage_root) / "users" / u.username
            udir.mkdir(parents=True, exist_ok=True)
            user_folders.append({
                "name": u.username,
                "path": "/users/" + u.username,
                "used_bytes": folder_size(udir),
            })

        homeos_dir = P(storage_root) / "HomeOS"
        disk_usage = shutil.disk_usage(storage_root)

        storage_info = {
            "main": storage_svc.get_main_storage_usage(),
            "drives": storage_svc.detect_external_drives(),
        }

        locations = {
            "homeos": {
                "name": "HomeOS",
                "path": "/HomeOS",
                "used_bytes": folder_size(homeos_dir),
            },
            "user_folders": user_folders,
            "current_user": current_user.username,
            "disk_total": disk_usage.total,
            "disk_used": disk_usage.used,
            "disk_free": disk_usage.free,
        }

        # Load user shortcuts
        from home_os.models.settings import Setting
        shortcut_key = f"shortcuts_{current_user.username}"
        shortcuts = json.loads(Setting.get(shortcut_key, "[]"))
        locations["shortcuts"] = shortcuts

        # Filter out HomeOS, users, and any symlinks (drive mounts) from regular entries
        hidden = {"HomeOS", "users"}
        for item in P(storage_root).iterdir():
            if item.is_symlink():
                hidden.add(item.name)
        entries = [e for e in entries if e["name"] not in hidden]

    return render_template(
        "files/browse.html", entries=entries, path=path, breadcrumbs=breadcrumbs,
        system_mode=system_mode, storage_info=storage_info, locations=locations,
    )


@files_bp.route("/api/files/upload", methods=["POST"])
@login_required
def upload():
    from flask import redirect, url_for

    svc, err = _get_file_service_from_request()
    if err:
        return err
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
    svc, err = _get_file_service_from_request()
    if err:
        return err
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
    svc, err = _get_file_service_from_request()
    if err:
        return err
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
    import shutil
    from pathlib import Path as P

    data = request.get_json()
    src = data.get("src", "")
    dest = data.get("dest", "")
    src_drive = data.get("src_drive", "")

    if not src or not dest:
        return jsonify({"ok": False, "error": "src and dest required"}), 400

    # Cross-source move: source is on a drive
    if src_drive:
        from home_os.services.storage_service import StorageService
        config = current_app.config["_raw_config"]
        storage_svc = StorageService(config["storage"]["root"])
        drive = storage_svc.get_drive_by_name(src_drive)
        if not drive:
            return jsonify({"ok": False, "error": "Drive not found"}), 404

        src_abs = (P(drive.mount_point) / src).resolve()
        try:
            src_abs.relative_to(P(drive.mount_point).resolve())
        except ValueError:
            return jsonify({"ok": False, "error": "Access denied"}), 403

        if not src_abs.exists():
            return jsonify({"ok": False, "error": "File not found"}), 404

        svc, err = _get_file_service_from_request()
        if err:
            return err
        dest_dir = svc._resolve_and_validate(dest)
        if not dest_dir.is_dir():
            return jsonify({"ok": False, "error": "Destination is not a directory"}), 400

        dest_path = dest_dir / src_abs.name
        if dest_path.exists():
            return jsonify({"ok": False, "error": "Already exists at destination"}), 409

        try:
            shutil.move(str(src_abs), str(dest_path))
            return jsonify({"ok": True, "data": {"path": str(P(dest) / src_abs.name)}})
        except (PermissionError, OSError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    # Same-source move
    svc, err = _get_file_service_from_request()
    if err:
        return err
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
    import shutil
    from pathlib import Path as P

    data = request.get_json()
    src = data.get("src", "")
    dest = data.get("dest", "")
    src_drive = data.get("src_drive", "")

    if not src or not dest:
        return jsonify({"ok": False, "error": "src and dest required"}), 400

    # Cross-source copy: source is on a drive
    if src_drive:
        from home_os.services.storage_service import StorageService
        config = current_app.config["_raw_config"]
        storage_svc = StorageService(config["storage"]["root"])
        drive = storage_svc.get_drive_by_name(src_drive)
        if not drive:
            return jsonify({"ok": False, "error": "Drive not found"}), 404

        src_abs = (P(drive.mount_point) / src).resolve()
        try:
            src_abs.relative_to(P(drive.mount_point).resolve())
        except ValueError:
            return jsonify({"ok": False, "error": "Access denied"}), 403

        if not src_abs.exists():
            return jsonify({"ok": False, "error": "File not found"}), 404

        svc, err = _get_file_service_from_request()
        if err:
            return err
        dest_dir = svc._resolve_and_validate(dest)
        if not dest_dir.is_dir():
            return jsonify({"ok": False, "error": "Destination is not a directory"}), 400

        dest_path = dest_dir / src_abs.name
        if dest_path.exists():
            return jsonify({"ok": False, "error": "Already exists at destination"}), 409

        try:
            if src_abs.is_dir():
                shutil.copytree(str(src_abs), str(dest_path))
            else:
                shutil.copy2(str(src_abs), str(dest_path))
            return jsonify({"ok": True, "data": {"path": str(P(dest) / src_abs.name)}})
        except (PermissionError, OSError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    # Same-source copy
    svc, err = _get_file_service_from_request()
    if err:
        return err
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
    import shutil
    from pathlib import Path as P

    data = request.get_json()
    path = data.get("path", "")
    src_drive = data.get("src_drive", "")

    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400

    # Delete from external drive — permanent delete (no trash for external drives)
    if src_drive:
        from home_os.services.storage_service import StorageService
        config = current_app.config["_raw_config"]
        storage_svc = StorageService(config["storage"]["root"])
        drive = storage_svc.get_drive_by_name(src_drive)
        if not drive:
            return jsonify({"ok": False, "error": "Drive not found"}), 404

        src_abs = (P(drive.mount_point) / path).resolve()
        try:
            src_abs.relative_to(P(drive.mount_point).resolve())
        except ValueError:
            return jsonify({"ok": False, "error": "Access denied"}), 403

        if not src_abs.exists():
            return jsonify({"ok": False, "error": "File not found"}), 404

        try:
            if src_abs.is_dir():
                shutil.rmtree(str(src_abs))
            else:
                src_abs.unlink()
            return jsonify({"ok": True, "data": {"deleted": path}})
        except (PermissionError, OSError) as e:
            return jsonify({"ok": False, "error": str(e)}), 403

    # Delete from internal storage — move to trash
    svc, err = _get_file_service_from_request()
    if err:
        return err
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
    from flask import redirect, url_for
    return redirect(url_for("files.browse"))


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


@files_bp.route("/api/files/shortcuts", methods=["POST"])
@login_required
def add_shortcut():
    from home_os.models.settings import Setting
    data = request.get_json()
    name = data.get("name", "").strip()
    path = data.get("path", "").strip()
    if not name or not path:
        return jsonify({"ok": False, "error": "Name and path required"}), 400

    key = f"shortcuts_{current_user.username}"
    shortcuts = json.loads(Setting.get(key, "[]"))
    # Prevent duplicates
    if any(s["name"] == name for s in shortcuts):
        return jsonify({"ok": False, "error": "Shortcut already exists"}), 409
    shortcuts.append({"name": name, "path": path})
    Setting.set(key, json.dumps(shortcuts))
    return jsonify({"ok": True})


@files_bp.route("/api/files/shortcuts/delete", methods=["POST"])
@login_required
def delete_shortcut():
    from home_os.models.settings import Setting
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name required"}), 400

    key = f"shortcuts_{current_user.username}"
    shortcuts = json.loads(Setting.get(key, "[]"))
    shortcuts = [s for s in shortcuts if s["name"] != name]
    Setting.set(key, json.dumps(shortcuts))
    return jsonify({"ok": True})


@files_bp.route("/api/files/list-dirs")
@login_required
def list_dirs():
    """List only subdirectories for a given path. Used by the path picker."""
    rel_path = request.args.get("path", "/")
    svc = get_file_service(system=False)
    try:
        resolved = svc._resolve_and_validate(rel_path)
    except (PermissionError, FileNotFoundError):
        return jsonify({"ok": False, "error": "Access denied"}), 403

    if not resolved.exists() or not resolved.is_dir():
        return jsonify({"ok": False, "error": "Not a directory"}), 400

    dirs = []
    try:
        for item in sorted(resolved.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir() and not item.name.startswith('.'):
                dirs.append(item.name)
    except PermissionError:
        pass

    return jsonify({"ok": True, "data": {"path": rel_path, "dirs": dirs}})


@files_bp.route("/api/files/transfer", methods=["POST"])
@login_required
def start_transfer():
    """Start a background file transfer (copy or move). Returns a task ID."""
    import shutil
    from pathlib import Path as P

    data = request.get_json()
    src = data.get("src", "")
    dest = data.get("dest", "")
    mode = data.get("mode", "copy")  # "copy" or "cut"
    src_drive = data.get("src_drive", "")

    if not src or not dest:
        return jsonify({"ok": False, "error": "src and dest required"}), 400

    config = current_app.config["_raw_config"]
    storage_root = config["storage"]["root"]
    trash_path = config["storage"]["trash_path"]
    retention = config["storage"].get("trash_retention_days", 30)

    # Resolve source
    if src_drive:
        from home_os.services.storage_service import StorageService
        storage_svc = StorageService(storage_root)
        drive = storage_svc.get_drive_by_name(src_drive)
        if not drive:
            return jsonify({"ok": False, "error": "Drive not found"}), 404
        src_abs = (P(drive.mount_point) / src).resolve()
        try:
            src_abs.relative_to(P(drive.mount_point).resolve())
        except ValueError:
            return jsonify({"ok": False, "error": "Access denied"}), 403
    else:
        from home_os.services.file_service import FileService
        svc = FileService(storage_root, trash_path, retention)
        src_abs = svc._resolve_and_validate(src)

    if not src_abs.exists():
        return jsonify({"ok": False, "error": "File not found"}), 404

    # Resolve destination
    from home_os.services.file_service import FileService
    dest_svc = FileService(storage_root, trash_path, retention)
    dest_dir = dest_svc._resolve_and_validate(dest)
    if not dest_dir.is_dir():
        return jsonify({"ok": False, "error": "Destination is not a directory"}), 400

    dest_path = dest_dir / src_abs.name
    if dest_path.exists():
        return jsonify({"ok": False, "error": "Already exists at destination"}), 409

    # Create transfer task
    transfer_id = str(uuid.uuid4())[:8]
    filename = src_abs.name
    src_size = src_abs.stat().st_size if src_abs.is_file() else 0

    _write_transfer(transfer_id, {
        "status": "running",
        "progress": 0,
        "filename": filename,
        "action": "move" if mode == "cut" else "copy",
        "size": src_size,
        "error": None,
        "owner": current_user.username,
    })

    def do_transfer():
        try:
            if mode == "cut":
                shutil.move(str(src_abs), str(dest_path))
            else:
                if src_abs.is_dir():
                    shutil.copytree(str(src_abs), str(dest_path))
                else:
                    shutil.copy2(str(src_abs), str(dest_path))
            _update_transfer(transfer_id, {"status": "done", "progress": 100})
            _schedule_transfer_cleanup(transfer_id)
        except Exception as e:
            _update_transfer(transfer_id, {"status": "error", "error": str(e)})
            _schedule_transfer_cleanup(transfer_id)

    t = threading.Thread(target=do_transfer, daemon=True)
    t.start()

    return jsonify({"ok": True, "data": {"id": transfer_id, "filename": filename}})


@files_bp.route("/api/files/transfer/<transfer_id>")
@login_required
def transfer_status(transfer_id):
    """Check status of a background transfer."""
    if not transfer_id.isalnum() or len(transfer_id) > 36:
        return jsonify({"ok": False, "error": "Invalid transfer ID"}), 400
    t = _read_transfer(transfer_id)
    if not t:
        return jsonify({"ok": False, "error": "Transfer not found"}), 404
    if t.get("owner") != current_user.username and not current_user.is_admin:
        return jsonify({"ok": False, "error": "Transfer not found"}), 404
    return jsonify({"ok": True, "data": t})
