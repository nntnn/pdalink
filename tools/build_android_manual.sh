#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_HOME:-$ROOT/.tools/android-sdk}"
LOCAL_JDK="$ROOT/.tools/jdk/temurin-17"
ANDROID_JAR="$SDK_DIR/platforms/android-35/android.jar"
BUILD_TOOLS="$SDK_DIR/build-tools/35.0.0"
OUT_DIR="$ROOT/android/app/build/manual"
GEN_DIR="$OUT_DIR/generated"
CLASS_DIR="$OUT_DIR/classes"
DEX_DIR="$OUT_DIR/dex"
RES_DIR="$ROOT/android/app/src/main/res"
RES_FLAT="$OUT_DIR/resources.zip"
KEYSTORE="$ROOT/android/app/build/debug.keystore"
LEGACY_KEYSTORE="$OUT_DIR/debug.keystore"
ZXING_JAR="$ROOT/.tools/android-deps/core-3.5.3.jar"

require_file() {
  if [[ ! -e "$1" ]]; then
    echo "missing required file: $1" >&2
    echo "run $ROOT/tools/bootstrap_android_tools.sh first" >&2
    exit 127
  fi
}

require_file "$ANDROID_JAR"
require_file "$BUILD_TOOLS/aapt2"
require_file "$BUILD_TOOLS/d8"
require_file "$BUILD_TOOLS/zipalign"
require_file "$BUILD_TOOLS/apksigner"
require_file "$ZXING_JAR"

java_major_version() {
  java -XshowSettings:properties -version 2>&1 \
    | awk -F'= ' '/java.specification.version =/{print $2; exit}' \
    | awk -F. '{print ($1 == "1" ? $2 : $1)}'
}

java_has_compiler_module() {
  java --list-modules 2>/dev/null | grep -q '^jdk.compiler@'
}

has_suitable_jdk() {
  command -v java >/dev/null 2>&1 || return 1
  command -v javac >/dev/null 2>&1 || return 1
  command -v keytool >/dev/null 2>&1 || return 1
  local major
  major="$(java_major_version)"
  [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 17 )) || return 1
  java_has_compiler_module
}

if ! has_suitable_jdk && [[ -x "$LOCAL_JDK/bin/java" ]]; then
  export PATH="$LOCAL_JDK/bin:$PATH"
fi

if ! has_suitable_jdk; then
  echo "missing suitable JDK 17+ with keytool; install JDK 17 or run $ROOT/tools/bootstrap_android_tools.sh" >&2
  exit 127
fi

mkdir -p "$(dirname "$KEYSTORE")"
if [[ ! -f "$KEYSTORE" && -f "$LEGACY_KEYSTORE" ]]; then
  cp "$LEGACY_KEYSTORE" "$KEYSTORE"
fi

rm -rf "$OUT_DIR"
mkdir -p "$GEN_DIR" "$CLASS_DIR" "$DEX_DIR"

LINK_INPUTS=()
if [[ -d "$RES_DIR" ]]; then
  "$BUILD_TOOLS/aapt2" compile --dir "$RES_DIR" -o "$RES_FLAT"
  LINK_INPUTS+=("$RES_FLAT")
fi

"$BUILD_TOOLS/aapt2" link \
  -o "$OUT_DIR/app-unsigned.apk" \
  -I "$ANDROID_JAR" \
  --manifest "$ROOT/android/app/src/main/AndroidManifest.xml" \
  --java "$GEN_DIR" \
  --min-sdk-version 23 \
  --target-sdk-version 35 \
  --version-code 1 \
  --version-name 0.1.0 \
  "${LINK_INPUTS[@]}"

mapfile -t JAVA_SOURCES < <(find \
  "$ROOT/android/app/src/main/java" \
  "$ROOT/android/shared/src/main/java" \
  "$GEN_DIR" \
  -name '*.java' -print)

java --add-modules jdk.compiler com.sun.tools.javac.Main \
  -source 8 \
  -target 8 \
  -classpath "$ANDROID_JAR:$ZXING_JAR" \
  -d "$CLASS_DIR" \
  "${JAVA_SOURCES[@]}"

"$BUILD_TOOLS/d8" \
  --lib "$ANDROID_JAR" \
  --min-api 23 \
  --output "$DEX_DIR" \
  "$ZXING_JAR" \
  $(find "$CLASS_DIR" -name '*.class' -print)

cp "$OUT_DIR/app-unsigned.apk" "$OUT_DIR/app-with-dex.apk"
if command -v zip >/dev/null 2>&1; then
  (
    cd "$DEX_DIR"
    zip -q -u "$OUT_DIR/app-with-dex.apk" classes.dex
  )
else
  python3 - "$OUT_DIR/app-with-dex.apk" "$DEX_DIR/classes.dex" <<'PY'
import sys
import zipfile

apk_path, dex_path = sys.argv[1:3]
with zipfile.ZipFile(apk_path, "a", compression=zipfile.ZIP_STORED) as apk:
    apk.write(dex_path, "classes.dex")
PY
fi

"$BUILD_TOOLS/zipalign" -p -f 4 "$OUT_DIR/app-with-dex.apk" "$OUT_DIR/app-aligned.apk"

if [[ ! -f "$KEYSTORE" ]]; then
  keytool -genkeypair \
    -keystore "$KEYSTORE" \
    -storepass android \
    -keypass android \
    -alias androiddebugkey \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=Android Debug,O=PdaLink,C=US" >/dev/null
fi

"$BUILD_TOOLS/apksigner" sign \
  --ks "$KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$OUT_DIR/app-debug.apk" \
  "$OUT_DIR/app-aligned.apk"

"$BUILD_TOOLS/apksigner" verify "$OUT_DIR/app-debug.apk"

echo "$OUT_DIR/app-debug.apk"
