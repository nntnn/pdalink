#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
URL="${PDALINK_SYSTEM_TEST_URL:-https://1.1.1.1/cdn-cgi/trace}"
IFACE="${PDALINK_TUN_IFACE:-pdalink0}"
ADDRESS="${PDALINK_TUN_ADDRESS:-10.66.0.2/24}"
MTU="${PDALINK_TUN_MTU:-1500}"
ROUTES="${PDALINK_TUN_ROUTES:-0.0.0.0/1,128.0.0.0/1}"
USB_LOCAL_PORT="${PDALINK_USB_LOCAL_PORT:-18080}"
USB_REMOTE_PORT="${PDALINK_USB_REMOTE_PORT:-8000}"
SYSTEM_PROXY_URL="${PDALINK_SYSTEM_PROXY_URL:-}"
USB_VERIFY_TIMEOUT="${PDALINK_USB_VERIFY_TIMEOUT:-15}"
START_TIMEOUT="${PDALINK_SYSTEM_START_TIMEOUT:-15}"
CURL_TIMEOUT="${PDALINK_SYSTEM_CURL_TIMEOUT:-30}"
DNS_SERVER="${PDALINK_SYSTEM_DNS_SERVER:-8.8.8.8}"
START_ANDROID=1
START_USB=1
DNS_CHECK=1
EXPECT_OFFLINE=0
APK=""
SYSTEM_PID=""
LOG_FILE=""
STATE_FILE=""
CLEANUP_ROUTES=0
CLEANUP_STOP_TIMEOUT="${PDALINK_SYSTEM_STOP_TIMEOUT:-5}"

usage() {
  cat <<EOF
Usage: $0 [options]

Starts the USB Android proxy, starts the built-in Linux TUN adapter, curls
without proxy environment variables, then cleans up routes.

Options:
  --install APK             Install an Android APK before starting the proxy
  --url URL                 Test URL, default: $URL
  --expect-offline          Fail if direct curl works before the USB path starts
  --no-start-android        Do not send the Android START intent
  --no-start-usb            Assume adb forward/proxy are already running
  --local-port PORT         Local adb forward port, default: $USB_LOCAL_PORT
  --remote-port PORT        Android proxy port, default: $USB_REMOTE_PORT
  --proxy-url URL           Proxy URL for system-start; defaults to local adb forward
  --dns-server IP           UDP/53 smoke target, default: $DNS_SERVER
  --no-dns-check            Skip the UDP/53 DNS smoke check
  --verify-timeout SECONDS  USB proxy verification timeout, default: $USB_VERIFY_TIMEOUT
  --start-timeout SECONDS   TUN route startup timeout, default: $START_TIMEOUT
  --curl-timeout SECONDS    Curl timeout, default: $CURL_TIMEOUT
  --iface IFACE             TUN interface, default: $IFACE
  --address CIDR            TUN address, default: $ADDRESS
  --mtu MTU                 TUN MTU, default: $MTU
  --routes ROUTES           Comma-separated split routes, default: $ROUTES

Offline test flow:
  1. Turn off laptop Wi-Fi/Ethernet, keep the phone on cellular data.
  2. Keep USB debugging authorized.
  3. Run: $0 --expect-offline

The default URL uses an IP literal so the first test proves TCP internet before
depending on the laptop's current DNS configuration. Pass --url https://ifconfig.me
when you want to include DNS resolution in the smoke test.
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
  if [[ -n "${LOG_FILE:-}" && -f "$LOG_FILE" ]]; then
    echo
    echo "== system-start log =="
    cat "$LOG_FILE" >&2 || true
  fi
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1 not found in PATH"
  fi
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "$CLEANUP_ROUTES" -eq 1 ]]; then
    if [[ -n "${SYSTEM_PID:-}" ]] && kill -0 "$SYSTEM_PID" >/dev/null 2>&1; then
      if [[ -n "${STATE_FILE:-}" && -f "$STATE_FILE" ]]; then
        log "Stopping state-tracked system adapter"
        sudo "$PYTHON" "$ROOT/linux/pdalink_lite.py" system-stop \
          --iface "$IFACE" \
          --address "$ADDRESS" \
          --mtu "$MTU" \
          --routes "$ROUTES" \
          --proxy-url "$SYSTEM_PROXY_URL" \
          --state-file "$STATE_FILE" \
          --stop-timeout "$CLEANUP_STOP_TIMEOUT" >/dev/null 2>&1 || true
        wait "$SYSTEM_PID" >/dev/null 2>&1 || true
      else
        log "Interrupting system adapter before fallback route cleanup"
        kill -INT "$SYSTEM_PID" >/dev/null 2>&1 || true
        wait_seconds="${CLEANUP_STOP_TIMEOUT%%.*}"
        if [[ ! "$wait_seconds" =~ ^[0-9]+$ ]]; then
          wait_seconds=5
        fi
        deadline=$((SECONDS + wait_seconds))
        while (( SECONDS <= deadline )) && kill -0 "$SYSTEM_PID" >/dev/null 2>&1; do
          sleep 1
        done
      fi
    fi
    if [[ -n "${SYSTEM_PID:-}" ]] && kill -0 "$SYSTEM_PID" >/dev/null 2>&1; then
      log "Terminating remaining system adapter process"
      kill -TERM "$SYSTEM_PID" >/dev/null 2>&1 || true
      wait "$SYSTEM_PID" >/dev/null 2>&1 || true
    fi
    log "Cleaning system routes"
    sudo "$PYTHON" "$ROOT/linux/pdalink_lite.py" system-stop \
      --iface "$IFACE" \
      --address "$ADDRESS" \
      --mtu "$MTU" \
      --routes "$ROUTES" \
      --proxy-url "$SYSTEM_PROXY_URL" \
      --state-file "$STATE_FILE" \
      --stop-timeout "$CLEANUP_STOP_TIMEOUT" >/dev/null 2>&1 || true
  fi
  if [[ -n "${LOG_FILE:-}" && -f "$LOG_FILE" ]]; then
    if [[ "$status" -eq 0 ]]; then
      rm -f "$LOG_FILE"
    else
      echo "system-start log preserved at $LOG_FILE" >&2
    fi
  fi
  exit "$status"
}

