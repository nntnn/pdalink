#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
LOCAL_PORT="${PDALINK_USB_LOCAL_PORT:-18080}"
REMOTE_PORT="${PDALINK_USB_REMOTE_PORT:-8000}"
VERIFY_TIMEOUT="${PDALINK_USB_VERIFY_TIMEOUT:-15}"
PROXY_URL="${PDALINK_RESCUE_PROXY_URL:-http://127.0.0.1:${LOCAL_PORT}}"
ROUTE_MODE="${PDALINK_RESCUE_ROUTE_MODE:-active_override}"
DNS_MODE="${PDALINK_RESCUE_DNS_MODE:-resolved}"
LOG_LINES="${PDALINK_RESCUE_LOG_LINES:-200}"
CURL_URL="${PDALINK_RESCUE_CURL_URL:-https://ifconfig.me}"

usage() {
  cat <<EOF
Usage:
  $0 status
  $0 wifi [--deep|--restart-resolved|--reset-networkmanager]
  $0 restart
  $0 udev [--dry-run] [--no-reload]
  $0 adb-reset
  $0 logs [--lines N]

Emergency shortcuts:
  status     Read-only diagnostics for ADB, forward, proxy, route, DNS, and curl.
  wifi       Stop PdaLink and remove pdalink0 route/DNS state so normal Wi-Fi works.
  restart    Clean the PdaLink path, then start USB proxy and system service again.
  udev       Refresh Android USB udev rules, reset ADB, and show USB status.
  adb-reset  Reset only adb server and the tcp:${LOCAL_PORT} forward.
  logs       Print recent watcher and system adapter logs.

Environment:
  PDALINK_USB_LOCAL_PORT      default: ${LOCAL_PORT}
  PDALINK_USB_REMOTE_PORT     default: ${REMOTE_PORT}
  PDALINK_RESCUE_PROXY_URL    default: ${PROXY_URL}
  PDALINK_RESCUE_ROUTE_MODE   default: ${ROUTE_MODE}
  PDALINK_RESCUE_DNS_MODE     default: ${DNS_MODE}
EOF
}

section() {
  printf '\n==> %s\n' "$*"
}

print_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

run() {
  print_cmd "$@"
  "$@"
}

try() {
  print_cmd "$@"
  "$@"
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    echo "WARN: command failed with status ${status}: $*" >&2
  fi
  return 0
}

try_sudo() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    try "$@"
  else
    try sudo "$@"
  fi
}

have() {
  command -v "$1" >/dev/null 2>&1
}

show_matching_routes() {
  print_cmd ip route show
  if have rg; then
    ip route show | rg 'pdalink0|0.0.0.0/1|128.0.0.0/1|default' || true
  else
    ip route show | grep -E 'pdalink0|0\.0\.0\.0/1|128\.0\.0\.0/1|default' || true
  fi
}

status_cmd() {
  section "PdaLink watcher"
  try bash "$ROOT/tools/pdalink.sh" watch status

  section "USB proxy"
  try bash "$ROOT/tools/pdalink.sh" usb status --local-port "$LOCAL_PORT" --remote-port "$REMOTE_PORT"

  section "System service"
  try bash "$ROOT/tools/pdalink.sh" system service status \
    --proxy-url "$PROXY_URL" \
    --route-mode "$ROUTE_MODE" \
    --dns-mode "$DNS_MODE"

  section "Routes"
  try ip route get 1.1.1.1
  show_matching_routes

  section "DNS"
  try resolvectl status pdalink0

  section "Internet probe"
  try curl -4 --noproxy '*' --max-time 10 -sS "$CURL_URL"
  printf '\n'
}

wifi_cmd() {
  local restart_resolved=0
  local reset_networkmanager=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --deep)
        restart_resolved=1
        reset_networkmanager=1
        shift
        ;;
      --restart-resolved)
        restart_resolved=1
        shift
        ;;
      --reset-networkmanager)
        reset_networkmanager=1
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        echo "ERROR: unknown wifi option: $1" >&2
        usage >&2
        return 2
        ;;
    esac
  done

  section "Stop PdaLink services"
  try bash "$ROOT/tools/pdalink.sh" system service stop \
    --proxy-url "$PROXY_URL" \
    --route-mode "$ROUTE_MODE" \
    --dns-mode "$DNS_MODE"
  try bash "$ROOT/tools/pdalink.sh" usb stop --local-port "$LOCAL_PORT" --remote-port "$REMOTE_PORT" --stop-android

  section "Remove pdalink0 route and DNS state"
  try_sudo resolvectl revert pdalink0
  try_sudo ip route del 0.0.0.0/1 dev pdalink0
  try_sudo ip route del 128.0.0.0/1 dev pdalink0
  try_sudo ip link del dev pdalink0
  try resolvectl flush-caches

  if [[ "$restart_resolved" -eq 1 ]]; then
    section "Restart systemd-resolved"
    try_sudo systemctl restart systemd-resolved
  fi

  if [[ "$reset_networkmanager" -eq 1 ]]; then
    section "Reset NetworkManager networking"
    try nmcli networking off
    sleep 2
    try nmcli networking on
  fi

  section "Post-cleanup status"
  show_matching_routes
  try resolvectl status pdalink0
  try curl -4 --noproxy '*' --max-time 10 -sS "$CURL_URL"
  printf '\n'
}

