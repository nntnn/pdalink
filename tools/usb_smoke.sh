#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB="$ROOT/tools/adb.sh"
PYTHON="${PYTHON:-python3}"
APK=""

usage() {
  cat <<EOF
Usage: $0 [--install APK] [linux/pdalink_lite.py usb-smoke options...]

Optionally installs an APK, then starts the Android proxy, creates an adb
tcp:18080 -> tcp:8000 forward, and curls through http://127.0.0.1:18080.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)
      APK="${2:-}"
      [[ -n "$APK" ]] || {
        echo "--install requires an APK path" >&2
        exit 2
      }
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

if [[ -n "$APK" ]]; then
  "$ADB" install -r "$APK"
fi

exec "$PYTHON" "$ROOT/linux/pdalink_lite.py" usb-smoke "$@"
