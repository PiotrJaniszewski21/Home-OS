#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DERIVED_DATA="$PROJECT_DIR/.build/xcode"
MAC_DERIVED_DATA="$PROJECT_DIR/.build/catalyst"
MODE="${1:-mac}"

cd "$PROJECT_DIR"
xcodegen generate --spec project.yml
case "$MODE" in
  mac)
    pkill -x HomeMusic 2>/dev/null || true
    xcodebuild \
      -project HomeMusic.xcodeproj \
      -scheme HomeMusic \
      -configuration Debug \
      -destination 'platform=macOS,variant=Mac Catalyst,name=My Mac' \
      -derivedDataPath "$MAC_DERIVED_DATA" \
      CONFIGURATION_BUILD_DIR="$PROJECT_DIR/dist" \
      -allowProvisioningUpdates \
      build
    APP_PATH="$PROJECT_DIR/dist/HomeMusic.app"
    /usr/bin/open -n "$APP_PATH"
    sleep 2
    if ! pgrep -x HomeMusic >/dev/null; then
      echo "HomeMusic built, but the Mac process did not remain running." >&2
      exit 1
    fi
    echo "HomeMusic is running from $APP_PATH"
    ;;
  simulator)
    xcodebuild \
      -project HomeMusic.xcodeproj \
      -scheme HomeMusic \
      -configuration Debug \
      -destination 'generic/platform=iOS Simulator' \
      -derivedDataPath "$DERIVED_DATA" \
      CODE_SIGNING_ALLOWED=NO \
      build
    ;;
  device)
    xcodebuild \
      -project HomeMusic.xcodeproj \
      -scheme HomeMusic \
      -configuration Debug \
      -destination 'generic/platform=iOS' \
      -derivedDataPath "$PROJECT_DIR/.build/device" \
      -allowProvisioningUpdates \
      build
    ;;
  *)
    echo "usage: $0 [mac|simulator|device]" >&2
    exit 2
    ;;
esac