restart_cmd() {
  section "Clean current PdaLink path"
  wifi_cmd

  section "Start USB proxy"
  run bash "$ROOT/tools/pdalink.sh" usb start \
    --local-port "$LOCAL_PORT" \
    --remote-port "$REMOTE_PORT" \
    --verify \
    --verify-timeout "$VERIFY_TIMEOUT" || return $?

  section "Start system service"
  run bash "$ROOT/tools/pdalink.sh" system service start \
    --proxy-url "$PROXY_URL" \
    --route-mode "$ROUTE_MODE" \
    --dns-mode "$DNS_MODE" || return $?

  section "Internet probe"
  try curl -4 --noproxy '*' --max-time 15 -sS "$CURL_URL"
  printf '\n'
}

adb_reset_cmd() {
  section "ADB forward before reset"
  try bash "$ROOT/tools/adb.sh" forward --list

  section "Reset ADB"
  try bash "$ROOT/tools/adb.sh" forward --remove "tcp:${LOCAL_PORT}"
  try bash "$ROOT/tools/adb.sh" kill-server
  try bash "$ROOT/tools/adb.sh" start-server

  section "ADB devices"
  try bash "$ROOT/tools/adb.sh" devices -l
}

udev_cmd() {
  local dry_run=0
  local install_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        dry_run=1
        install_args+=("--dry-run")
        shift
        ;;
      --no-reload)
        install_args+=("--no-reload")
        shift
        ;;
      --rules-file)
        install_args+=("--rules-file" "${2:-}")
        [[ -n "${2:-}" ]] || {
          echo "ERROR: --rules-file requires a value" >&2
          return 2
        }
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        echo "ERROR: unknown udev option: $1" >&2
        usage >&2
        return 2
        ;;
    esac
  done

  section "Refresh Android USB udev rules"
  if [[ "$dry_run" -eq 1 ]]; then
    run bash "$ROOT/tools/install_linux_udev.sh" "${install_args[@]}"
    return 0
  fi
  try_sudo bash "$ROOT/tools/install_linux_udev.sh" "${install_args[@]}"

  section "Reset ADB"
  try bash "$ROOT/tools/adb.sh" kill-server
  try bash "$ROOT/tools/adb.sh" start-server

  section "USB status"
  try bash "$ROOT/tools/pdalink.sh" usb status --local-port "$LOCAL_PORT" --remote-port "$REMOTE_PORT"
}

logs_cmd() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lines)
        LOG_LINES="${2:-}"
        [[ -n "$LOG_LINES" ]] || {
          echo "ERROR: --lines requires a value" >&2
          return 2
        }
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        echo "ERROR: unknown logs option: $1" >&2
        usage >&2
        return 2
        ;;
    esac
  done

  section "Watcher test log"
  try tail -n "$LOG_LINES" "$ROOT/.tools/runtime/unplug-replug-test.log"

  section "System adapter log"
  try tail -n "$LOG_LINES" "$ROOT/.tools/runtime/pdalink-system.log"

  section "Watcher state"
  try cat "$ROOT/.tools/runtime/pdalink-watch.json"

  section "System state"
  try cat "$ROOT/.tools/runtime/pdalink-system.json"
}

main() {
  case "${1:-}" in
    -h|--help|"")
      usage
      ;;
    status)
      shift
      status_cmd "$@"
      ;;
    wifi|restore-wifi|panic)
      shift
      wifi_cmd "$@"
      ;;
    restart)
      shift
      restart_cmd "$@"
      ;;
    udev)
      shift
      udev_cmd "$@"
      ;;
    adb-reset)
      shift
      adb_reset_cmd "$@"
      ;;
    logs)
      shift
      logs_cmd "$@"
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

main "$@"
