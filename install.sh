#!/bin/bash
set -e

# Home OS Installer
# Run with: sudo bash install.sh [install_path]

INSTALL_DIR="${1:-/opt/home-os}"
USER="homeos"
GROUP="homeos"

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

# Check Python 3.9+
PYTHON=""
for p in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$p" &>/dev/null; then
        PYTHON="$p"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Installing Python 3..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip
    PYTHON="python3"
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Using Python $PY_VERSION ($PYTHON)"

# Install system dependencies
echo ""
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3-venv \
    samba \
    openssl \
    curl

# Create system user with passwordless sudo
echo "[2/7] Creating system user..."
if ! id "$USER" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/bash "$USER"
fi

# Grant passwordless sudo to homeos user
echo "$USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/homeos
chmod 440 /etc/sudoers.d/homeos

# Create directory structure
echo "[3/7] Creating directory structure..."
mkdir -p "$INSTALL_DIR"/{app,config/tls,data/trash,storage,logs}

# Get application code
echo "[4/7] Installing application..."
REPO_URL="https://github.com/PiotrJaniszewski21/Home-OS.git"

if [ -d "home_os" ]; then
    # Running from project root
    cp -r home_os "$INSTALL_DIR/app/"
    cp requirements.txt "$INSTALL_DIR/app/"
    cp config.example.yaml "$INSTALL_DIR/config/config.yaml"
else
    # Clone from GitHub
    apt-get install -y -qq git
    TMPDIR=$(mktemp -d)
    git clone --depth 1 "$REPO_URL" "$TMPDIR"
    cp -r "$TMPDIR/home_os" "$INSTALL_DIR/app/"
    cp "$TMPDIR/requirements.txt" "$INSTALL_DIR/app/"
    cp "$TMPDIR/config.example.yaml" "$INSTALL_DIR/config/config.yaml"
    rm -rf "$TMPDIR"
fi

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

# Update config to use installed paths
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

ai:
  provider: ""
  ollama:
    url: http://localhost:11434
    model: llama3
  claude:
    api_key: ""
    model: claude-sonnet-4-6-20250514
  openai:
    api_key: ""
    model: gpt-4o
  bedrock:
    access_key: ""
    secret_key: ""
    region: us-east-1
    model: anthropic.claude-sonnet-4-6-20250514-v1:0
EOF

# Create systemd service
echo "[7/7] Creating systemd service..."
cat > /etc/systemd/system/home-os.service << EOF
[Unit]
Description=Home OS
After=network.target

[Service]
Type=exec
User=$USER
Group=$GROUP
WorkingDirectory=$INSTALL_DIR/app
Environment=HOME_OS_CONFIG=$INSTALL_DIR/config/config.yaml
ExecStart=$INSTALL_DIR/app/venv/bin/gunicorn \\
    --bind 0.0.0.0:443 \\
    --workers 3 \\
    --certfile $INSTALL_DIR/config/tls/cert.pem \\
    --keyfile $INSTALL_DIR/config/tls/key.pem \\
    --access-logfile $INSTALL_DIR/logs/access.log \\
    --error-logfile $INSTALL_DIR/logs/error.log \\
    "home_os.app:create_app()"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Set permissions
chown -R "$USER:$GROUP" "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/config"
chmod 600 "$INSTALL_DIR/config/secret.key"
chmod 600 "$INSTALL_DIR/config/tls/key.pem"

# Enable and start
systemctl daemon-reload
systemctl enable home-os
systemctl start home-os

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
