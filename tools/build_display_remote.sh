#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${ZR_DISPLAY_ZERO_HOST:-pdalink-zero}"
GRADLE_BIN="${GRADLE_BIN:-$ROOT/.tools/gradle/gradle-8.10.2/bin/gradle}"
LOCAL_JDK="$ROOT/.tools/jdk/temurin-17"
ANDROID_HOME="${ANDROID_HOME:-$ROOT/.tools/android-sdk}"

if [[ -z "${ZR_DISPLAY_AP_PSK:-}" ]]; then
    config="$(
        ssh \
            -o BatchMode=yes \
            -o ConnectTimeout=5 \
            -o ConnectionAttempts=1 \
            -- "$HOST" \
            "cat /etc/zr-impact/display-ap.conf"
    )"
    ZR_DISPLAY_AP_SSID="$(
        sed -n 's/^ZR_DISPLAY_AP_SSID=//p' <<<"$config" |
            sed -n '1p'
    )"
    ZR_DISPLAY_AP_PSK="$(
        sed -n 's/^ZR_DISPLAY_AP_PSK=//p' <<<"$config" |
            sed -n '1p'
    )"
    unset config
fi

if [[ ${#ZR_DISPLAY_AP_PSK} -lt 8 || ${#ZR_DISPLAY_AP_PSK} -gt 63 ]]; then
    echo "Zero display AP credential is missing or invalid." >&2
    exit 1
fi
ZR_DISPLAY_AP_SSID="${ZR_DISPLAY_AP_SSID:-ZR-Impact-Display}"

if [[ -z "${JAVA_HOME:-}" && -x "$LOCAL_JDK/bin/java" ]]; then
    export JAVA_HOME="$LOCAL_JDK"
    export PATH="$JAVA_HOME/bin:$PATH"
fi
if [[ ! -x "$GRADLE_BIN" ]]; then
    echo "Gradle is unavailable. Run tools/bootstrap_android_tools.sh." >&2
    exit 127
fi
if [[ ! -d "$ANDROID_HOME/platforms/android-35" ]]; then
    echo "Android SDK platform 35 is unavailable." >&2
    exit 127
fi

export ANDROID_HOME
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ZR_DISPLAY_AP_SSID
export ZR_DISPLAY_AP_PSK
export GRADLE_OPTS="${GRADLE_OPTS:--Xmx1024m -Dfile.encoding=UTF-8}"

"$GRADLE_BIN" \
    -p "$ROOT/android" \
    --no-daemon \
    --max-workers=1 \
    :displayremote:clean \
    :displayremote:assembleDebug \
    :displayremote:testDebugUnitTest

unset ZR_DISPLAY_AP_PSK
echo "Built the configured ZR Display Remote APK."
