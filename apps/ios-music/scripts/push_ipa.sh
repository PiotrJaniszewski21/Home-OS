#!/usr/bin/env bash
# push_ipa.sh — Build a signed IPA and push it to the AltStore server directory
# Usage:  bash apps/ios-music/scripts/push_ipa.sh [path/to/existing.ipa]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_HOST="homeos"
REMOTE_IPA_DIR="/opt/altstore"
EXPORT_DIR="$PROJECT_DIR/.build/ipa_export"
ARCHIVE_PATH="$PROJECT_DIR/.build/HomeMusic.xcarchive"

IPA_PATH="${1:-}"

if [[ -z "$IPA_PATH" ]]; then
  echo "==> Building IPA from source…"
  cd "$PROJECT_DIR"
  xcodegen generate --spec project.yml

  # Archive
  xcodebuild archive \
    -project HomeMusic.xcodeproj \
    -scheme HomeMusic \
    -configuration Debug \
    -destination "generic/platform=iOS" \
    -archivePath "$ARCHIVE_PATH" \
    -allowProvisioningUpdates \
    -allowProvisioningDeviceRegistration \
    | grep -E "(error:|warning:|Archive Succeeded|FAILED)"

  # Export options for Development signing
  mkdir -p "$EXPORT_DIR"
  cat > "$EXPORT_DIR/ExportOptions.plist" << 'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key>
  <string>development</string>
  <key>teamID</key>
  <string>7S5APW4X6P</string>
  <key>signingStyle</key>
  <string>automatic</string>
  <key>thinning</key>
  <string>&lt;none&gt;</string>
</dict>
</plist>
PLIST_EOF

  xcodebuild -exportArchive \
    -archivePath "$ARCHIVE_PATH" \
    -exportPath "$EXPORT_DIR" \
    -exportOptionsPlist "$EXPORT_DIR/ExportOptions.plist" \
    -allowProvisioningUpdates \
    | grep -E "(error:|warning:|Export Succeeded|FAILED)"

  IPA_PATH="$(find "$EXPORT_DIR" -name '*.ipa' | head -1)"
  echo "==> IPA exported: $IPA_PATH"
fi

if [[ ! -f "$IPA_PATH" ]]; then
  echo "❌ IPA not found: $IPA_PATH" >&2
  exit 1
fi

echo "==> Pushing to server…"
scp "$IPA_PATH" "${SSH_HOST}:${REMOTE_IPA_DIR}/HomeMusic.ipa"
echo ""
echo "✅  HomeMusic.ipa → ${SSH_HOST}:${REMOTE_IPA_DIR}/HomeMusic.ipa"
echo "   Open AltStore on your iPhone and tap 'Refresh All' to pick it up."
