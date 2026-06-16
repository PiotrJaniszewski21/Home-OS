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
rm -f /etc/systemd/system/home-os.service
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
