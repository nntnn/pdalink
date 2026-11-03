# Manual Test Checklist

## Prerequisites

- Android phone with mobile data enabled.
- USB debugging enabled.
- Linux laptop with Wi-Fi and NetworkManager.
- Android SDK/Gradle available, or Android Studio installed.
- For Windows development, WSL 1 can run source tests and shell syntax checks,
  but WSL 2 or real Linux is required for `/dev/net/tun` and route mutation.

For a fresh native Linux clone, start with the installer:

```bash
./tools/pdalink.sh install
```

It builds the APK, installs the limited Linux privilege helper, and installs
the Android app automatically when an authorized USB debugging device is
attached. If the device is missing or unauthorized, follow the command printed
by the installer and rerun the app-install portion after USB debugging is
approved.

## Build And Install

If using project-local tools:

```bash
./tools/bootstrap_android_tools.sh
./tools/build_android_manual.sh
./tools/adb.sh install -r android/app/build/manual/app-debug.apk
```

If `adb devices -l` shows `unauthorized`, unlock the phone and approve the USB
debugging RSA prompt, then rerun the install command.

The Gradle build is also available through `./tools/build_android.sh`. The
manual builder is kept for resource-constrained shells where Gradle cannot
start enough worker threads. The manual builder can use the repo-local
`.tools/jdk/temurin-17` JDK and does not require the system `zip` command.

If using Android Studio:

1. Open `android/`.
2. Build the `app` debug variant.
3. Install it on the USB-connected phone.

## Android App

1. Open `PdaLink`.
2. Grant requested Wi-Fi/nearby device/notification permissions.
3. Open `Connections`, select `Development USB`, and tap `Apply USB mode`.
4. Select `Wi-Fi Direct`, then choose `Automatic`, `2.4 GHz`, or `5 GHz`.
5. Tap `Apply Wi-Fi mode` and return to `Overview`.
6. Wait for the Wi-Fi card to show the group information.
7. Open `Diagnostics` and confirm the requested band, actual frequency, and
   `Wi-Fi ownership: PdaLink`.
8. Return to `Overview`; note the SSID, reveal or copy the passphrase, and copy
   the Wi-Fi proxy target.
9. Send an HTTP or HTTPS request through either proxy. Confirm the Overview
   client metric separates currently active clients from clients retained in
   recent in-memory history, for example `1 active · 2 known`. Multiple sockets
   from one client must not increase either client count.
10. Tap that metric and confirm `Proxy clients` shows the transport, client
    address (or `USB host`), destination host and port, method, start time,
    duration, and per-session traffic. After the request ends, confirm it moves
    to `Recently ended`.
11. Confirm Android Back and `‹ Overview` return to Overview without adding a
    fourth bottom-navigation destination.

The session list must not display a full HTTP URL path or query. Recent
destinations are memory-only, limited to the newest sessions, and disappear
when the corresponding proxy transport stops. `Known` therefore means clients
identified from those current and recent proxy sessions, not every device
associated with the Wi-Fi network.

If another app already owns a Wi-Fi Direct group, PdaLink must report that it
does not own the group, keep the Wi-Fi Direct proxy closed, and leave the
existing group untouched. Stop that group explicitly before retrying PdaLink.

Changing the band while a group is active briefly disconnects Wi-Fi Direct
clients and recreates the group. The app reuses the same SSID and passphrase.
An explicit 2.4 GHz or 5 GHz failure is reported instead of silently falling
back to another band. Android 9 and earlier expose `Automatic` only.

Verify all modes on Android 10 or later:

1. Start with `Automatic` and record the actual frequency.
2. Select `2.4 GHz`, tap `Apply Wi-Fi mode`, and confirm an actual frequency
   between 2400 and 2499 MHz.
3. Select `5 GHz`, tap `Apply Wi-Fi mode`, and confirm an actual frequency
   between 4900 and 5899 MHz.
4. Confirm the SSID and passphrase are unchanged across steps 2 and 3.
5. Confirm the USB proxy remains reachable throughout the band changes.

## Local-Only Hotspot Multi-Client

This mode is simpler than Wi-Fi Direct for ordinary Wi-Fi clients. Android
creates a local AP, but does not attach Android tethering or an internet
upstream to it. PdaLink listens on that AP and each client sends HTTP/HTTPS
traffic explicitly through the displayed proxy.

1. Open `Connections`, select `Development USB`, and tap `Apply USB mode`.
   Select `Local Hotspot` and tap `Apply Wi-Fi mode`.
2. Approve the Android or vendor hotspot dialog if it appears on first use.
3. In `Overview`, read the network, security, and proxy target from the Wi-Fi
   card. Reveal or copy the password when joining a client.
4. Join two clients using the current SSID and password.
5. Configure each Wi-Fi profile as local-only: no default route, no automatic
   DNS, and no IPv6 default route.
6. Fetch `<Wi-Fi target>/proxy.pac` and make an HTTPS request using the same
   target as an explicit HTTP proxy from both clients at the same time.
7. Confirm USB proxy traffic still succeeds while both Wi-Fi clients are
   active.

Expected results:

- The Wi-Fi target uses the dynamically discovered hotspot address and port
  `8001`; it is not assumed to be `192.168.49.1`.
