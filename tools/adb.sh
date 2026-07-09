#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_ADB="$ROOT/.tools/android-sdk/platform-tools/adb"

if [[ -x "$LOCAL_ADB" ]]; then
  exec "$LOCAL_ADB" "$@"
fi

if command -v adb >/dev/null 2>&1; then
  exec adb "$@"
fi

cat >&2 <<EOF
adb not found.

Run:
  $ROOT/tools/bootstrap_android_tools.sh

or install Android platform-tools and ensure adb is in PATH.
EOF
exit 127
