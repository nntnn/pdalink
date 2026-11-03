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

1. Start Local Hotspot or Wi-Fi Direct in the PdaLink host app.
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
the app-owned local Wi-Fi request.

The Android client uses the pinned official `tun2proxy 0.8.2` Android release.
Its HTTP CONNECT path carries TCP and virtual-DNS traffic. Generic UDP, QUIC,
IPv6, and ICMP are not guaranteed by the current PdaLink HTTP proxy contract.

## Linux

Scan with a camera and connect through NetworkManager:

```bash
./tools/pdalink.sh connection import --camera
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

The import command creates or updates a local-only NetworkManager profile,
connects it, and verifies the exact proxy PAC endpoint. It does not print the
password. Existing `wifi env`, `wifi internet`, or `system` commands still
control how Linux applications route through the verified proxy.

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
powershell -ExecutionPolicy Bypass -File .\tools\windows_connection_qr.ps1 -Camera -Action internet
```

Restore the previous WinINET settings and remove the owned WLAN profile with:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 stop
```

`-Action internet` affects proxy-aware Windows applications. It is not the
administrator-level Wintun whole-system path. The URI and plaintext password
remain in process memory only long enough to create the Windows secure string
and temporary WLAN profile; the helper does not print either value.