- Android reports the AP as local-only with no tethering upstream.
- Both clients receive HTTP responses through the PdaLink proxy while keeping
  their normal default route unchanged.
- `Clients` displays `unavailable`; Android's public Local-Only Hotspot
  reservation API does not provide this count to the app.

Stop and recreate the reservation before testing credential recovery. If the
SSID or password changes, replace the saved client profile values with the
current `Overview` card. Also check `Security`: some phones create a
WPA2/WPA3 transition network that older WPA2-only adapters cannot join even
when the password is correct.

On an authorized Linux ADB host, test automatic profile refresh without
printing the password:

```bash
./tools/pdalink.sh wifi local-hotspot sync --mode auto --connect
```

Then test the host-side rotation guard:

```bash
./tools/pdalink.sh wifi local-hotspot sync --mode pinned
# Recreate the Android reservation. A changed credential must be rejected.
./tools/pdalink.sh wifi local-hotspot sync --mode pinned
./tools/pdalink.sh wifi local-hotspot sync --mode pinned --reset-pin
```

The pin file contains the SSID and a password hash with mode `0600`, not the
password. The NetworkManager profile marks the secret agent-owned; PdaLink
passes it through a mode-`0600` one-use password file during activation and
removes that file afterward.
Pinned mode cannot force Android 15/API 35 to reuse credentials because its
public Local-Only Hotspot API does not accept an app-selected SSID or
passphrase. The PdaLink `Connections` page reports this policy; fixed custom
Local Hotspot credentials require Android 16/API 36 or later.
Verify that opening the app, refreshing status, restarting only the USB
watcher, and repeating a Local Hotspot start while its reservation is already
active do not change the credential fingerprint. A stop followed by a new
explicit start is a new Android reservation and may rotate it.

### Raspberry Pi Zero development target

This path requires USB for initial provisioning and for recovery after a real
Android credential rotation. Once provisioned, the same-credential cold-boot
path must work without USB.

1. On the Zero, run `sudo ./tools/setup_zero_usb_gadget.sh activate`. Confirm
   that Linux binds `cdc_ncm` and `cdc_acm`, then configure the host with
   `./tools/setup_zero_usb_host.sh usb0`.
2. Confirm bidirectional ping on `10.55.0.1/24` and key-only
   `ssh pdalink-zero`. Confirm the ACM login prompt remains available on the
   stable by-id serial device.
3. Only after the live link passes, run
   `sudo ./tools/setup_zero_usb_gadget.sh persist` and reboot. Repeat the ping,
   SSH, and serial checks.
4. Authorize the dedicated host key on the Zero, verify key-only SSH, then
   install `sudo ./tools/install_zero_target.sh` on the Zero. Confirm that the
   sudoers entry permits only `/usr/local/sbin/pdalink-target-apply` with no
   arguments, `/etc/pdalink/client.env` is mode `0600`, and password SSH is
   disabled while the ACM serial login prompt still works.
5. On the host, enable `local_hotspot_credentials=auto`,
   `local_hotspot_join=if_disconnected`, and
   `local_hotspot_target=pdalink-zero`, then restart the user watcher.
6. Stop and explicitly recreate the Android Local Hotspot. Within the watcher
   retry window, confirm both Linux and Zero use the new SSID, both profiles
   connect, and HTTPS succeeds from both machines. The ordinary Linux profile
   reports `psk-flags=1`. The Zero profile reports `autoconnect=yes`,
   `autoconnect-retries=0`, and `psk-flags=0`; its NetworkManager keyfile is
   root-owned mode `0600`, while the Zero target-state file contains only a
   credential hash.
7. Keep an unrelated Linux Wi-Fi connection active and repeat a watcher pass.
   `if_disconnected` must not replace that connection.
8. With the Android reservation still active and unchanged, remove the Zero USB
   cable, power-cycle the Zero from its power port, and confirm that it rejoins
   the Local Hotspot, starts `pdalink-client.service`, and reaches HTTPS without
   receiving another credential payload.

## Linux Connection

If the laptop used the same phone's normal Wi-Fi hotspot as an installation
safety path, turn that hotspot off after the USB proxy has been restored and
before creating the Wi-Fi Direct group. On devices that make hotspot and P2P
mutually exclusive, `dumpsys wifip2p` otherwise remains in `P2pDisabledState`.

Scan:

```bash
python3 linux/pdalink_lite.py scan
```

Connect:

```bash
python3 linux/pdalink_lite.py connect --ssid 'DIRECT-...' --password '...'
```

Or let the helper pick the strongest visible Android Direct group:

```bash
python3 linux/pdalink_lite.py connect-auto --password '...'
```

Probe:

```bash
python3 linux/pdalink_lite.py probe
```

Full diagnosis:

```bash
python3 linux/pdalink_lite.py doctor --adb
python3 linux/pdalink_lite.py doctor --curl-url https://ifconfig.me
```

Automated smoke loop after the laptop has joined the Android Wi-Fi Direct
network:

```bash
./tools/e2e_smoke.sh
```

## Linux Managed Wi-Fi Direct Proxy

On a NetworkManager + GNOME desktop, connect and apply the PAC in one
transaction:

```bash
./tools/pdalink.sh wifi internet start --password 'password-shown-in-android'
./tools/pdalink.sh wifi internet status
./tools/pdalink.sh wifi internet test --url https://cloudflare.com/cdn-cgi/trace
```

Expected status:

- the active Wi-Fi SSID matches `DIRECT-*`;
- the route to `192.168.49.1` uses the Wi-Fi interface;
- port `8001` serves a PAC advertising `192.168.49.1:8001`;
- `GNOME managed proxy` reports `state: active`;
- the HTTPS test returns HTTP content through the Direct proxy.

Restore the prior GNOME proxy values:

```bash
./tools/pdalink.sh wifi internet stop
```

Use `--keep-wifi` when testing restore without disconnecting the Direct group.
If status reports `drifted`, inspect the newer desktop proxy setting before
using `wifi internet stop --force`; the default stop deliberately preserves
settings changed after PdaLink started.

To rebuild, install, start through the ADB-safe debug activity, then verify:

```bash
./tools/build_android_manual.sh
./tools/e2e_smoke.sh --install android/app/build/manual/app-debug.apk
```

If probe times out, check the route:

```bash
ip route get 192.168.49.1
```

The route must use the Wi-Fi interface with a `192.168.49.x` source address. If
it says `via 192.168.0.1 dev enp...`, the laptop is still on the normal LAN and
has not joined the Android Wi-Fi Direct group.

Test HTTPS tunneling:

```bash
python3 linux/pdalink_lite.py curl-test --url https://ifconfig.me
```

The returned IP should be the phone's cellular egress IP.

The Android `Overview` and `Diagnostics` pages should show:

- `Mode: USB + Wi-Fi Direct`.
- `USB proxy: listening on 127.0.0.1:8000`.
- `Wi-Fi proxy: listening on 192.168.49.1:8001`.
- `Wi-Fi ownership: PdaLink`.
- `Wi-Fi clients:` with the number of devices currently joined to the
  Direct group.
- `Active proxy connections:` reported separately from group membership.
- The `Overview` Wi-Fi card with SSID, reveal/copy password controls, and proxy
  target. `Diagnostics` also shows the P2P interface.
- `Wi-Fi band:` with the requested mode and actual MHz.
- `USB target: http://127.0.0.1:8000`.
- `Wi-Fi target: http://192.168.49.1:8001`.
- `Last request:` changing when `probe` or `curl-test` runs.

For the multi-client gate, join the displayed group from two separate devices,
refresh the Android status, and require `Wi-Fi clients: 2` in `Diagnostics`. Run an HTTPS
proxy request from both devices at the same time, then disconnect one device and
require the count to return to `1`. Repeated connect/disconnect churn or either
request bypassing `192.168.49.1:8001` fails the gate.

## Debugging

Android logs:

```bash
./tools/adb.sh logcat -s PdaLinkProxy PdaLinkService
```

Check proxy manually:

```bash
curl -v -x http://192.168.49.1:8001 https://example.com/
curl -v http://192.168.49.1:8001/proxy.pac
```

Probe the supported Wi-Fi Direct port list:

```bash
python3 linux/pdalink_lite.py probe --ports 8000,8001
```

## USB Forward Prototype

This path does not require joining the Android Wi-Fi Direct network. It uses USB
debugging and ADB TCP forwarding. The preferred wrapper flow is:

```bash
./tools/build_android_manual.sh
./tools/pdalink.sh usb start --install android/app/build/manual/app-debug.apk
./tools/pdalink.sh usb status
./tools/pdalink.sh usb test --no-start-android
```

Equivalent manual commands:

```bash
python3 linux/pdalink_lite.py usb-start --verify
python3 linux/pdalink_lite.py usb-status
python3 linux/pdalink_lite.py usb-smoke --no-start-android --curl-attempts 3 --url https://ifconfig.me
```

`usb-smoke` retries curl by default because the forwarded proxy port can open
before Android has finished binding outbound proxy sockets to cellular data.
`pdalink.sh usb status` reports the ADB device, TCP forward, proxy probe, and a
next command to run.

## Concurrent USB And Wi-Fi Direct

Keep the existing USB forward and start Wi-Fi Direct additively:

```bash
./tools/pdalink.sh usb start --verify
./tools/pdalink.sh wifi start-android
./tools/pdalink.sh wifi connect --password 'password-shown-in-android'
```

Verify both listeners independently, then issue requests through both at the
same time:

```bash
curl -fsS -x http://127.0.0.1:18080 https://ifconfig.me/ip &
curl -fsS -x http://192.168.49.1:8001 https://ifconfig.me/ip &
wait
```

Stop only Wi-Fi Direct and prove the USB forward is still healthy:

```bash
./tools/pdalink.sh wifi stop-android
./tools/pdalink.sh usb status
```

Remove the local forward when finished:

```bash
./tools/pdalink.sh usb stop
```

## Windows Host Safety Notes

When the Windows host is already online through an existing desktop network
adapter, leave that adapter and the default Windows route alone. Do not use this
prototype to create a second Windows network adapter while that connection is
your only internet path.

The low-risk coexistence test is explicit proxy use only:

```powershell
adb devices -l
adb forward tcp:18080 tcp:8000
curl.exe -x http://127.0.0.1:18080 https://ifconfig.me
```

Run those commands from Windows PowerShell with a Windows `adb.exe` available in
`PATH`. The repo-local `.tools/android-sdk/platform-tools/adb` is the Linux
platform-tools binary used from WSL or bash, not a Windows PowerShell command.
If you installed Windows platform-tools repo-locally, call it explicitly:

```powershell
.\.tools\platform-tools-windows\adb.exe devices -l
```

Avoid `adb kill-server` during this test unless you are prepared to restore the
current Android USB connection. If `adb devices -l` shows `unauthorized`, unlock
the phone and approve the USB debugging prompt before continuing.

If Windows Device Manager shows a Samsung/Android ADB interface but
`adb devices -l` is empty while another desktop USB client is active, stop here
and do not force reconnect. Treat the existing desktop client as owning the
active USB data path until you have a second internet path or are ready to
recover the current connection.

Observed coexistence result on the Windows 10 host:

- The existing desktop network adapter kept the default route.
- Existing unrelated ADB forwards were left untouched.
- Repo-local Windows `adb.exe` detected `R3CR406AAGE device`.
- A separate forward `tcp:18080 -> tcp:8000` was added temporarily.
- `curl.exe http://127.0.0.1:18080/proxy.pac` returned the PdaLink PAC.
- `curl.exe -x http://127.0.0.1:18080 https://ifconfig.me` returned HTTP 200
  through the PdaLink proxy.
- Cleanup removed only `tcp:18080` and stopped `dev.pdalink.prototype`; the
  unrelated forwards remained.

The repeatable smoke wrapper is:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_proxy_smoke.ps1
```

## Windows USB Internet Mode

The first usable Windows mode is system proxy automation. It does not create a
new Windows network adapter yet, but it makes Windows proxy-aware traffic use
the Android USB proxy without adding `-x http://127.0.0.1:18080` to each
command.

Start:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 start
```

The start action:

- verifies an authorized ADB device
- starts `dev.pdalink.prototype`
- creates `tcp:18080 -> tcp:8000` unless the same forward already exists
- probes `http://127.0.0.1:18080/proxy.pac`
- curls through the explicit proxy once
- backs up the current WinINET user proxy settings under `.tools\runtime`
- enables `http=127.0.0.1:18080;https=127.0.0.1:18080`
- verifies that Windows system proxy resolution points to `127.0.0.1:18080`

Check status or run the system-proxy smoke again:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 test
```

Stop and restore the previous proxy settings:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 stop
```

The stop action restores the backed-up Windows proxy settings, removes only the
temporary PdaLink `tcp:18080` ADB forward, and stops the PdaLink debug service.
It leaves unrelated ADB forwards untouched.

This USB mode covers Windows components and applications that honor the
WinINET or .NET system proxy. It is not a raw-socket tunnel. The separate
experimental Wi-Fi Direct Wintun stage below covers IPv4 TCP and UDP/53, but
does not turn the USB path into a Windows virtual adapter.

## Windows Wi-Fi Direct Stage 1: Managed PAC

This implementation is present but must remain labelled unverified until the
following smoke passes on both Windows 10 and Windows 11. Start with the Android
app showing an active `DIRECT-*` group and `Wi-Fi Direct proxy: listening on
192.168.49.1:8001`.

First parse all Windows scripts without executing them:

```powershell
$failed = $false
Get-ChildItem .\tools\windows_*.ps1 | ForEach-Object {
  $tokens = $null
  $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile(
    $_.FullName, [ref]$tokens, [ref]$errors
  ) > $null
  if ($errors.Count) {
    $failed = $true
    $errors | Format-List
  }
}
if ($failed) { exit 1 }
```

Record the current user proxy values, then start. Do not pass the password as a
plain command-line string; the script prompts with `Read-Host -AsSecureString`.

```powershell
$internetSettings = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
  'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
)
$proxySnapshot = foreach ($name in 'ProxyEnable','ProxyServer','ProxyOverride','AutoConfigURL','AutoDetect') {
  if ($key.GetValueNames() -contains $name) {
    [pscustomobject]@{
      name = $name
      kind = $key.GetValueKind($name).ToString()
      value = $key.GetValue($name, $null,
        [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    }
  } else { [pscustomobject]@{ name = $name; kind = 'absent'; value = $null } }
}
$key.Close()
$proxySnapshot | ConvertTo-Json | Set-Content -Encoding UTF8 "$env:TEMP\pdalink-proxy-before.json"
Get-NetRoute -AddressFamily IPv4 | ConvertTo-Json -Depth 4 |
  Set-Content -Encoding UTF8 "$env:TEMP\pdalink-routes-before.json"
Get-DnsClientServerAddress | ConvertTo-Json -Depth 4 |
  Set-Content -Encoding UTF8 "$env:TEMP\pdalink-dns-before.json"
netsh wlan show profiles > "$env:TEMP\pdalink-wlan-before.txt"
netsh winhttp show proxy > "$env:TEMP\pdalink-winhttp-before.txt"

powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 start `
  -Ssid 'DIRECT-...'
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 test `
  -Url https://cloudflare.com/cdn-cgi/trace
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 stop
```

