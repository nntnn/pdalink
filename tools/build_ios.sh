#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IOS_DIR="$ROOT_DIR/ios"
VENDOR_DIR="$IOS_DIR/Vendor"
TUN2PROXY_VERSION="0.8.2"
TUN2PROXY_COMMIT="eed123fbbec06295bf83f9be36d5a0f64ed9a8cb"
TUN2PROXY_ARCHIVE="tun2proxy-${TUN2PROXY_COMMIT}.tar.gz"
TUN2PROXY_SOURCE_SHA256="20c06c80ad09d3c357fc3b3810c785ad09ff4ee04ed402f4aa60dcf6c936e43a"
TUN2PROXY_URL="https://github.com/tun2proxy/tun2proxy/archive/${TUN2PROXY_COMMIT}.tar.gz"
TUN2PROXY_PATCH="$IOS_DIR/patches/tun2proxy-0.8.2-ios-no-force-exit.patch"
TUN2PROXY_PATCH_SHA256="40e3cd39ca4cd589fb036e57a89bf2ff5e888d340bf7dd274d17eb4b7c4eea53"
TUN2PROXY_FRAMEWORK="$VENDOR_DIR/tun2proxy.xcframework"
TUN2PROXY_MARKER="$VENDOR_DIR/tun2proxy-${TUN2PROXY_VERSION}-ios-patched.verified"

usage() {
    printf '%s\n' \
        'Prepare and build the PdaLink iOS client on macOS.' \
        '' \
        'Usage:' \
        '  ./tools/build_ios.sh prepare   Verify, patch, and build tun2proxy for iOS.' \
        '  ./tools/build_ios.sh generate  Generate the Xcode project with XcodeGen.' \
        '  ./tools/build_ios.sh test      Run PdaLinkCore Swift unit tests.' \
        '  ./tools/build_ios.sh build     Generate and compile for a generic iOS device.'
}

# Prepare and build the PdaLink iOS client on macOS.
#
# Usage:
#   ./tools/build_ios.sh prepare   Verify, patch, and build tun2proxy for iOS.
#   ./tools/build_ios.sh generate  Generate the Xcode project with XcodeGen.
#   ./tools/build_ios.sh test      Run PdaLinkCore Swift unit tests.
#   ./tools/build_ios.sh build     Generate and compile for a generic iOS device.
#
# Requirements for generate/build: macOS, Xcode 16+, and XcodeGen.
# A physical iPhone and a signing team are required for the VPN smoke test.

sha256_file() {
    local path="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    else
        shasum -a 256 "$path" | awk '{print $1}'
    fi
}

require_command() {
    local command_name="$1"
    local install_hint="$2"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "$command_name is required ($install_hint)" >&2
        exit 1
    fi
}

