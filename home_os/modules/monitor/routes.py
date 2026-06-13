import platform
import time

import psutil
from flask import jsonify, render_template, request
from flask_login import login_required

from home_os.modules.monitor import monitor_bp

_boot_time = psutil.boot_time()


def get_system_metrics():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_seconds = int(time.time() - _boot_time)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    net = psutil.net_io_counters()

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
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


@monitor_bp.route("/dashboard")
@login_required
def dashboard():
    metrics = get_system_metrics()

    isp_download = 0
    isp_upload = 0
    try:
        from home_os.models.settings import Setting
        isp_download = float(Setting.get("isp_download_mbps", "0"))
        isp_upload = float(Setting.get("isp_upload_mbps", "0"))
    except Exception:
        pass

    return render_template("monitor/dashboard.html", metrics=metrics, isp_download=isp_download, isp_upload=isp_upload)


@monitor_bp.route("/api/monitor/metrics")
@login_required
def metrics_api():
    return jsonify({"ok": True, "data": get_system_metrics()})
