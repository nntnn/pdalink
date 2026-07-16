# PdaLink Android Prototype

This app runs an ADB USB HTTP/HTTPS proxy on Android loopback port `8000` and
one Wi-Fi proxy on port `8001`. The Wi-Fi transport can be either Wi-Fi Direct
or Android Local-Only Hotspot. USB and the selected Wi-Fi transport can carry
data concurrently through the same Android egress selector.

The Linux laptop joins the displayed Wi-Fi Direct SSID and uses:

```text
http://192.168.49.1:8001
```

as its proxy.

For Local-Only Hotspot, use the SSID, password, security mode, and dynamic
`http://<phone-address>:8001` target displayed by the app. Android chooses
these values, so do not assume the Wi-Fi Direct address `192.168.49.1`.

## Build

Use Android Studio or the project helper scripts:

```bash
../tools/bootstrap_android_tools.sh
../tools/build_android_manual.sh
```

## Runtime Notes

- The app requests cellular network access for outbound proxy sockets.
- If cellular is unavailable, it falls back to Android's default network.
- `ProxyService` is exported for existing ADB development automation but is
  guarded by Android's platform `DUMP` permission. ADB shell can invoke it;
  ordinary third-party apps cannot. The debug activity and Local Hotspot query
  receiver use the same guard. The query snapshot exists only in app memory.
- In USB mode the service binds the proxy to Android loopback before the laptop
  exposes it with `adb forward`.
- USB prototype mode reuses the same proxy service with
  `adb forward tcp:18080 tcp:8000`.
- Starting Wi-Fi Direct adds `192.168.49.1:8001` without closing the USB
  listener. `STOP_WIFI_DIRECT` removes only the Direct group/listener, while
  `STOP` still stops the entire service.
- Starting Local-Only Hotspot keeps USB running, stops only an app-owned
  Wi-Fi Direct group when necessary, reserves the hotspot through Android, and
  discovers the generated hotspot interface address before binding port
  `8001`. Wi-Fi Direct and Local-Only Hotspot are mutually exclusive.
- Local-Only Hotspot is a local link with no Android tethering upstream. Each
  client must use the displayed HTTP proxy explicitly; joining the SSID alone
  does not provide internet access.
- Android may rotate the Local-Only Hotspot SSID or password after the
  reservation is stopped or the app is reinstalled. An authorized ADB host can
  run `wifi local-hotspot sync --mode auto` to refresh its profile, or use
  `--mode pinned` to reject an unexpected rotation. Pinned mode does not force
  Android to reuse the old AP credentials.
- PdaLink never recreates a Local-Only Hotspot from persisted state, a status
  refresh, or a USB watcher restart. Only an explicit Local Hotspot start
  request may create a new reservation. Android can still stop an active
  reservation for a Settings change, emergency mode, process death, or Wi-Fi
  subsystem event; recovery then requires another explicit start.
- The public Local-Only Hotspot API does not expose the joined-client count to
  this app. Verify multiple clients by making simultaneous proxy requests.
- If Wi-Fi Direct reports `BUSY(2)`, the app checks for an existing group and
  otherwise removes stale group state before one retry.
- Only HTTP requests and HTTPS `CONNECT` are implemented in this slice.
- The proxy intentionally closes upstream HTTP connections after each request,
  caps request headers and fixed-size bodies, rejects unsupported transfer
  encodings, and reports malformed proxy requests with HTTP error responses.
- The app opens the Wi-Fi Direct proxy only for a group it owns. An unrelated
  active group is left running and reported as a conflict instead of being
  reused or removed.
- The main screen shows P2P group membership separately from active proxy
  connections, along with both endpoints, combined traffic totals, latest
  request, and latest error.
- SOCKS and full packet tunneling are deferred.
