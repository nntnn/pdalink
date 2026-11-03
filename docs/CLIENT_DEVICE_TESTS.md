# Client device acceptance tests

These are **manual verification for Claude**, not hardware results. Run from
this checkout after the offline checks. Keep a separate recovery connection and
local console available before changing routes. Never run two watchers against
the same runtime directory/TUN. Do not log QR URIs, passwords, or clipboard contents.
Use the same saved host SSID/password throughout; do not rotate credentials.

A pass requires new internet requests after every outage without reimporting the
QR. Existing TCP streams may break. Allow 120 seconds after the host group is
visible again. The Android client must not ask for Wi-Fi approval again when
the approved BSSID is unchanged. Record prompts, OS
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
   ./tools/pdalink.sh connection status --json
   ip route get 1.1.1.1
   resolvectl status pdalink0
   curl -4 --noproxy '*' --fail --max-time 30 https://example.com/
   ```

   Expected: running state, `state-proxy: http://192.168.49.1:8001`,
   `upstream-device: <Wi-Fi interface>`, connection transport `wifi-direct`
   with overall `connected`, internet route via `pdalink0`,
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
   The backend supervisor explicitly reconnects the owned WLAN profile on the
   same physical interface; repeat `test` and curl
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
   without that flag removes only its owned WLAN profile. If an owned
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
   network-selection and VPN approvals. Grant precise location and enable system
   Location so Android can disclose the connected AP's BSSID (including on
   Android 13+); Nearby Wi-Fi permission alone does not disclose it.
   Expected: Connected, foreground VPN
   notification and VPN indicator. The PAC must match the entire host template;
   wrong endpoint/port or redirected PAC must not start the VPN. Leave the
   initial "Connect to device?" dialog open for more than 60 s before approving:
   it must remain the same request, without a minute-by-minute replacement.
   Keep the app open until Connected, or open it once after the first approved
   connection. The visible activity selects the strongest scan result with the
   saved SSID and compatible security; the VPN process validates the profile and
   writes the normalized `bssid` to its `vpn` preferences. QR import also learns
   the pin if the group is visible and precise location is already granted.
   The service still tries connected WifiInfo opportunistically, but Android 12
   on the S10+ redacts it without foreground location access. No background
   location permission or location-type foreground service is added.
   A redacted `02:00:00:00:00:00`, malformed or missing BSSID must never be pinned;
   fix location permission/settings and reopen the connected app to learn a usable BSSID. With
   redaction unresolved, SSID-only requests can still require approval.

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

4. On the host stop **Wi-Fi Direct only**, wait 10–15 s, then start it again using
   Connections → Wi-Fi Direct → Apply Wi-Fi mode. Keep the same SSID, password
   and BSSID; a channel change (for example 2437 → 2462 MHz) is allowed.
   On the client expect a starting/retrying notification and eventual Connected
   **without any Wi-Fi approval dialog, Connect tap or rescan**. Retry fresh
   browser requests within 120 s of the SSID returning and verify host cellular
   egress. Record the BSSID, frequency, recovery time and any prompt; a prompt
   for an unchanged learned BSSID fails this case.
   Repeat with client Wi-Fi off/on. Pinned requests time out after 60 s and use
   5/10/20/40/60 s backoff (capped at 60 s indefinitely). Only one Wi-Fi request
   and one native engine may run; stale callbacks must not create a second VPN.

   Separately test a changed group BSSID with unchanged credentials. After
   **three consecutive pinned acquisition failures**, the old pin is cleared
   and the next request is SSID-only: one approval dialog is acceptable. Allow
   about 3 min 35 s from the first pinned request through all three timeouts and
   the 5/10/20 s retries, plus time for Android discovery. Leave this dialog open
   beyond 60 s: no app timeout may tear it down or stack another request. Approve,
   then repeat a short outage with the new BSSID: recovery must again need no tap.
   A long host outage can also exhaust the three attempts and require reapproval;
   record it separately from the unchanged-pin short-outage acceptance case.

   Import a new QR with a changed SSID, password or security mode: the old BSSID
   must be cleared immediately, even before Connect. Connect and approve the
   new group once, then verify no-dialog recovery. Reimporting identical Wi-Fi
   credentials (or changing only the proxy endpoint) must preserve the pin.

5. With Always-on enabled and the host ready, reboot and unlock the client:

   ```bash
   adb -s "$CLIENT_SERIAL" reboot
   # After unlocking, inspect without dumping profile/credentials:
   adb -s "$CLIENT_SERIAL" shell pidof dev.pdalink.client:vpn
   ```

   Expected: VPN foreground notification/Connected returns from the stored
   private service profile and learned BSSID without opening the activity,
   scanning or another Wi-Fi approval dialog. Verify
   new pages from the browser with mobile data still off. Repeat a client
   reboot while the host is briefly absent, then start host Direct before the
   three pinned failures and expect no-dialog recovery. For a longer absence,
   verify the three-failure SSID-only fallback separately as in step 4.
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
298 passed, 6 skipped, and only the known
`test_linux_install_dry_run_prints_udev_rules` failure (missing GTK dependencies).
The offline Gradle command above passed 38 host and 5 client unit tests and
assembled both debug APKs. The client APK contains the pinned native library
for all four configured ABIs. `bash -n tools/pdalink.sh`, `git diff --check`,
and the Direct `system-plan` review passed. Socket/route-loss tests use fakes;
no live network, service, phone, Windows driver or billing result is claimed.

