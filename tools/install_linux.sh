#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK_PATH="${PDALINK_APK:-$ROOT/android/app/build/manual/app-debug.apk}"
AUTO_BOOTSTRAP=1
BUILD_ANDROID=1
INSTALL_ANDROID=1
INSTALL_PRIVILEGES=1
INSTALL_UDEV=1
DRY_RUN=0

usage() {
  cat <<EOF
Usage: $0 [options]

Prepares a cloned PdaLink checkout for native Linux USB internet use.

Default actions:
  1. Check Linux prerequisites and TUN availability.
  2. Bootstrap repo-local Android tools when needed.
  3. Build the debug Android APK when needed.
  4. Install Android USB udev rules with sudo.
  5. Install the limited native Linux privilege helper with sudo.
  6. If an authorized USB debugging device is attached, install the APK.
     If USB is missing or unauthorized, print the exact manual command.

Options:
  --apk PATH                 Use an existing debug APK
  --skip-bootstrap           Do not download repo-local Android tools
  --skip-android-build       Do not build the Android APK
  --skip-android-install     Do not install the APK over ADB
  --skip-udev                Do not install Linux Android USB udev rules
  --skip-privileges          Do not run the native Linux privilege installer
  --dry-run                  Print actions without changing files or devices
  -h, --help                 Show this help

After install:
  ./tools/pdalink.sh device trust --connected --name my-phone --default
  ./tools/pdalink.sh config set auto_connect on_trusted_connect
  ./tools/pdalink.sh autostart enable
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

warn() {
  echo "WARN: $*" >&2
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

have_command() {
  command -v "$1" >/dev/null 2>&1
}

python_ok() {
  python3 - "$@" <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

print_package_hint() {
  local missing="$*"
  cat >&2 <<EOF
Missing required command(s): $missing

Install the equivalent packages for your distribution, for example:
  Debian/Ubuntu: sudo apt update && sudo apt install -y python3 curl unzip tar sudo iproute2
  Fedora:        sudo dnf install -y python3 curl unzip tar sudo iproute
  Arch:          sudo pacman -S --needed python curl unzip tar sudo iproute2
EOF
}

preflight() {
  log "Checking native Linux prerequisites"
  [[ "$(uname -s)" == "Linux" ]] || fail "this installer targets Linux"

  local required=(python3 ip)
  if [[ "$INSTALL_PRIVILEGES" -eq 1 || "$INSTALL_UDEV" -eq 1 ]]; then
    required+=(sudo)
  fi
  if [[ "$BUILD_ANDROID" -eq 1 && "$AUTO_BOOTSTRAP" -eq 1 ]] && ! android_tools_ready; then
    required+=(curl unzip tar)
  fi

  local missing=()
  for tool in "${required[@]}"; do
    if ! have_command "$tool"; then
      missing+=("$tool")
    fi
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    print_package_hint "${missing[*]}"
    exit 127
  fi
  if ! python_ok; then
    fail "python3 3.10+ is required by the current PdaLink helper code"
  fi

  if [[ -e /proc/sys/fs/binfmt_misc/WSLInterop || -n "${WSL_DISTRO_NAME:-}" ]]; then
    warn "this looks like WSL; native USB watching expects Linux adb to see the phone directly"
  fi
  if [[ ! -e /dev/net/tun ]]; then
    warn "/dev/net/tun is missing; system routing will not work until TUN is available"
  fi
}

android_tools_ready() {
  [[ -x "$ROOT/.tools/android-sdk/platform-tools/adb" ]] \
    && [[ -f "$ROOT/.tools/android-sdk/platforms/android-35/android.jar" ]] \
    && [[ -x "$ROOT/.tools/android-sdk/build-tools/35.0.0/aapt2" ]]
}

ensure_android_tools() {
  if [[ "$BUILD_ANDROID" -eq 0 ]]; then
    return
  fi
  if android_tools_ready; then
    return
  fi
  if [[ "$AUTO_BOOTSTRAP" -eq 0 ]]; then
    fail "Android tools are missing; rerun without --skip-bootstrap or provide --apk PATH --skip-android-build"
  fi
  log "Bootstrapping repo-local Android tools"
  run "$ROOT/tools/bootstrap_android_tools.sh"
}

ensure_apk() {
  if [[ "$BUILD_ANDROID" -eq 0 ]]; then
    [[ -f "$APK_PATH" ]] || fail "APK not found: $APK_PATH"
    return
  fi
  if [[ -f "$APK_PATH" ]]; then
    log "Using existing APK"
    echo "$APK_PATH"
    return
  fi
  ensure_android_tools
  log "Building Android debug APK"
  run "$ROOT/tools/build_android_manual.sh"
  [[ "$DRY_RUN" -eq 1 || -f "$APK_PATH" ]] || fail "APK build did not produce $APK_PATH"
}

install_privileges() {
  if [[ "$INSTALL_PRIVILEGES" -eq 0 ]]; then
    return
  fi
  log "Installing native Linux privilege helper"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run sudo "$ROOT/tools/install_linux_privileges.sh" --user "$(id -un)"
  else
    sudo "$ROOT/tools/install_linux_privileges.sh" --user "$(id -un)"
  fi
}

install_udev() {
  if [[ "$INSTALL_UDEV" -eq 0 ]]; then
    return
  fi
  log "Installing Android USB udev rules"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+'
    printf ' %q' "$ROOT/tools/install_linux_udev.sh" --dry-run --no-reload
    printf '\n'
    "$ROOT/tools/install_linux_udev.sh" --dry-run --no-reload
  else
    sudo "$ROOT/tools/install_linux_udev.sh"
  fi
}

adb_devices_output() {
  "$ROOT/tools/adb.sh" devices -l 2>&1 || true
}

install_android_app() {
  if [[ "$INSTALL_ANDROID" -eq 0 ]]; then
    return
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Checking USB debugging state"
    run "$ROOT/tools/adb.sh" devices -l
    run "$ROOT/tools/adb.sh" install -r "$APK_PATH"
    return
  fi

  log "Checking USB debugging state"
  local devices
  devices="$(adb_devices_output)"
  echo "$devices"

  if printf '%s\n' "$devices" | awk 'NR > 1 && $2 == "device" { found = 1 } END { exit found ? 0 : 1 }'; then
    log "Installing Android app over authorized USB debugging"
    run "$ROOT/tools/adb.sh" install -r "$APK_PATH"
    run "$ROOT/tools/adb.sh" shell am start-foreground-service \
      -n dev.pdalink.prototype/.ProxyService \
      -a dev.pdalink.prototype.START_USB
    return
  fi

  if printf '%s\n' "$devices" | awk 'NR > 1 && $2 == "unauthorized" { found = 1 } END { exit found ? 0 : 1 }'; then
    cat <<EOF

USB device is attached but not authorized.
Unlock the phone, approve the USB debugging RSA prompt, then run:

  ./tools/pdalink.sh install --skip-bootstrap --skip-android-build --skip-privileges

EOF
    return
  fi

  cat <<EOF

No authorized USB debugging device is attached.
The APK is ready here:

  $APK_PATH

Manual install later:

  ./tools/adb.sh devices -l
  ./tools/adb.sh install -r "$APK_PATH"

EOF
}

finish() {
  cat <<EOF

PdaLink Linux install preparation complete.

Start the managed native Linux USB internet path:

  ./tools/pdalink.sh device trust --connected --name my-phone --default
  ./tools/pdalink.sh config set auto_connect on_trusted_connect
  ./tools/pdalink.sh autostart enable

Inspect status from another shell:

  ./tools/pdalink.sh watch status
  ./tools/pdalink.sh system service status --proxy-url http://127.0.0.1:18080

Refresh Android USB permissions later:

  ./tools/pdalink.sh rescue udev

Stop/cleanup:

  Ctrl-C in the watcher shell
  ./tools/pdalink.sh system service stop --proxy-url http://127.0.0.1:18080
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apk)
      APK_PATH="${2:-}"
      [[ -n "$APK_PATH" ]] || fail "--apk requires a value"
      shift 2
      ;;
    --skip-bootstrap)
      AUTO_BOOTSTRAP=0
      shift
      ;;
    --skip-android-build)
      BUILD_ANDROID=0
      shift
      ;;
    --skip-android-install)
      INSTALL_ANDROID=0
      shift
      ;;
    --skip-udev)
      INSTALL_UDEV=0
      shift
      ;;
    --skip-privileges)
      INSTALL_PRIVILEGES=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

preflight
ensure_android_tools
ensure_apk
install_udev
install_privileges
install_android_app
finish
