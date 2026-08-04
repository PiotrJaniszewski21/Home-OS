#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$ROOT_DIR/HomeMusicApp"
DERIVED_DATA="$PROJECT_DIR/.build/xcode"
MODE="${1:-simulator}"

cd "$PROJECT_DIR"
xcodegen generate --spec project.yml
case "$MODE" in
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
    echo "usage: $0 [simulator|device]" >&2
    exit 2
    ;;
esac
