import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from flask import Response, abort, jsonify, render_template, request, stream_with_context
from flask_login import current_user, login_required
from werkzeug.http import dump_cookie

from home_os.modules.auth.routes import admin_required, fresh_session_required
from home_os.modules.media import media_bp
from home_os.services.system_service import run_privileged


# --- Service detection helpers ---

SERVICES = {
    "plex": {
        "name": "Plex Media Server",
        "service": "plexmediaserver",
        "package": "plexmediaserver",
        "paths": ["/usr/lib/plexmediaserver/Plex Media Server", "/usr/lib/plexmediaserver/plexmediaserver"],
        "port": 32400,
        "web_path": "/web",
    },
    "jellyfin": {
        "name": "Jellyfin",
        "service": "jellyfin",
        "package": "jellyfin",
        "paths": ["/usr/bin/jellyfin", "/usr/lib/jellyfin/bin/jellyfin"],
        "port": 8096,
        "web_path": "/web/",
    },
    "sonarr": {
        "name": "Sonarr",
        "service": "sonarr",
        "package": None,
        "paths": ["/opt/Sonarr/Sonarr"],
        "port": 8989,
        "web_path": "",
    },
    "radarr": {
        "name": "Radarr",
        "service": "radarr",
        "package": None,
        "paths": ["/opt/Radarr/Radarr"],
        "port": 7878,
        "web_path": "",
    },
    "prowlarr": {
        "name": "Prowlarr",
        "service": "prowlarr",
        "package": None,
        "paths": ["/opt/Prowlarr/Prowlarr"],
        "port": 9696,
        "web_path": "",
    },
    "overseerr": {
        "name": "Seerr",
        "service": "seerr",
        "package": None,
        "paths": ["/opt/seerr/package.json"],
        "port": 5055,
        "web_path": "",
    },
    "qbittorrent": {
        "name": "qBittorrent",
        "service": "qbittorrent-nox@homeos",
        "package": "qbittorrent-nox",
        "paths": ["/usr/bin/qbittorrent-nox"],
        "port": 8080,
        "web_path": "",
    },
    "flaresolverr": {
        "name": "FlareSolverr",
        "service": "flaresolverr",
        "package": None,
        "paths": ["/opt/flaresolverr/start.sh"],
        "port": 8191,
        "web_path": "",
    },
}

ARR_CONFIG_PATHS = {
    "sonarr": "/opt/Sonarr/data/config.xml",
    "radarr": "/opt/Radarr/data/config.xml",
    "prowlarr": "/opt/Prowlarr/data/config.xml",
}

ARR_STATS_ENDPOINTS = {
    "sonarr": {
        "library": "/api/v3/series",
        "missing": "/api/v3/wanted/missing?page=1&pageSize=1",
        "queue": "/api/v3/queue?page=1&pageSize=1",
    },
    "radarr": {
        "library": "/api/v3/movie",
        "queue": "/api/v3/queue?page=1&pageSize=1",
    },
    "prowlarr": {
        "indexers": "/api/v1/indexer",
        "applications": "/api/v1/applications",
        "history": "/api/v1/history?page=1&pageSize=1",
        "health": "/api/v1/health",
    },
}

MEDIA_GROUP = "homeos-media"


def _service_installed(key):
    """Check if a media service is installed."""
    info = SERVICES[key]
    for p in info["paths"]:
        if os.path.isfile(p):
            return True
    if info["package"]:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", info["package"]],
            capture_output=True, text=True, timeout=5,
        )
        return "install ok installed" in result.stdout
    return False


def _service_running(key):
    """Check if a media service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICES[key]["service"]],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _run_media_helper(action, *arguments, timeout=300):
    """Run an allowlisted privileged media action installed by Home OS."""
    if arguments:
        raise ValueError("media helper actions do not accept arguments")
    return subprocess.run(
        ["/usr/bin/systemctl", "start", f"home-os-media-helper@{action}.service"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _media_helper_unit(action):
    return f"home-os-media-helper@{action}.service"


def _media_helper_status(action):
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "show",
            _media_helper_unit(action),
            "--property=ActiveState,SubState,Result,ExecMainStatus",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to read update status")
    properties = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    active_state = properties.get("ActiveState", "inactive")
    result_state = properties.get("Result", "")
    return {
        "running": active_state in {"activating", "active"},
        "failed": active_state == "failed" or result_state not in {"", "success"},
        "active_state": active_state,
        "result": result_state,
        "exit_status": int(properties.get("ExecMainStatus", "0") or 0),
    }


def _start_media_helper(action):
    unit = _media_helper_unit(action)
    status = _media_helper_status(action)
    if status["running"]:
        return False
    subprocess.run(
        ["/usr/bin/systemctl", "reset-failed", unit],
        capture_output=True,
        text=True,
        timeout=10,
    )
    result = subprocess.run(
        ["/usr/bin/systemctl", "start", "--no-block", unit],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to start update")
    return True


def _plex_installed():
    return _service_installed("plex")


def _plex_running():
    return _service_running("plex")


def _plex_web_port():
    return 32400


@media_bp.route("/media")
@login_required
def media_view():
    from flask_login import current_user
    if not current_user.has_permission("media"):
        from flask import abort
        abort(403)
    return render_template("media/media.html")


@media_bp.route("/api/media/plex/status")
@login_required
def plex_status():
    from flask import current_app
    config = current_app.config.get("_raw_config", {})
    port = config.get("media", {}).get("plex_port", 32400)
    installed = _plex_installed()
    running = _plex_running() if installed else False
    return jsonify({
        "ok": True,
        "data": {
            "installed": installed,
            "running": running,
            "port": port,
        }
    })


@media_bp.route("/api/media/plex/install", methods=["POST"])
@fresh_session_required
def install_plex():
    """Install Plex Media Server on Debian/Ubuntu via direct .deb download."""
    if _plex_installed():
        return jsonify({"ok": False, "error": "Plex Media Server is already installed"}), 409

    try:
        install_script = """
