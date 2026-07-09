#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$ROOT/.tools"
SDK_DIR="$TOOLS_DIR/android-sdk"
GRADLE_DIR="$TOOLS_DIR/gradle"
CACHE_DIR="$TOOLS_DIR/cache"
LOCAL_JDK="$TOOLS_DIR/jdk/temurin-17"

PLATFORM_TOOLS_URL="${PLATFORM_TOOLS_URL:-https://dl.google.com/android/repository/platform-tools-latest-linux.zip}"
CMDLINE_TOOLS_URL="${CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip}"
GRADLE_URL="${GRADLE_URL:-https://services.gradle.org/distributions/gradle-8.10.2-bin.zip}"
PLATFORM_TOOLS_SHA256="${PLATFORM_TOOLS_SHA256:-}"
CMDLINE_TOOLS_SHA256="${CMDLINE_TOOLS_SHA256:-2d2d50857e4eb553af5a6dc3ad507a17adf43d115264b1afc116f95c92e5e258}"
GRADLE_SHA256="${GRADLE_SHA256:-31c55713e40233a8303827ceb42ca48a47267a0ad4bab9177123121e71524c26}"
TEMURIN17_SHA256="${TEMURIN17_SHA256:-}"

mkdir -p "$SDK_DIR" "$GRADLE_DIR" "$CACHE_DIR"

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

temurin_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      echo "x64"
      ;;
    aarch64|arm64)
      echo "aarch64"
      ;;
    *)
      return 1
      ;;
  esac
}

download() {
  local url="$1"
  local out="$2"
  local expected_sha256="${3:-}"
  if [[ ! -f "$out" ]]; then
    curl -L --fail --retry 3 -o "$out" "$url"
  fi
  verify_download "$out" "$expected_sha256" "$url"
}

verify_download() {
  local file="$1"
  local expected_sha256="$2"
  local url="$3"
  if [[ -z "$expected_sha256" ]]; then
    cat >&2 <<EOF
WARN: no SHA-256 configured for $url
      Set the matching *_SHA256 environment variable to verify this download.
EOF
    return
  fi
  if ! command -v sha256sum >/dev/null 2>&1; then
    echo "ERROR: sha256sum is required to verify $url" >&2
    exit 127
  fi
  local actual_sha256
  actual_sha256="$(sha256sum "$file" | awk '{print $1}')"
  if [[ "$actual_sha256" != "$expected_sha256" ]]; then
    cat >&2 <<EOF
ERROR: SHA-256 mismatch for $url
expected: $expected_sha256
actual:   $actual_sha256
file:     $file
EOF
    exit 1
  fi
}

ensure_jdk() {
  if has_suitable_jdk; then
    return
  fi
  if [[ -x "$LOCAL_JDK/bin/java" ]]; then
    export PATH="$LOCAL_JDK/bin:$PATH"
    if has_suitable_jdk; then
      return
    fi
  fi

  local arch
  if ! arch="$(temurin_arch)"; then
    cat >&2 <<EOF
No suitable JDK 17+ found, and this CPU architecture is not supported by the
repo-local Temurin downloader: $(uname -m)

Install JDK 17+ with javac and keytool, then rerun this script.
EOF
    exit 127
  fi

  local jdk_url="${TEMURIN17_URL:-https://api.adoptium.net/v3/binary/latest/17/ga/linux/${arch}/jdk/hotspot/normal/eclipse}"
  local jdk_archive="$CACHE_DIR/temurin-17-${arch}.tar.gz"
  download "$jdk_url" "$jdk_archive" "$TEMURIN17_SHA256"
  rm -rf "$LOCAL_JDK"
  mkdir -p "$LOCAL_JDK"
  tar -xzf "$jdk_archive" -C "$LOCAL_JDK" --strip-components=1
  export PATH="$LOCAL_JDK/bin:$PATH"

  if ! has_suitable_jdk; then
    echo "downloaded Temurin JDK is not usable: $LOCAL_JDK" >&2
    exit 127
  fi
}

ensure_jdk

download "$PLATFORM_TOOLS_URL" "$CACHE_DIR/platform-tools.zip" "$PLATFORM_TOOLS_SHA256"
rm -rf "$SDK_DIR/platform-tools"
unzip -q "$CACHE_DIR/platform-tools.zip" -d "$SDK_DIR"

download "$CMDLINE_TOOLS_URL" "$CACHE_DIR/commandlinetools.zip" "$CMDLINE_TOOLS_SHA256"
rm -rf "$SDK_DIR/cmdline-tools/latest"
mkdir -p "$SDK_DIR/cmdline-tools"
unzip -q "$CACHE_DIR/commandlinetools.zip" -d "$CACHE_DIR/cmdline-tools-unpack"
mv "$CACHE_DIR/cmdline-tools-unpack/cmdline-tools" "$SDK_DIR/cmdline-tools/latest"
rm -rf "$CACHE_DIR/cmdline-tools-unpack"

download "$GRADLE_URL" "$CACHE_DIR/gradle-8.10.2-bin.zip" "$GRADLE_SHA256"
if [[ ! -x "$GRADLE_DIR/gradle-8.10.2/bin/gradle" ]]; then
  unzip -q "$CACHE_DIR/gradle-8.10.2-bin.zip" -d "$GRADLE_DIR"
fi

export ANDROID_HOME="$SDK_DIR"
export ANDROID_SDK_ROOT="$SDK_DIR"
set +o pipefail
yes | "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null
set -o pipefail
"$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" \
  "platform-tools" \
  "platforms;android-35" \
  "build-tools;35.0.0"

cat <<EOF
Android tools installed under:
  $SDK_DIR
JDK:
  $(command -v java)

Build:
  $ROOT/tools/build_android.sh

ADB:
  $ROOT/tools/adb.sh devices
EOF
