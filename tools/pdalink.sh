#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
ADB="$ROOT/tools/adb.sh"
USB_LOCAL_PORT="${PDALINK_USB_LOCAL_PORT:-18080}"
USB_REMOTE_PORT="${PDALINK_USB_REMOTE_PORT:-8000}"
USB_VERIFY_TIMEOUT="${PDALINK_USB_VERIFY_TIMEOUT:-15}"

usage() {
  cat <<EOF
Usage:
  $0 install [linux-install options...]
  $0 usb start [--install APK] [--local-port PORT] [--remote-port PORT] [--verify] [--verify-timeout SECONDS] [--no-start-android]
  $0 usb status [--local-port PORT] [--remote-port PORT]
  $0 usb test [--install APK] [usb-smoke options...]
  $0 usb stop [--local-port PORT] [--remote-port PORT] [--stop-android]
  $0 wifi {start-android|stop-android|local-hotspot|scan|connect|status|internet|probe|env|curl-test|test|smoke|doctor} [...]
  $0 connection status [--json] [connection-status options...]
  $0 product-usb {status|check|aoa-start} [...]
  $0 status [--json]
  $0 device {scan|list|trust|untrust|set-default} [...]
  $0 config {list|get|set} [...]
  $0 autostart {enable|disable|status}
  $0 setup {plan|wizard|apply} [...]
  $0 rescue {status|wifi|restart|udev|adb-reset|logs}
  $0 system status [system-status options...]
  $0 system plan [system-plan options...]
  $0 system start [system-start options...]
  $0 system stop [system-stop options...]
  $0 system service {start|status|stop|logs} [system options...]
  $0 system adapter [system-adapter options...]
  $0 system test [system-connect-test options...]
  $0 system up [system-up options...]
  $0 system down [system-down options...]
  $0 watch {run|once|status} [watch options...]

Common USB flow:
  $0 usb start --install android/app/build/manual/app-debug.apk
  $0 usb status
  $0 usb test --no-start-android
  $0 usb stop

Native Linux managed flow:
  $0 install
  $0 device trust --connected --name my-phone --default
  $0 config set auto_connect on_trusted_connect
  $0 watch run
  $0 watch status

Wi-Fi Direct manual flow:
  $0 wifi start-android
  $0 wifi connect --password 'android-shown-password'
  $0 wifi status

Emergency shortcuts:
  $0 rescue status
  $0 rescue wifi
  $0 rescue udev
  $0 rescue restart
EOF
}

usb_usage() {
  cat <<EOF
Usage:
  $0 usb start [--install APK] [--local-port PORT] [--remote-port PORT] [--verify] [--verify-timeout SECONDS] [--no-start-android]
  $0 usb status [--local-port PORT] [--remote-port PORT]
  $0 usb test [--install APK] [usb-smoke options...]
  $0 usb stop [--local-port PORT] [--remote-port PORT] [--stop-android]
EOF
}

wifi_usage() {
  cat <<EOF
Usage:
  $0 wifi start-android [--install APK]
  $0 wifi stop-android
  $0 wifi local-hotspot sync [--mode auto|pinned] [--connect] [--reset-pin] [--start-android]
  $0 wifi scan
  $0 wifi connect [--ssid SSID] --password PASSWORD [--iface IFACE] [--ssid-pattern DIRECT-] [--host HOST] [--ports PORTS]
  $0 wifi status [--ssid-pattern DIRECT-] [--iface IFACE] [--host HOST] [--ports PORTS]
  $0 wifi internet start [--ssid SSID] --password PASSWORD [--iface IFACE] [--host HOST] [--ports PORTS] [--state-file PATH]
  $0 wifi internet status [--ssid-pattern DIRECT-] [--iface IFACE] [--host HOST] [--ports PORTS] [--state-file PATH]
  $0 wifi internet test [--url URL] [--host HOST] [--ports PORTS] [--state-file PATH]
  $0 wifi internet stop [--state-file PATH] [--keep-wifi] [--force]
  $0 wifi probe [--host HOST] [--ports PORTS] [--show-pac]
  $0 wifi env [--host HOST] [--ports PORTS] [--probe]
  $0 wifi curl-test [--url URL] [--host HOST] [--ports PORTS]
  $0 wifi test [e2e_smoke options...]
  $0 wifi smoke [e2e_smoke options...]
  $0 wifi doctor [doctor options...]

Manual Wi-Fi Direct flow:
  1. Start Wi-Fi Direct in the Android app or run: $0 wifi start-android
  2. Copy the password shown in the Android app.
  3. For a connection only, run: $0 wifi connect --password '...'
  4. For a managed GNOME proxy, run: $0 wifi internet start --password '...'
  5. Run: $0 wifi internet status

Local Hotspot credential sync (ADB only):
  $0 wifi local-hotspot sync --mode auto --start-android --connect
  $0 wifi local-hotspot sync --mode pinned --connect
  $0 wifi local-hotspot sync --mode pinned --reset-pin --connect
EOF
}

