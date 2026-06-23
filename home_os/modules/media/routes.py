import os
import subprocess

import httpx
from flask import Response, jsonify, render_template, request, stream_with_context
from flask_login import login_required

from home_os.modules.auth.routes import admin_required
from home_os.modules.media import media_bp


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
@admin_required
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
        result = subprocess.run(
            ["sudo", "bash", "-c", install_script],
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
    except Exception as e:
        return jsonify({"ok": False, "error": "Cannot connect to Plex. Is it running?"}), 503


@media_bp.route("/api/media/plex/update", methods=["POST"])
@admin_required
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
        result = subprocess.run(
            ["sudo", "bash", "-c", update_script],
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
@admin_required
def start_plex():
    """Start Plex Media Server service."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", "plexmediaserver"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to start"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to start Plex"}), 500


@media_bp.route("/api/media/plex/stop", methods=["POST"])
@admin_required
def stop_plex():
    """Stop Plex Media Server service."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", "plexmediaserver"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to stop"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to stop Plex"}), 500


@media_bp.route("/api/media/plex/uninstall", methods=["POST"])
@admin_required
def uninstall_plex():
    """Uninstall Plex Media Server."""
    if not _plex_installed():
        return jsonify({"ok": False, "error": "Plex is not installed"}), 404

    try:
        result = subprocess.run(
            ["sudo", "bash", "-c", "systemctl stop plexmediaserver 2>/dev/null; apt-get purge -y plexmediaserver"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr[-300:] or "Uninstall failed"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Uninstall failed"}), 500


# --- Generic *arr service endpoints ---

ARR_INSTALL_SCRIPTS = {
    "sonarr": """
set -e
apt-get install -y -qq curl sqlite3
curl -fsSL "https://services.sonarr.tv/v1/download/main/latest?version=4&os=linux&arch=x64" -o /tmp/sonarr.tar.gz
tar -xzf /tmp/sonarr.tar.gz -C /opt/
rm -f /tmp/sonarr.tar.gz
useradd -r -s /bin/false sonarr 2>/dev/null || true
chown -R sonarr:sonarr /opt/Sonarr
cat > /etc/systemd/system/sonarr.service << 'UNIT'
[Unit]
Description=Sonarr
After=network.target
[Service]
Type=simple
User=sonarr
Group=sonarr
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
chown -R radarr:radarr /opt/Radarr
cat > /etc/systemd/system/radarr.service << 'UNIT'
[Unit]
Description=Radarr
After=network.target
[Service]
Type=simple
User=radarr
Group=radarr
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


def _setup_media_folders(service):
    """Create storage folders and set permissions after install."""
    from flask import current_app
    from pathlib import Path

    config = current_app.config["_raw_config"]
    storage_root = config["storage"]["root"]
    homeos_dir = Path(storage_root) / "HomeOS"

    folder_map = {
        "sonarr": ("Series", "sonarr"),
        "radarr": ("Movies", "radarr"),
        "prowlarr": (None, "prowlarr"),
        "plex": ("Movies", "plex"),
        "qbittorrent": ("Downloads", "homeos"),
        "flaresolverr": (None, None),
        "overseerr": (None, None),
    }

    entry = folder_map.get(service)
    if not entry or not entry[0]:
        return

    folder_name, user = entry
    target = homeos_dir / folder_name
    target.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            ["sudo", "chown", "-R", f"{user}:{user}", str(target)],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["sudo", "chmod", "775", str(target)],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    # Also create Downloads folder for torrent clients
    if service in ("sonarr", "radarr", "qbittorrent"):
        downloads = homeos_dir / "Downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["sudo", "chmod", "777", str(downloads)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass


@media_bp.route("/api/media/<service>/install", methods=["POST"])
@admin_required
def arr_install(service):
    """Install a media service."""
    if service == "plex":
        return install_plex()
    if service not in ARR_INSTALL_SCRIPTS:
        return jsonify({"ok": False, "error": "Unknown service"}), 404
    if _service_installed(service):
        return jsonify({"ok": False, "error": f"{SERVICES[service]['name']} is already installed"}), 409

    try:
        timeout = 600 if service == "overseerr" else 300
        result = subprocess.run(
            ["sudo", "bash", "-c", ARR_INSTALL_SCRIPTS[service]],
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
@admin_required
def arr_start(service):
    """Start a media service."""
    if service == "plex":
        return start_plex()
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", SERVICES[service]["service"]],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to start"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to start"}), 500


@media_bp.route("/api/media/<service>/stop", methods=["POST"])
@admin_required
def arr_stop(service):
    """Stop a media service."""
    if service == "plex":
        return stop_plex()
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", SERVICES[service]["service"]],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or "Failed to stop"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Failed to stop"}), 500


@media_bp.route("/api/media/<service>/port", methods=["POST"])
@admin_required
def arr_save_port(service):
    """Save port configuration and update the actual service port."""
    if service not in SERVICES:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    import yaml
    from flask import current_app
    from home_os.config import get_config_path

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

    config_path = get_config_path()
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(config_path), suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
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
            subprocess.run(
                ["sudo", "bash", "-c",
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
            subprocess.run(
                ["sudo", "bash", "-c",
                 f"sed -i 's|<Port>{old_port}</Port>|<Port>{new_port}</Port>|' {config_file} && "
                 f"systemctl restart {SERVICES[service]['service']}"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    elif service == "overseerr":
        # Update systemd unit Environment=PORT and restart
        try:
            subprocess.run(
                ["sudo", "bash", "-c",
                 f"sed -i 's/Environment=PORT={old_port}/Environment=PORT={new_port}/' "
                 f"/etc/systemd/system/overseerr.service && "
                 f"systemctl daemon-reload && systemctl restart overseerr"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    elif service == "flaresolverr":
        try:
            subprocess.run(
                ["sudo", "bash", "-c",
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
@admin_required
def arr_uninstall(service):
    """Uninstall a media service."""
    if service == "plex":
        return uninstall_plex()
    if service not in ARR_UNINSTALL_SCRIPTS:
        return jsonify({"ok": False, "error": "Unknown service"}), 404
    if not _service_installed(service):
        return jsonify({"ok": False, "error": f"{SERVICES[service]['name']} is not installed"}), 404

    try:
        result = subprocess.run(
            ["sudo", "bash", "-c", ARR_UNINSTALL_SCRIPTS[service]],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr[-300:] or "Uninstall failed"}), 500
    except Exception:
        return jsonify({"ok": False, "error": "Uninstall failed"}), 500


# === Auto-Delete Watched Media ===

@media_bp.route("/api/media/autodelete/config")
@admin_required
def autodelete_config():
    """Get auto-delete configuration."""
    from home_os.models.settings import Setting
    config = {
        "enabled": Setting.get("autodelete_enabled", "false") == "true",
        "delay_hours": int(Setting.get("autodelete_delay_hours", "24")),
        "threshold": int(Setting.get("autodelete_threshold", "85")),
        "movies": Setting.get("autodelete_movies", "true") == "true",
        "tv": Setting.get("autodelete_tv", "true") == "true",
        "plex_token": Setting.get("autodelete_plex_token", ""),
        "sonarr_api_key": Setting.get("autodelete_sonarr_key", ""),
        "radarr_api_key": Setting.get("autodelete_radarr_key", ""),
    }
    return jsonify({"ok": True, "data": config})


@media_bp.route("/api/media/autodelete/config", methods=["POST"])
@admin_required
def autodelete_save_config():
    """Save auto-delete configuration."""
    from home_os.models.settings import Setting
    data = request.get_json()

    fields = {
        "autodelete_enabled": "true" if data.get("enabled") else "false",
        "autodelete_delay_hours": str(int(data.get("delay_hours", 24))),
        "autodelete_threshold": str(int(data.get("threshold", 85))),
        "autodelete_movies": "true" if data.get("movies") else "false",
        "autodelete_tv": "true" if data.get("tv") else "false",
        "autodelete_plex_token": data.get("plex_token", ""),
        "autodelete_sonarr_key": data.get("sonarr_api_key", ""),
        "autodelete_radarr_key": data.get("radarr_api_key", ""),
        # Also write keys that the cleanup service reads directly
        "plex_token": data.get("plex_token", ""),
        "sonarr_api_key": data.get("sonarr_api_key", ""),
        "radarr_api_key": data.get("radarr_api_key", ""),
    }

    for key, value in fields.items():
        Setting.set(key, value)

    # Manage systemd timer based on enabled state
    enabled = data.get("enabled", False)
    _manage_autodelete_timer(enabled)

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
    service_unit = """[Unit]
Description=Home OS Media Auto-Delete
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/opt/home-os
ExecStart=/opt/home-os/venv/bin/python -c "import sys; sys.path.insert(0, '/opt/home-os/app'); from home_os.services.media_cleanup import run_cleanup_cycle; run_cleanup_cycle()"
User=homeos
"""

    timer_unit = """[Unit]
Description=Home OS Media Auto-Delete Timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=1min

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
        subprocess.run(
            ["sudo", "bash", "-c", script],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


# --- Media service reverse proxies ---

PROXY_PREFIXES = {
    "qbt": "qbittorrent",
    "sonarr": "sonarr",
    "radarr": "radarr",
    "prowlarr": "prowlarr",
    "seerr": "overseerr",
}


@media_bp.route("/svc/<prefix>/", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@media_bp.route("/svc/<prefix>/<path:subpath>", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@admin_required
def service_proxy(prefix, subpath=""):
    from flask import current_app

    service = PROXY_PREFIXES.get(prefix)
    if not service:
        return "Unknown service", 404

    config = current_app.config.get("_raw_config", {})
    port = config.get("media", {}).get(f"{service}_port", SERVICES[service]["port"])

    # Services with UrlBase configured expect the full /svc/<prefix>/... path
    url_base_services = ("sonarr", "radarr", "prowlarr")
    if service in url_base_services:
        target = f"http://localhost:{port}/svc/{prefix}/{subpath}"
    else:
        target = f"http://localhost:{port}/{subpath}"

    if request.query_string:
        target += "?" + request.query_string.decode()

    headers = {k: v for k, v in request.headers if k.lower() not in ("host", "cookie", "referer", "origin", "accept-encoding")}

    # Forward service-specific cookies (don't leak Home OS session to services)
    service_cookies = {
        "qbittorrent": "SID",
    }
    if service in service_cookies:
        cookie_name = service_cookies[service]
        cookie_val = request.cookies.get(cookie_name)
        if cookie_val:
            headers["Cookie"] = f"{cookie_name}={cookie_val}"

    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            resp = client.request(
                method=request.method,
                url=target,
                headers=headers,
                content=request.get_data(),
            )
    except httpx.ConnectError:
        return f"{SERVICES[service]['name']} is not running", 502

    excluded_headers = {"transfer-encoding", "connection", "content-encoding", "content-length"}
    response_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in excluded_headers
    }

    # Services that need path rewriting (no native UrlBase support)
    rewrite_services = ("plex", "qbittorrent", "overseerr")

    # Rewrite Location headers
    if "location" in response_headers:
        loc = response_headers["location"]
        base_url = f"http://localhost:{port}"
        if loc.startswith(base_url):
            loc = loc[len(base_url):]
        if service in rewrite_services and loc.startswith("/"):
            loc = f"/svc/{prefix}{loc}"
        response_headers["location"] = loc

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


