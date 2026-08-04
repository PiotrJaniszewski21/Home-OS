#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run this script as root: sudo bash $0" >&2
    exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_SOURCE="$SOURCE_DIR/home-os-media-helper"
RESTART_HOME_OS=true
if [[ "${1:-}" == "--no-restart" ]]; then
    RESTART_HOME_OS=false
elif [[ $# -gt 0 ]]; then
    echo "Usage: sudo bash $0 [--no-restart]" >&2
    exit 1
fi

if [[ ! -f "$HELPER_SOURCE" ]]; then
    echo "Missing helper: $HELPER_SOURCE" >&2
    exit 1
fi

install -D -o root -g root -m 0755 "$HELPER_SOURCE" /usr/local/libexec/home-os-media-helper

cat > /etc/systemd/system/home-os-media-helper@.service <<'UNIT'
[Unit]
Description=Home OS allowlisted media action (%i)

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/home-os-media-helper %i
User=root
Group=root
UMask=0022
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=false
UNIT

cat > /etc/systemd/system/torrserver.service <<'UNIT'
[Unit]
Description=TorrServer local torrent streaming engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=homeos
Group=homeos-media
ExecStart=/usr/local/bin/torrserver -p 8090 -i 127.0.0.1 -d /opt/home-os/data/instant-stream/torrserver
Restart=on-failure
RestartSec=3
UMask=0007
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/home-os/data/instant-stream
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/home-os-instant-stream.service <<'UNIT'
[Unit]
Description=Refresh Home OS instant Jellyfin streams
After=network-online.target torrserver.service qbittorrent-nox@homeos.service
Wants=network-online.target

[Service]
Type=oneshot
User=root
Group=homeos-media
WorkingDirectory=/opt/home-os/app
Environment=HOME_OS_CONFIG=/opt/home-os/config/config.yaml
Environment=HOME_OS_INSTANT_STREAM_STATE=/opt/home-os/data/instant-stream/instant_streams.json
ExecStart=/opt/home-os/app/venv/bin/python -m home_os.services.instant_stream
UMask=0007
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/home-os/data/instant-stream /opt/home-os/storage/HomeOS/Movies /opt/home-os/storage/HomeOS/Series
CapabilityBoundingSet=
RestrictSUIDSGID=true
LockPersonality=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UNIT

cat > /etc/systemd/system/home-os-instant-stream.timer <<'UNIT'
[Unit]
Description=Poll Arr downloads for instant Jellyfin streaming

[Timer]
OnBootSec=45s
OnUnitActiveSec=30s
AccuracySec=5s
Persistent=true
Unit=home-os-instant-stream.service

[Install]
WantedBy=timers.target
UNIT

rm -f \
    /etc/sudoers.d/homeos \
    /etc/polkit-1/rules.d/50-home-os-media.rules \
    /etc/systemd/system/home-os.service.d/media-helper.conf

systemctl daemon-reload
if [[ "$RESTART_HOME_OS" == true ]] && systemctl cat home-os.service >/dev/null 2>&1; then
    systemctl restart home-os
    systemctl is-active --quiet home-os
fi
echo "Home OS media installer helper is ready."
