#!/usr/bin/env bash
set -euo pipefail

CMDLINE=/boot/firmware/cmdline.txt
BACKUP=/boot/firmware/cmdline.txt.pdalink-g_serial
GADGET=/sys/kernel/config/usb_gadget/pdalink
INSTALLED_SCRIPT=/usr/local/sbin/pdalink-zero-usb-gadget
UNIT=/etc/systemd/system/pdalink-zero-usb-gadget.service
PROFILE=pdalink-zero-usb

usage() {
  cat <<'EOF'
Usage: setup_zero_usb_gadget.sh {activate|persist|start|stop}

  activate  replace a running legacy gadget with ACM + NCM for live testing
  persist   install the tested ACM + NCM gadget for future boots
  start     create and bind the configfs gadget
  stop      unbind and remove the configfs gadget
EOF
}

require_zero_2() {
  if [[ ! -r /proc/device-tree/model ]] \
      || ! tr -d '\0' </proc/device-tree/model | grep -q 'Raspberry Pi Zero 2'; then
    echo "This installer is intended for a Raspberry Pi Zero 2." >&2
    exit 1
  fi
}

start_gadget() {
  modprobe libcomposite
  mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
  if [[ -e $GADGET/UDC ]] && [[ -n $(<"$GADGET/UDC") ]]; then
    return 0
  fi

  mkdir -p "$GADGET"
  printf '0x2e8a' > "$GADGET/idVendor"
  printf '0x0014' > "$GADGET/idProduct"
  printf '0x0200' > "$GADGET/bcdUSB"
  printf '0x0100' > "$GADGET/bcdDevice"

  mkdir -p "$GADGET/strings/0x409"
  printf 'pdalink-zero2w' > "$GADGET/strings/0x409/serialnumber"
  printf 'PdaLink' > "$GADGET/strings/0x409/manufacturer"
  printf 'Zero 2 W Control' > "$GADGET/strings/0x409/product"

  mkdir -p "$GADGET/configs/c.1/strings/0x409"
  printf 'ACM console + NCM management' > "$GADGET/configs/c.1/strings/0x409/configuration"
  printf '250' > "$GADGET/configs/c.1/MaxPower"

  mkdir -p "$GADGET/functions/ncm.usb0"
  printf '02:50:44:4c:00:01' > "$GADGET/functions/ncm.usb0/dev_addr"
  printf '02:50:44:4c:00:02' > "$GADGET/functions/ncm.usb0/host_addr"
  mkdir -p "$GADGET/functions/acm.usb0"
  [[ -e $GADGET/configs/c.1/ncm.usb0 ]] \
    || ln -s "$GADGET/functions/ncm.usb0" "$GADGET/configs/c.1/ncm.usb0"
  [[ -e $GADGET/configs/c.1/acm.usb0 ]] \
    || ln -s "$GADGET/functions/acm.usb0" "$GADGET/configs/c.1/acm.usb0"

  udc="$(find /sys/class/udc -mindepth 1 -maxdepth 1 -printf '%f\n' | head -n 1)"
  [[ -n $udc ]] || { echo "No USB device controller found." >&2; exit 1; }
  printf '%s' "$udc" > "$GADGET/UDC"
}

stop_gadget() {
  [[ -d $GADGET ]] || return 0
  [[ ! -e $GADGET/UDC ]] || printf '' > "$GADGET/UDC"
  rm -f "$GADGET/configs/c.1/ncm.usb0" "$GADGET/configs/c.1/acm.usb0"
  rmdir "$GADGET/functions/ncm.usb0" "$GADGET/functions/acm.usb0" 2>/dev/null || true
  rmdir "$GADGET/configs/c.1/strings/0x409" "$GADGET/configs/c.1" 2>/dev/null || true
  rmdir "$GADGET/strings/0x409" "$GADGET/strings" 2>/dev/null || true
  rmdir "$GADGET" 2>/dev/null || true
}

ensure_network_profile() {
  if nmcli -g connection.id connection show "$PROFILE" >/dev/null 2>&1; then
    nmcli connection modify "$PROFILE" \
      connection.interface-name usb0 connection.autoconnect yes \
      ipv4.method manual ipv4.addresses 10.55.0.1/24 \
      ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
  elif nmcli -g connection.id connection show usb0 >/dev/null 2>&1; then
    nmcli connection modify usb0 \
      connection.id "$PROFILE" connection.interface-name usb0 connection.autoconnect yes \
      ipv4.method manual ipv4.addresses 10.55.0.1/24 \
      ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
  else
    nmcli connection add type ethernet ifname usb0 con-name "$PROFILE" \
      ipv4.method manual ipv4.addresses 10.55.0.1/24 \
      ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
  fi
}

activate_gadget() {
  systemctl stop serial-getty@ttyGS0.service usb-gadget-network.service 2>/dev/null || true
  ip link set usb0 down 2>/dev/null || true
  modprobe -r g_cdc g_serial g_ether 2>/dev/null || true
  start_gadget
  ensure_network_profile
  systemctl start serial-getty@ttyGS0.service usb-gadget-network.service
  echo "Live ACM + NCM gadget activated. Test 10.55.0.1 before running persist."
}

persist_gadget() {
  [[ -d $GADGET ]] && [[ -n $(<"$GADGET/UDC") ]] \
    || { echo "Run activate and verify the USB link first." >&2; exit 1; }
  [[ -e $BACKUP ]] || cp -a "$CMDLINE" "$BACKUP"
  sed -i -E 's/modules-load=dwc2,(g_serial|g_cdc|g_ether)/modules-load=dwc2/' "$CMDLINE"
  grep -q 'modules-load=[^ ]*dwc2' "$CMDLINE" \
    || { echo "Unexpected USB boot configuration; no unit installed." >&2; exit 1; }

  if [[ $(readlink -f "$0") != "$INSTALLED_SCRIPT" ]]; then
    install -m 0755 "$0" "$INSTALLED_SCRIPT"
  fi
  install -m 0644 /dev/stdin "$UNIT" <<'UNIT_EOF'
[Unit]
Description=PdaLink Zero ACM + NCM USB gadget
After=sys-kernel-config.mount
Before=network-pre.target
Wants=sys-kernel-config.mount

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/pdalink-zero-usb-gadget start
ExecStop=/usr/local/sbin/pdalink-zero-usb-gadget stop

[Install]
WantedBy=sysinit.target
UNIT_EOF
  systemctl daemon-reload
  systemctl enable pdalink-zero-usb-gadget.service \
    serial-getty@ttyGS0.service usb-gadget-network.service >/dev/null
  echo "Persistent ACM + NCM gadget installed."
}

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi
require_zero_2

case "${1:-}" in
  activate) activate_gadget ;;
  persist) persist_gadget ;;
  start) start_gadget ;;
  stop) stop_gadget ;;
  *) usage >&2; exit 2 ;;
esac
