import hashlib
import hmac
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

import httpx
from flask import Response, abort, jsonify, render_template, request, stream_with_context
from flask_login import current_user, login_required

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
INSTANT_STREAM_STATE = Path("/opt/home-os/data/instant-stream/instant_streams.json")
INSTANT_STREAM_TOKEN = Path("/opt/home-os/data/instant-stream/access-token")
TORRSERVER_BINARY = Path("/usr/local/bin/torrserver")
DYNAMIC_LIBRARY_PLUGIN = Path(
    "/var/lib/jellyfin/plugins/DynamicLibrary/Jellyfin.Plugin.DynamicLibrary.dll"
)
LOGGER = logging.getLogger("home_os.media")


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


def _systemd_active(unit):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _instant_stream_state():
    from home_os.services.instant_stream import load_state

    return load_state(INSTANT_STREAM_STATE)


def _torrserver_hls_ready():
    try:
        response = httpx.get("http://127.0.0.1:8090/gst/settings", timeout=2)
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and payload.get("built_in") is True
    except (httpx.HTTPError, ValueError):
        return False


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
    try:
        update_script = """
set -e
ARCH=$(dpkg --print-architecture)
DEB_URL=$(curl -sL "https://plex.tv/api/downloads/5.json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for release in data['computer']['Linux']['releases']:
    if 'debian' in release['url'] and '$ARCH' in release['url']:
        print(release['url'])
        break
")
if [ -z "$DEB_URL" ]; then
    echo "Could not find download URL"
    exit 1
fi
TMPFILE=$(mktemp /tmp/plexmediaserver_XXXXX.deb)
curl -fsSL -o "$TMPFILE" "$DEB_URL"
dpkg -i "$TMPFILE" || apt-get install -f -y -qq
rm -f "$TMPFILE"
systemctl restart plexmediaserver
echo "Updated successfully!"
"""
        result = run_privileged(
            ["bash", "-c", update_script],
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode == 0:
            return jsonify({"ok": True})
        else:
            error = result.stderr[-300:] or result.stdout[-300:] or "Update failed"
            return jsonify({"ok": False, "error": error}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Update timed out"}), 408
    except Exception:
        return jsonify({"ok": False, "error": "Update failed"}), 500


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


@media_bp.route("/api/media/instant-stream/status")
@login_required
def instant_stream_status():
    from home_os.services.instant_stream import read_access_token

    state = _instant_stream_state()
    streams = state.get("streams", {})
    return jsonify({
        "ok": True,
        "data": {
            "installed": TORRSERVER_BINARY.is_file(),
            "running": _systemd_active("torrserver.service"),
            "enabled": _systemd_active("home-os-instant-stream.timer"),
            "hls_ready": _torrserver_hls_ready(),
            "jellyfin_plugin": DYNAMIC_LIBRARY_PLUGIN.is_file(),
            "playback_configured": bool(read_access_token(INSTANT_STREAM_TOKEN)),
            "active_streams": len(streams) if isinstance(streams, dict) else 0,
            "last_run": state.get("last_run"),
            "last_error": state.get("last_error"),
        },
    })


@media_bp.route("/api/media/instant-stream/install", methods=["POST"])
@fresh_session_required
def instant_stream_install():
    try:
        result = _run_media_helper("install-torrserver", timeout=600)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Instant Streaming setup timed out"}), 408
    if result.returncode == 0:
        return jsonify({"ok": True})
    error = result.stderr[-500:] or result.stdout[-500:] or "Instant Streaming setup failed"
    return jsonify({"ok": False, "error": error}), 500


@media_bp.route("/api/media/instant-stream/start", methods=["POST"])
@fresh_session_required
def instant_stream_start():
    if not TORRSERVER_BINARY.is_file():
        return jsonify({"ok": False, "error": "Instant Streaming is not installed"}), 409
    result = _run_media_helper("start-instant-streaming", timeout=30)
    if result.returncode == 0:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": result.stderr[-500:] or "Could not start Instant Streaming"}), 500


@media_bp.route("/api/media/instant-stream/stop", methods=["POST"])
@fresh_session_required
def instant_stream_stop():
    result = _run_media_helper("stop-instant-streaming", timeout=30)
    if result.returncode == 0:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": result.stderr[-500:] or "Could not stop Instant Streaming"}), 500


@media_bp.route("/api/media/instant-stream/uninstall", methods=["POST"])
@fresh_session_required
def instant_stream_uninstall():
    result = _run_media_helper("uninstall-torrserver", timeout=60)
    if result.returncode == 0:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": result.stderr[-500:] or "Could not remove Instant Streaming"}), 500


def _instant_play_api_authorized():
    from home_os.services.instant_stream import read_access_token

    expected = read_access_token(INSTANT_STREAM_TOKEN)
    supplied = request.headers.get("X-Api-Key", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _instant_play_signature(torrent_hash, expires, file_id):
    from home_os.services.instant_stream import read_access_token

    key = read_access_token(INSTANT_STREAM_TOKEN).encode("utf-8")
    payload = f"{torrent_hash.lower()}:{file_id}:{expires}".encode("ascii")
    return hmac.new(key, payload, hashlib.sha256).hexdigest() if key else ""


def _instant_play_stream_authorized(torrent_hash):
    try:
        expires = int(request.args.get("expires", "0"))
        file_id = int(request.args.get("file_id", "-1"))
    except ValueError:
        return False
    supplied = request.args.get("signature", "")
    if file_id < 0 or expires < int(time.time()) or expires > int(time.time()) + 8 * 60 * 60:
        return False
    expected = _instant_play_signature(torrent_hash, expires, file_id)
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


@media_bp.route("/api/media/instant-play/embedarr/health")
def instant_play_embedarr_health():
    if not _instant_play_api_authorized():
        abort(403)
    return jsonify({"ok": True})


@media_bp.route(
    "/api/media/instant-play/embedarr/api/admin/library/movies",
    methods=["POST"],
)
def instant_play_embedarr_add_movie():
    from home_os.services.instant_stream import InstantPlayResolver, InstantPlayUnavailable

    if not _instant_play_api_authorized():
        abort(403)

    media_id = str((request.get_json(silent=True) or {}).get("id") or "")
    resolver = InstantPlayResolver()
    try:
        resolver.prepare_movie(media_id)
    except InstantPlayUnavailable as error:
        return jsonify({"success": False, "error": str(error)}), 503
    except (ET.ParseError, OSError, RuntimeError, httpx.HTTPError, ValueError):
        LOGGER.exception("Could not pre-request instant movie playback for %s", media_id)
        return jsonify({"success": False, "error": "Instant playback could not be prepared"}), 502
    finally:
        resolver.close()
    return jsonify({
        "success": True,
        "message": "The movie request is being prepared",
        "filesCreated": [],
    })


@media_bp.route("/api/media/instant-play/embedarr/api/url/movie/<imdb_id>")
def instant_play_embedarr_movie_url(imdb_id):
    from home_os.services.instant_stream import InstantPlayResolver, InstantPlayUnavailable

    if not _instant_play_api_authorized():
        abort(403)

    resolver = InstantPlayResolver()
    try:
        stream = resolver.resolve_movie(imdb_id)
    except InstantPlayUnavailable as error:
        LOGGER.warning("Instant movie playback is not ready for %s: %s", imdb_id, error)
        return jsonify({"error": str(error)}), 503
    except (ET.ParseError, OSError, RuntimeError, httpx.HTTPError, ValueError):
        LOGGER.exception("Instant movie playback failed for %s", imdb_id)
        return jsonify({"error": "Instant playback could not be prepared"}), 502
    finally:
        resolver.close()

    expires = int(time.time()) + 6 * 60 * 60
    authorization = {
        "index": str(stream["file_id"]),
        "file_id": str(stream["file_id"]),
        "expires": str(expires),
        "signature": _instant_play_signature(stream["hash"], expires, stream["file_id"]),
    }
    stream_url = (
        f"/api/media/instant-play/hls/{stream['hash']}/master.m3u8?"
        f"{urlencode(authorization)}"
    )
    return jsonify({
        "url": stream_url,
        "id": imdb_id.lower(),
        "type": "movie",
    })


@media_bp.route(
    "/api/media/instant-play/hls/<torrent_hash>/<path:resource>",
    methods=["GET", "HEAD"],
)
def instant_play_hls(torrent_hash, resource):
    from home_os.services.instant_stream import (
        TORRSERVER_URL,
        mark_stream_active,
        rewrite_hls_playlist,
    )

    if not _instant_play_stream_authorized(torrent_hash):
        abort(403)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", torrent_hash):
        abort(404)
    if not (
        resource in {"master.m3u8", "init.mp4", "heartbeat"}
        or re.fullmatch(r"seg/[0-9]+\.m4s", resource)
    ):
        abort(404)
    if resource == "master.m3u8" and request.args.get("index") != request.args.get("file_id"):
        abort(403)

    allowed_parameters = {
        key: value
        for key, value in request.args.items()
        if key in {"index", "id", "fileID", "audio", "seconds"}
    }
    upstream_headers = {}
    if request.headers.get("Range"):
        upstream_headers["Range"] = request.headers["Range"]

    client = httpx.Client(timeout=httpx.Timeout(120, read=None), follow_redirects=False)
    try:
        upstream_request = client.build_request(
            "GET",
            f"{TORRSERVER_URL}/gst/{torrent_hash.lower()}/{resource}",
            params=allowed_parameters,
            headers=upstream_headers,
        )
        upstream = client.send(upstream_request, stream=True)
    except httpx.HTTPError:
        client.close()
        return Response("The local stream is unavailable", status=502)

    response_headers = {}
    for header in (
        "Content-Type",
        "Cache-Control",
        "Accept-Ranges",
        "Content-Range",
        "ETag",
        "Last-Modified",
    ):
        if header in upstream.headers:
            response_headers[header] = upstream.headers[header]

    if request.method == "HEAD":
        status_code = upstream.status_code
        upstream.close()
        client.close()
        return Response(status=status_code, headers=response_headers)

    if resource == "master.m3u8" and upstream.status_code < 400:
        try:
            playlist = upstream.read().decode("utf-8")
        finally:
            upstream.close()
            client.close()
        mark_stream_active(INSTANT_STREAM_STATE, torrent_hash.lower())
        base_path = request.path.rsplit("/", 1)[0]
        content = rewrite_hls_playlist(
            playlist,
            base_path,
            {
                "file_id": request.args["file_id"],
                "expires": request.args["expires"],
                "signature": request.args["signature"],
            },
        )
        return Response(
            content,
            status=200,
            headers=response_headers,
            content_type="application/vnd.apple.mpegurl",
        )

    def generate():
        try:
            yield from upstream.iter_bytes(chunk_size=64 * 1024)
        finally:
            upstream.close()
            client.close()

    return Response(
        stream_with_context(generate()),
        status=upstream.status_code,
        headers=response_headers,
    )


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


# === Auto-Delete Watched Media ===

@media_bp.route("/api/media/autodelete/config")
@fresh_session_required
def autodelete_config():
    """Get auto-delete configuration."""
    from home_os.models.settings import Setting
    config = {
        "enabled": Setting.get("autodelete_enabled", "false") == "true",
        "delay_hours": int(Setting.get("autodelete_delay_hours", "24")),
        "threshold": int(Setting.get("autodelete_threshold", "85")),
        "movies": Setting.get("autodelete_movies", "true") == "true",
        "tv": Setting.get("autodelete_tv", "true") == "true",
        "plex_token_configured": bool(Setting.get("autodelete_plex_token", "")),
        "sonarr_api_key_configured": bool(Setting.get("autodelete_sonarr_key", "")),
        "radarr_api_key_configured": bool(Setting.get("autodelete_radarr_key", "")),
    }
    return jsonify({"ok": True, "data": config})


@media_bp.route("/api/media/autodelete/config", methods=["POST"])
@fresh_session_required
def autodelete_save_config():
    """Save auto-delete configuration."""
    from home_os.models.settings import Setting
    data = request.get_json(silent=True) or {}
    try:
        delay_hours = int(data.get("delay_hours", 24))
        threshold = int(data.get("threshold", 85))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Delay and threshold must be numbers"}), 400
    if not 1 <= delay_hours <= 8760:
        return jsonify({"ok": False, "error": "Delay must be between 1 and 8760 hours"}), 400
    if not 50 <= threshold <= 100:
        return jsonify({"ok": False, "error": "Threshold must be between 50 and 100 percent"}), 400
    existing_plex_token = Setting.get("autodelete_plex_token", "")
    plex_token = str(data.get("plex_token", "")).strip() or existing_plex_token
    sonarr_key = str(data.get("sonarr_api_key", "")).strip() or Setting.get("autodelete_sonarr_key", "")
    radarr_key = str(data.get("radarr_api_key", "")).strip() or Setting.get("autodelete_radarr_key", "")
    if data.get("enabled") and not plex_token:
        return jsonify({"ok": False, "error": "A Plex token is required"}), 400

    fields = {
        "autodelete_enabled": "true" if data.get("enabled") else "false",
        "autodelete_delay_hours": str(delay_hours),
        "autodelete_threshold": str(threshold),
        "autodelete_movies": "true" if data.get("movies") else "false",
        "autodelete_tv": "true" if data.get("tv") else "false",
        "autodelete_plex_token": plex_token,
        "autodelete_sonarr_key": sonarr_key,
        "autodelete_radarr_key": radarr_key,
        # Also write keys that the cleanup service reads directly
        "plex_token": plex_token,
        "sonarr_api_key": sonarr_key,
        "radarr_api_key": radarr_key,
    }

    for key, value in fields.items():
        Setting.set(key, value)

    # Manage systemd timer based on enabled state
    enabled = data.get("enabled", False)
    if not _manage_autodelete_timer(enabled):
        return jsonify({"ok": False, "error": "Could not update the cleanup timer"}), 500

    return jsonify({"ok": True})


@media_bp.route("/api/media/autodelete/status")
@admin_required
def autodelete_status():
    """Get current auto-delete status (pending items, recent deletions)."""
    from home_os.models.settings import Setting
    from home_os.services.media_cleanup import get_cleanup_status
    try:
        config = {
            "enabled": Setting.get("autodelete_enabled", "false") == "true",
            "plex_url": "http://localhost:32400",
            "plex_token": Setting.get("autodelete_plex_token", ""),
            "delay_hours": int(Setting.get("autodelete_delay_hours", "24")),
            "threshold": int(Setting.get("autodelete_threshold", "85")),
            "movies": Setting.get("autodelete_movies", "true") == "true",
            "tv": Setting.get("autodelete_tv", "true") == "true",
            "sonarr_url": "http://localhost:8989",
            "sonarr_api_key": Setting.get("autodelete_sonarr_key", ""),
            "radarr_url": "http://localhost:7878",
            "radarr_api_key": Setting.get("autodelete_radarr_key", ""),
            "state_file": "/opt/home-os/data/autodelete_state.json",
        }
        status = get_cleanup_status(config)
        return jsonify({"ok": True, "data": status})
    except Exception as e:
        return jsonify({"ok": True, "data": {"pending": [], "recent_deletions": [], "error": str(e)}})


@media_bp.route("/api/media/autodelete/run", methods=["POST"])
@admin_required
def autodelete_run_now():
    """Manually trigger a cleanup cycle."""
    from home_os.services.media_cleanup import run_cleanup_cycle
    try:
        result = run_cleanup_cycle()
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@media_bp.route("/api/media/autodelete/test", methods=["POST"])
@admin_required
def autodelete_test_connection():
    """Test connectivity to Plex, Sonarr, or Radarr."""
    import httpx

    data = request.get_json()
    service = data.get("service", "")
    token_or_key = data.get("key", "")

    if not token_or_key:
        return jsonify({"ok": False, "error": "No key provided"}), 400

    try:
        if service == "plex":
            port = _get_port("plex") if _service_installed("plex") else 32400
            resp = httpx.get(
                f"http://localhost:{port}/",
                headers={"X-Plex-Token": token_or_key, "Accept": "application/json"},
                timeout=5,
            )
            if resp.status_code == 200:
                name = resp.json().get("MediaContainer", {}).get("friendlyName", "Plex")
                return jsonify({"ok": True, "data": {"message": f"Connected to {name}"}})
            elif resp.status_code == 401:
                return jsonify({"ok": False, "error": "Invalid token"}), 401
            else:
                return jsonify({"ok": False, "error": f"HTTP {resp.status_code}"}), 502

        elif service == "sonarr":
            port = _get_port("sonarr") if _service_installed("sonarr") else 8989
            resp = httpx.get(
                f"http://localhost:{port}/api/v3/system/status",
                headers={"X-Api-Key": token_or_key},
                timeout=5,
            )
            if resp.status_code == 200:
                ver = resp.json().get("version", "unknown")
                return jsonify({"ok": True, "data": {"message": f"Sonarr v{ver}"}})
            elif resp.status_code == 401:
                return jsonify({"ok": False, "error": "Invalid API key"}), 401
            else:
                return jsonify({"ok": False, "error": f"HTTP {resp.status_code}"}), 502

        elif service == "radarr":
            port = _get_port("radarr") if _service_installed("radarr") else 7878
            resp = httpx.get(
                f"http://localhost:{port}/api/v3/system/status",
                headers={"X-Api-Key": token_or_key},
                timeout=5,
            )
            if resp.status_code == 200:
                ver = resp.json().get("version", "unknown")
                return jsonify({"ok": True, "data": {"message": f"Radarr v{ver}"}})
            elif resp.status_code == 401:
                return jsonify({"ok": False, "error": "Invalid API key"}), 401
            else:
                return jsonify({"ok": False, "error": f"HTTP {resp.status_code}"}), 502

        else:
            return jsonify({"ok": False, "error": "Unknown service"}), 400

    except httpx.ConnectError:
        return jsonify({"ok": False, "error": f"{service.title()} not reachable"}), 502
    except httpx.TimeoutException:
        return jsonify({"ok": False, "error": "Connection timed out"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _manage_autodelete_timer(enabled):
    """Create/enable or stop/disable the systemd timer for auto-delete."""
    from flask import current_app

    service_unit = """[Unit]
Description=Home OS Media Auto-Delete
After=network.target plexmediaserver.service sonarr.service radarr.service

[Service]
Type=oneshot
WorkingDirectory=/opt/home-os
ExecStart=/opt/home-os/app/venv/bin/python -c "import sys; sys.path.insert(0, '/opt/home-os/app'); from home_os.services.media_cleanup import run_cleanup_cycle; run_cleanup_cycle()"
User=root
UMask=0002
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/home-os/data /opt/home-os/storage
"""

    timer_unit = """[Unit]
Description=Home OS Media Auto-Delete Timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
"""

    if enabled:
        # Write unit files and enable timer
        script = f"""
cat > /etc/systemd/system/home-os-autodelete.service << 'UNIT'
{service_unit}UNIT

cat > /etc/systemd/system/home-os-autodelete.timer << 'UNIT'
{timer_unit}UNIT

systemctl daemon-reload
systemctl enable home-os-autodelete.timer
systemctl start home-os-autodelete.timer
"""
    else:
        script = """
systemctl stop home-os-autodelete.timer 2>/dev/null
systemctl disable home-os-autodelete.timer 2>/dev/null
rm -f /etc/systemd/system/home-os-autodelete.service
rm -f /etc/systemd/system/home-os-autodelete.timer
systemctl daemon-reload
"""

    try:
        result = run_privileged(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            current_app.logger.error("Failed to update auto-delete timer: %s", result.stderr[-500:])
            return False
        return True
    except Exception as exc:
        current_app.logger.error("Failed to update auto-delete timer: %s", exc)
        return False


# --- Media service reverse proxies ---

PROXY_PREFIXES = {
    "qbt": "qbittorrent",
    "sonarr": "sonarr",
    "radarr": "radarr",
    "prowlarr": "prowlarr",
    "seerr": "overseerr",
    "jellyfin": "jellyfin",
}


@media_bp.route("/svc/<prefix>/", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@media_bp.route("/svc/<prefix>/<path:subpath>", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
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

    headers = {k: v for k, v in request.headers if k.lower() not in (
        "host", "cookie", "referer", "origin", "accept-encoding",
        "connection", "content-length", "proxy-connection", "te", "trailer",
        "transfer-encoding", "upgrade",
        "x-forwarded-for", "x-forwarded-proto", "x-real-ip",
        "cf-connecting-ip", "cf-ipcountry", "cf-ray", "cf-visitor",
    )}

    # Forward service-specific cookies (don't leak Home OS session to services)
    service_cookies = {
        "qbittorrent": "SID",
    }
    if service in service_cookies:
        cookie_name = service_cookies[service]
        cookie_val = request.cookies.get(cookie_name)
        if cookie_val:
            headers["Cookie"] = f"{cookie_name}={cookie_val}"

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

    excluded_headers = {"transfer-encoding", "connection", "content-encoding", "content-length"}
    response_headers = []
    for key, value in resp.headers.multi_items():
        if key.lower() in excluded_headers:
            continue
        if key.lower() == "set-cookie":
            value = re.sub(
                r"(?i)Path=/($|;)",
                rf"Path=/svc/{prefix}/\1",
                value,
            )
        response_headers.append((key, value))

    # Services that need path rewriting (no native UrlBase support)
    rewrite_services = ("plex", "qbittorrent", "overseerr")

    # Rewrite Location headers
    location = resp.headers.get("location")
    if location:
        loc = location
        base_url = f"http://localhost:{port}"
        if loc.startswith(base_url):
            loc = loc[len(base_url):]
        if service in rewrite_services and loc.startswith("/"):
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
    content_type = resp.headers.get("content-type", "")

    # Rewrite root-relative paths for services without UrlBase
    if service in rewrite_services:
        if "text/html" in content_type or "javascript" in content_type or "text/css" in content_type:
            import re as _re
            pp = f"/svc/{prefix}".encode()
            # Protect protocol-relative URLs
            content = content.replace(b"://", b":\x00//")
            # Rewrite href="/...", src="/...", and quoted string paths in JS
            content = _re.sub(rb'((?:href|src|action)\s*=\s*["\'])/(?!/)', lambda m: m.group(1) + pp + b"/", content)
            content = _re.sub(rb'(fetch\(\s*["\'])/(?!/)', lambda m: m.group(1) + pp + b"/", content)
            content = _re.sub(rb'("|\')/(api|_next|static|login|settings|discover|movie|tv|collection|request|issue|user|profile)/', lambda m: m.group(1) + pp + b"/" + m.group(2) + b"/", content)
            content = content.replace(b"url(/", b"url(" + pp + b"/")
            # Restore protocol-relative URLs
            content = content.replace(b":\x00//", b"://")

    return Response(content, status=resp.status_code, headers=response_headers)



# Keep legacy /qbt/ route working
@media_bp.route("/qbt/", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@media_bp.route("/qbt/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@admin_required
def qbt_proxy(subpath=""):
    return service_proxy("qbt", subpath)
