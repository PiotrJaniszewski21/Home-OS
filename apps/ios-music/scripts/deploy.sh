#!/usr/bin/env bash
# deploy.sh — Build HomeMusic and deploy over Wi-Fi to all paired iPhones & home server
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_HOST="homeos"
REMOTE_IPA_DIR="/opt/altstore"

CARLOS_UDID="00008110-001208182644401E"
PIOTR_UDID="00008150-000142A81A20401C"

echo "==> [1/4] Regenerating Xcode project…"
cd "$PROJECT_DIR"
xcodegen generate --spec project.yml >/dev/null

echo "==> [2/4] Building HomeMusic with multi-device provisioning profile…"
xcodebuild -project HomeMusic.xcodeproj \
  -scheme HomeMusic \
  -configuration Debug \
  -destination "id=$CARLOS_UDID" \
  -allowProvisioningUpdates \
  -allowProvisioningDeviceRegistration \
  build >/dev/null

APP_PATH="$(find "$HOME/Library/Developer/Xcode/DerivedData" -name "HomeMusic.app" -path "*/HomeMusic-avmduuygxhyzfcfsrfadudmikkan/Build/Products/Debug-iphoneos/*" | head -1)"

if [[ -z "$APP_PATH" || ! -d "$APP_PATH" ]]; then
  echo "❌ Error: HomeMusic.app not found in DerivedData" >&2
  exit 1
fi

echo "==> [3/4] Deploying update over Wi-Fi to devices…"

# Carlos's iPhone
if xcrun devicectl list devices 2>&1 | grep -q "$CARLOS_UDID"; then
  echo "📲 Deploying to Carlos’s iPhone over Wi-Fi…"
  xcrun devicectl device install app --device "$CARLOS_UDID" "$APP_PATH" >/dev/null && echo "   ✅ Installed on Carlos’s iPhone!" || echo "   ⚠️ Carlos’s iPhone not reachable right now."
fi

# Piotr's iPhone
if xcrun devicectl list devices 2>&1 | grep -q "$PIOTR_UDID"; then
  echo "📲 Deploying to Piotr’s iPhone over Wi-Fi…"
  xcrun devicectl device install app --device "$PIOTR_UDID" "$APP_PATH" >/dev/null && echo "   ✅ Installed on Piotr’s iPhone!" || echo "   ⚠️ Piotr’s iPhone not reachable right now."
fi

echo "==> [4/4] Uploading HomeMusic.ipa to Home Server (192.168.0.8)…"
rm -rf /tmp/Payload /tmp/HomeMusic.ipa
mkdir -p /tmp/Payload
cp -R "$APP_PATH" /tmp/Payload/
(cd /tmp && zip -q -r HomeMusic.ipa Payload)
scp /tmp/HomeMusic.ipa "${SSH_HOST}:${REMOTE_IPA_DIR}/HomeMusic.ipa" >/dev/null
echo "   ✅ Backup copy saved at ${SSH_HOST}:${REMOTE_IPA_DIR}/HomeMusic.ipa"

echo ""
echo "🎉 Deploy complete!"
