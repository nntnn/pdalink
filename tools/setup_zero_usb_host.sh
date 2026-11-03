#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-}"
PROFILE=pdalink-zero-usb
KEY="$HOME/.ssh/pdalink_zero_ed25519"
SSH_CONFIG="$HOME/.ssh/config"

if [[ -z $IFACE || ! -d /sys/class/net/$IFACE ]]; then
  echo "Usage: $0 USB_ETHERNET_INTERFACE" >&2
  exit 2
fi

if nmcli -g connection.id connection show "$PROFILE" >/dev/null 2>&1; then
  nmcli connection modify "$PROFILE" \
    connection.interface-name "$IFACE" connection.autoconnect yes \
    ipv4.method manual ipv4.addresses 10.55.0.2/24 \
    ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
else
  nmcli connection add type ethernet ifname "$IFACE" con-name "$PROFILE" \
    ipv4.method manual ipv4.addresses 10.55.0.2/24 \
    ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
fi
nmcli connection up "$PROFILE" ifname "$IFACE"

install -d -m 0700 "$HOME/.ssh"
if [[ ! -f $KEY ]]; then
  ssh-keygen -q -t ed25519 -N '' -C pdalink-zero-control -f "$KEY"
fi
touch "$SSH_CONFIG"
chmod 0600 "$SSH_CONFIG"
if ! grep -q '^Host pdalink-zero$' "$SSH_CONFIG"; then
  {
    printf '\nHost pdalink-zero\n'
    printf '  HostName 10.55.0.1\n'
    printf '  User pi\n'
    printf '  IdentityFile %s\n' "$KEY"
    printf '  IdentitiesOnly yes\n'
  } >> "$SSH_CONFIG"
fi

echo "Host USB management configured."
echo "Public key to authorize on the Zero:"
cat "$KEY.pub"
