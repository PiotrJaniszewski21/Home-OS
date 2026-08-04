#!/bin/bash
set -e

# Home OS Installer
# Run with: sudo bash install.sh [install_path]

INSTALL_DIR="${1:-/opt/home-os}"
USER="homeos"
GROUP="homeos"
MEDIA_GROUP="homeos-media"

echo "============================================"
echo "  Home OS Installer"
echo "============================================"
echo ""
echo "Install directory: $INSTALL_DIR"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run as root (sudo bash install.sh)"
    exit 1
fi

# Check OS
if ! command -v apt-get &>/dev/null; then
    echo "Error: This installer requires apt-get (Debian/Ubuntu)"
    exit 1
fi

# Check Python 3.10+
PYTHON=""
for p in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$p" &>/dev/null; then
        PYTHON="$p"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Installing Python 3.10+..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip
    PYTHON="python3"
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "Error: Home OS requires Python 3.10 or newer."
    exit 1
fi
echo "Using Python $PY_VERSION ($PYTHON)"

# Install system dependencies
echo ""
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3-venv \
    openssl \
    curl

# Keep a service account for qBittorrent and shared media access. The Home OS
# backend itself runs as root because it is the server administration plane.
echo "[2/7] Creating system user..."
if ! id "$USER" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/bash "$USER"
fi
groupadd --force "$MEDIA_GROUP"
usermod -aG "$MEDIA_GROUP" "$USER"

# Create directory structure
echo "[3/7] Creating directory structure..."
mkdir -p "$INSTALL_DIR"/{app,app/data,config/tls,data/trash,storage,logs}

# Get application code
echo "[4/7] Installing application..."
REPO_URL="https://github.com/PiotrJaniszewski21/Home-OS.git"

if [ -d "home_os" ]; then
    # Running from project root
    cp -r home_os "$INSTALL_DIR/app/"
    cp requirements.txt "$INSTALL_DIR/app/"
    mkdir -p "$INSTALL_DIR/app/config"
    cp \
        config/home-os-music-cache.service \
        config/home-os-music-cache.timer \
        "$INSTALL_DIR/app/config/"
    mkdir -p "$INSTALL_DIR/app/scripts"
    cp \
        scripts/home-os-media-helper \
        scripts/install-media-helper.sh \
        scripts/configure-root-backend.sh \
        "$INSTALL_DIR/app/scripts/"
else
    # Clone from GitHub
    apt-get install -y -qq git
    TMPDIR=$(mktemp -d)
    git clone --depth 1 "$REPO_URL" "$TMPDIR"
    cp -r "$TMPDIR/server/home_os" "$INSTALL_DIR/app/"
    cp "$TMPDIR/server/requirements.txt" "$INSTALL_DIR/app/"
    mkdir -p "$INSTALL_DIR/app/config"
    cp \
        "$TMPDIR/server/config/home-os-music-cache.service" \
        "$TMPDIR/server/config/home-os-music-cache.timer" \
        "$INSTALL_DIR/app/config/"
    mkdir -p "$INSTALL_DIR/app/scripts"
    cp \
        "$TMPDIR/server/scripts/home-os-media-helper" \
        "$TMPDIR/server/scripts/install-media-helper.sh" \
        "$TMPDIR/server/scripts/configure-root-backend.sh" \
        "$INSTALL_DIR/app/scripts/"
    rm -rf "$TMPDIR"
fi

chmod 0755 \
    "$INSTALL_DIR/app/scripts/home-os-media-helper" \
    "$INSTALL_DIR/app/scripts/install-media-helper.sh" \
    "$INSTALL_DIR/app/scripts/configure-root-backend.sh"
bash "$INSTALL_DIR/app/scripts/install-media-helper.sh" --no-restart

# Create virtual environment and install deps
echo "[5/7] Setting up Python environment..."
$PYTHON -m venv "$INSTALL_DIR/app/venv"
"$INSTALL_DIR/app/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/app/venv/bin/pip" install --quiet -r "$INSTALL_DIR/app/requirements.txt"