direct_curl() {
  env \
    -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    -u all_proxy -u ALL_PROXY \
    curl --fail --show-error --silent --location --http1.1 --noproxy '*' \
      --max-time "$1" "$URL"
}

proxy_pac_url() {
  printf '%s/proxy.pac\n' "${SYSTEM_PROXY_URL%/}"
}

check_proxy_pac() {
  curl --fail --show-error --silent --location --http1.1 \
    --max-time "$USB_VERIFY_TIMEOUT" "$(proxy_pac_url)" >/dev/null
}

check_udp_dns() {
  "$PYTHON" - "$DNS_SERVER" <<'PY'
import os
import random
import socket
import struct
import sys

server = sys.argv[1]
query_id = random.randint(0, 65535)
name = b"example" + bytes([3]) + b"com" + b"\0"
packet = (
    struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    + bytes([7])
    + name
    + struct.pack("!HH", 1, 1)
)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(float(os.environ.get("PDALINK_SYSTEM_DNS_TIMEOUT", "10")))
sock.sendto(packet, (server, 53))
response, _ = sock.recvfrom(512)
if len(response) < 12:
    raise SystemExit("short DNS response")
rid, flags, _qd, answers, _ns, _ar = struct.unpack("!HHHHHH", response[:12])
if rid != query_id:
    raise SystemExit("DNS response id mismatch")
if flags & 0x000F:
    raise SystemExit(f"DNS rcode {flags & 0x000F}")
if answers < 1:
    raise SystemExit("DNS response had no answers")
print(f"DNS OK {server}:53 answers={answers}")
PY
}

