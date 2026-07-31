#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="HomeOS"
LEGACY_APP_NAME="HomeOS-Files"
BUNDLE_ID="uk.co.petershomenet.homeos"
PROVIDER_ID="uk.co.petershomenet.homeos.fileprovider.v2"
LEGACY_PROVIDER_ID="uk.co.petershomenet.homeos.fileprovider"
SCHEME="HomeOS"
CONFIGURATION="Debug"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_FILE="$PROJECT_DIR/HomeOS.xcodeproj"
SPEC_FILE="$PROJECT_DIR/project.yml"
DERIVED_DATA="$PROJECT_DIR/.build/xcode"
NO_SIGN_DERIVED_DATA="$DERIVED_DATA-nosign"
APP_BUNDLE="$DERIVED_DATA/Build/Products/$CONFIGURATION/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
PROVIDER_BUNDLE="$APP_BUNDLE/Contents/PlugIns/HomeOSFileProvider.appex"
NO_SIGN_APP_BUNDLE="$NO_SIGN_DERIVED_DATA/Build/Products/$CONFIGURATION/$APP_NAME.app"
NO_SIGN_PROVIDER_BUNDLE="$NO_SIGN_APP_BUNDLE/Contents/PlugIns/HomeOSFileProvider.appex"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

usage() {
  echo "usage: $0 [run|build|--debug|--logs|--telemetry|--verify|--reset-file-provider|--no-sign]" >&2
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
  local pids
  pids="$(pgrep -x "$APP_NAME" 2>/dev/null || true)"
  [[ -z "$pids" ]] || kill $pids >/dev/null 2>&1 || true
  pids="$(pgrep -x "$LEGACY_APP_NAME" 2>/dev/null || true)"
  [[ -z "$pids" ]] || kill $pids >/dev/null 2>&1 || true
}

running_app_pids() {
  while read -r pid; do
    local command
    [[ -n "$pid" ]] || continue
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ "$command" == "$APP_BINARY"* ]] && echo "$pid"
  done < <(pgrep -x "$APP_NAME" 2>/dev/null || true)
  return 0
}

verify_running_app() {
  local pids
  pids="$(running_app_pids)"
  if [[ -z "$pids" ]]; then
    echo "HomeOS did not launch from expected binary: $APP_BINARY" >&2
    echo "Matching HomeOS processes:" >&2
    pgrep -laf "$APP_NAME" >&2 || true
    exit 1
  fi
  echo "HomeOS running from $APP_BINARY (pid: ${pids//$'\n'/, })"
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

Signed build failed. The Home OS Finder folder uses a real File Provider
extension plus shared Keychain entitlements, so Xcode must be signed into the
Apple Development team configured in apps/macos-files/project.yml and able to create
provisioning profiles for:

  - uk.co.petershomenet.homeos
  - uk.co.petershomenet.homeos.fileprovider.v2

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
  unregister_stale_provider_bundles
}

open_app() {
  [[ -d "$APP_BUNDLE" ]] || build_app
  /usr/bin/open "$APP_BUNDLE"
}

open_app_with_args() {
  [[ -d "$APP_BUNDLE" ]] || build_app
  /usr/bin/open "$APP_BUNDLE" --args "$@"
}

register_app_and_provider() {
  [[ -d "$APP_BUNDLE" ]] || {
    echo "missing app bundle: $APP_BUNDLE" >&2
    exit 1
  }
  [[ -d "$PROVIDER_BUNDLE" ]] || {
    echo "missing File Provider extension: $PROVIDER_BUNDLE" >&2
    exit 1
  }

  unregister_stale_provider_bundles
  /usr/bin/pluginkit -e ignore -i "$LEGACY_PROVIDER_ID" >/dev/null 2>&1 || true
  "$LSREGISTER" -f -R -trusted "$APP_BUNDLE"
  /usr/bin/pluginkit -r "$PROVIDER_BUNDLE" >/dev/null 2>&1 || true
  /usr/bin/pluginkit -a "$PROVIDER_BUNDLE" >/dev/null 2>&1 || true
  /usr/bin/pluginkit -e use -i "$PROVIDER_ID" >/dev/null 2>&1 || true
}

unregister_stale_provider_bundles() {
  if [[ -d "$NO_SIGN_PROVIDER_BUNDLE" ]]; then
    /usr/bin/pluginkit -r "$NO_SIGN_PROVIDER_BUNDLE" >/dev/null 2>&1 || true
  fi
  if [[ -d "$NO_SIGN_APP_BUNDLE" ]]; then
    "$LSREGISTER" -u "$NO_SIGN_APP_BUNDLE" >/dev/null 2>&1 || true
  fi
  if [[ -d "$NO_SIGN_PROVIDER_BUNDLE" ]]; then
    "$LSREGISTER" -u "$NO_SIGN_PROVIDER_BUNDLE" >/dev/null 2>&1 || true
  fi
}

remove_no_sign_build_products() {
  if [[ -d "$NO_SIGN_DERIVED_DATA" ]]; then
    rm -rf "$NO_SIGN_DERIVED_DATA"
  fi
}

verify_bundle() {
  [[ -d "$APP_BUNDLE" ]] || {
    echo "missing app bundle: $APP_BUNDLE" >&2
    exit 1
  }
  [[ -d "$PROVIDER_BUNDLE" ]] || {
    echo "missing File Provider extension: $PROVIDER_BUNDLE" >&2
    exit 1
  }
  /usr/bin/codesign --verify --deep --strict "$APP_BUNDLE"
  /usr/bin/codesign -d --entitlements :- "$APP_BUNDLE" >/dev/null 2>&1
  /usr/bin/codesign -d --entitlements :- "$PROVIDER_BUNDLE" >/dev/null 2>&1
}

wait_for_reset_app_to_exit() {
  for _ in {1..40}; do
    if [[ -z "$(running_app_pids)" ]]; then
      return
    fi
    sleep 0.5
  done

  echo "HomeOS did not exit after File Provider reset." >&2
  pgrep -laf "$APP_NAME" >&2 || true
  exit 1
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
  --reset-file-provider|reset-file-provider)
    kill_app
    unregister_stale_provider_bundles
    remove_no_sign_build_products
    build_app
    verify_bundle
    register_app_and_provider
    open_app_with_args --reset-file-provider-domain --quit-after-reset
    wait_for_reset_app_to_exit
    /usr/bin/killall fileproviderd >/dev/null 2>&1 || true
    /usr/bin/killall Finder >/dev/null 2>&1 || true
    open_app
    sleep 1
    verify_running_app
    /usr/bin/fileproviderctl dump "$PROVIDER_ID" || true
    ;;
  --no-sign|no-sign)
    build_without_signing
    ;;
  *)
    usage
    exit 2
    ;;
esac
