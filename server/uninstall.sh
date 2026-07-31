#!/bin/bash
set -e

INSTALL_DIR="${1:-/opt/home-os}"

echo "============================================"
echo "  Home OS Uninstaller"
echo "============================================"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run as root (sudo bash uninstall.sh)"
    exit 1
fi

read -p "This will remove Home OS from $INSTALL_DIR. Continue? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo "Stopping service..."
systemctl stop home-os 2>/dev/null || true
systemctl disable home-os 2>/dev/null || true
systemctl disable --now home-os-http-redirect 2>/dev/null || true
systemctl disable --now home-os-autodelete.timer 2>/dev/null || true
systemctl stop home-os-autodelete.service 2>/dev/null || true
systemctl disable --now home-os-instant-stream.timer 2>/dev/null || true
systemctl disable --now torrserver.service 2>/dev/null || true
if [ -f /etc/samba/smb.conf ]; then
    sed -i "\|include = $INSTALL_DIR/config/smb_shares.conf|d" /etc/samba/smb.conf
fi
rm -f /etc/systemd/system/home-os.service
rm -f /etc/systemd/system/home-os-http-redirect.service
rm -f /etc/systemd/system/home-os-autodelete.service
rm -f /etc/systemd/system/home-os-autodelete.timer
rm -f /etc/systemd/system/home-os-autodelete.service.d/override.conf
rmdir /etc/systemd/system/home-os-autodelete.service.d 2>/dev/null || true
rm -f /etc/systemd/system/home-os-media-helper@.service
rm -f /etc/systemd/system/home-os-instant-stream.service
rm -f /etc/systemd/system/home-os-instant-stream.timer
rm -f /etc/systemd/system/torrserver.service
rm -f /etc/polkit-1/rules.d/50-home-os-media.rules
rm -f /etc/sudoers.d/homeos
rm -f /usr/local/libexec/home-os-media-helper
rm -f /usr/local/bin/torrserver
systemctl daemon-reload

read -p "Remove all data (storage files, database)? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo "Removed $INSTALL_DIR (all data deleted)"
else
    rm -rf "$INSTALL_DIR/app" "$INSTALL_DIR/config/tls" "$INSTALL_DIR/logs"
    echo "Removed app files. Data preserved at $INSTALL_DIR/data and $INSTALL_DIR/storage"
fi

echo ""
echo "Home OS uninstalled."