wait_for_system_route() {
  local deadline
  deadline=$((SECONDS + START_TIMEOUT))
  while (( SECONDS <= deadline )); do
    if [[ -n "${SYSTEM_PID:-}" ]] && ! kill -0 "$SYSTEM_PID" >/dev/null 2>&1; then
      return 1
    fi
    if [[ -n "${STATE_FILE:-}" && -f "$STATE_FILE" ]]; then
      return 0
    fi
    if grep -q "system internet:" "$LOG_FILE" 2>/dev/null; then
      return 0
    fi
    if command -v ip >/dev/null 2>&1 \
      && ip route show 0.0.0.0/1 2>/dev/null | grep -Eq "\\bdev[[:space:]]+$IFACE\\b"; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)
      APK="${2:-}"
      [[ -n "$APK" ]] || fail "--install requires an APK path"
      shift 2
      ;;
    --url)
      URL="${2:-}"
      [[ -n "$URL" ]] || fail "--url requires a value"
      shift 2
      ;;
    --expect-offline)
      EXPECT_OFFLINE=1
      shift
      ;;
    --no-start-android)
      START_ANDROID=0
      shift
      ;;
    --no-start-usb)
      START_USB=0
      shift
      ;;
    --local-port)
      USB_LOCAL_PORT="${2:-}"
      [[ -n "$USB_LOCAL_PORT" ]] || fail "--local-port requires a value"
      shift 2
      ;;
    --remote-port)
      USB_REMOTE_PORT="${2:-}"
      [[ -n "$USB_REMOTE_PORT" ]] || fail "--remote-port requires a value"
      shift 2
      ;;
    --proxy-url)
      SYSTEM_PROXY_URL="${2:-}"
      [[ -n "$SYSTEM_PROXY_URL" ]] || fail "--proxy-url requires a value"
      shift 2
      ;;
    --dns-server)
      DNS_SERVER="${2:-}"
      [[ -n "$DNS_SERVER" ]] || fail "--dns-server requires a value"
      shift 2
      ;;
    --no-dns-check)
      DNS_CHECK=0
      shift
      ;;
    --verify-timeout)
      USB_VERIFY_TIMEOUT="${2:-}"
      [[ -n "$USB_VERIFY_TIMEOUT" ]] || fail "--verify-timeout requires a value"
      shift 2
      ;;
    --start-timeout)
      START_TIMEOUT="${2:-}"
      [[ -n "$START_TIMEOUT" ]] || fail "--start-timeout requires a value"
      shift 2
      ;;
    --curl-timeout)
      CURL_TIMEOUT="${2:-}"
      [[ -n "$CURL_TIMEOUT" ]] || fail "--curl-timeout requires a value"
      shift 2
      ;;
    --iface)
      IFACE="${2:-}"
      [[ -n "$IFACE" ]] || fail "--iface requires a value"
      shift 2
      ;;
    --address)
      ADDRESS="${2:-}"
      [[ -n "$ADDRESS" ]] || fail "--address requires a value"
      shift 2
      ;;
    --mtu)
      MTU="${2:-}"
      [[ -n "$MTU" ]] || fail "--mtu requires a value"
      shift 2
      ;;
    --routes)
      ROUTES="${2:-}"
      [[ -n "$ROUTES" ]] || fail "--routes requires a value"
      shift 2
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

require_command "$PYTHON"
require_command curl
require_command sudo

if [[ -z "$SYSTEM_PROXY_URL" ]]; then
  SYSTEM_PROXY_URL="http://127.0.0.1:$USB_LOCAL_PORT"
fi

if [[ "$EXPECT_OFFLINE" -eq 1 ]]; then
  log "Checking that direct internet is offline before USB system routing"
  if direct_curl 5 >/dev/null 2>&1; then
    fail "direct curl succeeded before the USB route started; turn off laptop Wi-Fi/Ethernet or omit --expect-offline"
  fi
  echo "direct curl failed as expected"
fi

if [[ "$START_USB" -eq 1 ]]; then
  usb_args=(--local-port "$USB_LOCAL_PORT" --remote-port "$USB_REMOTE_PORT" --verify --verify-timeout "$USB_VERIFY_TIMEOUT")
  if [[ -n "$APK" ]]; then
    usb_args=(--install "$APK" "${usb_args[@]}")
  fi
  if [[ "$START_ANDROID" -eq 0 ]]; then
    usb_args+=("--no-start-android")
  fi
  log "Starting USB proxy"
  run "$ROOT/tools/pdalink.sh" usb start "${usb_args[@]}"
else
  log "Checking existing proxy"
  run curl --fail --show-error --silent --location --http1.1 \
    --max-time "$USB_VERIFY_TIMEOUT" "$(proxy_pac_url)"
fi

log "Preparing sudo for TUN route changes"
run sudo -v

LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/pdalink-system-start.XXXXXX.log")"
STATE_FILE="$(mktemp "${TMPDIR:-/tmp}/pdalink-system-state.XXXXXX.json")"
rm -f "$STATE_FILE"
trap cleanup EXIT INT TERM

log "Starting system internet adapter"
sudo env PYTHONUNBUFFERED=1 "$PYTHON" "$ROOT/linux/pdalink_lite.py" system-start \
  --iface "$IFACE" \
  --address "$ADDRESS" \
  --mtu "$MTU" \
  --routes "$ROUTES" \
  --proxy-url "$SYSTEM_PROXY_URL" \
  --state-file "$STATE_FILE" \
  >"$LOG_FILE" 2>&1 &
SYSTEM_PID=$!
CLEANUP_ROUTES=1

if ! wait_for_system_route; then
  fail "system route did not become ready within ${START_TIMEOUT}s"
fi

log "Running no-proxy internet curl through Android USB"
direct_curl "$CURL_TIMEOUT"
echo
if [[ "$DNS_CHECK" -eq 1 ]]; then
  log "Running UDP/53 DNS smoke through Android USB"
  check_udp_dns
fi
echo "PdaLink USB system internet test passed"
