# PdaLink

PdaLink is an Android-to-Linux internet-sharing prototype. It provides a local
Android proxy, USB and Wi-Fi Direct transport experiments, Linux route
automation, and diagnostic tooling for development and manual testing.

Install on native Linux:

```bash
./tools/pdalink.sh install
./tools/pdalink.sh device trust --connected --name my-phone --default
./tools/pdalink.sh config set auto_connect on_trusted_connect
./tools/pdalink.sh autostart enable
```

For a fresh-machine walkthrough, including LLM-agent instructions and USB
authorization branches, read `INSTALL.md`.

Start with:

- `INSTALL.md`
- `docs/DELIVERY_PLAN.md`
- `docs/IMPLEMENTATION_PLAN.md`
- `docs/MANUAL_TEST.md`

Prototype layout:

- `android/`: Android app that serves the ADB USB proxy on loopback port `8000`
  and can concurrently serve Wi-Fi Direct on `192.168.49.1:8001`.
- `linux/`: Python helper for NetworkManager connection, proxy probing, and
  curl-based smoke tests, including an ADB USB-forward proxy mode and an
  experimental TUN/system-routing control plane.
- `tools/`: optional local Android SDK/Gradle/ADB bootstrap helpers.

Quick Linux-side checks:

```bash
python3 -m unittest discover -s tests
python3 linux/pdalink_lite.py --help
python3 linux/pdalink_lite.py doctor
./tools/pdalink.sh usb status
```

On Windows 10 hosts, WSL 1 is enough for source work, Python unit tests, and
shell syntax checks. It is not enough for the experimental system-routing path:
if `/dev/net/tun` is missing, do not run `system-start`.

Build the Android APK without Android Studio:

```bash
./tools/bootstrap_android_tools.sh
./tools/build_android_manual.sh
```

The manual builder uses the repo-local Temurin 17 JDK when the current `PATH`
does not provide a suitable JDK 17+ with `keytool`, `javac`, and
`jdk.compiler`. It can package `classes.dex` without requiring the system `zip`
command.

The bootstrap script verifies fixed-version downloads when a known SHA-256 is
available and warns before using version-floating downloads that cannot be
verified by default.

After the Android app is started and the laptop is on the phone's `DIRECT-*`
network, the fastest end-to-end check is:

```bash
./tools/e2e_smoke.sh
```

The wrapper exposes the same Wi-Fi Direct flow without calling the Python
helper directly:

```bash
./tools/pdalink.sh wifi start-android
./tools/pdalink.sh wifi scan
./tools/pdalink.sh wifi connect --password 'password-shown-in-android'
./tools/pdalink.sh wifi status
./tools/pdalink.sh wifi test
./tools/pdalink.sh wifi stop-android
```

On Linux desktops using NetworkManager and GNOME, one managed command can join
the Direct group and apply the PdaLink PAC for proxy-aware desktop apps:

```bash
./tools/pdalink.sh wifi internet start --password 'password-shown-in-android'
./tools/pdalink.sh wifi internet status
./tools/pdalink.sh wifi internet test
./tools/pdalink.sh wifi internet stop
```

`wifi internet start` verifies the Direct route and PAC before changing GNOME
settings. It saves the previous effective proxy values in a user-private state
file and restores them on `stop`. If the proxy is changed by another tool while
PdaLink is active, normal stop refuses to overwrite that newer setting; use
`--force` only for deliberate snapshot recovery. Pass `--keep-wifi` to restore
the proxy without disconnecting a Direct connection created by the command.
Non-GNOME desktops can continue to use `wifi env --probe`.

Starting Wi-Fi Direct is additive: the Android USB proxy remains on
`127.0.0.1:8000` for existing ADB forwards while the Direct proxy listens on
`192.168.49.1:8001`. `wifi stop-android` removes only the Direct group and
listener; it does not stop the USB proxy. Use the app's `Stop All` action or the
full Android `STOP` action only when both transports should stop.

On phones that cannot run hotspot and Wi-Fi Direct together, use the hotspot
only as the APK-install safety path. Restore and verify USB first, then turn the
hotspot off before `wifi start-android`; the USB route keeps the laptop online
while it joins the `DIRECT-*` network.

