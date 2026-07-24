#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK="$ROOT/android/displayremote/build/outputs/apk/debug/displayremote-debug.apk"

"$ROOT/tools/build_display_remote.sh"
adb install -r "$APK"

echo "Installed ZR Display Remote."
