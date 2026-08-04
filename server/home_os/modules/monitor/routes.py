import platform
import re
import time
from datetime import datetime, timedelta, timezone
from math import ceil

import psutil
from flask import jsonify, render_template
from flask_login import login_required

from home_os.models import MusicPlaybackMetric
from home_os.modules.monitor import monitor_bp

_boot_time = psutil.boot_time()


def _root_disk_name(partitions=None, counters=None):
    partitions = partitions or psutil.disk_partitions(all=True)
    counters = counters or psutil.disk_io_counters(perdisk=True) or {}
    root_partition = next((item for item in partitions if item.mountpoint == "/"), None)
    if root_partition is None:
        return None

    device = root_partition.device.rsplit("/", 1)[-1]
    parent = device
    for pattern in (
        r"^(nvme\d+n\d+)p\d+$",
        r"^(mmcblk\d+)p\d+$",
        r"^((?:sd|vd|xvd)[a-z]+)\d+$",
    ):
        match = re.match(pattern, device)
        if match:
            parent = match.group(1)
            break

    if parent in counters:
        return parent
    if device in counters:
        return device
    return None


def _disk_activity_percent(before, after, elapsed_seconds):
    if before is None or after is None or elapsed_seconds <= 0:
        return None
    before_busy = getattr(before, "busy_time", None)
    after_busy = getattr(after, "busy_time", None)
    if before_busy is None or after_busy is None:
        return None
    busy_seconds = max(0, after_busy - before_busy) / 1000
    return round(min(100, busy_seconds / elapsed_seconds * 100), 1)


def _sample_cpu_and_disk(interval=0.5):
    before_counters = psutil.disk_io_counters(perdisk=True) or {}
    disk_name = _root_disk_name(counters=before_counters)
    before = before_counters.get(disk_name) if disk_name else None
    started = time.monotonic()
    cpu_percent = psutil.cpu_percent(interval=interval)
    elapsed = time.monotonic() - started
    after_counters = psutil.disk_io_counters(perdisk=True) or {}
    activity_percent = _disk_activity_percent(
        before,
        after_counters.get(disk_name) if disk_name else None,
        elapsed,
    )
    return cpu_percent, disk_name, activity_percent


def get_system_metrics():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_seconds = int(time.time() - _boot_time)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    net = psutil.net_io_counters()
    cpu_percent, disk_name, disk_activity_percent = _sample_cpu_and_disk()

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": psutil.cpu_count(),
        "memory": {
            "total_gb": round(mem.total / (1024**3), 1),
            "used_gb": round(mem.used / (1024**3), 1),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "percent": round(disk.used / disk.total * 100, 1),
            "device": disk_name,
            "activity_percent": disk_activity_percent,
        },
        "network": {
            "sent_gb": round(net.bytes_sent / (1024**3), 2),
            "recv_gb": round(net.bytes_recv / (1024**3), 2),
        },
        "uptime": f"{days}d {hours}h",
        "uptime_seconds": uptime_seconds,
        "platform": platform.system(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
    }


def _playback_percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _summarize_playback_records(records):
    successful = [record for record in records if record.success]
    source_ready = [
        record.source_ready_ms
        for record in records
        if record.source_ready_ms is not None
    ]
    audible = [
        record.audible_ms
        for record in successful
        if record.audible_ms is not None
    ]
    samples = len(records)
    failures = samples - len(successful)
    fallbacks = sum(bool(record.fallback_used) for record in records)
    return {
        "samples": samples,
        "successes": len(successful),
        "avg_source_ready_ms": (
            round(sum(source_ready) / len(source_ready))
            if source_ready
            else None
        ),
        "avg_audible_ms": (
            round(sum(audible) / len(audible))
            if audible
            else None
        ),
        "p95_audible_ms": _playback_percentile(audible, 0.95),
        "failure_rate": round(failures / samples * 100, 1) if samples else 0,
        "fallback_rate": round(fallbacks / samples * 100, 1) if samples else 0,
    }


def _group_playback_records(records, attribute):
    grouped = {}
    for record in records:
        key = getattr(record, attribute)
        grouped.setdefault(key, []).append(record)
    return [
        {"name": name, **_summarize_playback_records(group)}
        for name, group in sorted(
            grouped.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]


def get_music_playback_metrics(window_hours=7 * 24):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    records = (
        MusicPlaybackMetric.query
        .filter(MusicPlaybackMetric.recorded_at >= cutoff)
        .order_by(MusicPlaybackMetric.recorded_at.desc())
        .limit(5000)
        .all()
    )
    return {
        "window_hours": window_hours,
        "overall": _summarize_playback_records(records),
        "scenarios": _group_playback_records(records, "scenario"),
        "sources": _group_playback_records(records, "source_kind"),
    }


@monitor_bp.route("/dashboard")
@login_required
def dashboard():
    metrics = get_system_metrics()
    music_playback = get_music_playback_metrics()

    isp_download = 0
    isp_upload = 0
    try:
        from home_os.models.settings import Setting
        isp_download = float(Setting.get("isp_download_mbps", "0"))
        isp_upload = float(Setting.get("isp_upload_mbps", "0"))
    except Exception:
        pass

    return render_template(
        "monitor/dashboard.html",
        metrics=metrics,
        music_playback=music_playback,
        isp_download=isp_download,
        isp_upload=isp_upload,
    )


@monitor_bp.route("/api/monitor/metrics")
@login_required
def metrics_api():
    return jsonify({"ok": True, "data": get_system_metrics()})


@monitor_bp.route("/api/monitor/music-playback")
@login_required
def music_playback_metrics_api():
    return jsonify({"ok": True, "data": get_music_playback_metrics()})
