"""Tools available to the AI assistant for querying the system."""

import os
from datetime import datetime, timezone
from pathlib import Path

import psutil


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by name within storage",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename search term"},
                    "extensions": {"type": "string", "description": "Comma-separated extensions to filter (e.g. 'pdf,txt')"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List contents of a directory in storage",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (e.g. '/' or '/documents')"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_storage_info",
            "description": "Get storage usage information (total, used, free space)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Get current system metrics (CPU, memory, disk, network, uptime)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_large_files",
            "description": "Find the largest files in storage",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to search (default: /)"},
                    "count": {"type": "integer", "description": "Number of results (default: 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_files",
            "description": "Get recently modified files",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of results (default: 10)"},
                },
            },
        },
    },
]


class AIToolExecutor:
    def __init__(self, storage_root):
        from home_os.config import ROOT_DIR

        self.storage_root = Path(storage_root)
        if not self.storage_root.is_absolute():
            self.storage_root = ROOT_DIR / self.storage_root

    ALLOWED_TOOLS = [
        "search_files", "list_directory", "get_storage_info",
        "get_system_status", "get_large_files", "get_recent_files",
    ]

    def execute(self, tool_name, arguments):
        if tool_name not in self.ALLOWED_TOOLS:
            return {"error": "Unknown tool"}
        method = getattr(self, f"tool_{tool_name}", None)
        if not method:
            return {"error": "Unknown tool"}
        try:
            return method(**arguments)
        except Exception:
            return {"error": "Tool execution failed"}

    def tool_search_files(self, query, extensions=None):
        query_lower = query.lower()
        ext_list = None
        if extensions:
            ext_list = [e.strip().lower() for e in extensions.split(",")]

        results = []
        for root, dirs, files in os.walk(self.storage_root):
            for name in files + dirs:
                if query_lower in name.lower():
                    full = Path(root) / name
                    if ext_list and full.is_file():
                        ext = full.suffix.lstrip(".").lower()
                        if ext not in ext_list:
                            continue
                    rel = str(full.relative_to(self.storage_root))
                    stat = full.stat()
                    results.append({
                        "name": name,
                        "path": "/" + rel,
                        "size_bytes": stat.st_size if full.is_file() else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "is_directory": full.is_dir(),
                    })
                if len(results) >= 50:
                    break
            if len(results) >= 50:
                break

        return {"results": results, "count": len(results)}

    def tool_list_directory(self, path="/"):
        resolved = (self.storage_root / path.lstrip("/")).resolve()
        try:
            resolved.relative_to(self.storage_root.resolve())
        except ValueError:
            return {"error": "Path outside storage"}

        if not resolved.is_dir():
            return {"error": f"Not a directory: {path}"}

        entries = []
        for item in sorted(resolved.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "is_directory": item.is_dir(),
                    "size_bytes": stat.st_size if item.is_file() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            except (PermissionError, OSError):
                continue

        return {"path": path, "entries": entries, "count": len(entries)}

    def tool_get_storage_info(self):
        usage = psutil.disk_usage(str(self.storage_root))
        return {
            "total_gb": round(usage.total / (1024**3), 2),
            "used_gb": round(usage.used / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "percent_used": round(usage.percent, 1),
        }

    def tool_get_system_status(self):
        import time
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        uptime = int(time.time() - psutil.boot_time())

        return {
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "cpu_cores": psutil.cpu_count(),
            "memory_used_gb": round(mem.used / (1024**3), 1),
            "memory_total_gb": round(mem.total / (1024**3), 1),
            "memory_percent": mem.percent,
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "network_sent_gb": round(net.bytes_sent / (1024**3), 2),
            "network_recv_gb": round(net.bytes_recv / (1024**3), 2),
            "uptime_hours": round(uptime / 3600, 1),
        }

    def tool_get_large_files(self, path="/", count=10):
        resolved = (self.storage_root / path.lstrip("/")).resolve()
        try:
            resolved.relative_to(self.storage_root.resolve())
        except ValueError:
            return {"error": "Path outside storage"}

        files = []
        for root, dirs, filenames in os.walk(resolved):
            for name in filenames:
                full = Path(root) / name
                try:
                    size = full.stat().st_size
                    rel = str(full.relative_to(self.storage_root))
                    files.append({"path": "/" + rel, "size_bytes": size})
                except (PermissionError, OSError):
                    continue

        files.sort(key=lambda f: f["size_bytes"], reverse=True)
        return {"files": files[:count]}

    def tool_get_recent_files(self, count=10):
        files = []
        for root, dirs, filenames in os.walk(self.storage_root):
            for name in filenames:
                full = Path(root) / name
                try:
                    stat = full.stat()
                    rel = str(full.relative_to(self.storage_root))
                    files.append({
                        "path": "/" + rel,
                        "size_bytes": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    })
                except (PermissionError, OSError):
                    continue

        files.sort(key=lambda f: f["modified"], reverse=True)
        return {"files": files[:count]}