product_usb_usage() {
  cat <<EOF
Usage:
  $0 product-usb status [--state-file PATH] [--json]
  $0 product-usb check [--state-file PATH] [--json] [--curl-url URL]
  $0 product-usb aoa-start [--dry-run] [--serial FINGERPRINT] [--vid HEX] [--pid HEX]
  $0 product-usb bridge [--vid HEX] [--pid HEX] [--local-port PORT] [--state-file PATH] [--once]
EOF
}

connection_usage() {
  cat <<EOF
Usage:
  $0 connection status [--json] [--local-port PORT] [--remote-port PORT] [--route-target IP] [--curl-url URL]

Shows whether Ubuntu internet is currently flowing through the PdaLink USB
tunnel and pdalink0 route.
EOF
}

system_usage() {
  cat <<EOF
Usage:
  $0 system status [--adapter-command CMD] [--no-probe-proxy]
  $0 system plan [--adapter-command CMD]
  $0 system start [--proxy-url URL] [--no-dns] [--verbose]
  $0 system stop [--state-file PATH] [--stop-timeout SECONDS]
  $0 system service {start|status|stop|logs} [system options...]
  $0 system adapter [--proxy-url URL] [--no-dns] [--verbose]
  $0 system test [--expect-offline] [--url URL]
  $0 system up [--apply --adapter-ready] [system options...]
  $0 system down [--apply] [system options...]

This is the experimental whole-system routing control plane. It expects a
packet adapter that can bridge a Linux TUN interface to the Android HTTP proxy.
The built-in adapter handles IPv4 TCP and UDP/53 DNS for the USB proxy path.
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

run() {
  {
    printf '+'
    local mask_next=0
    local arg
    for arg in "$@"; do
      if [[ "$mask_next" -eq 1 ]]; then
        printf ' %q' "<redacted>"
        mask_next=0
        continue
      fi
      printf ' %q' "$arg"
      case "$arg" in
        --password|wifi-sec.psk)
          mask_next=1
          ;;
      esac
    done
    printf '\n'
  } >&2
  "$@"
}

fail() {
  echo "ERROR: $*" >&2
  echo "Run '$0 usb status' for the current USB tunnel state." >&2
  exit 1
}

require_python() {
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    fail "$PYTHON not found in PATH"
  fi
}

install_if_requested() {
  local apk=""
  local -n out_args_ref=$1
  shift
  out_args_ref=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --install)
        apk="${2:-}"
        [[ -n "$apk" ]] || fail "--install requires an APK path"
        shift 2
        ;;
      *)
        out_args_ref+=("$1")
        shift
        ;;
    esac
  done

  if [[ -n "$apk" ]]; then
    log "Installing APK"
    run "$ADB" install -r "$apk"
  fi
}

usb_start() {
  local args=()
  local pass=()
  local no_start=0
  install_if_requested args "$@"
  while [[ ${#args[@]} -gt 0 ]]; do
    case "${args[0]}" in
      --local-port)
        USB_LOCAL_PORT="${args[1]:-}"
        [[ -n "$USB_LOCAL_PORT" ]] || fail "--local-port requires a value"
        shift_array args 2
        ;;
      --remote-port)
        USB_REMOTE_PORT="${args[1]:-}"
        [[ -n "$USB_REMOTE_PORT" ]] || fail "--remote-port requires a value"
        shift_array args 2
        ;;
      --verify-timeout)
        USB_VERIFY_TIMEOUT="${args[1]:-}"
        [[ -n "$USB_VERIFY_TIMEOUT" ]] || fail "--verify-timeout requires a value"
        shift_array args 2
        ;;
      --verify)
        # The wrapper always verifies after creating the forward. Accept this
        # for parity with lower-level pdalink_lite.py and system-plan output.
        shift_array args 1
        ;;
      --no-start-android)
        no_start=1
        shift_array args 1
        ;;
      -h|--help)
        usb_usage
        exit 0
        ;;
      *)
        fail "unknown usb start option: ${args[0]}"
        ;;
    esac
  done
  pass=(--local-port "$USB_LOCAL_PORT" --remote-port "$USB_REMOTE_PORT" --verify --verify-timeout "$USB_VERIFY_TIMEOUT")
  if [[ "$no_start" -eq 1 ]]; then
    pass+=("--no-start-android")
  fi
  log "Starting USB proxy"
  run "$PYTHON" "$ROOT/linux/pdalink_lite.py" usb-start "${pass[@]}"
}