Reconnect change verification: the requested offline
`:client:testDebugUnitTest :client:assembleDebug :app:testDebugUnitTest` passed
with 11 client tests and 38 host tests. Rebuilt with the shared read-only
`PDALINK_TUN2PROXY_LIBS` path above for a device-ready client APK; without that
variable this worktree's default native library directory was empty. Pytest
again reported 298 passed, 6 skipped and only the same known GTK dependency
failure. No device reconnect or approval behavior was exercised here.

Round 3 Android checks for Claude (S10+, SDK 31): with Location on and precise
location granted, approve once, return to the foreground app and confirm only
that `vpn.xml` contains a valid normalized `bssid` (do not dump the credentials).
Repeat a short host outage without approval. Repeat QR import while the group
is visible, then import changed credentials while the old VPN is connected:
the old connection must not overwrite the new profile's pin. With multiple APs
for the same SSID, confirm the strongest security-compatible scan is selected.
If scans are empty/redacted, leave the app and reopen after the group is visible;
the app reads cached OS scans and does not force scans or defeat OS throttling.

## Round 3 Windows checks for Claude

Use Windows 11 Pro / PowerShell 5.1 / Python 3.13 x64 again, including an elevated
OpenSSH logon. Profile import uses `user=all` when elevated and `user=current`
otherwise. The profile name is `PdaLink-SSID-<UTF-8 SSID hex>`; importing or
starting with the same SSID updates that profile in place. Old GUID profiles
are not bulk-deleted; normal cleanup still recognizes the old ownership format.

The backend now launches through CIM `Win32_Process.Create`, outside the
launcher's job object ([Microsoft job object documentation](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)).
It opens its own protected runtime stdout/stderr logs. Its recovery thread calls
the controller with bounded 5/10/20/40/60-second backoff (60-second cap). The
controller uses `netsh wlan show networks` on the saved interface, connects only
the owned profile when its exact SSID is visible, and verifies the saved physical
index/alias and on-link subnet route before restoring a missing owned proxy /32.
It never changes the backend's socket interface pin, takes over a conflicting
/32, or recreates a pre-existing unowned /32. Start/stop/recovery share a mutex;
cleanup persists `stopping` before deleting routes to suppress further recovery.

1. Import/start twice with the same SSID and inspect `netsh wlan show profiles`:
   one stable profile, no new GUID profile. Start from elevated SSH, end that
   SSH session, reconnect over Ethernet and check `status`, PID/start time and
   a fresh transparent request. Expect the same backend PID, active routes and
   working request. Readiness errors remain in the saved stderr log.
2. Repeat host Direct stop/start with Ethernet connected (Windows' minimize
   simultaneous connections policy enabled). Observe disconnect then explicit
   connection attempts, recovery within 120 seconds of SSID visibility, same
   Wi-Fi index, restored `192.168.49.1/32`, and fresh requests through the host.
   Repeat Wi-Fi off/on. Record WLAN events and elapsed time. A changed adapter
   index must require explicit stop/start; no automatic repinning is allowed.
3. Exercise stop with zero, one and several exact route matches, including
   after an outage; expect no StrictMode `.Count` error. Cleanup failure must
   retain state with recovery disabled. Retry stop after resolving the failure.
4. Start immediately after association while DHCP is still installing routes.
   Transient `Find-NetRoute` error 1232 must retry rather than abort immediately;
   a genuinely absent route must still end in a bounded readiness failure.
5. DHCP can install `0.0.0.0/0 via 192.168.49.1` on Wi-Fi. The group owner does
   **no NAT**. This change leaves that DHCP default route and interface metric
   unchanged: a winning Wi-Fi default can blackhole internet, especially on a
   Wi-Fi-only client after `stop -KeepWifi`. Record defaults and metrics before,
   during and after the test (`Get-NetRoute -DestinationPrefix '0.0.0.0/0'` and
   `Get-NetIPInterface -AddressFamily IPv4`). Use normal stop and disconnect from
   the Direct group to restore the usual uplink if affected. The tunnel's split
   defaults carry supported traffic while active; do not use DHCP connectivity
   as proof of tunnel success. Automatic metric/default-route management remains
   outside this round (B7's documentation option).

No Windows or Android device result is claimed by these changes.

Round 3 offline verification: the requested Gradle tasks passed (14 client unit
tests, 38 host unit tests, client debug APK assembled). The APK includes
`libtun2proxy.so` for arm64-v8a, armeabi-v7a, x86 and x86_64 using the shared
read-only native library path above. Full pytest: **304 passed, 8 skipped, 1
known GTK dependency failure** (`test_linux_install_dry_run_prints_udev_rules`).
PowerShell parser/runtime tests are skipped because this host has no PowerShell;
the added empty/single/multiple route and transient-1232 tests must run on Windows.
A subsequent focused Windows static/backend run passed all 45 selected tests.
`git diff --check` passed. Recovery timing, CIM launcher survival and Android
permission behavior still require the device checks above.
