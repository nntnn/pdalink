#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

install -d -m 0755 /opt/pdalink/linux /usr/local/sbin /etc/pdalink /var/lib/pdalink
install -m 0644 "$ROOT/linux/pdalink_lite.py" /opt/pdalink/linux/pdalink_lite.py
install -m 0755 "$ROOT/linux/pdalink_target.py" /usr/local/sbin/pdalink-target-apply

if [[ ! -f /etc/pdalink/client.env ]]; then
  current_proxy="$(
    sed -n 's/.*--proxy-url \(http:\/\/[^[:space:]]*\).*/\1/p' \
      /etc/systemd/system/pdalink-client.service 2>/dev/null | head -n 1
  )"
  if [[ ! $current_proxy =~ ^http://(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)[0-9.]+:[0-9]+$ ]]; then
    echo "Cannot preserve the current private PdaLink proxy URL." >&2
    echo "Create /etc/pdalink/client.env with PDALINK_PROXY_URL=http://HOST:PORT first." >&2
    exit 1
  fi
  umask 077
  printf 'PDALINK_PROXY_URL=%s\n' "$current_proxy" > /etc/pdalink/client.env
fi
chmod 0600 /etc/pdalink/client.env

install -m 0644 /dev/stdin /etc/systemd/system/pdalink-client.service <<'UNIT'
[Unit]
Description=PdaLink Local Hotspot TUN client
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
EnvironmentFile=/etc/pdalink/client.env
ExecStart=/usr/bin/python3 /opt/pdalink/linux/pdalink_lite.py system-start --proxy-url ${PDALINK_PROXY_URL} --dns-mode off --tcp-idle-timeout 60 --state-file /run/pdalink/system.json
Restart=on-failure
RestartSec=5
TasksMax=512
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

install -m 0440 /dev/stdin /etc/sudoers.d/pdalink-target <<'SUDOERS'
pi ALL=(root) NOPASSWD: /usr/local/sbin/pdalink-target-apply
SUDOERS
visudo -cf /etc/sudoers.d/pdalink-target >/dev/null

if [[ -s /home/pi/.ssh/authorized_keys ]]; then
  install -d -m 0755 /etc/ssh/sshd_config.d
  install -m 0644 /dev/stdin /etc/ssh/sshd_config.d/60-pdalink-key-only.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
SSHD
  sshd -t
else
  echo "Warning: pi has no authorized SSH key; password SSH was left enabled." >&2
fi

systemctl daemon-reload
systemctl enable pdalink-client.service >/dev/null
systemctl restart pdalink-client.service
systemctl is-active --quiet pdalink-client.service
if [[ -s /home/pi/.ssh/authorized_keys ]]; then
  systemctl reload ssh
fi

echo "PdaLink Zero target installed."