`wifi connect` marks the NetworkManager profile as local-only
(`never-default`, ignored auto DNS, IPv6 ignored, no autoconnect) so an active
USB tether or normal internet connection keeps the default route. Ubuntu may
still show a "no internet" notification for the Direct SSID because that SSID is
only the local phone link; it is harmless as long as `wifi status` shows the
route to `192.168.49.1` on the Wi-Fi interface.

If Ubuntu repeatedly prompts for the Wi-Fi password, the saved passphrase is
probably stale. Start Wi-Fi Direct again on Android, copy the current password
shown in the app, and rerun `./tools/pdalink.sh wifi connect --password '...'`;
the wrapper updates the saved NetworkManager secret before reconnecting.

The cross-platform host contract and the planned Windows/macOS native helpers
are tracked in `docs/HOST_AUTOMATION_PLAN.md`. Until their real-host smoke tests
pass, Windows and macOS Wi-Fi Direct proxy setup remains manual.

For a USB-debugging-only prototype path, forward the Android proxy to localhost
and curl through USB:

```bash
./tools/pdalink.sh usb start --install android/app/build/manual/app-debug.apk
./tools/pdalink.sh usb test --no-start-android
./tools/pdalink.sh usb stop
```

This ADB USB path is the development and recovery transport. Product-mode
artifacts must not require Android USB debugging: the ADB-free USB product
transport has to carry the same proxy/TUN behavior with USB debugging disabled
before that path is considered product-ready.

For the ADB-free product path, inspect the product USB readiness gate before
attempting route changes:

```bash
./tools/pdalink.sh product-usb status
./tools/pdalink.sh product-usb check
./tools/pdalink.sh product-usb aoa-start --dry-run --serial SHA256:<host-fingerprint>
```

Real AOA handoff uses the phone's current `lsusb` id before accessory mode. On
the Samsung SM-G996N testbed, `04e8:6860` re-enumerated as Google AOA
`18d1:2d01`; Linux hosts need udev access for that id or `adb devices -l` will
report `no permissions`.

After the Android app shows Product USB mode, start the Linux bridge. It opens
the AOA bulk endpoints, exchanges the Product USB hello/trust frame, serves a
local `/proxy.pac`, and refreshes the readiness state consumed by the route
guard:

```bash
./tools/pdalink.sh product-usb bridge --local-port 18080
./tools/pdalink.sh watch once --transport product-usb --local-port 18080
```

The bridge marks `host_trust=trusted` only after the Android side replies to the
hello frame. AOA ids with ADB enabled, such as `18d1:2d01`, still report
`usb_debugging=on` and remain blocked for product-mode release validation until
Android USB debugging is disabled.

On Windows 10, this USB proxy path has been verified with repo-local Windows
platform-tools and a separate `tcp:18080 -> tcp:8000` ADB forward while leaving
the existing default route and unrelated ADB forwards untouched.

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_proxy_smoke.ps1
```

To make Windows proxy-aware traffic use the USB path without per-command `-x`
flags, enable the Windows user proxy wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 start
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 stop
```

This backs up the current WinINET user proxy settings, points HTTP/HTTPS proxy
resolution at `127.0.0.1:18080`, verifies a system-proxy request, and restores
the previous settings on stop. It is the usable Windows USB internet mode for
proxy-aware applications; raw socket full-tunnel behavior still needs the
planned Windows TUN/virtual-adapter slice.

For WSL 2 system-routing tests on a Windows host, keep the ADB forward on
Windows localhost and expose it to WSL through a temporary user-mode relay:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_tcp_relay.ps1 `
  -ListenAddress 192.168.2.193 -ListenPort 18081 `
  -TargetAddress 127.0.0.1 -TargetPort 18080
```

Then run `system-start` in WSL 2 with
`--proxy-url http://192.168.2.193:18081`. On the verified Windows 10 / WSL 2
host, IPv4 TCP and UDP/53 DNS both worked through this path.

For experimental whole-system routing on Linux, inspect the TUN control-plane
plan and run the built-in USB internet adapter:

```bash
./tools/pdalink.sh system status --no-probe-proxy
./tools/pdalink.sh system plan
./tools/pdalink.sh system test --expect-offline
sudo python3 linux/pdalink_lite.py system-start
sudo ./tools/pdalink.sh system stop
```

To check whether Ubuntu is actually using PdaLink for internet egress, use the
connection status command. It combines the ADB device state, USB forward, local
proxy probe, active route, and a direct internet probe:

```bash
./tools/pdalink.sh connection status
./tools/pdalink.sh connection status --json
```

`connected` means the USB tunnel is up, `pdalink0` is the active route for the
probe target, and the internet probe succeeds. `degraded` means the tunnel is
up but Ubuntu is not routing through `pdalink0` or the probe failed.
`disconnected` means the local PdaLink path itself is not ready.

For native Linux, install the limited privilege rule once so future service
starts do not block on a password prompt:

```bash
sudo ./tools/install_linux_privileges.sh --user "$USER"
```

The installer copies root-owned helper files under `/opt/pdalink` and writes a
narrow sudoers rule for `system-start`, `system-stop`, and `system-down`. After
that, the service wrapper uses `sudo -n`; it either runs immediately or fails
fast with a setup error instead of waiting for a password.
Re-run the installer after updating PdaLink so the root-owned helper copy stays
in sync with the checked-out code. `system status` prints the runtime binary,
checkout binary, and runtime drift line when a state-tracked service is active.

Linux hosts also need udev access to the Android USB IDs used by the ADB and
Product USB paths. `./tools/pdalink.sh install` refreshes those rules by
default. If `./tools/adb.sh devices -l` reports `no permissions`, run:

```bash
./tools/pdalink.sh rescue udev
```

If ADB still reports `no permissions`, unplug and reconnect the phone so udev
can apply the refreshed rules to the new USB device node. `unauthorized` is a
different state: unlock the phone and approve the USB debugging RSA prompt.

For repeatable local operation after the USB proxy is reachable, use the service
wrapper without `sudo`. It keeps pid, state, and log files under
`.tools/runtime` and reuses `system-stop` for cleanup:

```bash
./tools/pdalink.sh system service start --proxy-url http://127.0.0.1:18080
./tools/pdalink.sh system service status --proxy-url http://127.0.0.1:18080
./tools/pdalink.sh system service logs
./tools/pdalink.sh system service stop --proxy-url http://127.0.0.1:18080
```

For native Linux daily use, run the watcher instead. It keeps the four-step USB
flow managed in the background: watch ADB, start the Android debug proxy, create
the USB forward and system route service, then clean up when the USB/debugging
state disappears:

```bash
./tools/pdalink.sh device trust --connected --name my-phone --default
./tools/pdalink.sh config set auto_connect on_trusted_connect
./tools/pdalink.sh autostart enable
./tools/pdalink.sh watch run
./tools/pdalink.sh watch status
./tools/pdalink.sh watch once
```

`autostart enable` keeps a user-level watcher running in the background. When a
trusted USB-debugging phone is connected, the watcher starts the Android proxy,
creates the ADB forward, starts the system route service, and cleans it up when
the device disappears. The watcher assumes the Linux `adb` it invokes can see
the phone and that Linux can reach the resulting forward at
`http://127.0.0.1:18080`. WSL 2 development with a Windows-owned ADB server
still needs the manual Windows relay path instead of this native watcher.

```bash
./tools/pdalink.sh system service start --proxy-url http://192.168.2.193:18081
./tools/pdalink.sh system service status --proxy-url http://192.168.2.193:18081
./tools/pdalink.sh system service stop --proxy-url http://192.168.2.193:18081
```

Only run `system-start` on a real Linux or WSL 2 environment with
`/dev/net/tun`. If the Windows host is currently online through an existing
desktop network adapter, keep that adapter and the default Windows route
untouched; test this prototype through explicit proxy commands first.

`system-start` writes a runtime state file after routes are installed. If the
foreground shell is lost, `sudo ./tools/pdalink.sh system stop` uses that state
to stop the recorded process when possible and remove the recorded routes and
TUN interface.
