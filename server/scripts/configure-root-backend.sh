#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${1:-/opt/home-os}"
MEDIA_GROUP="homeos-media"
SERVICE_USER="homeos"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run this script as root: sudo bash $0 [install_path]" >&2
    exit 1
fi

if [[ ! -x "$INSTALL_DIR/app/venv/bin/gunicorn" ]]; then
    echo "Home OS virtual environment not found under $INSTALL_DIR/app" >&2
    exit 1
fi

groupadd --force "$MEDIA_GROUP"
if id "$SERVICE_USER" >/dev/null 2>&1; then
    usermod -aG "$MEDIA_GROUP" "$SERVICE_USER"
fi

for service_user in plex jellyfin sonarr radarr; do
    if id "$service_user" >/dev/null 2>&1; then
        usermod -aG "$MEDIA_GROUP" "$service_user"
    fi
done

chown -R root:root \
    "$INSTALL_DIR/app" \
    "$INSTALL_DIR/config" \
    "$INSTALL_DIR/data" \
    "$INSTALL_DIR/logs"
chmod 700 "$INSTALL_DIR/config" "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/app/data"
chmod 600 "$INSTALL_DIR/config/config.yaml"
[[ ! -f "$INSTALL_DIR/config/secret.key" ]] || chmod 600 "$INSTALL_DIR/config/secret.key"
[[ ! -f "$INSTALL_DIR/config/tls/key.pem" ]] || chmod 600 "$INSTALL_DIR/config/tls/key.pem"

mkdir -p "$INSTALL_DIR/storage/HomeOS"
chown root:"$MEDIA_GROUP" "$INSTALL_DIR/storage"
chmod 2750 "$INSTALL_DIR/storage"
chown root:"$MEDIA_GROUP" "$INSTALL_DIR/storage/HomeOS"
chmod 2770 "$INSTALL_DIR/storage/HomeOS"

for folder_name in Movies Series Downloads; do
    media_path="$INSTALL_DIR/storage/HomeOS/$folder_name"
    mkdir -p "$media_path"
    chgrp -R "$MEDIA_GROUP" "$media_path"
    chmod -R g+rwX,o-rwx "$media_path"
    find "$media_path" -type d -exec chmod g+s {} +
done

if [[ -f /etc/samba/smb.conf ]]; then
    sed -i "\|include = $INSTALL_DIR/config/smb_shares.conf|d" /etc/samba/smb.conf
fi
rm -f "$INSTALL_DIR/config/smb_shares.conf"
sed -i '0,/^  port: 4443$/s//  port: 443/' "$INSTALL_DIR/config/config.yaml"

cat > /etc/systemd/system/home-os.service <<UNIT
[Unit]
Description=Home OS
After=network.target

[Service]
Type=exec
User=root
Group=root
WorkingDirectory=$INSTALL_DIR/app
Environment=HOME_OS_CONFIG=$INSTALL_DIR/config/config.yaml
ExecStart=$INSTALL_DIR/app/venv/bin/gunicorn --bind [::]:443 --workers 3 --worker-class gevent --no-control-socket --certfile $INSTALL_DIR/config/tls/cert.pem --keyfile $INSTALL_DIR/config/tls/key.pem --access-logfile $INSTALL_DIR/logs/access.log --error-logfile $INSTALL_DIR/logs/error.log home_os.app:create_app()
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
UNIT

cat > /etc/systemd/system/home-os-http-redirect.service <<UNIT
[Unit]
Description=Home OS HTTP to HTTPS Redirect
After=network.target
Before=home-os.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
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
UNIT

rm -f \
    /etc/sudoers.d/homeos \
    /etc/polkit-1/rules.d/50-home-os-media.rules \
    /etc/systemd/system/home-os.service.d/media-helper.conf

systemctl daemon-reload
systemctl enable home-os home-os-http-redirect
systemctl restart home-os
systemctl restart home-os-http-redirect
systemctl is-active --quiet home-os
systemctl is-active --quiet home-os-http-redirect

echo "Home OS root backend configured successfully."