Acceptance:

- the temporary WLAN XML no longer exists after profile import;
- the state file contains no password or `keyMaterial`;
- `status` reports `active`, exact PAC, Wi-Fi interface, and `192.168.49.x`
  source while running;
- `test` resolves the public URL through `192.168.49.1:8001` using the Windows
  system proxy;
- stop restores all five WinINET values, including value absence and registry
  type, then removes only the GUID-owned WLAN profile;
- existing default routes, DNS, USB ADB forwards, WinHTTP proxy, and unrelated
  WLAN profiles are unchanged.

Repeat with initial WinINET states `none`, manual proxy, existing PAC, and
auto-detect, including a `REG_EXPAND_SZ` PAC value containing `%LOCALAPPDATA%`
and non-ASCII SSID/interface names. Compare the typed registry snapshot and the
route, DNS, WLAN, and WinHTTP snapshots after stop. Also exercise wrong
SSID/password, unreachable PAC, and a reachable non-canonical PAC (including a
correct endpoint hidden in a comment or dead branch); all must leave WinINET
untouched. While active, change `AutoConfigURL` manually and confirm normal stop
refuses drift. Inspect it before using `stop -Force` to recover the saved
snapshot.

## Windows Wi-Fi Direct Stage 2: Experimental Wintun IPv4 Tunnel

This stage requires Python 3.10+ and an elevated PowerShell window. It carries
IPv4 TCP and UDP/53 DNS only. It does not carry IPv6, ICMP, arbitrary UDP, or
QUIC, and the hand-built TCP state machine is not product-grade.

Install the pinned official signed Wintun DLL once, then start without Stage 1
PAC state active:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 setup
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 start `
  -Ssid 'DIRECT-...'
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 status
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 test `
  -Url https://cloudflare.com/cdn-cgi/trace
```

While active, verify ownership and route selection:

```powershell
Find-NetRoute -RemoteIPAddress 192.168.49.1
Find-NetRoute -RemoteIPAddress 1.1.1.1
Get-NetRoute -DestinationPrefix 192.168.49.1/32,0.0.0.0/1,128.0.0.0/1
Get-NetAdapter -Name 'PdaLink-*'
Get-NetIPInterface -InterfaceAlias 'PdaLink-*' -AddressFamily IPv4
Get-DnsClientServerAddress -InterfaceAlias 'PdaLink-*' -AddressFamily IPv4
curl.exe --proxy "" --noproxy "*" -4 https://cloudflare.com/cdn-cgi/trace
Resolve-DnsName example.com -Type A -Server 1.1.1.1 -DnsOnly -NoHostsFile
```

Expected:

- the proxy `/32` route and selected source stay on the Wi-Fi interface;
- both `/1` routes select the GUID-suffixed PdaLink Wintun adapter;
- DNS servers are assigned only to that Wintun interface;
- no-proxy `curl.exe -4` and an A-record lookup succeed;
- the protected ProgramData state includes PID/start ticks, exact interfaces,
  routes, DLL hash and cleanup paths, but no Wi-Fi password.

Stop and verify exact cleanup:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 stop
Get-NetRoute -DestinationPrefix 192.168.49.1/32,0.0.0.0/1,128.0.0.0/1 `
  -ErrorAction SilentlyContinue
Get-NetAdapter -Name 'PdaLink-*' -ErrorAction SilentlyContinue
Get-NetIPInterface -InterfaceAlias 'PdaLink-*' -AddressFamily IPv4 -ErrorAction SilentlyContinue
Get-DnsClientServerAddress -InterfaceAlias 'PdaLink-*' -AddressFamily IPv4 `
  -ErrorAction SilentlyContinue
netsh wlan show profiles
Test-Path "$env:ProgramData\PdaLink\runtime\windows-wifi-direct-tunnel.json"
```

The PdaLink route/adapter/interface/DNS queries must return no owned object and
the state path must return `False`. Repeat with a bad proxy, a foreign pre-existing `/1`
route, forced Python termination, Android disconnect, and suspend/resume.
`start` must roll back partial state; stale recovery must remove only entries
matching the saved prefix, interface, and next hop after first confirming the
saved adapter name/index pair. While active, change an owned `/1` or proxy `/32`
route metric and confirm stop still removes it. Simulate interface-index reuse
and confirm stop retains state without deleting the new adapter's routes. Keep
the state file when any cleanup step fails. Use `stop -Force` only to ignore
deletion of an already-removed PdaLink WLAN profile, never to delete a foreign
route. The post-stop proxy `/32`, DNS, MTU/metric, GUID WLAN profile, and
ProgramData state must all be absent or equal their pre-start snapshots.

Performance remains a manual diagnostic gate: compare three explicit-proxy and
three Wintun transfers, require Wintun median throughput at least 70% of the
explicit proxy, verify a 1 GiB transfer checksum, keep an idle TCP session for
30 minutes, and test 64 simultaneous TCP/DNS operations. Any disconnect,
checksum mismatch, unbounded memory growth, or failure to clean routes blocks a
product-ready claim.

## WSL 2 Relay For Windows ADB Forward

