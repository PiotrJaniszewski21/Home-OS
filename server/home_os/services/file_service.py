import os
import errno
import shutil
import stat
import time
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from home_os.extensions import db
from home_os.models.trash import TrashEntry


class FileService:
    def __init__(
        self,
        storage_root,
        trash_path,
        trash_retention_days=30,
        owner_id=None,
        manage_all_trash=False,
        quota_bytes=None,
        allow_external_symlinks=True,
    ):
        from home_os.config import ROOT_DIR

        self.storage_root = Path(storage_root)
        if not self.storage_root.is_absolute():
            self.storage_root = ROOT_DIR / self.storage_root
        self.storage_root.mkdir(parents=True, exist_ok=True)

        self.trash_path = Path(trash_path)
        if not self.trash_path.is_absolute():
            self.trash_path = ROOT_DIR / self.trash_path
        self.trash_path.mkdir(parents=True, exist_ok=True)
        self.trash_retention_days = trash_retention_days
        self.owner_id = owner_id
        self.manage_all_trash = manage_all_trash
        self.quota_bytes = quota_bytes
        self.allow_external_symlinks = allow_external_symlinks

    def _path_size(self, path):
        if path.is_file():
            return path.stat().st_size
        total = 0
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                candidate = Path(root) / name
                if candidate.is_symlink():
                    continue
                try:
                    total += candidate.stat().st_size
                except OSError:
                    continue
        return total

    def used_bytes(self):
        return self._path_size(self.storage_root)

    def _ensure_capacity(self, additional_bytes):
        if self.quota_bytes is None:
            return
        if additional_bytes < 0:
            raise ValueError("Invalid file size")
        if self.used_bytes() + additional_bytes > self.quota_bytes:
            raise OSError(errno.EDQUOT, "Storage quota exceeded")

    def _resolve_and_validate(self, relative_path, user=None):
        """Resolve a relative path to an absolute path within storage root."""
        relative_path = relative_path.lstrip("/")
        target = self.storage_root / relative_path

        # System mode (root = /) allows full filesystem access
        if str(self.storage_root) == "/":
            resolved = target.resolve()
            return resolved

        # Allow symlinks at the top level of storage root (e.g. drive mounts)
        # but block symlinks in subdirectories to prevent traversal attacks
        parts = Path(relative_path).parts
        top_level_symlink = None
        check = self.storage_root
        for i, part in enumerate(parts):
            check = check / part
            if check.is_symlink():
                if not self.allow_external_symlinks:
                    raise PermissionError("Access denied: symlinks not allowed")
                if i == 0:
                    top_level_symlink = check.resolve()
                else:
                    raise PermissionError("Access denied: symlinks not allowed")

        resolved = target.resolve()

        if top_level_symlink:
            # Reject symlinks pointing to overly broad targets
            try:
                top_level_symlink.relative_to(self.storage_root.resolve())
            except ValueError:
                # Target is outside storage root — ensure it's not an ancestor of it
                try:
                    self.storage_root.resolve().relative_to(top_level_symlink)
                    raise PermissionError("Access denied: symlink target too broad")
                except ValueError:
                    pass
            try:
                resolved.relative_to(top_level_symlink)
            except ValueError:
                raise PermissionError("Access denied: path outside storage root")
        else:
            try:
                resolved.relative_to(self.storage_root.resolve())
            except ValueError:
                raise PermissionError("Access denied: path outside storage root")

        return resolved

    def _ensure_not_storage_root(self, path):
        if path == self.storage_root.resolve():
            raise PermissionError("The storage root cannot be modified")

    @staticmethod
    def _ensure_not_nested_destination(src, dest):
        if not src.is_dir():
            return
        try:
            dest.resolve(strict=False).relative_to(src.resolve())
        except ValueError:
            return
        raise ValueError("A directory cannot be copied or moved into itself")

    def list_directory(self, relative_path="/", sort_by="name", reverse=False):
        """List contents of a directory."""
        resolved = self._resolve_and_validate(relative_path)

        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {relative_path}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {relative_path}")

        entries = []
        for item in resolved.iterdir():
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "path": str(Path(relative_path) / item.name),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "extension": item.suffix.lstrip(".").lower() if item.is_file() else None,
                })
            except (PermissionError, OSError):
                continue

        key_map = {
            "name": lambda e: (not e["is_dir"], e["name"].lower()),
            "size": lambda e: (not e["is_dir"], e["size"] or 0),
            "modified": lambda e: (not e["is_dir"], e["modified"]),
        }
        entries.sort(key=key_map.get(sort_by, key_map["name"]), reverse=reverse)
        return entries

    def get_file_info(self, relative_path):
        """Get metadata for a single file or directory."""
        resolved = self._resolve_and_validate(relative_path)

        if not resolved.exists():
            raise FileNotFoundError(f"Not found: {relative_path}")
        stat = resolved.stat()
        return {
            "name": resolved.name,
            "path": relative_path,
            "is_dir": resolved.is_dir(),
            "size": stat.st_size if resolved.is_file() else self._dir_size(resolved),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            "extension": resolved.suffix.lstrip(".").lower() if resolved.is_file() else None,
        }

    def _dir_size(self, path):
        """Calculate total size of a directory (non-recursive for speed)."""
        total = 0
        try:
            for item in path.iterdir():
                if item.is_file():
                    total += item.stat().st_size
        except (PermissionError, OSError):
            pass
        return total

    def create_directory(self, relative_path):
        """Create a new directory."""
        resolved = self._resolve_and_validate(relative_path)

        if resolved.exists():
            raise FileExistsError(f"Already exists: {relative_path}")

        resolved.mkdir(parents=True)
        self._inherit_shared_path_mode(resolved)
        return self.get_file_info(relative_path)

    def rename(self, relative_path, new_name):
        """Rename a file or directory."""
        resolved = self._resolve_and_validate(relative_path)

        if not resolved.exists():
            raise FileNotFoundError(f"Not found: {relative_path}")
        self._ensure_not_storage_root(resolved)

        if "/" in new_name or "\\" in new_name:
            raise ValueError("Invalid name")

        new_path = resolved.parent / new_name
        if new_path.exists():
            raise FileExistsError(f"Already exists: {new_name}")

        resolved.rename(new_path)
        parent = str(Path(relative_path).parent)
        return str(Path(parent) / new_name)

    def move(self, src_relative, dest_relative):
        """Move a file or directory to a new location."""
        src = self._resolve_and_validate(src_relative)
        dest_dir = self._resolve_and_validate(dest_relative)

        if not src.exists():
            raise FileNotFoundError(f"Not found: {src_relative}")
        self._ensure_not_storage_root(src)
        if not dest_dir.is_dir():
            raise NotADirectoryError(f"Destination is not a directory: {dest_relative}")

        dest = dest_dir / src.name
        self._ensure_not_nested_destination(src, dest)
        if dest.exists():
            raise FileExistsError(f"Already exists at destination: {src.name}")

        shutil.move(str(src), str(dest))
        self._inherit_shared_path_mode(dest)
        return str(Path(dest_relative) / src.name)

    def copy(self, src_relative, dest_relative):
        """Copy a file or directory."""
        src = self._resolve_and_validate(src_relative)
        dest_dir = self._resolve_and_validate(dest_relative)

        if not src.exists():
            raise FileNotFoundError(f"Not found: {src_relative}")
        self._ensure_not_storage_root(src)
        if not dest_dir.is_dir():
            raise NotADirectoryError(f"Destination is not a directory: {dest_relative}")

        dest = dest_dir / src.name
        self._ensure_not_nested_destination(src, dest)
        if dest.exists():
            raise FileExistsError(f"Already exists at destination: {src.name}")

        self._ensure_capacity(self._path_size(src))

        if src.is_dir():
            shutil.copytree(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))

        self._inherit_shared_path_mode(dest)
        return str(Path(dest_relative) / src.name)

    def delete(self, relative_path):
        """Move a file/directory to trash."""
        resolved = self._resolve_and_validate(relative_path)

        if not resolved.exists():
            raise FileNotFoundError(f"Not found: {relative_path}")
        self._ensure_not_storage_root(resolved)

        trash_name = f"{int(time.time())}_{uuid.uuid4().hex}_{resolved.name}"
        trash_dest = self.trash_path / trash_name

        size = self._path_size(resolved)

        shutil.move(str(resolved), str(trash_dest))

        expires = datetime.now(timezone.utc).timestamp() + (self.trash_retention_days * 86400)
        entry = TrashEntry(
            user_id=self.owner_id,
            original_path=relative_path,
            trash_path=str(trash_dest),
            size_bytes=size,
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
        )
        db.session.add(entry)
        db.session.commit()
        return entry

    def list_trash(self):
        """List items in trash."""
        query = TrashEntry.query.filter_by(restored=False)
        if not self.manage_all_trash:
            query = query.filter_by(user_id=self.owner_id)
        return query.order_by(
            TrashEntry.deleted_at.desc()
        ).all()

    def _get_trash_entry(self, trash_id):
        query = TrashEntry.query.filter_by(id=trash_id)
        if not self.manage_all_trash:
            query = query.filter_by(user_id=self.owner_id)
        entry = query.first()
        if not entry:
            raise FileNotFoundError("Trash entry not found")
        return entry

    def _resolve_trash_path(self, entry):
        trash_path = Path(entry.trash_path).resolve()
        try:
            trash_path.relative_to(self.trash_path.resolve())
        except ValueError as error:
            raise PermissionError("Trash entry is outside the trash directory") from error
        return trash_path

    def restore_from_trash(self, trash_id):
        """Restore a file from trash to its original location."""
        entry = self._get_trash_entry(trash_id)
        if entry.restored:
            raise FileNotFoundError("Trash entry not found")

        trash_path = self._resolve_trash_path(entry)
        if not trash_path.exists():
            raise FileNotFoundError("Trashed file no longer exists on disk")

        original = self._resolve_and_validate(entry.original_path)
        if original.exists():
            raise FileExistsError(f"Original path already occupied: {entry.original_path}")

        self._ensure_capacity(entry.size_bytes)

        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trash_path), str(original))

        entry.restored = True
        db.session.commit()
        return entry

    def permanent_delete(self, trash_id):
        """Permanently delete a file from trash."""
        entry = self._get_trash_entry(trash_id)

        trash_path = self._resolve_trash_path(entry)
        if trash_path.exists():
            if trash_path.is_dir():
                shutil.rmtree(str(trash_path))
            else:
                trash_path.unlink()

        db.session.delete(entry)
        db.session.commit()

    def empty_trash(self):
        """Permanently delete all items in trash."""
        entries = self.list_trash()
        for entry in entries:
            trash_path = self._resolve_trash_path(entry)
            if trash_path.exists():
                if trash_path.is_dir():
                    shutil.rmtree(str(trash_path))
                else:
                    trash_path.unlink()
            db.session.delete(entry)
        db.session.commit()

    def search(self, query, relative_path="/", extensions=None):
        """Search for files by name."""
        resolved = self._resolve_and_validate(relative_path)
        query_lower = query.lower()
        results = []

        for root, dirs, files in os.walk(resolved):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in dirs + files:
                if (Path(root) / name).is_symlink():
                    continue
                if query_lower in name.lower():
                    full = Path(root) / name
                    rel = str(full.relative_to(self.storage_root))

                    if extensions:
                        ext = full.suffix.lstrip(".").lower()
                        if ext not in extensions:
                            continue

                    try:
                        stat = full.stat()
                        results.append({
                            "name": name,
                            "path": "/" + rel,
                            "is_dir": full.is_dir(),
                            "size": stat.st_size if full.is_file() else None,
                            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                            "extension": full.suffix.lstrip(".").lower() if full.is_file() else None,
                        })
                    except (PermissionError, OSError):
                        continue

            if len(results) >= 100:
                break

        return results

    def save_upload(self, relative_dir, file_storage, max_bytes=None):
        """Save an uploaded file."""
        from werkzeug.utils import secure_filename

        dest_dir = self._resolve_and_validate(relative_dir)

        if not dest_dir.is_dir():
            raise NotADirectoryError(f"Not a directory: {relative_dir}")

        filename = file_storage.filename
        if not filename:
            raise ValueError("No filename")

        safe_name = secure_filename(filename)
        if not safe_name:
            raise ValueError("Invalid filename")
        dest = dest_dir / safe_name

        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        available_quota = None
        if self.quota_bytes is not None:
            available_quota = max(0, self.quota_bytes - self.used_bytes())
        limits = [limit for limit in (max_bytes, available_quota) if limit is not None]
        effective_limit = min(limits) if limits else None

        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=dest_dir,
            prefix=".homeos-upload-",
        )
        bytes_written = 0
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                while True:
                    chunk = file_storage.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if effective_limit is not None and bytes_written > effective_limit:
                        raise OSError(errno.EDQUOT, "Upload exceeds storage limit")
                    temporary_file.write(chunk)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, dest)
            self._inherit_shared_path_mode(dest)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        rel_path = str(Path(relative_dir) / dest.name)
        return self.get_file_info(rel_path)

    @staticmethod
    def _inherit_shared_path_mode(path):
        parent_stat = path.parent.stat()
        if not parent_stat.st_mode & stat.S_ISGID:
            return

        group_id = parent_stat.st_gid
        paths = [path]
        if path.is_dir() and not path.is_symlink():
            paths.extend(
                child
                for child in path.rglob("*")
                if not child.is_symlink()
            )
        for child in paths:
            os.chown(child, -1, group_id, follow_symlinks=False)
            child.chmod(0o2770 if child.is_dir() else 0o660)
