# PdaLink Native Linux Install

This guide is for a fresh Linux machine where you want to test:

```text
Android internet <-> USB <-> Linux OS internet
```

PdaLink currently uses Android USB debugging and ADB forwarding for the
development USB path.

## Quick Start

Clone the repository, then run the Linux installer:

```bash
git clone https://github.com/nntnn/pdalink.git
cd pdalink
./tools/pdalink.sh install
```

The installer checks local prerequisites, bootstraps repo-local Android build
tools, builds the debug APK, installs Android USB udev rules, installs the
limited Linux privilege helper, installs the per-user GTK desktop client, and
then handles the Android app based on USB state:

- Authorized USB debugging device attached: installs and starts the Android app.
- USB device attached but unauthorized: asks you to approve the RSA prompt and
  prints the rerun command.
- No USB device attached: leaves the APK ready and prints the manual install
  command for later.

After install, start the managed USB internet path:

```bash
./tools/pdalink.sh device trust --connected --name my-phone --default
./tools/pdalink.sh config set auto_connect on_trusted_connect
./tools/pdalink.sh autostart enable
```

Then inspect the background watcher:

```bash
./tools/pdalink.sh watch status
curl -4 http://1.1.1.1/cdn-cgi/trace
```

Open the native Ubuntu client from the application menu as `PdaLink`, or run:

```bash
./tools/pdalink.sh client run
```

Use the client's `Disconnect` action for normal use. It disables the desired
connection and cleans up owned routes while leaving the supervisor available
for reconnect and resume diagnostics. For maintenance-only cleanup:

```bash
./tools/pdalink.sh system service stop --proxy-url http://127.0.0.1:18080
```

## Linux Prerequisites

The installer expects:

- Linux with `/dev/net/tun` available for full system routing.
- `python3` 3.10 or newer.
- PyGObject with GTK4 and libadwaita introspection data.
- `bash`, `curl`, `unzip`, `tar`, `sudo`, and `ip`.
- ADB-visible Android phone with USB debugging enabled for automatic app
  installation.

Package examples:

```bash
sudo apt update && sudo apt install -y python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 curl unzip tar sudo iproute2
sudo dnf install -y python3 curl unzip tar sudo iproute
sudo pacman -S --needed python curl unzip tar sudo iproute2
```

Pass `--skip-client-install` when preparing a headless host without GTK.

The Android bootstrap downloads repo-local Android platform tools, command-line
tools, Gradle, and Temurin JDK 17 when the system does not already have a
suitable JDK.

## Initial USB States

Authorized device:

```bash
./tools/adb.sh devices -l
# ... device ...
./tools/pdalink.sh install
```

The APK is installed automatically.

Unauthorized device:

```bash
./tools/adb.sh devices -l
# ... unauthorized ...
```

Unlock the phone, approve the USB debugging RSA prompt, then run:

```bash
./tools/pdalink.sh install --skip-bootstrap --skip-android-build --skip-privileges
```

No permissions:

```bash
./tools/adb.sh devices -l
# ... no permissions ...
```

This means Linux cannot open the USB device node. Refresh the managed udev
rules, reset ADB, and re-check USB state:

```bash
./tools/pdalink.sh rescue udev
./tools/pdalink.sh connection status
```

If ADB still reports `no permissions`, unplug and reconnect the phone so udev
can apply the refreshed rules. The installer runs the udev step by default; use
`--skip-udev` only when a machine policy requires manual rule management.

No device connected:

```bash
./tools/pdalink.sh install
```

The installer builds the APK and prints:

```bash
./tools/adb.sh install -r android/app/build/manual/app-debug.apk
```

Run that command after plugging in the phone and approving USB debugging.

## LLM Agent Prompt

If you want an LLM coding agent to install and test this repo on a Linux
machine, give it this prompt:

```text
You are in a fresh clone of nntnn/pdalink on native Linux. Install PdaLink for
USB-based Android internet without inventing new commands.

Run:
  ./tools/pdalink.sh install

If adb reports unauthorized, tell me to unlock the phone and approve the USB
debugging RSA prompt, then rerun:
  ./tools/pdalink.sh install --skip-bootstrap --skip-android-build --skip-privileges

If adb reports no permissions, run:
  ./tools/pdalink.sh rescue udev

Do not run system-start directly. After installation, explicitly trust the
connected Android device and start the managed path:
  ./tools/pdalink.sh device trust --connected --name my-phone --default
  ./tools/pdalink.sh config set auto_connect on_trusted_connect
  ./tools/pdalink.sh autostart enable

Verify from another shell:
  ./tools/pdalink.sh watch status
  curl -4 http://1.1.1.1/cdn-cgi/trace

If USB is unplugged, the watcher should clean up its managed route service. If
you need explicit cleanup, run:
  ./tools/pdalink.sh system service stop --proxy-url http://127.0.0.1:18080

Report the exact command output for failed prerequisite, adb authorization,
Linux USB permission, TUN, proxy, route, or curl checks.
```

## Current Limits

- ADB/USB debugging is required.
- IPv4 TCP is supported through HTTP `CONNECT`.
- UDP support is limited to DNS destination port `53`.
- IPv6, ICMP, arbitrary UDP, and QUIC/HTTP3 are not implemented yet.
- WSL 2 with a Windows-owned ADB server is a development workaround, not the
  native Linux target flow.
