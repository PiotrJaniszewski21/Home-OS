import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from home_os.extensions import db
from home_os.models.trash import TrashEntry


class FileService:
    def __init__(self, storage_root, trash_path, trash_retention_days=30):
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
        return self.get_file_info(relative_path)

    def rename(self, relative_path, new_name):
        """Rename a file or directory."""
        resolved = self._resolve_and_validate(relative_path)

        if not resolved.exists():
            raise FileNotFoundError(f"Not found: {relative_path}")

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
        if not dest_dir.is_dir():
            raise NotADirectoryError(f"Destination is not a directory: {dest_relative}")

        dest = dest_dir / src.name
        if dest.exists():
            raise FileExistsError(f"Already exists at destination: {src.name}")

        shutil.move(str(src), str(dest))
        return str(Path(dest_relative) / src.name)

    def copy(self, src_relative, dest_relative):
        """Copy a file or directory."""
        src = self._resolve_and_validate(src_relative)
        dest_dir = self._resolve_and_validate(dest_relative)

        if not src.exists():
            raise FileNotFoundError(f"Not found: {src_relative}")
        if not dest_dir.is_dir():
            raise NotADirectoryError(f"Destination is not a directory: {dest_relative}")

        dest = dest_dir / src.name
        if dest.exists():
            raise FileExistsError(f"Already exists at destination: {src.name}")

        if src.is_dir():
            shutil.copytree(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))

        return str(Path(dest_relative) / src.name)

    def delete(self, relative_path):
        """Move a file/directory to trash."""
        resolved = self._resolve_and_validate(relative_path)

        if not resolved.exists():
            raise FileNotFoundError(f"Not found: {relative_path}")

        trash_name = f"{int(time.time())}_{resolved.name}"
        trash_dest = self.trash_path / trash_name

        size = resolved.stat().st_size if resolved.is_file() else self._dir_size(resolved)

        shutil.move(str(resolved), str(trash_dest))

        expires = datetime.now(timezone.utc).timestamp() + (self.trash_retention_days * 86400)
        entry = TrashEntry(
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
        return TrashEntry.query.filter_by(restored=False).order_by(
            TrashEntry.deleted_at.desc()
        ).all()

    def restore_from_trash(self, trash_id):
        """Restore a file from trash to its original location."""
        entry = TrashEntry.query.get(trash_id)
        if not entry or entry.restored:
            raise FileNotFoundError("Trash entry not found")

        trash_path = Path(entry.trash_path)
        if not trash_path.exists():
            raise FileNotFoundError("Trashed file no longer exists on disk")

        original = self._resolve_and_validate(entry.original_path)
        if original.exists():
            raise FileExistsError(f"Original path already occupied: {entry.original_path}")

        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trash_path), str(original))

        entry.restored = True
        db.session.commit()
        return entry

    def permanent_delete(self, trash_id):
        """Permanently delete a file from trash."""
        entry = TrashEntry.query.get(trash_id)
        if not entry:
            raise FileNotFoundError("Trash entry not found")

        trash_path = Path(entry.trash_path)
        if trash_path.exists():
            if trash_path.is_dir():
                shutil.rmtree(str(trash_path))
            else:
                trash_path.unlink()

        db.session.delete(entry)
        db.session.commit()

    def empty_trash(self):
        """Permanently delete all items in trash."""
        entries = TrashEntry.query.filter_by(restored=False).all()
        for entry in entries:
            trash_path = Path(entry.trash_path)
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
            for name in dirs + files:
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

    def save_upload(self, relative_dir, file_storage):
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

        file_storage.save(str(dest))
        rel_path = str(Path(relative_dir) / dest.name)
        return self.get_file_info(rel_path)