shift_array() {
  local -n array_ref=$1
  local count=$2
  array_ref=("${array_ref[@]:count}")
}

usb_status() {
  require_python
  run "$PYTHON" "$ROOT/linux/pdalink_lite.py" usb-status "$@"
}

usb_test() {
  require_python
  log "Running USB smoke test"
  run "$ROOT/tools/usb_smoke.sh" "$@"
}

usb_stop() {
  require_python
  run "$PYTHON" "$ROOT/linux/pdalink_lite.py" usb-stop "$@"
}

wifi_start_android() {
  local apk=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --install)
        apk="${2:-}"
        [[ -n "$apk" ]] || fail "--install requires an APK path"
        shift 2
        ;;
      -h|--help)
        wifi_usage
        return 0
        ;;
      *)
        fail "unknown wifi start-android option: $1"
        ;;
    esac
  done
  if [[ -n "$apk" ]]; then
    log "Installing APK"
    run "$ADB" install -r "$apk"
  fi
  log "Starting Android Wi-Fi Direct proxy"
  run "$ADB" shell am start-foreground-service \
    -n dev.pdalink.prototype/.ProxyService \
    -a dev.pdalink.prototype.START_WIFI_DIRECT
}

wifi_stop_android() {
  if [[ $# -gt 0 ]]; then
    case "$1" in
      -h|--help)
        wifi_usage
        return 0
        ;;
      *)
        fail "unknown wifi stop-android option: $1"
        ;;
    esac
  fi
  log "Stopping Android Wi-Fi Direct proxy"
  run "$ADB" shell am start-foreground-service \
    -n dev.pdalink.prototype/.ProxyService \
    -a dev.pdalink.prototype.STOP_WIFI_DIRECT
}

wifi_connect() {
  local ssid=""
  local password=""
  local iface=""
  local host="${PDALINK_HOST:-192.168.49.1}"
  local ports="${PDALINK_PORTS:-8000,8001}"
  local verify_timeout="${PDALINK_WIFI_VERIFY_TIMEOUT:-15}"
  local ssid_pattern="${PDALINK_WIFI_SSID_PATTERN:-DIRECT-}"
  local no_verify=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ssid)
        ssid="${2:-}"
        [[ -n "$ssid" ]] || fail "--ssid requires a value"
        shift 2
        ;;
      --password)
        password="${2:-}"
        [[ -n "$password" ]] || fail "--password requires a value"
        shift 2
        ;;
      --iface)
        iface="${2:-}"
        [[ -n "$iface" ]] || fail "--iface requires a value"
        shift 2
        ;;
      --host)
        host="${2:-}"
        [[ -n "$host" ]] || fail "--host requires a value"
        shift 2
        ;;
      --ports)
        ports="${2:-}"
        [[ -n "$ports" ]] || fail "--ports requires a value"
        shift 2
        ;;
      --verify-timeout)
        verify_timeout="${2:-}"
        [[ -n "$verify_timeout" ]] || fail "--verify-timeout requires a value"
        shift 2
        ;;
      --ssid-pattern)
        ssid_pattern="${2:-}"
        [[ -n "$ssid_pattern" ]] || fail "--ssid-pattern requires a value"
        shift 2
        ;;
      --no-verify)
        no_verify=1
        shift
        ;;
      -h|--help)
        wifi_usage
        return 0
        ;;
      *)
        fail "unknown wifi connect option: $1"
        ;;
    esac
  done

  require_python
  if [[ -n "$ssid" ]]; then
    local args=(connect --ssid "$ssid" --host "$host" --ports "$ports" --verify-timeout "$verify_timeout")
    if [[ -n "$password" ]]; then
      args+=(--password "$password")
    fi
    if [[ -n "$iface" ]]; then
      args+=(--iface "$iface")
    fi
    if [[ "$no_verify" -eq 0 ]]; then
      args+=(--verify)
    fi
    run "$PYTHON" "$ROOT/linux/pdalink_lite.py" "${args[@]}"
    return
  fi

  if [[ "$no_verify" -eq 1 ]]; then
    fail "--no-verify requires --ssid; automatic selection always verifies"
  fi
  local args=(connect-auto --ssid-pattern "$ssid_pattern" --host "$host" --ports "$ports" --verify-timeout "$verify_timeout")
  if [[ -n "$password" ]]; then
    args+=(--password "$password")
  fi
  if [[ -n "$iface" ]]; then
    args+=(--iface "$iface")
  fi
  run "$PYTHON" "$ROOT/linux/pdalink_lite.py" "${args[@]}"
}