# Generate TLS certificate
echo "[6/7] Generating TLS certificate..."
if [ ! -f "$INSTALL_DIR/config/tls/cert.pem" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$INSTALL_DIR/config/tls/key.pem" \
        -out "$INSTALL_DIR/config/tls/cert.pem" \
        -days 3650 \
        -subj "/CN=home-os/O=HomeOS/C=US" \
        2>/dev/null
fi

# Generate secret key
if [ ! -f "$INSTALL_DIR/config/secret.key" ]; then
    openssl rand -hex 32 > "$INSTALL_DIR/config/secret.key"
    chmod 600 "$INSTALL_DIR/config/secret.key"
fi

# Create the installed config once; upgrades preserve all user settings.
if [ ! -f "$INSTALL_DIR/config/config.yaml" ]; then
    cat > "$INSTALL_DIR/config/config.yaml" << EOF
server:
  host: 0.0.0.0
  port: 443
  debug: false
  secret_key_file: $INSTALL_DIR/config/secret.key

database:
  path: $INSTALL_DIR/data/home_os.db

storage:
  root: $INSTALL_DIR/storage
  trash_path: $INSTALL_DIR/data/trash
  trash_retention_days: 30
EOF
fi

# Create systemd service
echo "[7/7] Creating systemd service..."
cat > /etc/systemd/system/home-os.service << EOF
[Unit]
Description=Home OS
After=network.target

[Service]
Type=exec
User=root
Group=root
WorkingDirectory=$INSTALL_DIR/app
Environment=HOME_OS_CONFIG=$INSTALL_DIR/config/config.yaml
Environment=HOME_OS_MUSIC_CACHE_DIR=/run/home-os/music-streams
Environment=HOME_OS_MUSIC_FEED_CACHE_DIR=/run/home-os/music-feeds
Environment=HOME_OS_MUSIC_GENRE_CACHE_DIR=/var/cache/home-os/music-genres
Environment=HOME_OS_MUSIC_METADATA_CACHE_DIR=/var/cache/home-os/music-metadata
Environment=HOME_OS_YTDLP_CACHE_DIR=/var/cache/home-os/yt-dlp
Environment=HOME_OS_MUSIC_AUDIO_CACHE_DIR=/var/cache/home-os/music-audio
RuntimeDirectory=home-os
RuntimeDirectoryMode=0700
CacheDirectory=home-os
CacheDirectoryMode=0700
ExecStart=$INSTALL_DIR/app/venv/bin/gunicorn \\
    --bind [::]:443 \\
    --workers 3 \\
    --worker-class gevent \\
    --timeout 120 \\
    --graceful-timeout 30 \\
    --no-control-socket \\
    --certfile $INSTALL_DIR/config/tls/cert.pem \\
    --keyfile $INSTALL_DIR/config/tls/key.pem \\
    --access-logfile $INSTALL_DIR/logs/access.log \\
    --error-logfile $INSTALL_DIR/logs/error.log \\
    "home_os.app:create_app()"
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=false
PrivateTmp=false
PrivateDevices=false
ProtectSystem=false
ProtectHome=false
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=false
RestrictSUIDSGID=false
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/home-os-http-redirect.service << EOF
[Unit]
Description=Home OS HTTP to HTTPS Redirect
After=network.target
Before=home-os.service

[Service]
Type=simple
User=$USER
Group=$GROUP
WorkingDirectory=$INSTALL_DIR/app
ExecStart=$INSTALL_DIR/app/venv/bin/python -m home_os.http_redirect
Restart=always
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
RestrictAddressFamilies=AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
EOF

sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    "$INSTALL_DIR/app/config/home-os-music-cache.service" \
    > /etc/systemd/system/home-os-music-cache.service
cp "$INSTALL_DIR/app/config/home-os-music-cache.timer" \
    /etc/systemd/system/home-os-music-cache.timer

# Remove the retired media auto-delete feature from upgraded installations.
systemctl stop home-os-autodelete.timer home-os-autodelete.service 2>/dev/null || true
systemctl disable home-os-autodelete.timer 2>/dev/null || true
rm -f \
    /etc/systemd/system/home-os-autodelete.timer \
    /etc/systemd/system/home-os-autodelete.service \
    /etc/systemd/system/home-os-autodelete.service.d/override.conf
rmdir /etc/systemd/system/home-os-autodelete.service.d 2>/dev/null || true
rm -f "$INSTALL_DIR/data"/autodelete_state*.json*
if [ -f "$INSTALL_DIR/data/home_os.db" ]; then
    "$INSTALL_DIR/app/venv/bin/python" - "$INSTALL_DIR/data/home_os.db" <<'PY'
import sqlite3
import sys

database = sqlite3.connect(sys.argv[1])
with database:
    database.execute("DELETE FROM settings WHERE key LIKE 'autodelete_%'")
    database.execute(
        "DELETE FROM settings WHERE key IN (?, ?, ?)",
        ("plex_token", "sonarr_api_key", "radarr_api_key"),
    )
database.close()
PY
fi

# Set permissions
chown -R root:root "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/config"
chmod 700 "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/app/data"
mkdir -p "$INSTALL_DIR/storage/HomeOS"
chown root:"$MEDIA_GROUP" "$INSTALL_DIR/storage"
chown root:"$MEDIA_GROUP" "$INSTALL_DIR/storage/HomeOS"
chmod 2750 "$INSTALL_DIR/storage"
chmod 2770 "$INSTALL_DIR/storage/HomeOS"
chmod 600 "$INSTALL_DIR/config/config.yaml"
chmod 600 "$INSTALL_DIR/config/secret.key"
chmod 600 "$INSTALL_DIR/config/tls/key.pem"

if [ -f /etc/samba/smb.conf ]; then
    sed -i "\|include = $INSTALL_DIR/config/smb_shares.conf|d" /etc/samba/smb.conf
fi
rm -f "$INSTALL_DIR/config/smb_shares.conf"

# Enable and start
systemctl daemon-reload
systemctl enable home-os home-os-http-redirect home-os-music-cache.timer
systemctl start home-os home-os-http-redirect
systemctl start home-os-music-cache.timer

echo ""
echo "============================================"
echo "  Home OS installed successfully!"
echo "============================================"
echo ""
echo "  URL:  https://$(hostname -I | awk '{print $1}')"
echo "  Dir:  $INSTALL_DIR"
echo ""
echo "  Open the URL to create your admin account."
echo ""
echo "  Commands:"
echo "    systemctl status home-os"
echo "    systemctl restart home-os"
echo "    journalctl -u home-os -f"
echo ""
