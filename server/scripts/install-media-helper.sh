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

systemctl disable --now home-os-instant-stream.timer torrserver.service 2>/dev/null || true
rm -f \
    /etc/systemd/system/home-os-instant-stream.service \
    /etc/systemd/system/home-os-instant-stream.timer \
    /etc/systemd/system/torrserver.service \
    /usr/local/bin/torrserver
rm -rf \
    /opt/home-os/data/instant-stream \
    /var/lib/jellyfin/plugins/DynamicLibrary
rm -f /var/lib/jellyfin/plugins/configurations/Jellyfin.Plugin.DynamicLibrary.xml
find /opt/home-os/storage/HomeOS -type f -name 'HomeOS Instant - *.strm' -delete 2>/dev/null || true

rm -f \
    /etc/sudoers.d/homeos \
    /etc/polkit-1/rules.d/50-home-os-media.rules \
    /etc/systemd/system/home-os.service.d/media-helper.conf

systemctl daemon-reload
if systemctl is-active --quiet jellyfin.service; then
    systemctl restart jellyfin.service
fi
if [[ "$RESTART_HOME_OS" == true ]] && systemctl cat home-os.service >/dev/null 2>&1; then
    systemctl restart home-os
    systemctl is-active --quiet home-os
fi
echo "Home OS media installer helper is ready."