wifi_python_cmd() {
  local command="$1"
  shift
  require_python
  run "$PYTHON" "$ROOT/linux/pdalink_lite.py" "$command" "$@"
}

wifi_smoke() {
  require_python
  log "Running Wi-Fi Direct smoke test"
  run "$ROOT/tools/e2e_smoke.sh" "$@"
}

system_cmd() {
  local command="$1"
  shift
  require_python
  run "$PYTHON" "$ROOT/linux/pdalink_lite.py" "$command" "$@"
}

device_usage() {
  cat <<EOF
Usage:
  $0 device scan [--json]
  $0 device list [--json]
  $0 device trust [SERIAL] [--connected] [--name NAME] [--default]
  $0 device untrust SERIAL
  $0 device set-default SERIAL
EOF
}

config_usage() {
  cat <<EOF
Usage:
  $0 config list [--json]
  $0 config get KEY
  $0 config set KEY VALUE

Keys:
  auto_connect  off | on_trusted_connect | when_offline
  route_mode    active_override | fallback_low_priority
  dns_mode      resolved | off
  notify        auto | off
EOF
}

setup_usage() {
  cat <<EOF
Usage:
  $0 setup plan [--json]
  $0 setup wizard
  $0 setup apply [--trust SERIAL|--trust-connected] [--name NAME] [--default] [--autostart enable|disable] [--auto-connect VALUE] [--route-mode VALUE] [--dns-mode VALUE]

Agent-friendly example:
  $0 setup apply --trust R3CR406AAGE --name my-phone --default --autostart enable --auto-connect on_trusted_connect --route-mode active_override
EOF
}

autostart_usage() {
  cat <<EOF
Usage:
  $0 autostart enable
  $0 autostart disable
  $0 autostart status
EOF
}

systemd_user_dir() {
  local config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  printf '%s\n' "$config_home/systemd/user"
}

pdalink_unit_path() {
  printf '%s\n' "$(systemd_user_dir)/pdalink-watch.service"
}

require_systemctl_user() {
  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
}

write_autostart_unit() {
  local dir unit
  dir="$(systemd_user_dir)"
  unit="$(pdalink_unit_path)"
  mkdir -p "$dir"
  cat >"$unit" <<EOF
[Unit]
Description=PdaLink trusted Android USB watcher
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=/usr/bin/env bash $ROOT/tools/pdalink.sh watch run
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  chmod 0644 "$unit"
  echo "$unit"
}

autostart_cmd() {
  local action="${1:-}"
  case "$action" in
    enable)
      require_systemctl_user
      local unit
      unit="$(write_autostart_unit)"
      log "Enabling PdaLink user service"
      run systemctl --user daemon-reload
      run systemctl --user enable --now "$(basename "$unit")"
      ;;
    disable)
      require_systemctl_user
      log "Disabling PdaLink user service"
      run systemctl --user disable --now pdalink-watch.service || true
      run systemctl --user daemon-reload
      ;;
    status)
      require_systemctl_user
      run systemctl --user status --no-pager pdalink-watch.service
      ;;
    -h|--help|"")
      autostart_usage
      ;;
    *)
      autostart_usage >&2
      exit 2
      ;;
  esac
}

