#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/home-os-1337x-bridge"
SERVICE="home-os-1337x-bridge.service"
FILES=(app.py core.py uindex_core.py tgx_core.py)

if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]] ||
    ! systemctl cat "$SERVICE" >/dev/null 2>&1; then
    echo "Bridge is not installed; run install.sh first." >&2
    exit 1
fi

for file in "${FILES[@]}"; do
    if [[ ! -f "$SCRIPT_DIR/$file" ]]; then
        echo "Missing source file: $SCRIPT_DIR/$file" >&2
        exit 1
    fi
done

"$INSTALL_DIR/venv/bin/python" -m py_compile \
    "${FILES[@]/#/$SCRIPT_DIR/}"

BACKUP_DIR="$(mktemp -d /run/home-os-1337x-bridge-update.XXXXXX)"
for file in "${FILES[@]}"; do
    if [[ -f "$INSTALL_DIR/$file" ]]; then
        cp -a "$INSTALL_DIR/$file" "$BACKUP_DIR/$file"
    fi
done

rollback() {
    local status=$?
    if [[ "$status" -eq 0 ]]; then
        rm -rf "$BACKUP_DIR"
        return
    fi
    echo "Bridge update failed; restoring the previous files." >&2
    for file in "${FILES[@]}"; do
        if [[ -f "$BACKUP_DIR/$file" ]]; then
            install -o root -g root -m 0644 \
                "$BACKUP_DIR/$file" "$INSTALL_DIR/$file"
        else
            rm -f "$INSTALL_DIR/$file"
        fi
    done
    systemctl restart "$SERVICE" || true
    rm -rf "$BACKUP_DIR"
    exit "$status"
}
trap rollback EXIT

for file in "${FILES[@]}"; do
    install -o root -g root -m 0644 \
        "$SCRIPT_DIR/$file" "$INSTALL_DIR/$file"
done

"$INSTALL_DIR/venv/bin/python" -m py_compile \
    "${FILES[@]/#/$INSTALL_DIR/}"
systemctl restart "$SERVICE"

for attempt in {1..90}; do
    if curl -fsS --max-time 3 http://127.0.0.1:8787/health >/dev/null; then
        echo "Bridge update completed successfully."
        exit 0
    fi
    sleep 2
done

systemctl status "$SERVICE" --no-pager >&2 || true
exit 1
