# PdaLink Linux Helper

The Linux side is intentionally small. It uses NetworkManager for Wi-Fi and
`curl` for the first end-to-end data-path test.

For a fresh native Linux clone, prefer the installer:

```bash
./tools/pdalink.sh install
./tools/pdalink.sh watch run
```

The installer bootstraps repo-local Android tools/JDK when needed, builds the
debug APK, installs the limited privilege helper, and installs the Android app
automatically when USB debugging is already authorized.

```bash
python3 linux/pdalink_lite.py scan
python3 linux/pdalink_lite.py connect --ssid 'DIRECT-...' --password '...'
python3 linux/pdalink_lite.py connect-auto --password '...'
python3 linux/pdalink_lite.py probe
python3 linux/pdalink_lite.py doctor --curl-url https://ifconfig.me
python3 linux/pdalink_lite.py curl-test --url https://ifconfig.me
```

For USB debugging mode, expose the Android proxy as a localhost proxy:

```bash
./tools/pdalink.sh usb start
./tools/pdalink.sh usb status
./tools/pdalink.sh usb test --no-start-android
./tools/pdalink.sh usb stop
```

The default USB proxy is:

```text
http://127.0.0.1:18080
```

`usb-smoke` retries curl a few times after the local port opens, because the
Android proxy may need a short moment before cellular egress is ready.

The wrapper prints the underlying command before it runs it, so the lower-level
`python3 linux/pdalink_lite.py usb-*` commands remain visible for debugging.

The Android manual builder is WSL-friendly:

```bash
./tools/build_android_manual.sh
```

It automatically uses `.tools/jdk/temurin-17` if `java` is not in `PATH`, and
falls back to Python's `zipfile` module if the system `zip` command is absent.

## Windows 10 / WSL Notes

WSL 1 can run the repo's Python tests and shell syntax checks:

```bash
python3 -m unittest discover -s tests
bash -n tools/*.sh
python3 linux/pdalink_lite.py system-status --no-probe-proxy
```

On WSL 1, `/dev/net/tun` is normally unavailable. In that state,
`system-status` should report `tun: MISSING`, and `system-start` must not be
used. Use WSL 2 or a real Linux host before testing route mutation or the
built-in TUN adapter.

On WSL 2, `/dev/net/tun` is available, but Windows ADB forwards listen on
Windows localhost. WSL 2 cannot reach that listener through its own
`127.0.0.1`, so use a temporary Windows-side relay from the WSL default gateway
address to `127.0.0.1:18080`:

```powershell
$wslHost = (wsl -e sh -lc "ip route | awk '/default/ {print $3; exit}'").Trim()
powershell -ExecutionPolicy Bypass -File .\tools\windows_tcp_relay.ps1 `
  -ListenAddress $wslHost -ListenPort 18081 `
  -TargetAddress 127.0.0.1 -TargetPort 18080
```

Then point WSL commands at `http://$wslHost:18081` instead of
`http://127.0.0.1:18080`.

If Windows is currently connected through an existing desktop network adapter,
avoid changing Windows routes or creating another Windows network adapter during
prototype work. The low-risk path is to keep the existing connection online and
test this project only with explicit proxy commands through an ADB forwarded
port. That Windows-side test needs a Windows `adb.exe`; the repo-local
`.tools/android-sdk/platform-tools/adb` binary is for Linux/WSL shells. If
Windows platform-tools are installed under this repo, use
`.tools/platform-tools-windows/adb.exe` from PowerShell.

This coexistence path has been verified while keeping the Windows default route
unchanged: leave unrelated ADB forwards alone, add a temporary
`tcp:18080 -> tcp:8000` forward for PdaLink, curl through
`http://127.0.0.1:18080`, then remove only the temporary forward.

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_proxy_smoke.ps1
```

Experimental whole-system routing uses a Linux TUN interface, split-default
routes, and the built-in USB internet adapter. The adapter reads IPv4 TCP from
the TUN interface, dials through the Android HTTP proxy with `CONNECT`, and
converts UDP/53 DNS queries to TCP DNS through the same proxy path:

```bash
python3 linux/pdalink_lite.py system-status --no-probe-proxy
python3 linux/pdalink_lite.py system-plan
```

After the USB proxy is already reachable at `http://127.0.0.1:18080`, run the
built-in adapter as root:

```bash
sudo python3 linux/pdalink_lite.py system-start
```

For native Linux daily use, install the limited privilege rule once:

```bash
sudo ./tools/install_linux_privileges.sh --user "$USER"
```

This copies root-owned helper code to `/opt/pdalink`, installs
`/usr/local/libexec/pdalink/pdalink-root`, and writes a sudoers drop-in that
allows only the PdaLink system route commands without a password prompt. The
runtime wrapper uses `sudo -n`, so it never waits for interactive password
input in the background. Re-run the installer after updating PdaLink so the
root-owned helper copy stays in sync.

For a repeatable background run loop, use the service wrapper without `sudo`.
It stores runtime files in `.tools/runtime` and delegates cleanup to
`system-stop`:

```bash
./tools/pdalink.sh system service start --proxy-url http://127.0.0.1:18080
./tools/pdalink.sh system service status --proxy-url http://127.0.0.1:18080
./tools/pdalink.sh system service logs
./tools/pdalink.sh system service stop --proxy-url http://127.0.0.1:18080
```

For native Linux daily operation, prefer the watcher. It is the managed version
of the target flow:

1. `pdalink-watch` keeps running as the normal user.
2. When USB appears, it verifies that `adb devices -l` reports an authorized
   Android debugging device.
3. It starts `dev.pdalink.prototype`, creates `tcp:18080 -> tcp:8000`, probes
   `/proxy.pac`, then starts the system routing service through the installed
   privilege helper.
4. If the device is unplugged, becomes unauthorized, or the proxy path fails,
   it stops the managed service and removes only the forward it created.

```bash
./tools/pdalink.sh watch run
./tools/pdalink.sh watch status
./tools/pdalink.sh watch once
```

The watcher is for native Linux or any environment where Linux `adb` directly
sees the USB device. In WSL 2 with a Windows-owned ADB server, keep using the
manual Windows relay and explicit `--proxy-url` service commands.

`system-start` probes the proxy before changing routes. Stop it with `Ctrl-C`;
cleanup removes the split-default routes. It also writes a runtime state file
after route setup, so a lost foreground shell can be cleaned with:

```bash
sudo ./tools/pdalink.sh system stop
```

For the Windows 10 / WSL 2 host verified here, the working command was:

```bash
python3 linux/pdalink_lite.py system-status --proxy-url http://192.168.2.193:18081
sudo python3 linux/pdalink_lite.py system-start --proxy-url http://192.168.2.193:18081
```

With `system-start` active, `curl -4 https://ifconfig.me` worked without proxy
environment variables, and a direct UDP/53 query to `8.8.8.8` returned DNS
answers through the adapter. Run `system-start` in a foreground shell when
possible; if it was backgrounded in a way that ignores `Ctrl-C`, stop the
process and clean up with `sudo ./tools/pdalink.sh system stop`. If no state
file is available, fall back to `sudo python3 linux/pdalink_lite.py system-down
--apply`.

For a one-command offline smoke test, turn off laptop Wi-Fi/Ethernet, keep the
phone on cellular data, then run:

```bash
./tools/pdalink.sh system test --expect-offline
```

The test starts the USB proxy, runs `system-start` in the background, curls the
test URL without proxy environment variables, runs a UDP/53 DNS check, and
cleans routes before exit. It uses `https://1.1.1.1/cdn-cgi/trace` by default to
prove TCP internet before depending on DNS; pass `--url https://ifconfig.me` to
include ordinary resolver behavior too.

For WSL 2, start the Windows relay first and pass its URL directly:

```bash
sudo ./tools/system_connect_test.sh --no-start-usb \
  --proxy-url http://192.168.2.193:18081 \
  --url http://1.1.1.1/cdn-cgi/trace
```

The older external-adapter control commands remain available for experiments:

```bash
python3 linux/pdalink_lite.py system-up
python3 linux/pdalink_lite.py system-down
sudo python3 linux/pdalink_lite.py system-up --apply --adapter-ready
sudo python3 linux/pdalink_lite.py system-down --apply
```

After the laptop is already connected to the Android `DIRECT-*` network, use
the repo smoke loop for repeated device testing:

```bash
./tools/e2e_smoke.sh
```

For shells that support proxy environment variables:

```bash
eval "$(python3 linux/pdalink_lite.py env --probe)"
curl https://example.com/
```