setup_apply() {
  local trust_serial=""
  local trust_connected=0
  local trust_name=""
  local make_default=0
  local autostart_action=""
  local auto_connect=""
  local route_mode=""
  local dns_mode=""
  local notify=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --trust)
        trust_serial="${2:-}"
        [[ -n "$trust_serial" ]] || fail "--trust requires a serial"
        shift 2
        ;;
      --trust-connected)
        trust_connected=1
        shift
        ;;
      --name)
        trust_name="${2:-}"
        [[ -n "$trust_name" ]] || fail "--name requires a value"
        shift 2
        ;;
      --default)
        make_default=1
        shift
        ;;
      --autostart)
        autostart_action="${2:-}"
        [[ "$autostart_action" == "enable" || "$autostart_action" == "disable" ]] || fail "--autostart must be enable or disable"
        shift 2
        ;;
      --auto-connect)
        auto_connect="${2:-}"
        [[ -n "$auto_connect" ]] || fail "--auto-connect requires a value"
        shift 2
        ;;
      --route-mode)
        route_mode="${2:-}"
        [[ -n "$route_mode" ]] || fail "--route-mode requires a value"
        shift 2
        ;;
      --dns-mode)
        dns_mode="${2:-}"
        [[ -n "$dns_mode" ]] || fail "--dns-mode requires a value"
        shift 2
        ;;
      --notify)
        notify="${2:-}"
        [[ -n "$notify" ]] || fail "--notify requires a value"
        shift 2
        ;;
      -h|--help)
        setup_usage
        return 0
        ;;
      *)
        fail "unknown setup apply option: $1"
        ;;
    esac
  done

  if [[ -n "$trust_serial" && "$trust_connected" -eq 1 ]]; then
    fail "use either --trust SERIAL or --trust-connected, not both"
  fi

  if [[ "$trust_connected" -eq 1 || -n "$trust_serial" ]]; then
    local trust_args=(device-trust)
    if [[ "$trust_connected" -eq 1 ]]; then
      trust_args+=(--connected)
    else
      trust_args+=("$trust_serial")
    fi
    if [[ -n "$trust_name" ]]; then
      trust_args+=(--name "$trust_name")
    fi
    if [[ "$make_default" -eq 1 ]]; then
      trust_args+=(--default)
    fi
    system_cmd "${trust_args[@]}"
  fi

  if [[ -n "$auto_connect" ]]; then
    system_cmd config-set auto_connect "$auto_connect"
  fi
  if [[ -n "$route_mode" ]]; then
    system_cmd config-set route_mode "$route_mode"
  fi
  if [[ -n "$dns_mode" ]]; then
    system_cmd config-set dns_mode "$dns_mode"
  fi
  if [[ -n "$notify" ]]; then
    system_cmd config-set notify "$notify"
  fi
  if [[ -n "$autostart_action" ]]; then
    autostart_cmd "$autostart_action"
  fi
}

setup_wizard() {
  cat <<EOF
PdaLink setup wizard

This wizard keeps choices explicit. It does not trust a device or enable
autostart without your answer.
EOF
  system_cmd device-scan
  read -r -p "Trust the single connected authorized device? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    read -r -p "Device name [my-phone]: " name
    name="${name:-my-phone}"
    system_cmd device-trust --connected --name "$name" --default
  fi
  read -r -p "Enable autostart watcher now? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    system_cmd config-set auto_connect on_trusted_connect
    autostart_cmd enable
  fi
}

