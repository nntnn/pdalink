# PdaLink Connection QR

The Android host can show one QR that contains the current local Wi-Fi and
proxy settings. The same payload is accepted by the Android client and the
Linux and Windows helpers.

## Payload

Version 1 uses a URI with percent-encoded query values:

```text
pdalink://connect?v=1&transport=local-hotspot&ssid=...&security=wpa2-psk&password=...&proxy=http%3A%2F%2F10.0.0.1%3A8001
```

Required fields are `v`, `transport`, `ssid`, `security`, `password`, and
`proxy`. Current transports are `local-hotspot` and `wifi-direct`. Current
security values are `wpa2-psk`, `wpa3-sae`, and `wpa2-wpa3`.

All clients reject duplicate or unknown fields, invalid Wi-Fi credentials,
non-HTTP proxy URLs, and proxy addresses outside RFC 1918 IPv4 space. The QR
does not contain an ADB credential or proxy authentication token.

## Host

1. Select the recommended Wi-Fi Direct mode and tap `Apply Wi-Fi mode`.
   Local Hotspot remains available, with Android-managed rotating credentials.
2. Open `Overview` and tap `Connection QR` in the active Wi-Fi section.
3. Keep the dialog visible while the client scans it.

The QR dialog blocks Android screenshots. It still contains the active Wi-Fi
password, so only show it to a trusted client. Stopping and recreating Android
Local-Only Hotspot may rotate the payload.

## Android Client

Build both Android applications:

```bash
./tools/bootstrap_android_tools.sh
./tools/build_android.sh
```

Artifacts:

```text
android/app/build/outputs/apk/debug/app-debug.apk
android/client/build/outputs/apk/debug/client-debug.apk
```

Open `PdaLink Client`, scan the host QR, and tap `Connect`. Android presents a
Wi-Fi network approval and a VPN approval. The client then verifies the host
PAC before starting its TUN packet engine. `Disconnect` releases the VPN and
the app-owned local Wi-Fi request and disables automatic retry. The QR profile
is retained privately, so reopening the app never requires another scan.

While connected, the foreground service retries Wi-Fi loss, request timeout,
PAC startup failure and tunnel exit, with a 5–60 second backoff. It verifies the
PAC again before recreating the VPN. In Android Settings → VPN → PdaLink Client,
enable **Always-on VPN** for client reboot recovery; leave **Block connections
without VPN** off. VPN consent, Nearby Wi-Fi permission, and initial network
approval still require the user. Reapproval can be needed when Android sees a
new AP identity after host restart. Force-stop, revoked permissions and vendor
battery restrictions can require reopening the client. See
[platform limits and verification](WIFI_DIRECT_FIXED_GROUP.md).

The Android client uses the pinned official `tun2proxy 0.8.2` Android release.
Its HTTP CONNECT path carries TCP and virtual-DNS traffic. Generic UDP, QUIC,
IPv6, and ICMP are not guaranteed by the current PdaLink HTTP proxy contract.

## Linux

Scan with a camera and connect through NetworkManager:

```bash
./tools/pdalink.sh connection import --camera --internet
```

Read an existing QR image:

```bash
./tools/pdalink.sh connection import --image ./connection.png
```

Read a URI from stdin without placing the Wi-Fi password in shell history:

```bash
printf '%s\n' "$PDALINK_CONNECTION_URI" | ./tools/pdalink.sh connection import
```

Camera and image decoding use OpenCV's QR detector. Install `opencv-python` in
the selected Python environment when that optional input path is needed. URI
input does not require OpenCV.

If NetworkManager refuses profile changes for the current user, keep camera
access unprivileged and elevate only the import step:

```bash
./tools/scan_connection_qr.py --camera 0 | sudo python3 linux/pdalink_lite.py connection-import
```

Direct import creates/updates a NetworkManager profile with autoconnect enabled,
unlimited retries, saved PSK, no default route, no DHCP DNS, and IPv6 disabled.
NetworkManager rejoins after link loss and login/reboot. The password is never
printed. `--internet` additionally persists a verified GNOME PAC with the existing
private restore snapshot; GNOME proxy-aware apps resume new requests after
reconnection without a PdaLink background process. Restore it with
`./tools/pdalink.sh wifi internet stop --keep-wifi`. Import without `--internet`
only joins/probes; non-GNOME desktops must configure the PAC in their own proxy
settings. The GTK app still controls the separate USB supervisor.
Local Hotspot imports retain their temporary credential policy.

## Windows 10

The Windows helper first checks the clipboard for a PdaLink URI. It can also
scan a camera or image through the same optional OpenCV adapter:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1 -Camera
powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1 -Image .\connection.png
```

To join Wi-Fi and apply the verified PAC to the current user's WinINET proxy
settings:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1 -Camera
```

Restore the previous WinINET settings and remove the owned WLAN profile with:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 stop
```

The default action is `internet`: this is the one-time Windows entry point.
It stores an automatic WLAN profile in Windows and a persistent per-user PAC.
Both survive client reboot; new proxy-aware requests resume when WLAN rejoins.
No script needs to remain running. `-Action connect` joins without changing PAC;
`-Action show` only inspects the QR. Explicit stop removes the owned WLAN profile.
If another Wi-Fi network takes priority, Windows may keep it until disconnected;
OS Wi-Fi policy and radio availability still govern automatic selection.

`-Action internet` affects proxy-aware Windows applications. It is not the
administrator-level Wintun whole-system path. The URI and plaintext password
remain in process memory only long enough to create the Windows secure string
and temporary WLAN profile; the helper does not print either value.
