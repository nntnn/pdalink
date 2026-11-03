#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANDROID_HOME="${ANDROID_HOME:-$ROOT/.tools/android-sdk}"
GRADLE_BIN="${GRADLE_BIN:-$ROOT/.tools/gradle/gradle-8.10.2/bin/gradle}"
LOCAL_JDK="$ROOT/.tools/jdk/temurin-17"
SIGNING_KEYSTORE="${PDALINK_ANDROID_KEYSTORE:-$ROOT/android/app/build/debug.keystore}"

if [[ -z "${JAVA_HOME:-}" && -x "$LOCAL_JDK/bin/java" ]]; then
  export JAVA_HOME="$LOCAL_JDK"
  export PATH="$JAVA_HOME/bin:$PATH"
fi

if ! command -v java >/dev/null 2>&1; then
  echo "java not found. Run $ROOT/tools/bootstrap_android_tools.sh first or set JAVA_HOME." >&2
  exit 127
fi

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

gradle_args=(
  -p "$ROOT/android"
  --no-daemon
  --max-workers=1
)
if [[ -f "$SIGNING_KEYSTORE" ]]; then
  gradle_args+=(
    "-Pandroid.injected.signing.store.file=$SIGNING_KEYSTORE"
    "-Pandroid.injected.signing.store.password=android"
    "-Pandroid.injected.signing.key.alias=androiddebugkey"
    "-Pandroid.injected.signing.key.password=android"
  )
fi

exec "$GRADLE_BIN" "${gradle_args[@]}" assembleDebug "$@"
