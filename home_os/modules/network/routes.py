import os
import socket
import subprocess

import psutil
from flask import current_app, jsonify, render_template, request
from flask_login import login_required

from home_os.modules.auth.routes import admin_required
from home_os.modules.network import network_bp


@network_bp.route("/network")
@admin_required
def network_view():
    return render_template("network/network.html")


@network_bp.route("/api/network/status")
@admin_required
def network_status():
    interfaces = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters(pernic=True)

    for name, addr_list in addrs.items():
        if name == "lo" or name.startswith("lo"):
            continue

        iface = {
            "name": name,
            "is_up": stats.get(name, None) and stats[name].isup,
            "speed": stats[name].speed if name in stats else 0,
            "mtu": stats[name].mtu if name in stats else 0,
            "addresses": [],
            "bytes_sent": 0,
            "bytes_recv": 0,
        }

        for addr in addr_list:
            if addr.family == socket.AF_INET:
                iface["addresses"].append({"type": "IPv4", "address": addr.address, "netmask": addr.netmask})
            elif addr.family == socket.AF_INET6:
                iface["addresses"].append({"type": "IPv6", "address": addr.address})
            elif addr.family == psutil.AF_LINK:
                iface["mac"] = addr.address

        if name in io:
            iface["bytes_sent"] = io[name].bytes_sent
            iface["bytes_recv"] = io[name].bytes_recv

        interfaces.append(iface)

    # Total bandwidth
    total_io = psutil.net_io_counters()

    # Connected devices (ARP table)
    devices = _get_arp_table()

    # DNS / gateway
    gateway = _get_default_gateway()

    return jsonify({
        "ok": True,
        "data": {
            "interfaces": interfaces,
            "total": {
                "bytes_sent": total_io.bytes_sent,
                "bytes_recv": total_io.bytes_recv,
                "packets_sent": total_io.packets_sent,
                "packets_recv": total_io.packets_recv,
            },
            "devices": devices,
            "gateway": gateway,
            "hostname": socket.gethostname(),
        }
    })


@network_bp.route("/api/network/speed")
@admin_required
def network_speed():
    """Get current transfer rates (call twice with interval to calculate speed)."""
    io = psutil.net_io_counters()
    return jsonify({
        "ok": True,
        "data": {
            "bytes_sent": io.bytes_sent,
            "bytes_recv": io.bytes_recv,
            "timestamp": __import__("time").time(),
        }
    })


def _get_arp_table():
    """Get devices on the local network via ARP."""
    devices = []
    try:
        result = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if "incomplete" in line:
                continue
            parts = line.split()
            if len(parts) >= 4:
                hostname = parts[0].strip("()")
                ip = ""
                mac = ""
                for p in parts:
                    if p.startswith("(") and p.endswith(")"):
                        ip = p.strip("()")
                    elif ":" in p and len(p) == 17:
                        mac = p
                    elif "." in p and p[0].isdigit():
                        ip = p
                if ip:
                    devices.append({
                        "hostname": hostname if hostname != ip else "",
                        "ip": ip,
                        "mac": mac,
                    })
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return devices


@network_bp.route("/api/network/settings")
@admin_required
def network_settings():
    from home_os.models.settings import Setting
    return jsonify({
        "ok": True,
        "data": {
            "setup_complete": Setting.get("network_setup_complete", "false") == "true",
            "isp_download": float(Setting.get("isp_download_mbps", "0")),
            "isp_upload": float(Setting.get("isp_upload_mbps", "0")),
            "isp_name": Setting.get("isp_name", ""),
        }
    })


@network_bp.route("/api/network/settings", methods=["POST"])
@admin_required
def save_network_settings():
    from home_os.models.settings import Setting
    data = request.get_json()

    if "isp_download" in data:
        Setting.set("isp_download_mbps", str(data["isp_download"]))
    if "isp_upload" in data:
        Setting.set("isp_upload_mbps", str(data["isp_upload"]))
    if "isp_name" in data:
        Setting.set("isp_name", data["isp_name"])

    Setting.set("network_setup_complete", "true")
    return jsonify({"ok": True})


def _adguard_binary():
    """Find AdGuard Home binary path."""
    paths = ["/opt/AdGuardHome/AdGuardHome", "/usr/local/bin/AdGuardHome", "/usr/bin/AdGuardHome"]
    for p in paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    result = subprocess.run(["which", "AdGuardHome"], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _adguard_web_port():
    """Read AdGuard Home web UI port from its config file."""
    import yaml as _yaml
    config_paths = ["/opt/AdGuardHome/AdGuardHome.yaml", "/etc/AdGuardHome/AdGuardHome.yaml"]
    for path in config_paths:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    cfg = _yaml.safe_load(f)
                address = cfg.get("http", {}).get("address", "")
                if ":" in address:
                    return int(address.rsplit(":", 1)[1])
            except (OSError, ValueError, TypeError):
                pass
    return 3000


@network_bp.route("/api/network/adguard/install", methods=["POST"])
@admin_required
def install_adguard():
    """One-click AdGuard Home installer (runs with sudo)."""
    try:
        if _adguard_binary():
            return jsonify({"ok": False, "error": "AdGuard Home is already installed"}), 409

        result = subprocess.run(
            ["sudo", "bash", "-c", "curl -s -S -L https://raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/scripts/install.sh | sh -s -- -v"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            return jsonify({"ok": True, "data": {"output": result.stdout[-500:]}})
        else:
            return jsonify({"ok": False, "error": result.stderr[-300:] or "Install failed"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Install timed out"}), 408
    except Exception:
        return jsonify({"ok": False, "error": "Install failed"}), 500


@network_bp.route("/api/network/adguard/status")
@admin_required
def adguard_installed():
    """Check if AdGuard Home is installed and running."""
    installed = _adguard_binary() is not None
    running = False
    port = 3000

    if installed:
        port = _adguard_web_port()
        try:
            check = subprocess.run(["systemctl", "is-active", "AdGuardHome"], capture_output=True, text=True, timeout=5)
            running = check.stdout.strip() == "active"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return jsonify({"ok": True, "data": {"installed": installed, "running": running, "port": port}})


def _get_default_gateway():
    """Get default gateway IP."""
    try:
        gateways = psutil.net_if_addrs()
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout:
            parts = result.stdout.split()
            if "via" in parts:
                return parts[parts.index("via") + 1]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.split("\n"):
            if "gateway" in line:
                return line.split(":")[1].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None