WSL 2 has its own network namespace. A Windows ADB forward such as
`tcp:18080 -> tcp:8000` listens on Windows `127.0.0.1`, not WSL's
`127.0.0.1`. If the system-routing test runs inside WSL 2, expose the Windows
listener through the WSL default gateway address:

```powershell
$wslHost = (wsl -e sh -lc "ip route | awk '/default/ {print $3; exit}'").Trim()
powershell -ExecutionPolicy Bypass -File .\tools\windows_tcp_relay.ps1 `
  -ListenAddress $wslHost -ListenPort 18081 `
  -TargetAddress 127.0.0.1 -TargetPort 18080
```

Leave that relay running while testing from WSL 2:

```bash
curl --fail http://192.168.2.193:18081/proxy.pac
curl --fail -x http://192.168.2.193:18081 https://ifconfig.me
python3 linux/pdalink_lite.py system-status --proxy-url http://192.168.2.193:18081
sudo ./tools/system_connect_test.sh --no-start-usb \
  --proxy-url http://192.168.2.193:18081 \
  --url http://1.1.1.1/cdn-cgi/trace
```

Replace `192.168.2.193` with the WSL default gateway printed on your machine.
The smoke script runs `system-start`, verifies no-proxy IPv4 TCP plus UDP/53
DNS, then calls `system-stop` for cleanup. Stop the relay PowerShell process
after the WSL test.

## Experimental System Routing

The USB proxy path above is still an HTTP/HTTPS proxy. To route applications
that do not support proxy settings, Linux uses a TUN interface plus the built-in
packet adapter. The adapter converts IPv4 TCP streams to HTTP `CONNECT` through
the Android proxy and converts UDP/53 DNS queries to TCP DNS over the same proxy
path.

Check local readiness:

```bash
./tools/pdalink.sh system status --no-probe-proxy
```

Expected on a machine with local TUN support:

```text
tool ip: OK ...
tun: OK /dev/net/tun
adapter: BUILTIN sudo python3 linux/pdalink_lite.py system-start ...
```

Expected on WSL 1:

```text
tool ip: OK ...
tun: MISSING /dev/net/tun
next:
  - enable the Linux TUN device and permissions for /dev/net/tun
```

If TUN is missing, stop here. Do not run `system-start`, `system test`, or
`system up --apply` from that environment.

Print the dry-run route plan:

```bash
./tools/pdalink.sh system plan
```

Start the USB proxy first:

```bash
./tools/pdalink.sh usb start --verify
```

One-command offline connection test:

```bash
./tools/pdalink.sh system test --expect-offline
```

Use this after turning off laptop Wi-Fi/Ethernet while keeping the phone on
cellular data and USB debugging authorized. The script starts the USB proxy,
starts `system-start` in the background, runs `curl` without proxy environment
variables, then removes the split-default routes before exiting. If direct
internet still works before the USB route starts, `--expect-offline` fails so
the test does not produce a false positive. The default URL is
`https://1.1.1.1/cdn-cgi/trace`, so the first smoke proves TCP internet without
depending on the laptop's current DNS resolver. Use `--url https://ifconfig.me`
for a DNS-including test.

## PRD #3 Long-Lived TCP Soak Commands

Run these only after the product or ADB route path is active and `curl` proof
already confirms traffic through `pdalink0`.

Set a target SSH server and a writable log directory:

```bash
export SOAK_HOST='user@host'
export SOAK_KEY=~/.ssh/id_ed25519
export SOAK_DURATION=900
export SOAK_DIR=.tools/runtime/long-lived-tcp
mkdir -p "$SOAK_DIR"
```

Idle soak (no app payload, keepalive only):

```bash
timeout "$SOAK_DURATION"s ssh -i "$SOAK_KEY" -vv \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=6 -o TCPKeepAlive=no \
  -o NumberOfPasswordPrompts=0 "$SOAK_HOST" "sleep $SOAK_DURATION" \
  2>&1 | tee "$SOAK_DIR/idle-$(date -u +%Y%m%dT%H%M%SZ).log"
```

Active soak (small repeated remote writes; adjust the loop count if
`SOAK_DURATION` is not 900):

```bash
timeout "$SOAK_DURATION"s ssh -i "$SOAK_KEY" -vv \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=6 -o TCPKeepAlive=no \
  -o NumberOfPasswordPrompts=0 "$SOAK_HOST" \
  'i=0; while [ "$i" -lt 180 ]; do echo "$(date -Iseconds) active-step:$i"; i=$((i+1)); sleep 5; done' \
  2>&1 | tee "$SOAK_DIR/active-$(date -u +%Y%m%dT%H%M%SZ).log"
```

SCP soak (upload/download loop; keep `seq` count consistent with
`SOAK_DURATION`):