prepare_dependency() {
    local patch_actual
    patch_actual="$(sha256_file "$TUN2PROXY_PATCH")"
    if [[ "$patch_actual" != "$TUN2PROXY_PATCH_SHA256" ]]; then
        echo "tun2proxy patch checksum mismatch: expected $TUN2PROXY_PATCH_SHA256, got $patch_actual" >&2
        exit 1
    fi

    mkdir -p "$VENDOR_DIR"
    if [[ -d "$TUN2PROXY_FRAMEWORK" && -f "$TUN2PROXY_MARKER" ]] \
        && grep -Fxq "$TUN2PROXY_SOURCE_SHA256 $TUN2PROXY_COMMIT $TUN2PROXY_PATCH_SHA256" "$TUN2PROXY_MARKER"; then
        echo "tun2proxy ${TUN2PROXY_VERSION} iOS patch is already verified"
        return
    fi
    if [[ -e "$TUN2PROXY_FRAMEWORK" ]]; then
        echo "Refusing to overwrite unverified $TUN2PROXY_FRAMEWORK" >&2
        echo "Move it aside, then rerun prepare." >&2
        exit 1
    fi

    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "Building the patched tun2proxy iOS XCFramework requires macOS and Xcode." >&2
        exit 1
    fi
    require_command curl "included with macOS"
    require_command tar "included with macOS"
    require_command patch "included with macOS"
    require_command xcodebuild "install Xcode 16 or later"
    require_command rustup "install from https://rustup.rs"
    require_command cargo "install from https://rustup.rs"
    require_command cbindgen "cargo install --locked cbindgen"

    local temp_dir archive source_dir actual
    temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/pdalink-ios.XXXXXX")"
    archive="$temp_dir/$TUN2PROXY_ARCHIVE"
    source_dir="$temp_dir/source"
    trap 'rm -rf "$temp_dir"' EXIT

    curl --fail --location --proto '=https' --tlsv1.2 "$TUN2PROXY_URL" --output "$archive"
    actual="$(sha256_file "$archive")"
    if [[ "$actual" != "$TUN2PROXY_SOURCE_SHA256" ]]; then
        echo "tun2proxy source checksum mismatch: expected $TUN2PROXY_SOURCE_SHA256, got $actual" >&2
        exit 1
    fi
    mkdir -p "$source_dir"
    tar -xzf "$archive" -C "$source_dir" --strip-components=1
    (cd "$source_dir" && patch -p1 -N -F 0 < "$TUN2PROXY_PATCH")

    rustup target add aarch64-apple-ios
    (cd "$source_dir" && cargo build --locked --release --target aarch64-apple-ios)
    mkdir -p "$source_dir/target/include"
    (cd "$source_dir" && cbindgen --config cbindgen.toml -o target/include/tun2proxy.h)
    printf '%s\n' \
        'framework module tun2proxy {' \
        '    umbrella header "tun2proxy.h"' \
        '    export *' \
        '    module * { export * }' \
        '}' > "$source_dir/target/include/tun2proxy.modulemap"

    if [[ ! -f "$source_dir/target/aarch64-apple-ios/release/libtun2proxy.a" ]]; then
        echo "tun2proxy build did not produce the expected static library" >&2
        exit 1
    fi
    xcodebuild -create-xcframework \
        -library "$source_dir/target/aarch64-apple-ios/release/libtun2proxy.a" \
        -headers "$source_dir/target/include" \
        -output "$source_dir/tun2proxy.xcframework"
    mv "$source_dir/tun2proxy.xcframework" "$TUN2PROXY_FRAMEWORK"
    printf '%s %s %s\n' \
        "$TUN2PROXY_SOURCE_SHA256" \
        "$TUN2PROXY_COMMIT" \
        "$TUN2PROXY_PATCH_SHA256" > "$TUN2PROXY_MARKER"
    rm -rf "$temp_dir"
    trap - EXIT
    echo "Prepared verified and iOS-patched tun2proxy ${TUN2PROXY_VERSION}"
}

generate_project() {
    prepare_dependency
    if ! command -v xcodegen >/dev/null 2>&1; then
        echo "xcodegen is required (brew install xcodegen)" >&2
        exit 1
    fi
    (cd "$IOS_DIR" && xcodegen generate)
}

run_tests() {
    if ! command -v swift >/dev/null 2>&1; then
        echo "Swift is required to run PdaLinkCore tests" >&2
        exit 1
    fi
    (cd "$IOS_DIR" && swift test)
}

build_project() {
    generate_project
    if ! command -v xcodebuild >/dev/null 2>&1; then
        echo "xcodebuild is required" >&2
        exit 1
    fi
    xcodebuild \
        -project "$IOS_DIR/PdaLinkClient.xcodeproj" \
        -scheme PdaLinkClient \
        -configuration Debug \
        -destination 'generic/platform=iOS' \
        CODE_SIGNING_ALLOWED=NO \
        build
}

case "${1:-}" in
    prepare) prepare_dependency ;;
    generate) generate_project ;;
    test) run_tests ;;
    build) build_project ;;
    -h|--help|help) usage ;;
    *) usage >&2; exit 2 ;;
esac
