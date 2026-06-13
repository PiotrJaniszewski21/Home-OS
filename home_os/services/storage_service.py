import subprocess
from pathlib import Path

import psutil


class DetectedDrive:
    def __init__(self, name, device, mount_point, filesystem, total, used, free):
        self.name = name
        self.device = device
        self.mount_point = mount_point
        self.filesystem = filesystem
        self.total_bytes = total
        self.used_bytes = used
        self.free_bytes = free

    @property
    def percent_used(self):
        if self.total_bytes == 0:
            return 0
        return round(self.used_bytes / self.total_bytes * 100, 1)

    def to_dict(self):
        return {
            "name": self.name,
            "device": self.device,
            "mount_point": self.mount_point,
            "filesystem": self.filesystem,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "percent_used": self.percent_used,
        }


class StorageService:
    EXTERNAL_PREFIXES = ["/media/", "/mnt/", "/run/media/"]

    def __init__(self, storage_root):
        from home_os.config import ROOT_DIR

        self.storage_root = Path(storage_root)
        if not self.storage_root.is_absolute():
            self.storage_root = ROOT_DIR / self.storage_root
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def get_main_storage_usage(self):
        usage = psutil.disk_usage(str(self.storage_root))
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent_used": round(usage.percent, 1),
        }

    def detect_external_drives(self):
        """Find mounted external/USB drives."""
        drives = []
        seen_devices = set()

        for partition in psutil.disk_partitions(all=False):
            mount = partition.mountpoint
            device = partition.device

            if device in seen_devices:
                continue

            is_external = any(mount.startswith(prefix) for prefix in self.EXTERNAL_PREFIXES)
            if not is_external:
                continue

            seen_devices.add(device)

            try:
                usage = psutil.disk_usage(mount)
            except (PermissionError, OSError):
                continue

            name = Path(mount).name or device
            drives.append(DetectedDrive(
                name=name,
                device=device,
                mount_point=mount,
                filesystem=partition.fstype,
                total=usage.total,
                used=usage.used,
                free=usage.free,
            ))

        return drives

    def get_drive_by_name(self, name):
        """Find a specific external drive by its name."""
        for drive in self.detect_external_drives():
            if drive.name == name:
                return drive
        return None