main() {
  case "${1:-}" in
    -h|--help|"")
      usage
      ;;
    install)
      shift
      run "$ROOT/tools/install_linux.sh" "$@"
      ;;
    setup)
      shift
      if [[ "${1:-}" == "plan" ]]; then
        shift
        system_cmd setup-plan "$@"
      elif [[ "${1:-}" == "wizard" ]]; then
        shift
        setup_wizard "$@"
      elif [[ "${1:-}" == "apply" ]]; then
        shift
        setup_apply "$@"
      elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" || -z "${1:-}" ]]; then
        setup_usage
      else
        setup_usage >&2
        exit 2
      fi
      ;;
    rescue)
      shift
      run bash "$ROOT/tools/pdalink_rescue.sh" "$@"
      ;;
    status)
      shift
      system_cmd status "$@"
      ;;
    device)
      shift
      case "${1:-}" in
        scan)
          shift
          system_cmd device-scan "$@"
          ;;
        list)
          shift
          system_cmd device-list "$@"
          ;;
        trust)
          shift
          system_cmd device-trust "$@"
          ;;
        untrust)
          shift
          system_cmd device-untrust "$@"
          ;;
        set-default)
          shift
          system_cmd device-set-default "$@"
          ;;
        -h|--help|"")
          device_usage
          ;;
        *)
          device_usage >&2
          exit 2
          ;;
      esac
      ;;
    autostart)
      shift
      autostart_cmd "$@"
      ;;
    config)
      shift
      case "${1:-}" in
        list)
          shift
          system_cmd config-list "$@"
          ;;
        get)
          shift
          system_cmd config-get "$@"
          ;;
        set)
          shift
          system_cmd config-set "$@"
          ;;
        -h|--help|"")
          config_usage
          ;;
        *)
          config_usage >&2
          exit 2
          ;;
      esac
      ;;
    usb)
      shift
      case "${1:-}" in
        start)
          shift
          require_python
          usb_start "$@"
          ;;
        status|next)
          shift
          usb_status "$@"
          ;;
        test|smoke)
          shift
          usb_test "$@"
          ;;
        stop)
          shift
          usb_stop "$@"
          ;;
        -h|--help|"")
          usb_usage
          ;;
        *)
          usb_usage >&2
          exit 2
          ;;
      esac
      ;;
    connection)
      shift
      case "${1:-}" in
        status)
          shift
          system_cmd connection-status "$@"
          ;;
        -h|--help|"")
          connection_usage
          ;;
        *)
          connection_usage >&2
          exit 2
          ;;
      esac
      ;;
    product-usb)
      shift
      case "${1:-}" in
        status)
          shift
          system_cmd product-usb-status "$@"
          ;;
        check|smoke)
          shift
          system_cmd product-usb-check "$@"
          ;;
        aoa-start)
          shift
          system_cmd product-usb-aoa-start "$@"
          ;;
        bridge)
          shift
          system_cmd product-usb-bridge "$@"
          ;;
        -h|--help|"")
          product_usb_usage
          ;;
        *)
          product_usb_usage >&2
          exit 2
          ;;
      esac
      ;;
    system|tun)
      shift
      case "${1:-}" in
        status)
          shift
          system_cmd system-status "$@"
          ;;
        plan)
          shift
          system_cmd system-plan "$@"
          ;;
        start)
          shift
          system_cmd system-start "$@"
          ;;
        stop)
          shift
          system_cmd system-stop "$@"
          ;;
        service)
          shift
          run bash "$ROOT/tools/pdalink_system_service.sh" "$@"
          ;;
        adapter)
          shift
          system_cmd system-adapter "$@"
          ;;
        test|smoke)
          shift
          run "$ROOT/tools/system_connect_test.sh" "$@"
          ;;
        up)
          shift
          system_cmd system-up "$@"
          ;;
        down)
          shift
          system_cmd system-down "$@"
          ;;
        -h|--help|"")
          system_usage
          ;;
        *)
          system_usage >&2
          exit 2
          ;;
      esac
      ;;
    watch|supervise)
      shift
      require_python
      {
        printf '+'
        printf ' %q' "$PYTHON" "$ROOT/tools/pdalink_watch.py" "$@"
        printf '\n'
      } >&2
      exec "$PYTHON" "$ROOT/tools/pdalink_watch.py" "$@"
      ;;
    wifi)
      shift
      case "${1:-}" in
        start-android)
          shift
          wifi_start_android "$@"
          ;;
        stop-android)
          shift
          wifi_stop_android "$@"
          ;;
        local-hotspot)
          shift
          case "${1:-}" in
            sync)
              shift
              wifi_python_cmd local-hotspot-sync "$@"
              ;;
            -h|--help|"")
              wifi_usage
              ;;
            *)
              wifi_usage >&2
              exit 2
              ;;
          esac
          ;;
        scan)
          shift
          wifi_python_cmd scan "$@"
          ;;
        connect)
          shift
          wifi_connect "$@"
          ;;
        status)
          shift
          wifi_python_cmd wifi-status "$@"
          ;;
        internet)
          shift
          case "${1:-}" in
            start)
              shift
              wifi_python_cmd wifi-internet-start "$@"
              ;;
            status)
              shift
              wifi_python_cmd wifi-internet-status "$@"
              ;;
            test)
              shift
              wifi_python_cmd wifi-internet-test "$@"
              ;;
            stop)
              shift
              wifi_python_cmd wifi-internet-stop "$@"
              ;;
            -h|--help|"")
              wifi_usage
              ;;
            *)
              wifi_usage >&2
              exit 2
              ;;
          esac
          ;;
        probe)
          shift
          wifi_python_cmd probe "$@"
          ;;
        env)
          shift
          wifi_python_cmd env "$@"
          ;;
        curl-test)
          shift
          wifi_python_cmd curl-test "$@"
          ;;
        test|smoke)
          shift
          wifi_smoke "$@"
          ;;
        doctor)
          shift
          wifi_python_cmd doctor "$@"
          ;;
        -h|--help|"")
          wifi_usage
          ;;
        *)
          wifi_usage >&2
          exit 2
          ;;
      esac
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
