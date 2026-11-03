# Client device acceptance tests

These are **manual verification for Claude**, not hardware results. Run from
this checkout after the offline checks. Keep a separate recovery connection and
local console available before changing routes. Never run two watchers against
the same runtime directory/TUN. Do not log QR URIs, passwords, or clipboard contents.
Use the same saved host SSID/password throughout; do not rotate credentials.

A pass requires new internet requests after every outage without reimporting the
QR. Existing TCP streams may break. Allow 120 seconds after the host group is
visible again (Android can need network approval again). Record prompts, OS
versions, elapsed recovery time and host cellular/fallback status. A timeout,
rescan, manual VPN restart, route loop or stale cleanup is a fail for that case.
Do not count another uplink as success: disable other client internet uplinks
for the isolated test, retaining a local recovery console. Enable mobile data
on the host, disable Android system tethering, and verify PdaLink reports cellular
egress. Connectivity alone does not establish carrier billing treatment.

## Linux: manual Direct tunnel and failover

1. First arrange a maintenance window: stop the live watcher from its own
   checkout/service, and confirm its tunnel has cleaned up. This run did not
   stop it. Refresh the installed root helper from **this checkout**; otherwise
   the old `/opt` copy lacks socket binding. These commands are for Claude only:

   ```bash
   cd /media/nntnn/SWAP/projects/pdanet-tunnel
   systemctl --user stop pdalink-watch.service
   sudo ./tools/install_linux_privileges.sh --user "$USER"
   ./tools/pdalink.sh system service status
   ```

   Expected: no running old adapter, no owned split routes/DNS left behind.
   Do not run a second watcher if the old process is still active. The installer
   updates the limited root helper; no new sudoers command is required.

2. Start Direct on the host via Connections → Wi-Fi Direct → Apply Wi-Fi mode.
   Join the previously imported `pdalink-wifi-direct` profile; import once if new:

   ```bash
   ./tools/pdalink.sh connection import --camera
   # For an already imported profile, use instead:
   nmcli connection up pdalink-wifi-direct
   ip route get 192.168.49.1
   curl --noproxy '*' --fail --max-time 10 http://192.168.49.1:8001/proxy.pac
   python3 linux/pdalink_lite.py system-plan --proxy-url http://192.168.49.1:8001 --route-mode active_override --dns-mode resolved
   ```

   Expected: proxy route on Wi-Fi, source `192.168.49.x`, no gateway; exact PAC
   advertises `192.168.49.1:8001`. Profile has never-default and ignores DHCP DNS.
   Review plan: split IPv4 defaults on `pdalink0`, resolved DNS on that TUN,
   physical-interface binding, no ADB forward/start command for Direct.

3. Unplug USB and run:

   ```bash
   ./tools/pdalink.sh wifi tunnel start --route-mode active_override --dns-mode resolved
   ./tools/pdalink.sh wifi tunnel status
   ip route get 1.1.1.1
   resolvectl status pdalink0
   curl -4 --noproxy '*' --fail --max-time 30 https://example.com/
   ```

   Expected: running state, `state-proxy: http://192.168.49.1:8001`,
   `upstream-device: <Wi-Fi interface>`, internet route via `pdalink0`,
   per-link DNS `1.1.1.1 8.8.8.8` and `~.`, successful HTML. If Tailscale was
   already running, confirm fresh connectivity after its compatibility restart
   (`tailscale status`); inactive Tailscale must stay inactive.

4. Record the Wi-Fi interface from step 2 and run locally, replacing `wlan0`:

   ```bash
   WIFI_IFACE=wlan0
   nmcli device disconnect "$WIFI_IFACE"
   ip route get 192.168.49.1
   curl -4 --noproxy '*' --max-time 10 https://example.com/
   tail -n 40 .tools/runtime/pdalink-system.log
   nmcli connection up pdalink-wifi-direct
   curl -4 --noproxy '*' --fail --max-time 30 https://example.com/
   ./tools/pdalink.sh wifi tunnel stop
   ip route show dev pdalink0
   ```

   During loss the proxy route may show `pdalink0`; this is deliberate stress
   coverage. **Pass:** upstream connections fail on the bound Wi-Fi device,
   no recursive CONNECT/flow storm or runaway CPU, and new requests recover
   after reconnection. Inspect `ss -tnp` and the adapter log during the outage.
   Binding is kernel enforced; route output alone cannot prove socket safety.
   After stop, the owned TUN/routes/DNS/state are gone; original defaults remain.

5. Only after the manual tunnel checks pass, test opt-in watcher failover in
   the foreground, with the installed watcher still stopped:

   ```bash
   ./tools/pdalink.sh config set auto_connect on_trusted_connect
   ./tools/pdalink.sh watch run --wifi-direct-failover
   ```

   From a second local terminal:

   ```bash
   ./tools/pdalink.sh watch status --wifi-direct-failover
   curl -4 --noproxy '*' --fail --max-time 30 https://example.com/
   ```

   With trusted healthy USB, expect `health-transport: adb` and loopback proxy.
   Unplug USB: expect `wifi-direct`, Direct proxy and successful new requests.
   Replug USB: stay on Direct until USB has been continuously healthy for 20 s
   and the Direct selection has lasted 30 s. Repeated brief USB arrivals must
   not switch back. Direct loss may bypass the dwell to recover over healthy
   USB. Internet health must follow the selected proxy. An LTE outage is
   reported as `health-internet: failing`, not used to oscillate transports.
   Stop with Ctrl-C and verify cleanup. Repeat host Direct stop/start with USB
   absent: no route loop, automatic recovery once Wi-Fi and canonical PAC return.

