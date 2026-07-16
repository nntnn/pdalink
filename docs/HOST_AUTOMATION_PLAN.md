# Wi-Fi Direct Host Automation Plan

## Goal

Keep the Android Wi-Fi Direct proxy transport and remove per-application proxy
setup on supported desktop hosts. A user supplies the current `DIRECT-*`
credentials once; the native host tool joins the group, verifies PdaLink, and
applies the host proxy with an explicit restore path.

This does not turn Wi-Fi Direct into Android tethering. TV, IoT, and other
hosts without a PdaLink helper still need a manual proxy configuration.

Current delivery status: the Linux NetworkManager + GNOME slice is implemented
and live-tested. Windows and macOS remain follow-up slices until each can pass
the required smoke test on its native host.

## Common Host Contract

Each supported platform exposes the same lifecycle:

```text
start  -> join DIRECT-* -> verify route and PAC -> snapshot proxy -> apply PAC
status -> report Wi-Fi, route, PAC, proxy ownership, and restore state
test   -> make one HTTP(S) request through the host-managed proxy
stop   -> restore the exact prior proxy values -> optionally disconnect Wi-Fi
```

The managed PAC URL is:

```text
http://192.168.49.1:8001/proxy.pac
```

Before host settings change, the tool must prove that the PAC advertises the
expected `192.168.49.1:8001` proxy over the local Wi-Fi Direct route.

## Safety And Ownership

- Never print or persist the Wi-Fi password outside the OS Wi-Fi credential
  store.
- Store only the previous proxy values, expected PdaLink PAC URL, connection
  name, and creation time in a user-private state file.
- Refuse a second `start` while restore state exists.
- Roll back the proxy snapshot if any post-snapshot start step fails.
- On `stop`, refuse to overwrite proxy settings that no longer match the
  PdaLink-owned values unless the user explicitly requests recovery.
- Do not replace the default route, DNS, unrelated Wi-Fi profiles, or an
  existing USB proxy path.
- Treat a reachable but unexpected PAC as an untrusted network and stop before
  mutation.

## Platform Slices

### 1. Linux / NetworkManager + GNOME

Commands:

```bash
./tools/pdalink.sh wifi internet start --password 'android-shown-password'
./tools/pdalink.sh wifi internet status
./tools/pdalink.sh wifi internet test
./tools/pdalink.sh wifi internet stop
```

NetworkManager owns the local-only Wi-Fi profile. `gsettings` owns the GNOME
PAC configuration. Non-GNOME desktops keep the existing `wifi env --probe`
fallback because a child process cannot safely change its parent shell.

Acceptance:

- `start` connects and verifies before changing GNOME proxy settings.
- State is written with mode `0600` and contains no SSID password.
- `status` distinguishes active, inactive, drifted, and unreachable states.
- Failed start restores prior GNOME proxy values and disconnects only a
  PdaLink connection created by that start.
- `stop` restores prior values and can leave Wi-Fi connected on request.

### 2. Windows 10/11

Add `tools/windows_wifi_direct_internet.ps1` with
`start|status|test|stop`. Use a temporary escaped WLAN profile for `netsh wlan`
and delete the plaintext temporary file immediately. Reuse the proven WinINET
snapshot, notification, and rollback behavior from
`windows_usb_internet.ps1`; use the Direct PAC instead of an ADB forward.

Acceptance additionally requires a real Windows host smoke because PowerShell,
WLAN profile import, and WinINET notifications cannot be executed in the
current Linux environment.

### 3. macOS

Add `tools/macos_wifi_direct_internet.sh` with the same lifecycle. Resolve the
Wi-Fi device and network service through `networksetup`, join with
`-setairportnetwork`, snapshot the automatic proxy URL/state, apply the PdaLink
PAC, and restore the snapshot on stop.

Acceptance additionally requires a real macOS smoke because `networksetup` is
not available in the current environment.

## Test And Review Gates

Linux automated gate:

```bash
python3 -m unittest tests.test_linux_cli
bash -n tools/pdalink.sh
python3 -m unittest discover -s tests
```

Windows/macOS scripts receive static contract tests in this repository plus a
documented real-host smoke. A platform is not marked verified until that smoke
has been run on the platform.

Review focuses on secret handling, transactional restore behavior, proxy drift,
route preservation, command quoting, and idempotent start/stop cycles. Runtime
performance is limited to one connection, PAC probe, and settings transaction;
no background polling is added.

## Delivery Order

1. Deliver and live-test the Linux vertical slice.
2. Extract/reuse Windows proxy-state behavior and verify on Windows 10/11.
3. Deliver the macOS `networksetup` slice and verify on a current macOS host.
4. Add credential handoff improvements such as copy buttons or QR guidance
   only after all three lifecycle implementations restore settings reliably.