set -e
ARCH=$(dpkg --print-architecture)
echo "Fetching latest Plex download URL..."
DEB_URL=$(curl -sL "https://plex.tv/api/downloads/5.json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for release in data['computer']['Linux']['releases']:
    if 'debian' in release['url'] and '$ARCH' in release['url']:
        print(release['url'])
        break
")
if [ -z "$DEB_URL" ]; then
    echo "Could not find download URL for architecture: $ARCH"
    exit 1
fi
echo "Downloading Plex Media Server..."
TMPFILE=$(mktemp /tmp/plexmediaserver_XXXXX.deb)
curl -fsSL -o "$TMPFILE" "$DEB_URL"
echo "Installing..."
dpkg -i "$TMPFILE" || apt-get install -f -y -qq
rm -f "$TMPFILE"
echo "Enabling service..."
systemctl enable plexmediaserver
systemctl start plexmediaserver
echo "Done!"
"""
        result = run_privileged(
            ["bash", "-c", install_script],
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode == 0:
            _setup_media_folders("plex")
            return jsonify({"ok": True, "data": {"output": result.stdout[-500:]}})
        else:
            error = result.stderr[-300:] or result.stdout[-300:] or "Install failed"
            return jsonify({"ok": False, "error": error}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Install timed out (3 minutes)"}), 408
    except Exception:
        return jsonify({"ok": False, "error": "Install failed"}), 500


@media_bp.route("/api/media/plex/stats")
@admin_required
def plex_stats():
    """Get Plex Media Server statistics."""
    try:
        port = _get_port("plex")
        base = f"http://localhost:{port}"

        # Server info
        resp = httpx.get(f"{base}/", headers={"Accept": "application/json"}, timeout=5)
        resp.raise_for_status()
        server = resp.json().get("MediaContainer", {})

        # Libraries
        resp = httpx.get(f"{base}/library/sections", headers={"Accept": "application/json"}, timeout=5)
        resp.raise_for_status()
        libraries = resp.json().get("MediaContainer", {}).get("Directory", [])

        library_stats = []
        total_items = 0
        for lib in libraries:
            lib_info = {
                "title": lib.get("title", ""),
                "type": lib.get("type", ""),
                "key": lib.get("key", ""),
            }
            # Get item count per library
            try:
                resp = httpx.get(
                    f"{base}/library/sections/{lib['key']}/all",
                    headers={"Accept": "application/json"},
                    params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 0},
                    timeout=5,
                )
                count = resp.json().get("MediaContainer", {}).get("totalSize", 0)
                lib_info["count"] = count
                total_items += count
            except Exception:
                lib_info["count"] = 0
            library_stats.append(lib_info)

        # Active sessions
        resp = httpx.get(f"{base}/status/sessions", headers={"Accept": "application/json"}, timeout=5)
        sessions = resp.json().get("MediaContainer", {}).get("size", 0)

        return jsonify({
            "ok": True,
            "data": {
                "server_name": server.get("friendlyName", "Plex"),
                "version": server.get("version", ""),
                "platform": server.get("platform", ""),
                "libraries": library_stats,
                "library_count": len(library_stats),
                "total_items": total_items,
                "active_sessions": sessions,
            }
        })
    except Exception:
        return jsonify({"ok": False, "error": "Cannot connect to Plex. Is it running?"}), 503


@media_bp.route("/api/media/plex/update", methods=["POST"])
@fresh_session_required
def update_plex():
    """Update Plex Media Server to the latest version."""
    return _start_service_update("plex")


@media_bp.route("/api/media/plex/start", methods=["POST"])
@fresh_session_required
def start_plex():
    """Start Plex Media Server service."""
    try:
        result = run_privileged(
            ["systemctl", "start", "plexmediaserver"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to start"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to start Plex"}), 500


@media_bp.route("/api/media/plex/stop", methods=["POST"])
@fresh_session_required
def stop_plex():
    """Stop Plex Media Server service."""
    try:
        result = run_privileged(
            ["systemctl", "stop", "plexmediaserver"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to stop"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to stop Plex"}), 500


@media_bp.route("/api/media/plex/uninstall", methods=["POST"])
@fresh_session_required
def uninstall_plex():
    """Uninstall Plex Media Server."""
    if not _plex_installed():
        return jsonify({"ok": False, "error": "Plex is not installed"}), 404

    try:
        result = run_privileged(
            ["bash", "-c", "systemctl stop plexmediaserver 2>/dev/null; apt-get purge -y plexmediaserver"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr[-300:] or "Uninstall failed"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Uninstall failed"}), 500


# --- Generic *arr service endpoints ---

ARR_INSTALL_SCRIPTS = {
    "jellyfin": """
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq curl ca-certificates
curl -fsSL https://repo.jellyfin.org/install-debuntu.sh -o /tmp/install-jellyfin.sh
bash /tmp/install-jellyfin.sh
rm -f /tmp/install-jellyfin.sh
systemctl enable jellyfin
systemctl start jellyfin
""",
    "sonarr": """
set -e
apt-get install -y -qq curl sqlite3
curl -fsSL "https://services.sonarr.tv/v1/download/main/latest?version=4&os=linux&arch=x64" -o /tmp/sonarr.tar.gz
tar -xzf /tmp/sonarr.tar.gz -C /opt/
rm -f /tmp/sonarr.tar.gz
useradd -r -s /bin/false sonarr 2>/dev/null || true
usermod -aG sonarr homeos 2>/dev/null || true
chown -R sonarr:sonarr /opt/Sonarr
cat > /etc/systemd/system/sonarr.service << 'UNIT'
[Unit]
Description=Sonarr
After=network.target
[Service]
Type=simple
User=sonarr
Group=sonarr
UMask=0002
ExecStart=/opt/Sonarr/Sonarr -nobrowser -data=/opt/Sonarr/data
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable sonarr
systemctl start sonarr
""",
    "radarr": """
set -e
apt-get install -y -qq curl sqlite3
curl -fsSL "https://radarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64" -o /tmp/radarr.tar.gz
tar -xzf /tmp/radarr.tar.gz -C /opt/
rm -f /tmp/radarr.tar.gz
useradd -r -s /bin/false radarr 2>/dev/null || true
usermod -aG radarr homeos 2>/dev/null || true
chown -R radarr:radarr /opt/Radarr
cat > /etc/systemd/system/radarr.service << 'UNIT'
[Unit]
Description=Radarr
After=network.target
[Service]
Type=simple
User=radarr
Group=radarr
UMask=0002
ExecStart=/opt/Radarr/Radarr -nobrowser -data=/opt/Radarr/data
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable radarr
systemctl start radarr
""",
    "prowlarr": """
set -e
apt-get install -y -qq curl sqlite3
curl -fsSL "https://prowlarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64" -o /tmp/prowlarr.tar.gz
tar -xzf /tmp/prowlarr.tar.gz -C /opt/
rm -f /tmp/prowlarr.tar.gz
useradd -r -s /bin/false prowlarr 2>/dev/null || true
chown -R prowlarr:prowlarr /opt/Prowlarr
cat > /etc/systemd/system/prowlarr.service << 'UNIT'
[Unit]
Description=Prowlarr
After=network.target
[Service]
Type=simple
User=prowlarr
Group=prowlarr
ExecStart=/opt/Prowlarr/Prowlarr -nobrowser -data=/opt/Prowlarr/data
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable prowlarr
systemctl start prowlarr
""",
    "overseerr": """
set -e
apt-get install -y -qq curl git

# Install Node 20 if not present
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi

# Install yarn
npm install -g yarn 2>/dev/null || true

# Clone and build
git clone https://github.com/sct/overseerr.git /opt/overseerr
cd /opt/overseerr
git checkout $(git describe --tags --abbrev=0)
yarn install --frozen-lockfile
yarn build

useradd -r -s /bin/false overseerr 2>/dev/null || true
mkdir -p /opt/overseerr/config
chown -R overseerr:overseerr /opt/overseerr

cat > /etc/systemd/system/overseerr.service << 'UNIT'
[Unit]
Description=Overseerr
After=network.target
[Service]
Type=simple
User=overseerr
Group=overseerr
WorkingDirectory=/opt/overseerr
ExecStart=/usr/bin/yarn start
Restart=on-failure
Environment=NODE_ENV=production
Environment=CONFIG_DIRECTORY=/opt/overseerr/config
Environment=PORT=5055
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable overseerr
systemctl start overseerr
""",
    "flaresolverr": """
set -e
apt-get install -y -qq python3 python3-pip python3-venv xvfb

# Install Chromium (package name varies by distro)
apt-get install -y -qq chromium 2>/dev/null || apt-get install -y -qq chromium-browser

# Create user with a home directory (Chrome needs it for profile/cache)
useradd -r -m -d /home/flaresolverr -s /bin/false flaresolverr 2>/dev/null || true
mkdir -p /home/flaresolverr && chown flaresolverr:flaresolverr /home/flaresolverr

# Set up venv and install FlareSolverr from PyPI
mkdir -p /opt/flaresolverr
python3 -m venv /opt/flaresolverr/venv
/opt/flaresolverr/venv/bin/pip install --upgrade pip
/opt/flaresolverr/venv/bin/pip install FlareSolverr

# Install legacy-cgi shim for Python 3.13+ (bottle dependency)
/opt/flaresolverr/venv/bin/pip install legacy-cgi 2>/dev/null || true

# Create launcher script
cat > /opt/flaresolverr/start.sh << 'LAUNCHER'
#!/bin/bash
cd /opt/flaresolverr
exec /opt/flaresolverr/venv/bin/python -m flaresolverr
LAUNCHER
chmod +x /opt/flaresolverr/start.sh

chown -R flaresolverr:flaresolverr /opt/flaresolverr

cat > /etc/systemd/system/flaresolverr.service << 'UNIT'
[Unit]
Description=FlareSolverr - Cloudflare bypass proxy
After=network.target

[Service]
Type=simple
User=flaresolverr
Group=flaresolverr
WorkingDirectory=/opt/flaresolverr
ExecStart=/opt/flaresolverr/start.sh
Restart=on-failure
Environment=LOG_LEVEL=info
Environment=LOG_HTML=false
Environment=CAPTCHA_SOLVER=none
Environment=TZ=UTC
Environment=HEADLESS=true
Environment=PORT=8191

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable flaresolverr
systemctl start flaresolverr
""",
    "qbittorrent": """
set -e
apt-get install -y -qq qbittorrent-nox
cat > /etc/systemd/system/qbittorrent-nox@.service << 'UNIT'
[Unit]
Description=qBittorrent-nox service for %i
After=network.target
[Service]
Type=simple
User=%i
Group=%i
ExecStart=/usr/bin/qbittorrent-nox --webui-port=8080
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable qbittorrent-nox@homeos
systemctl start qbittorrent-nox@homeos
""",
}

ARR_UNINSTALL_SCRIPTS = {
    "jellyfin": "systemctl stop jellyfin 2>/dev/null; systemctl disable jellyfin 2>/dev/null; apt-get purge -y jellyfin jellyfin-server jellyfin-web; apt-get autoremove -y; systemctl daemon-reload",
    "sonarr": "systemctl stop sonarr 2>/dev/null; systemctl disable sonarr 2>/dev/null; rm -f /etc/systemd/system/sonarr.service; rm -rf /opt/Sonarr; userdel sonarr 2>/dev/null; systemctl daemon-reload",
    "radarr": "systemctl stop radarr 2>/dev/null; systemctl disable radarr 2>/dev/null; rm -f /etc/systemd/system/radarr.service; rm -rf /opt/Radarr; userdel radarr 2>/dev/null; systemctl daemon-reload",
    "prowlarr": "systemctl stop prowlarr 2>/dev/null; systemctl disable prowlarr 2>/dev/null; rm -f /etc/systemd/system/prowlarr.service; rm -rf /opt/Prowlarr; userdel prowlarr 2>/dev/null; systemctl daemon-reload",
    "overseerr": "systemctl stop overseerr 2>/dev/null; systemctl disable overseerr 2>/dev/null; rm -f /etc/systemd/system/overseerr.service; rm -rf /opt/overseerr; userdel overseerr 2>/dev/null; systemctl daemon-reload",
    "qbittorrent": "systemctl stop qbittorrent-nox@homeos 2>/dev/null; systemctl disable qbittorrent-nox@homeos 2>/dev/null; rm -f /etc/systemd/system/qbittorrent-nox@.service; apt-get purge -y qbittorrent-nox; systemctl daemon-reload",
    "flaresolverr": "systemctl stop flaresolverr 2>/dev/null; systemctl disable flaresolverr 2>/dev/null; rm -f /etc/systemd/system/flaresolverr.service; rm -rf /opt/flaresolverr; userdel -r flaresolverr 2>/dev/null; systemctl daemon-reload",
}


def _get_port(service):
    """Get configured port for a service, falling back to default."""
    from flask import current_app
    config = current_app.config.get("_raw_config", {})
    media_config = config.get("media", {})
    port = media_config.get(f"{service}_port", SERVICES[service]["port"])
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = SERVICES[service]["port"]
    return port


def _read_arr_api_config(service):
    """Read an Arr API key and URL base without exposing either to the client."""
    config_path = ARR_CONFIG_PATHS[service]
    root = ET.parse(config_path).getroot()
    api_key = (root.findtext("ApiKey") or "").strip()
    if not api_key:
        raise RuntimeError("API key is not configured")

    raw_url_base = (root.findtext("UrlBase") or "").strip()
    url_base = f"/{raw_url_base.strip('/')}" if raw_url_base.strip("/") else ""
    return api_key, url_base


def _record_total(payload):
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("totalRecords", 0))
    except (TypeError, ValueError):
        return 0


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _summarize_arr_stats(service, payloads):
    """Convert service API responses into stable dashboard metrics."""
    if service == "sonarr":
        series = payloads.get("library", [])
        series = series if isinstance(series, list) else []
        episode_files = sum(
            _as_int(item.get("statistics", {}).get("episodeFileCount", 0))
            for item in series
            if isinstance(item, dict)
        )
        return [
            {"label": "Series", "value": len(series)},
            {"label": "Episodes", "value": episode_files},
            {"label": "Missing", "value": _record_total(payloads.get("missing"))},
            {"label": "Queue", "value": _record_total(payloads.get("queue"))},
        ]

    if service == "radarr":
        movies = payloads.get("library", [])
        movies = movies if isinstance(movies, list) else []
        return [
            {"label": "Movies", "value": len(movies)},
            {"label": "Downloaded", "value": sum(bool(item.get("hasFile")) for item in movies if isinstance(item, dict))},
            {"label": "Monitored", "value": sum(bool(item.get("monitored")) for item in movies if isinstance(item, dict))},
            {"label": "Queue", "value": _record_total(payloads.get("queue"))},
        ]

    indexers = payloads.get("indexers", [])
    applications = payloads.get("applications", [])
    health = payloads.get("health", [])
    indexers = indexers if isinstance(indexers, list) else []
    applications = applications if isinstance(applications, list) else []
    health = health if isinstance(health, list) else []
    return [
        {"label": "Indexers", "value": sum(bool(item.get("enable")) for item in indexers if isinstance(item, dict))},
        {"label": "Apps", "value": sum(bool(item.get("enable")) for item in applications if isinstance(item, dict))},
        {"label": "History", "value": _record_total(payloads.get("history"))},
        {"label": "Health Issues", "value": len(health)},
    ]


def _jellyfin_public_info():
    """Read Jellyfin's unauthenticated public server metadata."""
    response = httpx.get(
        f"http://127.0.0.1:{_get_port('jellyfin')}/svc/jellyfin/System/Info/Public",
        headers={"Accept": "application/json"},
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "server_name": data.get("ServerName", "Jellyfin"),
        "version": data.get("Version", ""),
        "operating_system": data.get("OperatingSystem", ""),
    }


@media_bp.route("/api/media/<service>/status")
@login_required
def arr_status(service):
    """Get status of a media service."""
    if service == "plex":
        return plex_status()
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    installed = _service_installed(service)
    running = _service_running(service) if installed else False

    return jsonify({
        "ok": True,
        "data": {
            "installed": installed,
            "running": running,
            "port": _get_port(service),
            "web_path": SERVICES[service]["web_path"],
        }
    })


@media_bp.route("/api/media/<service>/stats")
@login_required
def arr_stats(service):
    """Return dashboard statistics for supported media services."""
    if service == "jellyfin":
        if not _service_installed(service) or not _service_running(service):
            return jsonify({"ok": False, "error": "Jellyfin is not running"}), 409
        try:
            return jsonify({"ok": True, "data": _jellyfin_public_info()})
        except (httpx.HTTPError, ValueError):
            return jsonify({"ok": False, "error": "Jellyfin statistics are unavailable"}), 502
    if service not in ARR_STATS_ENDPOINTS:
        return jsonify({"ok": False, "error": "Statistics are not available for this service"}), 404
    if not _service_installed(service) or not _service_running(service):
        return jsonify({"ok": False, "error": f"{SERVICES[service]['name']} is not running"}), 409

    try:
        api_key, url_base = _read_arr_api_config(service)
        base_url = f"http://127.0.0.1:{_get_port(service)}{url_base}"
        payloads = {}
        with httpx.Client(headers={"X-Api-Key": api_key, "Accept": "application/json"}, timeout=6) as client:
            for name, endpoint in ARR_STATS_ENDPOINTS[service].items():
                response = client.get(f"{base_url}{endpoint}")
                response.raise_for_status()
                payloads[name] = response.json()
        return jsonify({"ok": True, "data": {"metrics": _summarize_arr_stats(service, payloads)}})
    except (ET.ParseError, OSError, RuntimeError):
        return jsonify({"ok": False, "error": "Service API is not configured"}), 503
    except (httpx.HTTPError, ValueError):
        return jsonify({"ok": False, "error": "Service statistics are unavailable"}), 502


def _start_service_update(service):
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404
    if not _service_installed(service):
        return jsonify({
            "ok": False,
            "error": f"{SERVICES[service]['name']} is not installed",
        }), 404
    try:
        started = _start_media_helper(f"update-{service}")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return jsonify({"ok": False, "error": "Could not start update"}), 500
    if not started:
        return jsonify({"ok": False, "error": "An update is already running"}), 409
    return jsonify({"ok": True, "data": {"running": True}}), 202


@media_bp.route("/api/media/<service>/update", methods=["POST"])
@fresh_session_required
def arr_update(service):
    """Start an asynchronous update for an installed media service."""
    if service == "plex":
        return update_plex()
    return _start_service_update(service)


@media_bp.route("/api/media/<service>/update-status")
@login_required
def arr_update_status(service):
    """Return the state of a media-service update job."""
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404
    try:
        status = _media_helper_status(f"update-{service}")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return jsonify({"ok": False, "error": "Could not read update status"}), 500
    return jsonify({"ok": True, "data": status})


def _setup_media_folders(service):
    """Create storage folders and set permissions after install."""
    from flask import current_app
    from pathlib import Path

    config = current_app.config["_raw_config"]
    storage_root = config["storage"]["root"]
    homeos_dir = Path(storage_root) / "HomeOS"

    folder_map = {
        "sonarr": ("Series",),
        "radarr": ("Movies",),
        "plex": ("Movies", "Series"),
        "jellyfin": ("Movies", "Series"),
        "qbittorrent": ("Downloads",),
    }
    service_users = {
        "sonarr": "sonarr",
        "radarr": "radarr",
        "plex": "plex",
        "jellyfin": "jellyfin",
        "qbittorrent": "homeos",
    }
    folders = folder_map.get(service, ())
    if not folders:
        return

    try:
        run_privileged(
            ["groupadd", "--force", MEDIA_GROUP],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for user in ("homeos", service_users[service]):
            run_privileged(
                ["usermod", "-aG", MEDIA_GROUP, user],
                capture_output=True,
                text=True,
                timeout=10,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    homeos_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_privileged(
            ["chgrp", MEDIA_GROUP, str(Path(storage_root))],
            capture_output=True,
            text=True,
            timeout=10,
        )
        run_privileged(
            ["chmod", "g+rx", str(Path(storage_root))],
            capture_output=True,
            text=True,
            timeout=10,
        )
        run_privileged(
            ["chgrp", MEDIA_GROUP, str(homeos_dir)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        run_privileged(
            ["chmod", "g+rwx,o-rwx,g+s", str(homeos_dir)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    targets = [homeos_dir / folder_name for folder_name in folders]
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        try:
            run_privileged(
                ["chgrp", "-R", MEDIA_GROUP, str(target)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            run_privileged(
                ["chmod", "-R", "g+rwX,o-rwx", str(target)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            run_privileged(
                ["find", str(target), "-type", "d", "-exec", "chmod", "g+s", "{}", "+"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if service == "jellyfin":
        try:
            _run_media_helper("configure-jellyfin-access", timeout=30)
        except Exception:
            pass
    try:
        run_privileged(
            ["systemctl", "try-restart", SERVICES[service]["service"]],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


@media_bp.route("/api/media/<service>/install", methods=["POST"])
@fresh_session_required
def arr_install(service):
    """Install a media service."""
    if service == "plex":
        return install_plex()
    if service not in ARR_INSTALL_SCRIPTS:
        return jsonify({"ok": False, "error": "Unknown service"}), 404
    if _service_installed(service):
        return jsonify({"ok": False, "error": f"{SERVICES[service]['name']} is already installed"}), 409

    try:
        if service == "jellyfin":
            result = _run_media_helper("install-jellyfin", timeout=600)
        else:
            timeout = 600 if service == "overseerr" else 300
            result = run_privileged(
                ["bash", "-c", ARR_INSTALL_SCRIPTS[service]],
                capture_output=True, text=True, timeout=timeout,
            )
        if result.returncode == 0:
            _setup_media_folders(service)
            return jsonify({"ok": True})
        error = result.stderr[-300:] or result.stdout[-300:] or "Install failed"
        return jsonify({"ok": False, "error": error}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Install timed out"}), 408
    except Exception:
        return jsonify({"ok": False, "error": "Install failed"}), 500


@media_bp.route("/api/media/<service>/start", methods=["POST"])
@fresh_session_required
def arr_start(service):
    """Start a media service."""
    if service == "plex":
        return start_plex()
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    try:
        if service == "jellyfin":
            result = _run_media_helper("start-jellyfin", timeout=15)
        else:
            result = run_privileged(
                ["systemctl", "start", SERVICES[service]["service"]],
                capture_output=True, text=True, timeout=15,
            )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to start"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to start"}), 500


@media_bp.route("/api/media/<service>/stop", methods=["POST"])
@fresh_session_required
def arr_stop(service):
    """Stop a media service."""
    if service == "plex":
        return stop_plex()
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    try:
        if service == "jellyfin":
            result = _run_media_helper("stop-jellyfin", timeout=15)
        else:
            result = run_privileged(
                ["systemctl", "stop", SERVICES[service]["service"]],
                capture_output=True, text=True, timeout=15,
            )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to stop"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to stop"}), 500


@media_bp.route("/api/media/<service>/port", methods=["POST"])
@fresh_session_required
def arr_save_port(service):
    """Save port configuration and update the actual service port."""
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    import yaml
    from flask import current_app

    data = request.get_json()
    port = data.get("port")
    if not port or not isinstance(port, int) or port < 1 or port > 65535:
        return jsonify({"ok": False, "error": "Invalid port"}), 400

    old_port = _get_port(service)

    config = current_app.config["_raw_config"]
    if "media" not in config:
        config["media"] = {}
    config["media"][f"{service}_port"] = port

    import os
    import tempfile

    config_path = current_app.config["_config_path"]
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(config_path), suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_path)
    except Exception:
        os.unlink(tmp_path)
        raise

    _apply_port_change(service, old_port, port)

    return jsonify({"ok": True})


def _apply_port_change(service, old_port, new_port):
    """Apply port change to the actual service."""
    if old_port == new_port:
        return

    if service == "qbittorrent":
        # Update via API (live) and systemd unit (persist across restarts)
        if _service_running("qbittorrent"):
            try:
                httpx.post(
                    f"http://localhost:{old_port}/api/v2/app/setPreferences",
                    data={"json": f'{{"web_ui_port": {new_port}}}'},
                    timeout=5,
                )
            except Exception:
                pass
        try:
            run_privileged(
                ["bash", "-c",
                 f"sed -i 's/--webui-port={old_port}/--webui-port={new_port}/' "
                 f"/etc/systemd/system/qbittorrent-nox@.service && systemctl daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass

    elif service in ("sonarr", "radarr", "prowlarr"):
        # Update config.xml and restart
        config_paths = {
            "sonarr": "/opt/Sonarr/data/config.xml",
            "radarr": "/opt/Radarr/data/config.xml",
            "prowlarr": "/opt/Prowlarr/data/config.xml",
        }
        config_file = config_paths[service]
        try:
            run_privileged(
                ["bash", "-c",
                 f"sed -i 's|<Port>{old_port}</Port>|<Port>{new_port}</Port>|' {config_file} && "
                 f"systemctl restart {SERVICES[service]['service']}"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    elif service == "jellyfin":
        try:
            _run_media_helper("set-jellyfin-port", timeout=15)
        except Exception:
            pass

    elif service == "overseerr":
        # Update systemd unit Environment=PORT and restart
        try:
            run_privileged(
                ["bash", "-c",
                 f"sed -i 's/Environment=PORT={old_port}/Environment=PORT={new_port}/' "
                 f"/etc/systemd/system/overseerr.service && "
                 f"systemctl daemon-reload && systemctl restart overseerr"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    elif service == "flaresolverr":
        try:
            run_privileged(
                ["bash", "-c",
                 f"sed -i 's/Environment=PORT={old_port}/Environment=PORT={new_port}/' "
                 f"/etc/systemd/system/flaresolverr.service && "
                 f"systemctl daemon-reload && systemctl restart flaresolverr"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    elif service == "plex":
        # Plex port is managed by Plex itself — config port just tells the proxy where to look
        pass


@media_bp.route("/api/media/<service>/uninstall", methods=["POST"])
@fresh_session_required
def arr_uninstall(service):
    """Uninstall a media service."""
    if service == "plex":
        return uninstall_plex()
    if service not in ARR_UNINSTALL_SCRIPTS:
        return jsonify({"ok": False, "error": "Unknown service"}), 404
    if not _service_installed(service):
        return jsonify({"ok": False, "error": f"{SERVICES[service]['name']} is not installed"}), 404

    try:
        if service == "jellyfin":
            result = _run_media_helper("uninstall-jellyfin", timeout=120)
        else:
            result = run_privileged(
                ["bash", "-c", ARR_UNINSTALL_SCRIPTS[service]],
                capture_output=True, text=True, timeout=60,
            )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr[-300:] or "Uninstall failed"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Uninstall failed"}), 500


# --- Media service reverse proxies ---

PROXY_PREFIXES = {
    "qbt": "qbittorrent",
    "sonarr": "sonarr",
    "radarr": "radarr",
    "prowlarr": "prowlarr",
    "seerr": "overseerr",
    "jellyfin": "jellyfin",
}

SERVICE_COOKIE_NAMES = {
    "qbittorrent": ("SID",),
    "overseerr": ("connect.sid", "_csrf", "XSRF-TOKEN"),
}

REWRITE_SERVICES = ("plex", "qbittorrent", "overseerr")
SEERR_PROXY_METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
SEERR_PROXY_ASSET_VERSION = "20260715-3"


@media_bp.route("/svc/<prefix>/", methods=SEERR_PROXY_METHODS)
@media_bp.route("/svc/<prefix>/<path:subpath>", methods=SEERR_PROXY_METHODS)
def service_proxy(prefix, subpath=""):
    if prefix == "jellyfin":
        return _proxy_service(prefix, subpath)
    if prefix == "seerr":
        return _media_service_proxy(prefix, subpath)
    return _admin_service_proxy(prefix, subpath)


@login_required
def _media_service_proxy(prefix, subpath=""):
    if not current_user.has_permission("media"):
        abort(403)
    return _proxy_service(prefix, subpath)


@admin_required
def _admin_service_proxy(prefix, subpath=""):
    return _proxy_service(prefix, subpath)


@media_bp.route("/api/v1", defaults={"subpath": ""}, methods=SEERR_PROXY_METHODS)
@media_bp.route("/api/v1/<path:subpath>", methods=SEERR_PROXY_METHODS)
def seerr_api_compatibility_proxy(subpath):
    suffix = f"/{subpath}" if subpath else ""
    return _media_service_proxy("seerr", f"api/v1{suffix}")


@media_bp.route("/imageproxy/<path:subpath>", methods=["GET", "HEAD"])
@media_bp.route("/avatarproxy/<path:subpath>", methods=["GET", "HEAD"])
def seerr_image_compatibility_proxy(subpath):
    proxy_root = request.path.split("/", 2)[1]
    return _media_service_proxy("seerr", f"{proxy_root}/{subpath}")


@media_bp.route("/logo_full.svg", methods=["GET", "HEAD"])
@media_bp.route("/logo_stacked.svg", methods=["GET", "HEAD"])
def seerr_logo_compatibility_proxy():
    return _media_service_proxy("seerr", request.path.lstrip("/"))


def _proxy_service(prefix, subpath=""):
    from flask import current_app

    service = PROXY_PREFIXES.get(prefix)
    if not service:
        return "Unknown service", 404

    config = current_app.config.get("_raw_config", {})
    port = config.get("media", {}).get(f"{service}_port", SERVICES[service]["port"])

    # Services with UrlBase configured expect the full /svc/<prefix>/... path
    url_base_services = ("sonarr", "radarr", "prowlarr", "jellyfin")
    if service in url_base_services:
        target = f"http://localhost:{port}/svc/{prefix}/{subpath}"
    else:
        target = f"http://localhost:{port}/{subpath}"

    if request.query_string:
        target += "?" + request.query_string.decode()

    excluded_request_headers = {
        "host", "cookie", "referer", "origin", "accept-encoding",
        "connection", "content-length", "proxy-connection", "te", "trailer",
        "transfer-encoding", "upgrade",
        "x-forwarded-for", "x-forwarded-proto", "x-real-ip",
        "cf-connecting-ip", "cf-ipcountry", "cf-ray", "cf-visitor",
    }
    if service in REWRITE_SERVICES:
        excluded_request_headers.update({
            "if-match", "if-modified-since", "if-none-match",
            "if-unmodified-since",
        })
    headers = {
        key: value
        for key, value in request.headers
        if key.lower() not in excluded_request_headers
    }

    service_cookie_parts = [
        f"{cookie_name}={request.cookies[cookie_name]}"
        for cookie_name in SERVICE_COOKIE_NAMES.get(service, ())
        if cookie_name in request.cookies
    ]
    if service_cookie_parts:
        headers["Cookie"] = "; ".join(service_cookie_parts)

    if service == "overseerr":
        headers.update({
            "Host": request.host,
            "X-Forwarded-Host": request.host,
            "X-Forwarded-Proto": request.scheme,
            "X-Forwarded-Prefix": f"/svc/{prefix}",
        })
        if request.remote_addr:
            headers["X-Forwarded-For"] = request.remote_addr
            headers["X-Real-IP"] = request.remote_addr

        public_origin = request.host_url.rstrip("/")
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") == public_origin:
            headers["Origin"] = origin
        referer = request.headers.get("Referer")
        if referer and referer.startswith(f"{public_origin}/"):
            headers["Referer"] = referer

    streaming_client = None
    try:
        if service == "jellyfin":
            streaming_client = httpx.Client(
                timeout=httpx.Timeout(30, read=None),
                follow_redirects=False,
            )
            upstream_request = streaming_client.build_request(
                method=request.method,
                url=target,
                headers=headers,
                content=request.get_data(),
            )
            resp = streaming_client.send(upstream_request, stream=True)
        else:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                resp = client.request(
                    method=request.method,
                    url=target,
                    headers=headers,
                    content=request.get_data(),
                )
    except httpx.ConnectError:
        if streaming_client:
            streaming_client.close()
        return f"{SERVICES[service]['name']} is not running", 502
    except httpx.TimeoutException:
        if streaming_client:
            streaming_client.close()
        return f"{SERVICES[service]['name']} timed out", 504
    except httpx.HTTPError:
        if streaming_client:
            streaming_client.close()
        return f"{SERVICES[service]['name']} proxy request failed", 502

    content_type = resp.headers.get("content-type", "")
    rewrite_content = (
        service in REWRITE_SERVICES
        and (
            "text/html" in content_type
            or "javascript" in content_type
            or "text/css" in content_type
        )
    )

    excluded_headers = {"transfer-encoding", "connection", "content-encoding", "content-length"}
    if rewrite_content:
        excluded_headers.update({"cache-control", "etag", "last-modified"})
    response_headers = []
    seerr_session_refreshed = False
    for key, value in resp.headers.multi_items():
        if key.lower() in excluded_headers:
            continue
        if key.lower() == "set-cookie":
            if service == "overseerr":
                value = re.sub(r"(?i)Path=/($|;)", r"Path=/\1", value)
                if value.lower().startswith("connect.sid="):
                    seerr_session_refreshed = True
            else:
                value = re.sub(
                    r"(?i)Path=/($|;)",
                    rf"Path=/svc/{prefix}/\1",
                    value,
                )
        response_headers.append((key, value))
    if rewrite_content:
        response_headers.append(("Cache-Control", "private, no-cache"))
    existing_seerr_session = request.cookies.get("connect.sid")
    if (
        service == "overseerr"
        and rewrite_content
        and "text/html" in content_type
        and existing_seerr_session
    ):
        if not seerr_session_refreshed:
            response_headers.append((
                "Set-Cookie",
                dump_cookie(
                    "connect.sid",
                    existing_seerr_session,
                    max_age=30 * 24 * 60 * 60,
                    path="/",
                    secure=request.is_secure,
                    httponly=True,
                    samesite="Lax",
                ),
            ))
        response_headers.append((
            "Set-Cookie",
            dump_cookie(
                "connect.sid",
                "",
                max_age=0,
                path=f"/svc/{prefix}/",
                secure=request.is_secure,
                httponly=True,
                samesite="Lax",
            ),
        ))

    # Services that need path rewriting (no native UrlBase support)
    # Rewrite Location headers
    location = resp.headers.get("location")
    if location:
        loc = location
        base_url = f"http://localhost:{port}"
        if loc.startswith(base_url):
            loc = loc[len(base_url):]
        if service in REWRITE_SERVICES and loc.startswith("/"):
            loc = f"/svc/{prefix}{loc}"
        response_headers = [
            (key, loc if key.lower() == "location" else value)
            for key, value in response_headers
        ]

    if service == "jellyfin":
        if request.method == "HEAD":
            resp.close()
            streaming_client.close()
            return Response(status=resp.status_code, headers=response_headers)

        def stream_jellyfin_response():
            try:
                yield from resp.iter_bytes()
            finally:
                resp.close()
                streaming_client.close()

        return Response(
            stream_with_context(stream_jellyfin_response()),
            status=resp.status_code,
            headers=response_headers,
        )

    content = resp.content
    # Rewrite root-relative paths for services without UrlBase
    if rewrite_content:
        import re as _re
        proxy_prefix = f"/svc/{prefix}".encode()
        content = content.replace(b"://", b":\x00//")
        content = _re.sub(
            rb'((?:href|src|action)\s*=\s*["\'])/(?!/)',
            lambda match: match.group(1) + proxy_prefix + b"/",
            content,
        )
        content = _re.sub(
            rb'(fetch\(\s*["\'`])/(?!/)',
            lambda match: match.group(1) + proxy_prefix + b"/",
            content,
        )
        content = _re.sub(
            rb'(["\'`])/(api|_next|static|login|settings|discover|movie|tv|collection|collections|request|requests|issue|issues|user|users|profile|profiles|imageproxy|avatarproxy)(?=[/?#])',
            lambda match: match.group(1) + proxy_prefix + b"/" + match.group(2),
            content,
        )
        content = _re.sub(
            rb'(["\'`])/(sw\.js|logo_full\.svg|logo_stacked\.svg)\1',
            lambda match: (
                match.group(1)
                + proxy_prefix
                + b"/"
                + match.group(2)
                + match.group(1)
            ),
            content,
        )
        content = _re.sub(
            rb'(\.(?:push|replace)\(\s*)(["\'`])/\2',
            lambda match: (
                match.group(1)
                + match.group(2)
                + proxy_prefix
                + b"/"
                + match.group(2)
            ),
            content,
        )
        if service == "overseerr" and "text/html" in content_type:
            asset_version = SEERR_PROXY_ASSET_VERSION.encode()
            content = _re.sub(
                rb'((?:src|href)=["\'])(/svc/seerr/_next/static/[^"\']+)(["\'])',
                lambda match: (
                    match.group(1)
                    + match.group(2)
                    + (b"&" if b"?" in match.group(2) else b"?")
                    + b"homeos_proxy="
                    + asset_version
                    + match.group(3)
                ),
                content,
            )
        content = content.replace(b"url(/", b"url(" + proxy_prefix + b"/")
        content = content.replace(b":\x00//", b"://")

    return Response(content, status=resp.status_code, headers=response_headers)



# Keep legacy /qbt/ route working
@media_bp.route("/qbt/", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@media_bp.route("/qbt/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@admin_required
def qbt_proxy(subpath=""):
    return service_proxy("qbt", subpath)