```bash
dd if=/dev/urandom of="$SOAK_DIR/pdalink-soak.bin" bs=1M count=8

SOAK_HOST="$SOAK_HOST" SOAK_KEY="$SOAK_KEY" SOAK_DIR="$SOAK_DIR" \
  timeout "$SOAK_DURATION"s bash -c '
for i in $(seq 1 120); do
  ts="$(date -u +%s)"
  scp -i "$SOAK_KEY" "$SOAK_DIR/pdalink-soak.bin" "$SOAK_HOST":/tmp/pdalink-soak-$ts.bin
  scp -i "$SOAK_KEY" "$SOAK_HOST":/tmp/pdalink-soak-$ts.bin "$SOAK_DIR/pdalink-soak-$ts.bin"
  cmp "$SOAK_DIR/pdalink-soak.bin" "$SOAK_DIR/pdalink-soak-$ts.bin"
  rm -f "$SOAK_DIR/pdalink-soak-$ts.bin"
  ssh -i "$SOAK_KEY" "$SOAK_HOST" "rm -f /tmp/pdalink-soak-$ts.bin"
done
' 2>&1 | tee "$SOAK_DIR/scp-$(date -u +%Y%m%dT%H%M%SZ).log"
```

Evidence format (append one JSON object per case):

```json
{
  "case": "idle|active|scp",
  "duration_s": 900,
  "ssh_host": "user@host",
  "start_utc": "2026-06-09T12:00:00Z",
  "end_utc": "2026-06-09T12:15:00Z",
  "exit_code": 0,
  "log": ".tools/runtime/long-lived-tcp/idle-20260609T120000Z.log",
  "reconnects": 0,
  "disruptions": [],
  "byte_checksum_ok": true
}
```

Pass criteria:

- Exit code is `0`.
- `reconnects` is `0` and no timeout/KEX/permission failures appear in `log`.
- `byte_checksum_ok` is `true` for SCP.
- The command output remains periodic for active soak and stays under the requested
  timeout.

## ADB-Free Product USB Release Gate

The ADB USB flow above is a development and recovery transport. A product-mode
release must pass with Android USB debugging disabled.

Before route mutation, inspect the product USB state:

```bash
./tools/pdalink.sh product-usb status
./tools/pdalink.sh product-usb check
```

The Linux AOA descriptor handoff can be inspected without touching USB:

```bash
./tools/pdalink.sh product-usb aoa-start --dry-run --serial SHA256:<host-fingerprint>
```

For a real AOA handoff, install PyUSB and pass the exact Android USB vendor and
product id shown by `lsusb` before the phone enters accessory mode. Do not run
the non-dry-run command against an arbitrary USB device.

```bash
./tools/pdalink.sh product-usb aoa-start --vid 04e8 --pid 6860 --serial SHA256:<host-fingerprint>
```

After a successful handoff, the device re-enumerates as Android Open Accessory.
On the Samsung SM-G996N testbed this changed `lsusb` from `04e8:6860` to
`18d1:2d01`. If `adb devices -l` then reports `no permissions`, install or
refresh Linux udev rules for the AOA USB id, reload udev, and reconnect the
phone:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ATTR{idProduct}=="2d01", MODE="0664", GROUP="plugdev"
```

After accessory attach, the Android app should show the Product USB consent
surface. In `Connections`, select `Product USB` and tap `Apply USB mode`, or
approve the accessory prompt. Then start
the Linux Product USB bridge in a separate terminal:

```bash
./tools/pdalink.sh product-usb bridge --local-port 18080
```

The bridge opens the AOA bulk endpoints, sends a Product USB `hello` frame with
the Linux host fingerprint, waits for the Android trust response, serves
`http://127.0.0.1:18080/proxy.pac`, and refreshes the Product USB readiness
state. The current tracer path validates the live accessory byte stream through
the PAC probe before route mutation. Full HTTP CONNECT byte-stream forwarding is
tracked separately from this PAC tracer bullet.

Expected ready state:

```text
transport: product-usb (product)
release-gate: ready
USB debugging: off
```

If status reports `permission missing`, approve the Android PdaLink USB
permission prompt. If it reports `host untrusted` or `host revoked`, approve or
restore the Linux host in the Android app and restart the bridge. If it reports
`usb debugging not disabled`, Android is still exposing an ADB-capable AOA id
such as `18d1:2d01`; disable Android USB debugging and reconnect before release
validation. If it reports `transport unavailable`, reconnect USB and enable
PdaLink USB mode. If it reports `state stale`, restart the Product USB bridge so
readiness is refreshed from a live transport before route mutation. Do not start
the TUN route path until the product USB status is ready.

The guarded route command for the no-ADB smoke is:

```bash
sudo python3 linux/pdalink_lite.py system-start --require-product-usb
```

The managed Product USB watcher path uses the same guard without creating ADB
forwards:

```bash
./tools/pdalink.sh watch once --transport product-usb
./tools/pdalink.sh watch status --transport product-usb
```

While it is active, verify traffic without an explicit proxy:

```bash
curl -4 https://ifconfig.me
```

Also run a UDP/53 DNS lookup through `pdalink0`, then stop and verify cleanup:

```bash
sudo ./tools/pdalink.sh system stop
```

Then run the built-in whole-system adapter as root:

```bash
sudo python3 linux/pdalink_lite.py system-start
```

For native Linux, install the privilege rule once before using the background
service loop:

```bash
sudo ./tools/install_linux_privileges.sh --user "$USER"
```

After this, `system service start/stop` uses `sudo -n` and must not ask for a
password. If the installer has not been run, the command should fail quickly
instead of hanging in the background waiting for input.

For repeatable background operation, use the service wrapper instead:

```bash
./tools/pdalink.sh system service start --proxy-url http://127.0.0.1:18080
./tools/pdalink.sh system service status --proxy-url http://127.0.0.1:18080
./tools/pdalink.sh system service logs
./tools/pdalink.sh system service stop --proxy-url http://127.0.0.1:18080
```

For the native Linux target flow, use the watcher after installing privileges
and approving the Android USB debugging prompt:

```bash
./tools/pdalink.sh watch run
```

In another terminal:

```bash
./tools/pdalink.sh watch status
curl -4 http://1.1.1.1/cdn-cgi/trace
```

Expected behavior:

- With no phone attached, status reports `device: MISSING` and no route service
  is started.
- With the phone attached but not authorized, status reports the ADB state and
  does not start routing.
- With an authorized phone, the watcher starts `dev.pdalink.prototype`, creates
  `tcp:18080 -> tcp:8000`, waits for `/proxy.pac`, then starts the system
  service.
- If USB is unplugged or debugging authorization disappears, the watcher stops
  its managed system service and removes only the ADB forward it created.

### Ubuntu desktop and suspend/resume recovery

Do not run the real suspend portion during a remote development session that
depends solely on this PdaLink connection. Arrange a second connection first,
or defer that portion until the session is complete.

Install and open the desktop client:

```bash
./tools/pdalink.sh client install
./tools/pdalink.sh client status
./tools/pdalink.sh client run
```

Confirm the client shows independent device, forward, proxy, system, hotspot,
target-relay, and resume stages. Click `Disconnect` and confirm
`pdalink-watch.service` remains active while owned routes and the USB forward
are cleaned up. Click `Connect USB` and require the UI to return to `Connected`.

For a real resume check, first record status, suspend Ubuntu, then resume and
inspect both views:

```bash
./tools/pdalink.sh watch status
journalctl --user -u pdalink-watch.service --since "10 minutes ago" --no-pager
```

Expected behavior:

- The journal reports `recovering-resume` and validates the device, ADB
  forward, Android proxy, TUN service, routes, and DNS again.
- A healthy owned path returns to `ready` without recreating unrelated network
  resources.
- A stale owned forward or unhealthy owned route service is repaired.
- Missing USB or ADB authorization is shown as a waiting state, and retry stays
  available from the client.
- Local-Hotspot profile sync and target SSH relay have separate health and
  backoff. A target relay failure must not take down an otherwise healthy USB
  path.

If you are testing from WSL 2 with a Windows-owned ADB forward, keep using the
manual relay path instead:

```bash
./tools/pdalink.sh system service start --proxy-url http://192.168.2.193:18081
./tools/pdalink.sh system service status --proxy-url http://192.168.2.193:18081
./tools/pdalink.sh system service logs
./tools/pdalink.sh system service stop --proxy-url http://192.168.2.193:18081
```

The wrapper stores pid, state, and log files under `.tools/runtime`. `stop`
delegates to `system-stop`, so cleanup still uses the recorded state file.

`system-start` probes the proxy before changing routes. If the USB proxy is not
reachable, it exits before opening the TUN route path.

In another shell, test traffic without an explicit proxy:

```bash
curl https://ifconfig.me
```

Stop with `Ctrl-C` in the `system-start` shell. Cleanup removes the
split-default routes and the TUN interface. `system-start` writes a runtime
state file after route setup; if the foreground shell is lost, run:

```bash
sudo ./tools/pdalink.sh system stop
```

This stops the recorded process when possible and removes the recorded
routes/interface.

On the verified Windows 10 / WSL 2 host, the proxy URL was the Windows relay:

```bash
sudo python3 linux/pdalink_lite.py system-start --proxy-url http://192.168.2.193:18081
```

While it was active:

- `ip route get 8.8.8.8` selected `dev pdalink0`.
- `curl -4 https://ifconfig.me` worked without `-x` or proxy environment
  variables.
- A UDP DNS query to `8.8.8.8:53` returned a valid response, covering the
  UDP/53-to-TCP-DNS path.

Run this command in a foreground shell so `Ctrl-C` reaches Python and triggers
cleanup. If it was started under `nohup` or another wrapper that ignores
`Ctrl-C`, terminate that process and run:

```bash
sudo ./tools/pdalink.sh system stop
```

If the runtime state file is missing, use
`sudo python3 linux/pdalink_lite.py system-down --apply` as the manual fallback.

Current limits:

- IPv4 TCP is supported.
- UDP support is limited to DNS on destination port `53`.
- Idle TCP cleanup is disabled by default. Pass `--tcp-idle-timeout SECONDS` to
  close idle TCP flows explicitly for diagnostic or resource-bounded runs.
- DNS proxy failures return a UDP DNS `SERVFAIL` response.
- IPv6, ICMP, arbitrary UDP, and QUIC/HTTP3 are not supported yet.
- If a browser prefers QUIC, force TCP/TLS or test with `curl` first.

The older external-adapter commands remain available for comparison:

```bash
./tools/pdalink.sh system up
./tools/pdalink.sh system down
sudo ./tools/pdalink.sh system up --apply --adapter-ready
sudo ./tools/pdalink.sh system down --apply
```
