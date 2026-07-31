#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/home-os-1337x-bridge"
STATE_DIR="/var/lib/home-os-1337x-bridge"
CACHE_DIR="/var/cache/home-os-1337x-bridge"
ENV_FILE="/etc/home-os-1337x-bridge.env"
SERVICE="home-os-1337x-bridge.service"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates curl python3 python3-venv

id -u home-os-1337x >/dev/null 2>&1 ||
    useradd --system --create-home --home-dir "$STATE_DIR" \
        --shell /usr/sbin/nologin home-os-1337x

install -d -o root -g root -m 0755 "$INSTALL_DIR"
install -d -o home-os-1337x -g home-os-1337x -m 0700 "$STATE_DIR" "$CACHE_DIR"
install -o root -g root -m 0644 "$SCRIPT_DIR/app.py" "$INSTALL_DIR/app.py"
install -o root -g root -m 0644 "$SCRIPT_DIR/core.py" "$INSTALL_DIR/core.py"
install -o root -g root -m 0644 \
    "$SCRIPT_DIR/uindex_core.py" "$INSTALL_DIR/uindex_core.py"
install -o root -g root -m 0644 \
    "$SCRIPT_DIR/tgx_core.py" "$INSTALL_DIR/tgx_core.py"
install -o root -g root -m 0644 \
    "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --disable-pip-version-check --quiet \
    --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --disable-pip-version-check --quiet \
    -r "$INSTALL_DIR/requirements.txt"

for attempt in 1 2 3; do
    if HOME="$STATE_DIR" XDG_CACHE_HOME="$CACHE_DIR" \
        INVISIBLE_PLAYWRIGHT_CACHE_DIR="$CACHE_DIR/invisible-playwright" \
        "$INSTALL_DIR/venv/bin/python" -m invisible_playwright fetch; then
        break
    fi
    if [[ "$attempt" -eq 3 ]]; then
        echo "Invisible Playwright download failed after $attempt attempts" >&2
        exit 1
    fi
    echo "Invisible Playwright download failed; retrying ($attempt/3)" >&2
    sleep 5
done
"$INSTALL_DIR/venv/bin/playwright" install-deps firefox
chown -R home-os-1337x:home-os-1337x "$STATE_DIR" "$CACHE_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
    umask 077
    printf 'BRIDGE_API_KEY=%s\n' "$(openssl rand -hex 24)" > "$ENV_FILE"
fi

cat > "/etc/systemd/system/$SERVICE" <<'UNIT'
[Unit]
Description=Home OS 1337x browser-backed Torznab bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=home-os-1337x
Group=home-os-1337x
WorkingDirectory=/opt/home-os-1337x-bridge
EnvironmentFile=/etc/home-os-1337x-bridge.env
Environment=HOME=/var/lib/home-os-1337x-bridge
Environment=XDG_CACHE_HOME=/var/cache/home-os-1337x-bridge
Environment=INVISIBLE_PLAYWRIGHT_CACHE_DIR=/var/cache/home-os-1337x-bridge/invisible-playwright
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/home-os-1337x-bridge/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8787 --no-access-log
Restart=on-failure
RestartSec=5
TimeoutStartSec=180
TimeoutStopSec=30
MemoryMax=2500M
TasksMax=512
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/tmp /var/lib/home-os-1337x-bridge /var/cache/home-os-1337x-bridge

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

for attempt in {1..90}; do
    if curl -fsS --max-time 3 http://127.0.0.1:8787/health >/dev/null; then
        exit 0
    fi
    sleep 2
done

systemctl status "$SERVICE" --no-pager >&2 || true
exit 1
