# PdaLink iOS Client

The iOS client scans the same `pdalink://connect` QR as the Android client,
joins the Android host Wi-Fi after explicit system approval, verifies the exact
`/proxy.pac` endpoint from the QR, and starts an iOS packet-tunnel extension.

The current client carries IPv4 TCP and virtual-DNS traffic through the
Android HTTP CONNECT proxy. Generic UDP, QUIC, ICMP, and IPv6 remain outside
the current PdaLink proxy contract.

## Requirements

- macOS with Xcode 16 or later
- XcodeGen 2.42 or later (`brew install xcodegen`)
- Rust through rustup, plus cbindgen (`cargo install --locked cbindgen`)
- A physical iPhone running iOS 17 or later
- An Apple Developer signing team with Hotspot Configuration and Network
  Extension capabilities enabled for both bundle identifiers
- An Android phone running the current PdaLink host app

The packet tunnel cannot be exercised meaningfully in the iOS Simulator. App
Store distribution of a VPN app also requires an organization developer
account and the disclosures required by App Review Guideline 5.4.

## Generate and build

The tunnel engine is built from the pinned `tun2proxy 0.8.2` commit. The helper
verifies the source archive and repository-owned patch hashes, disables the
upstream force-exit watchdog only for iOS, and builds an arm64 XCFramework into
the ignored `ios/Vendor/` directory. That patch is required because a packet
tunnel may stop and restart in one extension process; the upstream watchdog
would otherwise terminate a later connection two seconds after the prior run.

```bash
./tools/build_ios.sh prepare
./tools/build_ios.sh generate
./tools/build_ios.sh test
open ios/PdaLinkClient.xcodeproj
```

In Xcode, select a signing team for both `PdaLinkClient` and
`PdaLinkPacketTunnel`. If you change the containing app bundle identifier,
keep the extension identifier equal to `<app bundle id>.packet-tunnel`.

A non-signing generic-device compile can be run on macOS with:

```bash
./tools/build_ios.sh build
```

## Real-device smoke test

1. On Android, start PdaLink Local Hotspot and open `Overview > Connection QR`.
2. Install and open PdaLink Client on the iPhone.
3. Tap `Scan QR`, scan the Android QR, and confirm that only the SSID,
   transport, security, and private proxy endpoint are shown. The password
   must never be displayed or written to app preferences.
4. Tap `Connect` and approve both the Wi-Fi and VPN system prompts.
5. Confirm that the client reaches `Connected`, then open an IPv4 HTTPS site
   in Safari. On Android, confirm that `Proxy clients` shows the iPhone and
   traffic counters increase.
6. Lock the iPhone for one minute, unlock it, and confirm the VPN reconnects or
   remains connected and HTTPS still works.
7. Tap `Disconnect`. Confirm that the VPN stops, the app-owned Android Wi-Fi
   configuration is removed, and the iPhone returns to its previous network.

Repeat with the Android Wi-Fi Direct QR as a compatibility test. The code uses
the same public Wi-Fi join API for both transports, but Wi-Fi Direct legacy
client behavior must be verified on each target iPhone/Android combination.

## Security and routing notes

- QR parsing rejects unknown or duplicate fields, public proxy IPs, credentials
  in the proxy URL, and proxy paths, queries, or fragments.
- The Wi-Fi password exists only in the parsed in-memory connection object. The
  saved VPN provider configuration contains only SSID, private proxy IP, and
  port.
- The proxy gets an excluded `/32` route before the default IPv4 tunnel route,
  preventing the tunnel from recursively capturing its own HTTP CONNECT
  socket.
- The extension bridges the public `NEPacketTunnelFlow` API to `tun2proxy`
  through a Unix datagram socket pair. It does not use KVC or private APIs to
  extract Apple's internal `utun` file descriptor.
- The raw PAC probe is capped at 16 KiB and accepts only HTTP 200 containing
  the exact `PROXY <QR host>:<QR port>` directive after the response completes.
- Start/stop generations prevent a delayed Network Extension callback from
  reviving a tunnel that the user already stopped. Outbound packet batches wait
  for socket write readiness instead of being discarded after a short retry.