6. Enable unattended failover only after those tests pass. Set
   `Environment=PDALINK_WIFI_DIRECT_FAILOVER=1` in the user service override
   (`systemctl --user edit pdalink-watch.service`), ensure its ExecStart points
   to the intended updated checkout, then run:

   ```bash
   systemctl --user daemon-reload
   systemctl --user start pdalink-watch.service
   ./tools/pdalink.sh watch status
   ```

   Default remains OFF without the flag/environment variable. Removing the
   override opts out. A status invocation can read the saved active transport
   even without the environment variable. This run did not enable it.

## Windows 10/11: QR, proxy and Wintun

Use native PowerShell and native Python 3.10+ matching the Windows architecture.
The optional camera scanner needs the existing OpenCV tooling; clipboard import
has no scanner dependency. Start in the repository root. Keep the host SSID
available privately. Wintun tests require an elevated PowerShell window.

The explicit proxy probes use a nonmatching `.invalid` bypass name so ambient
NO_PROXY cannot bypass them. Transparent probes use `--noproxy "*"`. Avoid
empty native arguments: Windows PowerShell's legacy parser drops them; see
[Microsoft's native argument changes](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing#passing-arguments-to-native-commands).

1. Display the host Connection QR. Scan it with the existing camera tooling:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1 -Camera
   ```

   Alternatively copy the connection URI to the clipboard privately and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1
   ```

   Expected: imported automatic WLAN profile, canonical PAC verified before
   settings change, WinINET PAC set to the phone. Clear the clipboard afterwards:
   `Set-Clipboard -Value ''`. Do not use `-ConnectionUri` with a secret in shell
   history. A proxy-only profile does not provide a Wintun full tunnel.

2. Verify proxy-aware internet:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 status
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 test
   curl.exe -4 --fail --max-time 30 --proxy http://192.168.49.1:8001 --noproxy "pdalink-no-bypass.invalid" https://example.com/
   netsh.exe wlan show profiles
   ```

   Expected: active/owned PAC and successful internet. In Windows Wi-Fi settings,
   turn Wi-Fi off for 15 s and on. Separately stop Direct on the host using the
   host app (not Stop all), wait 15 s and start Direct with the same band. After
   each outage repeat `test`; expect automatic join and new requests within
   120 s without QR import or credential changes. Reboot Windows and repeat.

3. Stop phase 1 (restores its registry snapshot), then in **elevated** PowerShell:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 stop
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 setup
   $ssid = Read-Host 'Exact saved DIRECT SSID'
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 start -Ssid $ssid
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 status
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 test
   Get-NetRoute -AddressFamily IPv4 | Where-Object DestinationPrefix -In @('192.168.49.1/32','0.0.0.0/1','128.0.0.0/1')
   curl.exe -4 --noproxy "*" --fail --max-time 30 https://example.com/
   ```

   The helper securely prompts for the saved password. Expected: signed pinned
   Wintun 0.14.1; physical host `/32`, both owned split defaults on Wintun, DNS
   on Wintun, successful transparent IPv4 TCP/DNS. The Python backend must be
   launched with `--upstream-interface-index <verified Wi-Fi index>`; its
   IP_UNICAST_IF socket constraint remains effective if the host route vanishes.

4. Repeat Wi-Fi off/on and host Direct stop/start from step 2. During each
   outage run `status` and a bounded curl from step 3. Expected: requests fail
   without recursive CONNECTs or CPU/flow growth; Wintun process remains alive.
   WLAN autoconnect restores the same physical interface; repeat `test` and curl
   within 120 s. Existing streams can fail. If the adapter index changes (USB
   Wi-Fi adapter removal/replacement), explicitly stop/start; do not silently
   repin to an unverified route. Wintun does not supervise a Windows reboot.

5. Clean up and check stale state:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 stop -KeepWifi
   powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 status
   Get-NetRoute -AddressFamily IPv4 | Where-Object DestinationPrefix -In @('192.168.49.1/32','0.0.0.0/1','128.0.0.0/1')
   ```

   Expected: inactive, owned Wintun process/routes/DNS gone, pre-existing routes
   intact, saved automatic WLAN profile retained by `-KeepWifi`. Normal stop
   without that flag removes only its generated WLAN profile. If an owned
   process exited unexpectedly, status must say stale; use normal `stop` and
   inspect any retained-state error before retrying start. Never delete state
   or unrelated routes to make a test pass. Phase-1 PAC, rather than Wintun,
   remains the supported unattended Windows reboot path.

## Android client: QR, VPN and Always-on

Use an Android 10+ client distinct from the host. The host serves Direct;
install `android/client`, not `android/app`, on the client. For offline builds
using the shared read-only tools directory:

```bash
M=/media/nntnn/SWAP/projects/pdanet/.tools
export JAVA_HOME=$M/jdk/temurin-17 ANDROID_HOME=$M/android-sdk
export PDALINK_TUN2PROXY_LIBS=$M/android-deps/tun2proxy-0.8.2/tun2proxy-android-libs
(cd android && "$M/gradle/gradle-8.10.2/bin/gradle" --offline -q :app:testDebugUnitTest :client:testDebugUnitTest :app:assembleDebug :client:assembleDebug)
# Optional installation from a separate control machine; select the CLIENT serial:
adb devices -l
read -r -p 'Client ADB serial: ' CLIENT_SERIAL
adb -s "$CLIENT_SERIAL" install -r android/client/build/outputs/apk/debug/client-debug.apk
adb -s "$CLIENT_SERIAL" shell am start -n dev.pdalink.client/.MainActivity
```

Confirm the APK contains `lib/<client-ABI>/libtun2proxy.so`; a build without
`PDALINK_TUN2PROXY_LIBS` can succeed while omitting the native VPN engine.
The host APK is `android/app/build/outputs/apk/debug/app-debug.apk` and must be
installed on the host with its existing signing key to preserve app data.
No ADB connection is needed for normal client operation.

1. On the client open PdaLink Client → Scan QR → scan the host Connection QR.
   Expected: saved SSID, transport Wi-Fi Direct and proxy `192.168.49.1:8001`.
   Tap Connect; grant requested Wi-Fi/location (older Android), notification,
   network-selection and VPN approvals. Expected: Connected, foreground VPN
   notification and VPN indicator. The PAC must match the entire host template;
   wrong endpoint/port or redirected PAC must not start the VPN.

2. Turn off client mobile data and disconnect USB data. In a separate browser,
   load `https://example.com/?pdalink=1`, then `https://example.org/?pdalink=2`.
   Expected: new pages over VPN, host app records proxy requests and cellular
   egress. Reopen the PdaLink activity: active status and enabled Disconnect
   must return, including while reconnecting. Do not use the app's own PAC
   request as proof of transparent VPN traffic; its process bypasses the VPN.

3. Android Settings → Network/Connections → VPN → PdaLink Client gear →
   **Always-on VPN ON**, **Block connections without VPN OFF**. OEM labels vary;
   lockdown blocks the excluded physical proxy process. Return to the browser
   and repeat the new-page test. Lock/unlock the client and repeat.

4. On the host stop **Wi-Fi Direct only**, wait 15 s, then start it again using
   Connections → Wi-Fi Direct → Apply Wi-Fi mode. On the client expect a
   starting/retrying notification and eventual Connected without Connect or a
   rescan. Retry fresh browser requests within 120 s of the SSID returning.
   Record any Android Wi-Fi reapproval prompt; do not hide it in the results.
   Repeat with client Wi-Fi off/on, then with a host outage longer than 2 min.
   Reconnect backoff is 5/10/20/40/60 s, capped at 60 s indefinitely; only one
   native engine may run and stale callbacks must not create a second VPN.

5. With Always-on enabled and the host ready, reboot and unlock the client:

   ```bash
   adb -s "$CLIENT_SERIAL" reboot
   # After unlocking, inspect without dumping profile/credentials:
   adb -s "$CLIENT_SERIAL" shell pidof dev.pdalink.client:vpn
   ```

   Expected: VPN foreground notification/Connected returns from the stored
   private service profile without opening the activity or scanning. Verify
   new pages from the browser with mobile data still off. Repeat a client
   reboot while the host is absent, then start host Direct and expect recovery.
   If USB is unavailable, reboot through the power menu and inspect the VPN UI.

6. Disable Always-on in Settings, tap Disconnect in the client, then restart
   host Direct. Expected: no automatic VPN retry; saved QR remains available
   after closing/reopening the activity. Tap Connect to deliberately opt in
   again. User force-stop, permission denial, OS battery restrictions and
   revoked VPN authorization are separate conditions requiring user action.

## Scope of evidence

Linux/Windows prototype engines carry IPv4 TCP and UDP/53 translated to
DNS-over-TCP CONNECT; they do not carry IPv6, ICMP, arbitrary UDP or QUIC.
Do not use ping as a pass criterion, and do not infer an IPv6 kill switch.
Android uses the separate pinned native tun2proxy engine; these tests establish
browser/VPN/recovery behavior, not complete protocol coverage. None of these
runbooks establish lossless stream migration or carrier billing guarantees.

Offline verification in this worktree: `python3 -m pytest -q tests` produced
297 passed, 6 skipped, and only the known
`test_linux_install_dry_run_prints_udev_rules` failure (missing GTK dependencies).
The offline Gradle command above passed 38 host and 5 client unit tests and
assembled both debug APKs. The client APK contains the pinned native library
for all four configured ABIs. `bash -n tools/pdalink.sh`, `git diff --check`,
and the Direct `system-plan` review passed. Socket/route-loss tests use fakes;
no live network, service, phone, Windows driver or billing result is claimed.
