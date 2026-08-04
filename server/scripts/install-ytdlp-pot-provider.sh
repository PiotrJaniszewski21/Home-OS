#!/usr/bin/env bash
set -euo pipefail

readonly VERSION="1.3.1"
readonly REVISION="7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"
readonly REPOSITORY="https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SERVER_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly INSTALL_ROOT="${HOME_OS_INSTALL_ROOT:-/opt/home-os}"
readonly APP_ROOT="${HOME_OS_APP_ROOT:-$INSTALL_ROOT/app}"
readonly VENDOR_ROOT="$INSTALL_ROOT/vendor"
readonly TARGET="$VENDOR_ROOT/bgutil-ytdlp-pot-provider-$VERSION-${REVISION:0:8}"
readonly CURRENT="$VENDOR_ROOT/bgutil-ytdlp-pot-provider-current"
readonly UNIT_NAME="home-os-ytdlp-pot.service"

if [[ $EUID -ne 0 ]]; then
    echo "Run this installer as root." >&2
    exit 1
fi

for command in git node npm curl; do
    command -v "$command" >/dev/null || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

mkdir -p "$VENDOR_ROOT"
if [[ ! -f "$TARGET/server/build/main.js" ]]; then
    staging="$(mktemp -d "$VENDOR_ROOT/.bgutil-ytdlp-pot.XXXXXX")"
    cleanup() {
        if [[ -d ${staging:-} ]]; then
            find "$staging" -depth -delete
        fi
    }
    trap cleanup EXIT

    git clone --quiet "$REPOSITORY" "$staging"
    git -C "$staging" checkout --quiet "$REVISION"
    git -C "$staging" apply "$SERVER_ROOT/config/bgutil-ytdlp-pot-localhost.patch"
    (
        cd "$staging/server"
        npm ci --silent
        npx tsc
        npm prune --omit=dev --silent
    )
    chown -R root:root "$staging"
    chmod -R u=rwX,go=rX "$staging"
    mv "$staging" "$TARGET"
    staging=""
    trap - EXIT
fi

ln -sfn "$TARGET" "$CURRENT"
"$APP_ROOT/venv/bin/pip" install \
    --disable-pip-version-check \
    "bgutil-ytdlp-pot-provider==$VERSION"
install -o root -g root -m 0644 \
    "$SERVER_ROOT/config/$UNIT_NAME" \
    "/etc/systemd/system/$UNIT_NAME"

systemctl daemon-reload
systemctl enable --now "$UNIT_NAME"
curl --fail --silent --show-error \
    --max-time 10 \
    "http://127.0.0.1:4416/ping" >/dev/null

echo "Installed bgutil yt-dlp PO token provider $VERSION."
