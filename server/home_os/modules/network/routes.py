import os
import re
import socket
import subprocess
import tempfile

import httpx
import psutil
from flask import Response, current_app, jsonify, render_template, request
from flask_login import login_required

from home_os.modules.auth.routes import admin_required, fresh_session_required
from home_os.modules.network import network_bp
from home_os.services.system_service import run_privileged


@network_bp.route("/network")
@admin_required
def network_view():
    return render_template("network/network.html")


@network_bp.route("/api/network/status")
@admin_required
def network_status():
    interfaces = []
    primary_interface = _get_default_interface()
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters(pernic=True)

    for name, addr_list in addrs.items():
        if name == "lo" or name.startswith("lo"):
            continue

        iface = {
            "name": name,
            "kind": _interface_kind(name),
            "primary": name == primary_interface,
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

    active_interfaces = sum(
        1
        for interface in interfaces
        if interface["is_up"] and any(
            address["type"] == "IPv4" for address in interface["addresses"]
        )
    )

    # DNS / gateway
    gateway = _get_default_gateway()

    return jsonify({
        "ok": True,
        "data": {
            "interfaces": interfaces,
            "active_interfaces": active_interfaces,
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


@network_bp.route("/api/network/connection-info")
@login_required
def connection_info():
    port = 443
    hostname = socket.gethostname()
    hosts = []

    if _is_safe_hostname(hostname):
        hosts.append(f"{hostname}.local" if "." not in hostname else hostname)

    hosts.extend(_local_ipv4_addresses())

    local_urls = []
    for host in _dedupe(hosts):
        local_urls.append(f"https://{host}")

    return jsonify({
        "ok": True,
        "data": {
            "hostname": hostname,
            "port": port,
            "local_urls": _dedupe(local_urls),
        },
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


def _local_ipv4_addresses():
    addresses = []
    for addr_list in psutil.net_if_addrs().values():
        for addr in addr_list:
            if addr.family == socket.AF_INET and _is_local_ipv4(addr.address):
                addresses.append(addr.address)
    return _dedupe(addresses)


def _is_local_ipv4(address):
    import ipaddress

    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.version == 4
        and ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
    )


def _dedupe(items):
    return list(dict.fromkeys(item for item in items if item))


def _is_safe_hostname(hostname):
    if not hostname:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")
    return all(char in allowed for char in hostname)


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
@fresh_session_required
def install_adguard():
    """Install AdGuard Home through the root administration backend."""
    try:
        if _adguard_binary():
            return jsonify({"ok": False, "error": "AdGuard Home is already installed"}), 409

        result = run_privileged(
            ["bash", "-c", "curl -s -S -L https://raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/scripts/install.sh | sh -s -- -v"],
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

    return jsonify({
        "ok": True,
        "data": {
            "installed": installed,
            "running": running,
            "port": port,
            "web_url": "/network/adguard/",
        },
    })


@network_bp.route(
    "/network/adguard/",
    defaults={"subpath": ""},
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
@network_bp.route(
    "/network/adguard/<path:subpath>",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
@admin_required
def adguard_proxy(subpath):
    """Expose AdGuard Home through the authenticated Home OS domain."""
    port = _adguard_web_port()
    target = f"http://127.0.0.1:{port}/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", errors="ignore")

    excluded_request_headers = {
        "accept-encoding", "connection", "content-length", "cookie", "host",
        "origin", "referer", "transfer-encoding",
    }
    headers = {
        key: value for key, value in request.headers
        if key.lower() not in excluded_request_headers
    }
    adguard_session = request.cookies.get("agh_session")
    if adguard_session:
        headers["Cookie"] = f"agh_session={adguard_session}"

    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            upstream = client.request(
                method=request.method,
                url=target,
                headers=headers,
                content=request.get_data(),
            )
    except (httpx.ConnectError, httpx.TimeoutException):
        return "AdGuard Home is not responding", 502

    prefix = "/network/adguard"
    excluded_response_headers = {
        "connection", "content-encoding", "content-length", "transfer-encoding",
    }
    response_headers = []
    for key, value in upstream.headers.multi_items():
        lower_key = key.lower()
        if lower_key in excluded_response_headers:
            continue
        if lower_key == "location" and value.startswith("/"):
            value = prefix + value
        elif lower_key == "set-cookie":
            value = re.sub(r"(?i)path=/($|;)", rf"Path={prefix}/\1", value)
        response_headers.append((key, value))

    content = upstream.content
    content_type = upstream.headers.get("content-type", "")
    if any(kind in content_type for kind in ("text/html", "javascript", "text/css")):
        byte_prefix = prefix.encode()
        content = content.replace(b"://", b":\x00//")
        content = re.sub(
            rb'((?:href|src|action)\s*=\s*["\'])/(?!/)',
            lambda match: match.group(1) + byte_prefix + b"/",
            content,
        )
        content = re.sub(
            rb'(fetch\(\s*["\'])/(?!/)',
            lambda match: match.group(1) + byte_prefix + b"/",
            content,
        )
        content = re.sub(
            rb'(["\'])/(control|login|install|assets|static)(?=[/\.])',
            lambda match: match.group(1) + byte_prefix + b"/" + match.group(2),
            content,
        )
        content = content.replace(b"url(/", b"url(" + byte_prefix + b"/")
        content = content.replace(b":\x00//", b"://")

    return Response(content, status=upstream.status_code, headers=response_headers)


# --- Cloudflare Tunnel API ---


def _cloudflared_binary():
    """Find cloudflared binary path."""
    try:
        result = subprocess.run(["which", "cloudflared"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    for p in ["/usr/local/bin/cloudflared", "/usr/bin/cloudflared"]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _systemd_service_loaded(service_name):
    """Return whether systemd has a unit loaded for the tunnel service."""
    try:
        result = subprocess.run(
            ["systemctl", "show", service_name, "--property=LoadState", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "loaded"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _request_public_domain():
    """Infer a public domain when the admin is using Home OS remotely."""
    host_header = request.host.lower()
    if host_header.startswith("["):
        host = host_header.split("]", 1)[0][1:]
    else:
        host = host_header.split(":", 1)[0]
    if not host or host == "localhost" or "." not in host:
        return ""

    try:
        socket.inet_pton(socket.AF_INET, host)
        return ""
    except OSError:
        pass

    try:
        socket.inet_pton(socket.AF_INET6, host)
        return ""
    except OSError:
        return host


def _tunnel_uptime(service_name="cloudflared"):
    """Get cloudflared service uptime as human-readable string."""
    try:
        result = subprocess.run(
            ["systemctl", "show", service_name, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        line = result.stdout.strip()
        if "=" in line:
            ts_str = line.split("=", 1)[1].strip()
            if ts_str:
                from datetime import datetime, timezone
                import time as _time

                dt = datetime.strptime(ts_str, "%a %Y-%m-%d %H:%M:%S %Z")
                dt = dt.replace(tzinfo=timezone.utc)
                elapsed = _time.time() - dt.timestamp()
                if elapsed < 60:
                    return f"{int(elapsed)}s"
                elif elapsed < 3600:
                    return f"{int(elapsed // 60)}m"
                elif elapsed < 86400:
                    h = int(elapsed // 3600)
                    m = int((elapsed % 3600) // 60)
                    return f"{h}h {m}m"
                else:
                    d = int(elapsed // 86400)
                    h = int((elapsed % 86400) // 3600)
                    return f"{d}d {h}h"
    except Exception:
        pass
    return None


@network_bp.route("/api/network/tunnel/status")
@admin_required
def tunnel_status():
    from home_os.models.settings import Setting

    installed = _cloudflared_binary() is not None
    permanent_configured = _systemd_service_loaded("cloudflared")
    quick_configured = _systemd_service_loaded("cloudflared-quick")
    running = False
    uptime = None
    mode = Setting.get("cloudflare_tunnel_mode", "token")

    if quick_configured and not permanent_configured:
        mode = "quick"
    elif permanent_configured:
        mode = "token"

    if installed:
        service_name = "cloudflared-quick" if mode == "quick" else "cloudflared"
        try:
            check = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=5,
            )
            running = check.stdout.strip() == "active"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        if running:
            uptime = _tunnel_uptime(service_name)

    configured = (
        Setting.get("cloudflare_configured", "false") == "true"
        or permanent_configured
        or quick_configured
    )
    domain = Setting.get("cloudflare_domain", "")
    url = Setting.get("cloudflare_tunnel_url", "")

    if configured and mode == "token" and not domain:
        domain = _request_public_domain()
        if domain:
            url = f"https://{domain}"
            Setting.set("cloudflare_domain", domain)
            Setting.set("cloudflare_tunnel_url", url)
            Setting.set("cloudflare_configured", "true")
            Setting.set("cloudflare_tunnel_mode", mode)

    return jsonify({
        "ok": True,
        "data": {
            "installed": installed,
            "running": running,
            "uptime": uptime,
            "domain": domain,
            "url": url,
            "configured": configured,
            "mode": mode,
        }
    })


@network_bp.route("/api/network/tunnel/install", methods=["POST"])
@fresh_session_required
def tunnel_install():
    if _cloudflared_binary():
        return jsonify({"ok": True, "data": {"message": "cloudflared already installed"}})

    import platform
    arch = platform.machine()
    if arch in ("x86_64", "amd64"):
        deb_arch = "amd64"
    elif arch in ("aarch64", "arm64"):
        deb_arch = "arm64"
    else:
        return jsonify({"ok": False, "error": f"Unsupported architecture: {arch}"}), 400

    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{deb_arch}.deb"
    deb_descriptor, deb_path = tempfile.mkstemp(prefix="homeos-cloudflared-", suffix=".deb")
    os.close(deb_descriptor)

    try:
        dl = subprocess.run(
            ["curl", "-fsSL", "-o", deb_path, url],
            capture_output=True, text=True, timeout=60,
        )
        if dl.returncode != 0:
            return jsonify({"ok": False, "error": "Failed to download cloudflared"}), 500

        install = run_privileged(
            ["dpkg", "-i", deb_path],
            capture_output=True, text=True, timeout=30,
        )
        if install.returncode != 0:
            return jsonify({"ok": False, "error": install.stderr[:200] or "Install failed"}), 500

        if not _cloudflared_binary():
            return jsonify({"ok": False, "error": "Install succeeded but binary not found"}), 500

        return jsonify({"ok": True, "data": {"message": "cloudflared installed successfully"}})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Install timed out"}), 408
    finally:
        if os.path.exists(deb_path):
            os.unlink(deb_path)


@network_bp.route("/api/network/tunnel/connect", methods=["POST"])
@fresh_session_required
def tunnel_connect():
    import re
    data = request.get_json() or {}
    token = data.get("token", "").strip()

    if not token:
        return jsonify({"ok": False, "error": "Token is required"}), 400
    if not re.match(r"^[A-Za-z0-9_\-=+/.]+$", token):
        return jsonify({"ok": False, "error": "Invalid token format"}), 400

    try:
        result = run_privileged(
            ["cloudflared", "service", "install", token],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode != 0:
            stderr = result.stderr.lower()
            if "already exists" in stderr or "already installed" in stderr:
                run_privileged(
                    ["systemctl", "stop", "cloudflared"],
                    capture_output=True, text=True, timeout=10,
                )
                run_privileged(
                    ["cloudflared", "service", "uninstall"],
                    capture_output=True, text=True, timeout=10,
                )
                retry = run_privileged(
                    ["cloudflared", "service", "install", token],
                    capture_output=True, text=True, timeout=30,
                )
                if retry.returncode != 0:
                    return jsonify({"ok": False, "error": retry.stderr[:200] or "Reinstall failed"}), 500
            else:
                return jsonify({"ok": False, "error": result.stderr[:200] or "Service install failed"}), 500

        run_privileged(
            ["systemctl", "enable", "cloudflared"],
            capture_output=True, text=True, timeout=10,
        )
        run_privileged(
            ["systemctl", "start", "cloudflared"],
            capture_output=True, text=True, timeout=10,
        )

        import time
        for _ in range(5):
            time.sleep(1)
            check = subprocess.run(
                ["systemctl", "is-active", "cloudflared"],
                capture_output=True, text=True, timeout=5,
            )
            if check.stdout.strip() == "active":
                return jsonify({"ok": True, "data": {"message": "Tunnel connected"}})

        return jsonify({"ok": True, "data": {"message": "Service installed but may still be starting"}})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Connection timed out"}), 408


@network_bp.route("/api/network/tunnel/control", methods=["POST"])
@fresh_session_required
def tunnel_control():
    from home_os.models.settings import Setting

    data = request.get_json() or {}
    action = data.get("action", "")

    if action not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "error": "Invalid action"}), 400

    mode = Setting.get("cloudflare_tunnel_mode", "token")
    service_name = "cloudflared-quick" if mode == "quick" else "cloudflared"

    try:
        run_privileged(
            ["systemctl", action, service_name],
            capture_output=True, text=True, timeout=15,
        )

        import time
        time.sleep(1)
        check = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True, timeout=5,
        )
        running = check.stdout.strip() == "active"

        return jsonify({"ok": True, "data": {"message": f"Service {action}ed", "running": running}})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Operation timed out"}), 408


@network_bp.route("/api/network/tunnel/configure", methods=["POST"])
@fresh_session_required
def tunnel_configure():
    from home_os.models.settings import Setting

    data = request.get_json() or {}
    domain = data.get("domain", "").strip()
    url = data.get("url", "").strip()
    mode = data.get("mode", "token")

    Setting.set("cloudflare_domain", domain)
    Setting.set("cloudflare_tunnel_url", url)
    Setting.set("cloudflare_configured", "true" if domain else "false")
    if mode:
        Setting.set("cloudflare_tunnel_mode", mode)

    return jsonify({"ok": True})


@network_bp.route("/api/network/tunnel/reset", methods=["POST"])
@fresh_session_required
def tunnel_reset():
    from home_os.models.settings import Setting

    tunnel_mode = Setting.get("cloudflare_tunnel_mode", "token")

    try:
        if tunnel_mode == "quick":
            run_privileged(
                ["systemctl", "stop", "cloudflared-quick"],
                capture_output=True, text=True, timeout=10,
            )
            run_privileged(
                ["systemctl", "disable", "cloudflared-quick"],
                capture_output=True, text=True, timeout=10,
            )
            service_path = "/etc/systemd/system/cloudflared-quick.service"
            if os.path.exists(service_path):
                os.unlink(service_path)
                run_privileged(
                    ["systemctl", "daemon-reload"],
                    capture_output=True, text=True, timeout=10,
                )
        else:
            run_privileged(
                ["systemctl", "stop", "cloudflared"],
                capture_output=True, text=True, timeout=10,
            )
            run_privileged(
                ["systemctl", "disable", "cloudflared"],
                capture_output=True, text=True, timeout=10,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    Setting.set("cloudflare_tunnel_url", "")
    Setting.set("cloudflare_domain", "")
    Setting.set("cloudflare_configured", "false")
    Setting.set("cloudflare_tunnel_mode", "")

    return jsonify({"ok": True})


_QUICK_TUNNEL_SERVICE = """[Unit]
Description=Cloudflare Quick Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={binary} tunnel --url https://localhost --no-tls-verify
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


@network_bp.route("/api/network/tunnel/quick", methods=["POST"])
@fresh_session_required
def tunnel_quick_start():
    from home_os.models.settings import Setting

    binary = _cloudflared_binary()
    if not binary:
        return jsonify({"ok": False, "error": "cloudflared not installed"}), 400

    service_path = "/etc/systemd/system/cloudflared-quick.service"
    try:
        unit_content = _QUICK_TUNNEL_SERVICE.format(binary=binary)
        tmp_descriptor, tmp_path = tempfile.mkstemp(prefix="homeos-cloudflared-", suffix=".service")
        with os.fdopen(tmp_descriptor, "w") as f:
            f.write(unit_content)
        run_privileged(
            ["cp", tmp_path, service_path],
            capture_output=True, text=True, timeout=5,
        )
        os.unlink(tmp_path)

        run_privileged(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
        run_privileged(
            ["systemctl", "enable", "cloudflared-quick"],
            capture_output=True, text=True, timeout=10,
        )
        run_privileged(
            ["systemctl", "start", "cloudflared-quick"],
            capture_output=True, text=True, timeout=10,
        )

        import re as _re
        import time
        tunnel_url = None
        for attempt in range(15):
            time.sleep(2)
            logs = run_privileged(
                ["journalctl", "-u", "cloudflared-quick", "--no-pager", "-n", "100"],
                capture_output=True, text=True, timeout=5,
            )
            for line in logs.stdout.split("\n"):
                if "trycloudflare.com" in line:
                    match = _re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if match:
                        tunnel_url = match.group(0)
                        break
            if tunnel_url:
                break

        if tunnel_url:
            domain = tunnel_url.replace("https://", "")
            Setting.set("cloudflare_tunnel_url", tunnel_url)
            Setting.set("cloudflare_domain", domain)
            Setting.set("cloudflare_configured", "true")
            Setting.set("cloudflare_tunnel_mode", "quick")
            return jsonify({"ok": True, "data": {"url": tunnel_url, "domain": domain}})
        else:
            return jsonify({"ok": False, "error": "Tunnel started but URL not yet available. Check back in a moment."}), 500

    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Operation timed out"}), 408
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@network_bp.route("/api/network/tunnel/quick/status")
@admin_required
def tunnel_quick_status():
    from home_os.models.settings import Setting

    running = False
    try:
        check = subprocess.run(
            ["systemctl", "is-active", "cloudflared-quick"],
            capture_output=True, text=True, timeout=5,
        )
        running = check.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    url = Setting.get("cloudflare_tunnel_url", "")
    return jsonify({"ok": True, "data": {"running": running, "url": url}})


def _get_default_interface():
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        parts = result.stdout.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    except (subprocess.TimeoutExpired, FileNotFoundError, IndexError):
        pass
    return None


def _interface_kind(name):
    if name.startswith(("en", "eth")):
        return "Ethernet"
    if name.startswith(("wl", "wlan")):
        return "Wi-Fi"
    if name.startswith("tailscale"):
        return "Tailscale"
    if name.startswith(("br-", "docker", "veth")):
        return "Virtual"
    return "Network"


def _get_default_gateway():
    """Get default gateway IP."""
    try:
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
