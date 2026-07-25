#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="HomeOS-Music"
BUNDLE_ID="uk.co.petershomenet.homeos.music"
SCHEME="HomeOS-Music"
CONFIGURATION="Debug"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_FILE="$PROJECT_DIR/HomeOS.xcodeproj"
SPEC_FILE="$PROJECT_DIR/project.yml"
DERIVED_DATA="$PROJECT_DIR/.build/xcode-music"
NO_SIGN_DERIVED_DATA="$DERIVED_DATA-nosign"
APP_BUNDLE="$DERIVED_DATA/Build/Products/$CONFIGURATION/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"

usage() {
  echo "usage: $0 [run|build|--debug|--logs|--telemetry|--verify|--no-sign]" >&2
}

find_xcodegen() {
  if command -v xcodegen >/dev/null 2>&1; then
    command -v xcodegen
  elif [[ -x /opt/homebrew/bin/xcodegen ]]; then
    echo /opt/homebrew/bin/xcodegen
  else
    return 1
  fi
}

kill_app() {
  pkill -x "$APP_NAME" >/dev/null 2>&1 || true
}

generate_project() {
  local xcodegen_bin
  xcodegen_bin="$(find_xcodegen)" || {
    echo "xcodegen is required to generate $PROJECT_FILE from $SPEC_FILE" >&2
    exit 1
  }

  (
    cd "$PROJECT_DIR"
    "$xcodegen_bin" generate --spec "$SPEC_FILE"
  )
}

build_app() {
  generate_project
  if ! xcodebuild \
    -project "$PROJECT_FILE" \
    -scheme "$SCHEME" \
    -configuration "$CONFIGURATION" \
    -derivedDataPath "$DERIVED_DATA" \
    -allowProvisioningUpdates \
    build; then
    cat >&2 <<EOF

Signed build failed. Xcode must be signed into the Apple Development team
configured in apps/macos-files/project.yml and able to create a provisioning profile
for:

  - $BUNDLE_ID

For compile-only verification, run:
  $0 --no-sign

EOF
    exit 1
  fi
}

build_without_signing() {
  generate_project
  xcodebuild \
    -project "$PROJECT_FILE" \
    -scheme "$SCHEME" \
    -configuration "$CONFIGURATION" \
    -derivedDataPath "$NO_SIGN_DERIVED_DATA" \
    CODE_SIGNING_ALLOWED=NO \
    build
}

open_app() {
  [[ -d "$APP_BUNDLE" ]] || build_app
  /usr/bin/open -n "$APP_BUNDLE"
}

verify_running_app() {
  local pids
  pids="$(pgrep -x "$APP_NAME" 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    echo "HomeOS-Music did not launch from $APP_BINARY" >&2
    exit 1
  fi
  echo "HomeOS-Music running from $APP_BINARY (pid: ${pids//$'\n'/, })"
}

verify_bundle() {
  [[ -d "$APP_BUNDLE" ]] || {
    echo "missing app bundle: $APP_BUNDLE" >&2
    exit 1
  }
  /usr/bin/codesign --verify --deep --strict "$APP_BUNDLE"
  /usr/bin/codesign -d --entitlements :- "$APP_BUNDLE" >/dev/null 2>&1
}

case "$MODE" in
  run)
    kill_app
    build_app
    open_app
    ;;
  build)
    build_app
    ;;
  --debug|debug)
    kill_app
    build_app
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    kill_app
    build_app
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    kill_app
    build_app
    open_app
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --verify|verify)
    kill_app
    build_app
    verify_bundle
    open_app
    sleep 1
    verify_running_app
    ;;
  --no-sign|no-sign)
    build_without_signing
    ;;
  *)
    usage
    exit 2
    ;;
esac
