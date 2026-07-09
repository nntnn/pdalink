#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
RUNTIME_DIR="${PDALINK_RUNTIME_DIR:-$ROOT/.tools/runtime}"
PID_FILE="${PDALINK_SYSTEM_PID_FILE:-$RUNTIME_DIR/pdalink-system.pid}"
STATE_FILE="${PDALINK_SYSTEM_STATE_FILE:-${PDALINK_SYSTEM_STATE:-$RUNTIME_DIR/pdalink-system.json}}"
LOG_FILE="${PDALINK_SYSTEM_LOG_FILE:-$RUNTIME_DIR/pdalink-system.log}"
START_TIMEOUT="${PDALINK_SERVICE_START_TIMEOUT:-15}"
STOP_TIMEOUT="${PDALINK_SYSTEM_STOP_TIMEOUT:-5}"
ROOT_HELPER="${PDALINK_ROOT_HELPER:-/usr/local/libexec/pdalink/pdalink-root}"
CONFIG_ARGS=()

usage() {
  cat <<EOF
Usage: $0 {start|status|stop|logs} [system options...]

Runs the experimental PdaLink system internet adapter as a repeatable local
service wrapper. Runtime files are stored under:
  $RUNTIME_DIR

Commands:
  start   Start system-start in the background and wait for the state file
  status  Show wrapper pid/log paths and system-status output
  stop    Stop the state-tracked adapter and clean routes
  logs    Print the adapter log

System config options are passed through to linux/pdalink_lite.py system-*
commands, for example --proxy-url, --iface, --address, or --routes. The start
command also accepts runtime options such as --no-dns, --connect-timeout,
--tcp-idle-timeout, and --verbose.
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

fail() {
  echo "ERROR: $*" >&2
  if [[ -f "$LOG_FILE" ]]; then
    echo
    echo "== system service log =="
    cat "$LOG_FILE" >&2 || true
  fi
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1 not found in PATH"
  fi
}

root_command() {
  if [[ -x "$ROOT_HELPER" ]]; then
    printf '%s\n' "$ROOT_HELPER"
  else
    printf '%s\n' "$PYTHON"
    printf '%s\n' "$ROOT/linux/pdalink_lite.py"
  fi
}

run_root() {
  local command=()
  mapfile -t command < <(root_command)
  sudo -n "${command[@]}" "$@"
}

pid_running() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || [[ -d "/proc/$pid" ]]
}

recorded_pid() {
  if [[ -f "$PID_FILE" ]]; then
    tr -d '[:space:]' <"$PID_FILE"
  fi
}

state_pid() {
  if [[ -f "$STATE_FILE" ]]; then
    "$PYTHON" - "$STATE_FILE" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except OSError:
    raise SystemExit(0)
pid = payload.get("pid", "")
print(pid if isinstance(pid, int) else "")
PY
  fi
}

prepare_runtime_dir() {
  mkdir -p "$RUNTIME_DIR"
}

collect_config_args() {
  CONFIG_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --iface|--address|--mtu|--routes|--proxy-url|--route-mode|--route-metric|--dns-mode|--dns-servers)
        [[ -n "${2:-}" ]] || fail "$1 requires a value"
        CONFIG_ARGS+=("$1" "$2")
        shift 2
        ;;
      --iface=*|--address=*|--mtu=*|--routes=*|--proxy-url=*|--route-mode=*|--route-metric=*|--dns-mode=*|--dns-servers=*)
        CONFIG_ARGS+=("$1")
        shift
        ;;
      --connect-timeout|--tcp-idle-timeout|--local-port|--remote-port|--verify-timeout|--state-file)
        shift 2
        ;;
      --no-dns|--verbose|--start-usb|--no-start-android|--no-probe-proxy)
        shift
        ;;
      *)
        shift
        ;;
    esac
  done
}

