# Fixed Wi-Fi Direct group and reconnect

## Audit (2026-11-03, before implementation)

- Host `buildWifiP2pConfig` already saves SSID/PSK in private preferences on
  API 29+. Band changes, Stop all, and restart do not erase them. There is no
  explicit rotation action. Credential generation uses asynchronous persistence.
- Host service is sticky and restores enabled transports on process restart,
  but there is no boot/package-update receiver. Missing groups and channel
  disconnection can leave sharing idle. No Android tethering API is used:
  multiple clients use the app's own HTTP sockets, with a separate USB listener.
- Connection QR already supports Direct at 192.168.49.1:8001. Inactive UI selects
  Wi-Fi off; Direct needs recommendation/default selection.
- Android client discards imported profiles at process death, returns non-sticky,
  and shuts down on Wi-Fi loss, request timeout, PAC failure or native exit.
  Its isolated VPN process needs one owner for persistent profile state.
- Linux QR import calls the manual Direct profile helper (autoconnect=no).
  Local-only IPv4 settings exist, but IPv6 ignore can leave external IPv6
  configuration. Import only joins/probes; it does not enable desktop internet.
  GTK `pdalink_client.py` controls the USB supervisor, not QR import.
- Windows QR import defaults to join only. WLAN XML uses manual connection.
  WinINET PAC already persists over restart/reconnect; Wintun has a separate,
  experimental process/route lifecycle and no unattended reboot supervisor.
- Existing design Q9/Q19 and HOST_AUTOMATION_PLAN prohibit background joining;
  this task supersedes that rule for an explicitly imported Direct profile.

## Implementation contract

Only explicit credential rotation changes an existing valid saved Direct pair.
App data clearing/uninstall loses it; API <29 cannot pin it. Stop all disables
reboot recovery until sharing is started again, while retaining credentials.
Boot recovery occurs after unlock with granted Wi-Fi permissions; user force-stop,
permission revocation, disabled Wi-Fi and vendor background restrictions can
prevent it. The only added host permission is RECEIVE_BOOT_COMPLETED (normal,
not dangerous). No new dangerous permission or tethering API is introduced.

