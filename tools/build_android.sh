#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANDROID_HOME="${ANDROID_HOME:-$ROOT/.tools/android-sdk}"
GRADLE_BIN="${GRADLE_BIN:-$ROOT/.tools/gradle/gradle-8.10.2/bin/gradle}"

if [[ ! -x "$GRADLE_BIN" ]]; then
  if command -v gradle >/dev/null 2>&1; then
    GRADLE_BIN="$(command -v gradle)"
  else
    echo "gradle not found. Run $ROOT/tools/bootstrap_android_tools.sh first." >&2
    exit 127
  fi
fi

if [[ ! -d "$ANDROID_HOME/platforms/android-35" ]]; then
  echo "Android SDK platform android-35 not found under $ANDROID_HOME." >&2
  echo "Run $ROOT/tools/bootstrap_android_tools.sh or set ANDROID_HOME." >&2
  exit 127
fi

export ANDROID_HOME
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export GRADLE_OPTS="${GRADLE_OPTS:--Xmx1024m -Dfile.encoding=UTF-8}"
exec "$GRADLE_BIN" -p "$ROOT/android" --no-daemon --max-workers=1 assembleDebug "$@"
