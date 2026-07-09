#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB="$ROOT/tools/adb.sh"
PYTHON="${PYTHON:-python3}"
HOST="${PDALINK_HOST:-192.168.49.1}"
PORTS="${PDALINK_PORTS:-8000,8001}"
CURL_URL="${PDALINK_CURL_URL:-https://ifconfig.me}"
PROBE_TIMEOUT="${PDALINK_PROBE_TIMEOUT:-20}"
APK=""
START_ANDROID=1

usage() {
  cat <<EOF
Usage: $0 [--install APK] [--host HOST] [--ports PORTS] [--curl-url URL] [--probe-timeout SECONDS] [--no-start]

Starts the Android PdaLink proxy through the ADB-safe debug activity, waits for
the Linux proxy probe to pass, then runs doctor with a curl smoke test.
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

fail() {
  echo "ERROR: $*" >&2
  if declare -F collect_diagnostics >/dev/null; then
    collect_diagnostics >&2 || true
  fi
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)
      APK="${2:-}"
      [[ -n "$APK" ]] || fail "--install requires an APK path"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      [[ -n "$HOST" ]] || fail "--host requires a value"
      shift 2
      ;;
    --ports)
      PORTS="${2:-}"
      [[ -n "$PORTS" ]] || fail "--ports requires a value"
      shift 2
      ;;
    --curl-url)
      CURL_URL="${2:-}"
      [[ -n "$CURL_URL" ]] || fail "--curl-url requires a value"
      shift 2
      ;;
    --probe-timeout)
      PROBE_TIMEOUT="${2:-}"
      [[ -n "$PROBE_TIMEOUT" ]] || fail "--probe-timeout requires a value"
      shift 2
      ;;
    --no-start)
      START_ANDROID=0
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

collect_diagnostics() {
  echo
  echo "== diagnostics =="
  if command -v ip >/dev/null 2>&1; then
    run ip route get "$HOST" || true
  fi
  run "$ADB" devices -l || true
  run "$ADB" shell pidof dev.pdalink.prototype || true
  run "$ADB" shell dumpsys activity services dev.pdalink.prototype || true
  run "$ADB" logcat -d -t 120 -s PdaLinkProxy PdaLinkService || true
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1 not found in PATH"
  fi
}

wait_for_probe() {
  local deadline
  local last_log
  deadline=$((SECONDS + PROBE_TIMEOUT))
  last_log="$(mktemp "${TMPDIR:-/tmp}/pdalink-probe.XXXXXX")"
  trap 'rm -f "$last_log"' RETURN
  while (( SECONDS <= deadline )); do
    if "$PYTHON" "$ROOT/linux/pdalink_lite.py" probe --host "$HOST" --ports "$PORTS" >"$last_log" 2>&1; then
      cat "$last_log"
      return 0
    fi
    sleep 1
  done
  cat "$last_log" >&2 || true
  return 1
}

require_command "$PYTHON"

log "Checking ADB device"
run "$ADB" devices -l

if [[ -n "$APK" ]]; then
  log "Installing APK"
  run "$ADB" install -r "$APK"
fi

if [[ "$START_ANDROID" -eq 1 ]]; then
  log "Starting Android proxy service"
  run "$ADB" shell am start-foreground-service \
    -n dev.pdalink.prototype/.ProxyService \
    -a dev.pdalink.prototype.START_WIFI_DIRECT
fi

log "Waiting for proxy probe at $HOST ($PORTS)"
wait_for_probe || fail "proxy probe did not pass within ${PROBE_TIMEOUT}s"

log "Running doctor and curl smoke"
run "$PYTHON" "$ROOT/linux/pdalink_lite.py" doctor \
  --host "$HOST" \
  --ports "$PORTS" \
  --curl-url "$CURL_URL" \
  --adb

log "E2E smoke passed"