cleanup_failed_start() {
  local pid="$1"
  shift
  collect_config_args "$@"
  if pid_running "$pid"; then
    kill -INT "$pid" >/dev/null 2>&1 || true
  fi
  run_root system-stop "${CONFIG_ARGS[@]}" \
    --state-file "$STATE_FILE" \
    --stop-timeout "$STOP_TIMEOUT" >/dev/null 2>&1 || true
}

wait_for_state() {
  local pid="$1"
  local deadline
  deadline=$((SECONDS + START_TIMEOUT))
  while (( SECONDS <= deadline )); do
    if ! pid_running "$pid"; then
      return 1
    fi
    if [[ -f "$STATE_FILE" ]]; then
      return 0
    fi
    if grep -q "system internet:" "$LOG_FILE" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

start_service() {
  prepare_runtime_dir
  require_command "$PYTHON"
  require_command sudo

  local old_pid
  old_pid="$(recorded_pid)"
  if [[ -n "$old_pid" ]] && pid_running "$old_pid"; then
    fail "service already appears to be running with pid $old_pid"
  fi

  rm -f "$PID_FILE"
  : >"$LOG_FILE"
  log "Starting PdaLink system adapter"
  local root_command_args=()
  mapfile -t root_command_args < <(root_command)
  nohup sudo -n "${root_command_args[@]}" system-start "$@" \
    --state-file "$STATE_FILE" >>"$LOG_FILE" 2>&1 </dev/null &
  local pid=$!
  printf '%s\n' "$pid" >"$PID_FILE"

  if ! wait_for_state "$pid"; then
    rm -f "$PID_FILE"
    cleanup_failed_start "$pid" "$@"
    fail "system adapter did not become ready within ${START_TIMEOUT}s"
  fi
  local state_pid_value
  state_pid_value="$(state_pid)"
  if [[ -n "$state_pid_value" ]]; then
    printf '%s\n' "$state_pid_value" >"$PID_FILE"
  fi

  echo "service: running"
  echo "pid-file: $PID_FILE"
  echo "state-file: $STATE_FILE"
  echo "log-file: $LOG_FILE"
  echo "root-helper: ${root_command_args[*]}"
}

status_service() {
  prepare_runtime_dir
  collect_config_args "$@"
  local pid
  pid="$(state_pid)"
  if [[ -z "$pid" ]]; then
    pid="$(recorded_pid)"
  fi
  if [[ -n "$pid" ]] && pid_running "$pid"; then
    echo "wrapper: RUNNING pid $pid"
  elif [[ -n "$pid" ]]; then
    echo "wrapper: STALE pid $pid"
  else
    echo "wrapper: MISSING $PID_FILE"
  fi
  echo "state-file: $STATE_FILE"
  echo "log-file: $LOG_FILE"
  "$PYTHON" "$ROOT/linux/pdalink_lite.py" system-status "${CONFIG_ARGS[@]}" --state-file "$STATE_FILE" --no-probe-proxy
}

stop_service() {
  prepare_runtime_dir
  require_command "$PYTHON"
  require_command sudo
  collect_config_args "$@"
  log "Stopping PdaLink system adapter"
  run_root system-stop "${CONFIG_ARGS[@]}" \
    --state-file "$STATE_FILE" \
    --stop-timeout "$STOP_TIMEOUT"
  local pid
  pid="$(recorded_pid)"
  if [[ -z "$pid" ]] || ! pid_running "$pid"; then
    rm -f "$PID_FILE"
  fi
}

show_logs() {
  if [[ ! -f "$LOG_FILE" ]]; then
    fail "log file not found: $LOG_FILE"
  fi
  cat "$LOG_FILE"
}

main() {
  local command="${1:-}"
  if [[ -z "$command" || "$command" == "-h" || "$command" == "--help" ]]; then
    usage
    exit 0
  fi
  shift
  case "$command" in
    start)
      start_service "$@"
      ;;
    status)
      status_service "$@"
      ;;
    stop)
      stop_service "$@"
      ;;
    logs)
      show_logs
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