Android stores the imported URI privately with backup disabled. The VPN process
owns the enabled flag. A running foreground VPN retries transient outages;
Always-on VPN in Android Settings provides reboot startup. Network approval and
VPN approval remain mandatory. Android may require Wi-Fi approval again after
AP identity/BSSID changes even when SSID/PSK stay fixed. An unconditional
"forever, zero prompts" guarantee is therefore impossible with public APIs.
See [network requests](https://developer.android.com/develop/connectivity/wifi/wifi-bootstrap)
and [Always-on VPN](https://developer.android.com/develop/connectivity/vpn#always-on).
Do not enable lockdown: the proxy process must use the physical Wi-Fi network.

Linux imported Direct profiles opt in to NetworkManager autoconnect with unlimited
retries and retain the PSK in the OS credential store. They never supply default
routes or DNS. GNOME PAC is a persistent optional import action; other desktops
need their own proxy configuration. Reconnect restores new TCP connections;
existing streams interrupted by an outage are not resumable. USB remains separate.

Windows onboarding uses the existing QR script and persistent WinINET PAC,
with an automatic WLAN profile. This provides reboot recovery for proxy-aware
apps without a background PdaLink process. Experimental Wintun remains manual
across client reboot and is not the supported connect-once entry point.

## Optional Linux system tunnel

QR import still prepares the saved local-only Wi-Fi link. It does not enable
system routing. `./tools/pdalink.sh wifi tunnel start|stop|status` explicitly
controls the shared IPv4 TCP/DNS tunnel lifecycle without USB. Update the
installed root helper before using this path.

The built-in Linux adapter resolves the non-loopback proxy's upstream device
before creating the TUN or changing routes. Direct requires an on-link Wi-Fi
source in `192.168.49.0/24`. Every TCP CONNECT, including DNS-over-TCP, binds
with `SO_BINDTODEVICE`. Link/route removal therefore fails closed instead of
using `pdalink0`. This avoids a host-route lifecycle and keeps route cleanup
unchanged. Other remote proxies require a literal IPv4 endpoint and an existing
upstream route; loopback USB remains unbound. The state records the selected
device. An old remote-proxy state without binding is reported as UNBOUND.
External adapters must supply their own equivalent loop protection.

Watcher failover is explicitly opt-in with `--wifi-direct-failover` or
`PDALINK_WIFI_DIRECT_FAILOVER=1`, alongside the existing
`auto_connect=on_trusted_connect` policy. A trusted authorized USB device and
working forwarded PAC take priority. If that path cannot be prepared/probed,
the watcher accepts only a Wi-Fi Direct route and the canonical phone PAC.
It never joins/replaces a WLAN itself. The already imported NetworkManager
profile owns reconnection. The Direct PAC check is an identity sanity check,
not cryptographic authentication.

Return to USB requires 20 seconds of observed continuous health and 30 seconds
since selecting Direct. Startup, restart and resume reset USB health evidence.
An unavailable Direct link allows immediate recovery to healthy USB. Switching
stops only a watcher-owned tunnel via the existing service/root helper, then
starts the new proxy; failed cleanup prevents switching. Status and internet
health report/probe the selected endpoint. Egress failure alone does not switch
transports because both share the phone's cellular path. Existing TCP streams
are not migrated. Root DNS policy and the active Tailscale compatibility hook
apply equally to both transports.

Windows retains its automatic WLAN profile and owned `/32` route. Wintun also
constrains new proxy sockets with `IP_UNICAST_IF`, since a Wi-Fi outage may remove
the `/32`; a changed physical adapter index needs explicit stop/start. No
Windows reboot supervisor is added. Full manual acceptance criteria live in
[CLIENT_DEVICE_TESTS.md](CLIENT_DEVICE_TESTS.md); hardware remains unverified.

## Verification

All live network/phone/driver/service operations are deferred to manual testing.
Automated checks use dummy credentials and fake command runners only.

## Delivered behavior and remaining limits

The host retries a missing group/channel every 15 seconds without touching the
USB listener. Shutdown removes an owned group before clearing its ownership
flag, avoiding a leftover group after Stop all. Saved credentials are committed before use; stop and band switch
retain them. With Wi-Fi stopped, Connections → Rotate Wi-Fi Direct credentials
requires an explicit confirmation and invalidates every old client QR. Initial
creation or recovery from invalid/missing preferences also creates a pair.
Do not use the native-peer debug invitation/negotiation path for fixed-group
clients: that experimental path can negotiate platform credentials.

Android client preferences are backup-disabled and private to the app. The UI
stores its last import; only the isolated VPN process owns recovery intent and
its service profile, avoiding shared preference writes between processes.
Stale network/probe/native-exit callbacks cannot restart an obsolete generation.
The native engine is stopped before retry and a new one waits for its predecessor
to exit. Existing TCP streams still need application-level reconnection.

No claim is made for whole-system Linux/Windows routing from QR import: PAC
serves proxy-aware apps. Wintun reboot recovery and a mature full-tunnel data
path remain outside the supported flow (ADR-0003/0004). Multiple clients share
one group and proxy; actual S21+ station limits require the simultaneous test.
OS policy may prefer another saved WLAN; unavailable radios, force-stop or
user-denied approval cannot be overridden by PdaLink.

The native Android dependency can be read from shared build tools without
creating `.tools` here: export `PDALINK_TUN2PROXY_LIBS` to the pinned
`android-deps/tun2proxy-0.8.2/tun2proxy-android-libs` directory before building.
Unit tests do not load JNI, but a usable client APK must include these libraries.

## Manual device verification (not performed by this change)

1. Install the built host and client APKs manually on the intended phones. Keep
   a recovery connection available. On the S21+, grant requested Wi-Fi and
   notification permissions, turn on Wi-Fi/mobile data, open Connections,
   select Wi-Fi Direct, and tap Apply Wi-Fi mode. Open Overview → Connection QR.
   Compare QR/SSID privately; never paste passwords or QR URIs into test logs.
2. On Linux with NetworkManager/GNOME, run once:
   `./tools/pdalink.sh connection import --camera --internet`.
   Grant NetworkManager authorization if prompted. On other desktops import
   without `--internet` and set the desktop PAC to
   `http://192.168.49.1:8001/proxy.pac` once. Test using
   `./tools/pdalink.sh wifi internet test` on GNOME. Inspect the imported profile
   in NetworkManager: autoconnect yes, retries 0, no default route/DHCP DNS,
   IPv6 disabled. Ensure another connection still owns any physical default.
3. On Windows 10 and 11, run once in the user's PowerShell:
   `powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1 -Camera`.
   Install Python/OpenCV first if camera decoding is needed, or place the URI
   on the clipboard and omit `-Camera`. Use the internet helper's `status` and
   `test` actions; confirm the WLAN profile is automatic and WinINET uses the
   fixed PAC. Wintun is not needed for this supported path.
4. On the Android client, scan once, tap Connect, grant Wi-Fi/network/VPN
   approvals, then enable Always-on VPN in Android's VPN settings (lockdown off).
   Browse from another app. Close/reopen the activity and verify the saved
   profile, without rescanning.
5. With all three clients connected simultaneously, make requests on each.
   On the host change band (test 2.4 GHz for broad compatibility), restart the
   app/service, reboot and unlock, update the APK with the same signing key,
   and toggle Wi-Fi off/on. After each event allow up to two minutes, confirm
   the same SSID/PSK privately and successful new requests from every client.
   Record any Android reapproval prompts instead of assuming they cannot occur.
6. Reboot each client, log in/unlock, and verify new requests resume without
   rescanning. Repeat after a long host outage and client sleep/resume. Android
   must show its foreground VPN notification; Windows/Linux retain PAC settings.
7. Stop all on the host and reboot: sharing must remain stopped. Start Direct
   again: credentials must match. Stop Wi-Fi, explicitly rotate credentials,
   start Direct and verify old profiles fail until the new QR is imported.
   Declining the rotation dialog must preserve credentials.
8. Independently verify the existing USB ADB forward to `127.0.0.1:8000` before,
   during and after Wi-Fi start, band change, stop and rotation. Verify Direct
   remains available when USB alone is stopped. Check Android tethering remains
   off and proxy requests use the phone's app sockets/mobile egress.
9. Test explicit cleanup: Android Disconnect stops retries, Windows internet
   `stop` restores the proxy and removes its WLAN profile, Linux
   `wifi internet stop --keep-wifi` restores the proxy while retaining the
   imported autoconnect profile. Remove that saved profile through OS settings
   to opt out of automatic joining entirely.

## Offline results

- Python discovery: 287 tests, 280 passed, 6 skipped (optional platform/GTK
  dependencies), one known pre-existing failure:
  `test_linux_install_dry_run_prints_udev_rules` (missing GTK packages).
- Android offline build and both Java unit suites: 38 host + 1 client tests pass.
  Client APK checked for pinned JNI libraries on arm64-v8a, armeabi-v7a, x86,
  and x86_64. APKs were built, not installed.
- `bash -n tools/pdalink.sh` and `git diff --check` pass.
- No on-device, NetworkManager activation, service configuration, native Windows
  or simultaneous-client test was performed. All require the manual procedure
  above; hardware recovery is not marked verified by unit tests.
