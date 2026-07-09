# PdaLink Android Prototype

This app creates a Wi-Fi Direct group and runs a foreground HTTP/HTTPS proxy on
port `8000`, falling back to `8001` if needed.

The Linux laptop joins the displayed Wi-Fi Direct SSID and uses:

```text
http://192.168.49.1:8000
```

as its proxy.

## Build

Use Android Studio or the project helper scripts:

```bash
../tools/bootstrap_android_tools.sh
../tools/build_android_manual.sh
```

## Runtime Notes

- The app requests cellular network access for outbound proxy sockets.
- If cellular is unavailable, it falls back to Android's default network.
- For ADB-driven tests, start the foreground service directly so no Activity is
  brought to the foreground:
  `../tools/adb.sh shell am start-foreground-service -n dev.pdalink.prototype/.ProxyService -a dev.pdalink.prototype.START_USB`.
- `ProxyService` is exported for explicit ADB service starts; in USB mode it
  binds the proxy to Android loopback before the laptop exposes it with
  `adb forward`. The debug activity remains available as a compatibility shim
  for manual development.
- USB prototype mode reuses the same proxy service with
  `adb forward tcp:18080 tcp:8000`.
- If Wi-Fi Direct reports `BUSY(2)`, the app checks for an existing group and
  otherwise removes stale group state before one retry.
- Only HTTP requests and HTTPS `CONNECT` are implemented in this slice.
- The proxy intentionally closes upstream HTTP connections after each request,
  caps request headers and fixed-size bodies, rejects unsupported transfer
  encodings, and reports malformed proxy requests with HTTP error responses.
- The main screen shows the current proxy port, Wi-Fi Direct group, Linux target,
  latest request, and latest error.
- SOCKS and full packet tunneling are deferred.
