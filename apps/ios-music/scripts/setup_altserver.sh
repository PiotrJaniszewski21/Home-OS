#!/usr/bin/env bash
# setup_altserver.sh — Install AltStore server stack on 192.168.0.8
set -euo pipefail

SSH_HOST="homeos"
SUDO_PASS="DoryStinks"
IPA_DIR="/opt/altstore"

rsudo() { ssh "$SSH_HOST" "echo '$SUDO_PASS' | sudo -S $*"; }
remote() { ssh "$SSH_HOST" "$@"; }

echo "=== [1/6] Installing apt dependencies ==="
rsudo apt-get update -qq
rsudo apt-get install -y --no-install-recommends usbmuxd libimobiledevice6 libimobiledevice-utils avahi-daemon curl wget ca-certificates docker.io

echo "=== [2/6] Starting Docker and pulling anisette-v3-server ==="
rsudo systemctl enable --now docker
rsudo docker pull dadoum/anisette-v3-server:latest

echo "=== [3/6] Starting anisette-v3-server container ==="
rsudo docker rm -f anisette-v3 2>/dev/null || true
rsudo docker run -d --restart=always --name anisette-v3 -p 6969:6969 --volume anisette-v3_data:/home/Alcoholic/.config/anisette-v3/lib/ dadoum/anisette-v3-server:latest

echo "=== [4/6] Installing netmuxd ==="
rsudo curl -fsSL https://github.com/jkcoxson/netmuxd/releases/latest/download/netmuxd-x86_64-unknown-linux-musl -o /usr/local/bin/netmuxd
rsudo chmod +x /usr/local/bin/netmuxd

ssh "$SSH_HOST" "echo '$SUDO_PASS' | sudo -S tee /etc/systemd/system/netmuxd.service" << 'UNIT_EOF'
[Unit]
Description=netmuxd - network muxer for iOS devices
After=network.target

[Service]
ExecStart=/usr/local/bin/netmuxd
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT_EOF

rsudo systemctl daemon-reload
rsudo systemctl enable --now netmuxd

echo "=== [5/6] Installing AltServer-Linux ==="
rsudo curl -fsSL https://github.com/NyaMisty/AltServer-Linux/releases/latest/download/AltServer-x86_64 -o /usr/local/bin/AltServer
rsudo chmod +x /usr/local/bin/AltServer
rsudo mkdir -p "$IPA_DIR"
rsudo chmod 755 "$IPA_DIR"

ssh "$SSH_HOST" "echo '$SUDO_PASS' | sudo -S tee /etc/systemd/system/altserver.service" << 'UNIT_EOF'
[Unit]
Description=AltServer-Linux
After=network.target netmuxd.service docker.service

[Service]
Environment=ALTSERVER_ANISETTE_SERVER=http://127.0.0.1:6969
ExecStart=/usr/local/bin/AltServer
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT_EOF

rsudo systemctl daemon-reload
rsudo systemctl enable --now altserver

echo "=== [6/6] Verifying ==="
sleep 4
remote systemctl is-active netmuxd altserver
remote curl -s http://localhost:6969 | head -c 80

echo ""
echo "✅  AltStore server stack is running!"
echo ""
echo "━━━━ NEXT STEPS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Install AltStore on iPhone (Mac AltServer → Install AltStore)"
echo "2. In AltStore → Settings → Anisette Server: http://192.168.0.8:6969"
echo "3. Connect iPhone to server via USB once, tap Trust"
echo "4. Push IPA: bash apps/ios-music/scripts/push_ipa.sh"
echo "5. In AltStore → '+' → sideload IPA (auto-refreshes every 7 days)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
