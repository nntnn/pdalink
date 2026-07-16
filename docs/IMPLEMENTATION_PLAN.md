# PdaLink Prototype Implementation Plan

## Target

Build a clean-room Android-to-Linux tethering prototype with the smallest
verifiable data path:

```text
Linux laptop
  -> Wi-Fi Direct group
  -> Android HTTP/HTTPS proxy on 192.168.49.1:8000
  -> Android cellular socket
  -> Internet
```

The first prototype uses USB debugging for installing, logging, and inspecting
the Android app.

## Success Criteria

- Android app can create a Wi-Fi Direct group.
- Linux laptop can join the group.
- Linux can reach the Android proxy at `192.168.49.1:8000` or fallback `8001`.
- `curl -x http://192.168.49.1:8000 https://ifconfig.me` returns a public IP.
- Android log/status shows accepted proxy connections.

## Diagnostics Hardening Slice

The first live run proved the proxy path works after the Linux laptop is
actually attached to the Android Wi-Fi Direct group. The next slice makes that
state reproducible and self-diagnosing.

Target result:

- Linux can explain why `probe` fails before the user manually reasons about
  routes, ports, or missing tools.
- Linux can auto-select a visible `DIRECT-*` network when the passphrase is
  supplied, then verify the proxy immediately after connecting.
- Android publishes one status snapshot containing proxy port, Wi-Fi Direct
  SSID/interface, Linux target URL, recent request, and recent error.
- Android proxy handling fails loudly on malformed targets or unsupported body
  framing instead of truncating traffic silently.

Stop condition:

- Python tests pass for CLI parsing, route diagnostics, network selection, and
  doctor output.
- Android manual debug APK builds with the project-local manual builder.
- If a phone is attached and a Direct link is active, `doctor` can confirm the
  working proxy and curl through it.
- A local pre-landing review is run against the feature diff. Remote PR review
  is skipped unless a git remote appears.

## Test Loop And Proxy Hardening Slice

Target result:

- One command can start the Android proxy through the ADB-safe debug activity,
  wait for the Linux proxy probe, and run the HTTPS curl smoke test.
- Proxy request failures produce explicit HTTP errors instead of silent socket
  resets where possible.
- Proxy resource handling tracks active clients, applies socket timeouts, and
  bounds request headers and fixed-size request bodies.

Stop condition:

- `./tools/e2e_smoke.sh --install android/app/build/manual/app-debug.apk`
  passes on a USB-attached phone after the laptop is on the Direct link.
- Android manual build succeeds.
- Python unit tests and shell syntax checks pass.
- A local diff review finds no blocking correctness or security issue.

## USB Forward Prototype Slice

Target result:

- Linux can expose the Android proxy over USB debugging without joining the
  Wi-Fi Direct network.
- ADB starts the Android foreground service directly, without launching an
  Activity on the phone.
- `adb forward tcp:18080 tcp:8000` maps the laptop's localhost proxy to the
  Android proxy.
- `curl -x http://127.0.0.1:18080 https://ifconfig.me` reaches the internet
  through the Android proxy path.
- The smoke loop retries curl after the local port opens, because Android
  cellular network binding can lag behind service startup.

Stop condition:

- Linux unit tests cover ADB forward parsing and USB command orchestration.
- `python3 linux/pdalink_lite.py usb-smoke` succeeds with the phone attached.
- Existing Wi-Fi Direct smoke remains available through `./tools/e2e_smoke.sh`.

## USB Usability Slice

Target result:

- A user can operate the USB path through `./tools/pdalink.sh usb start`,
  `status`, `test`, and `stop` without remembering the lower-level Python
  command names.
- `usb status` reports ADB device authorization, active TCP forward state,
  localhost proxy probe state, and the next command to run.

Stop condition:

- Unit tests cover ADB device parsing and status next-step selection.
- Shell syntax checks pass for the new wrapper.
- The wrapper can run `usb status` and `usb test` against the attached phone.

## System Routing / TUN Control Plane Slice

Target result:

- Linux has an explicit whole-system routing control plane instead of only
  per-command HTTP proxy environment variables.
- `system-status` checks `iproute2`, `/dev/net/tun`, the forwarded Android
  proxy, and whether a packet adapter command was supplied.
- `system-plan` prints a dry-run plan for the TUN interface, split-default IPv4
  routes, packet-adapter command, and rollback commands.
- `system-up` and `system-down` are dry-run by default. Actual route mutation
  requires `--apply`, and `system-up --apply` also requires `--adapter-ready`
  so traffic is not accidentally blackholed before the packet adapter exists.

Stop condition:

- Python unit tests cover route parsing, adapter-command templating, TUN command
  generation, status next-step output, and dry-run/apply guardrails.
- `python3 linux/pdalink_lite.py system-plan` prints a coherent end-to-end
  command sequence without changing routes.
- `python3 linux/pdalink_lite.py system-status --no-probe-proxy` reports local
  TUN capability and the missing packet-adapter boundary.
- The wrapper exposes the same flow through `./tools/pdalink.sh system ...`.
- A local service wrapper exposes `./tools/pdalink.sh system service
  start/status/stop/logs` with stable pid, state, and log files.
- A native Linux installer writes root-owned helper files plus a narrow sudoers
  drop-in so runtime service commands use `sudo -n` instead of interactive
  password prompts.

## Built-In USB Internet Adapter Slice

Target result:

- Linux can use the Android USB proxy as a system internet path without passing
  `-x http://127.0.0.1:18080` to each command.
- A built-in user-space adapter opens a Linux TUN interface and converts
  outbound IPv4 TCP streams into HTTP `CONNECT` streams through the Android
  proxy.
- UDP/53 DNS queries are converted to TCP DNS through the same proxy path, so
  common resolver configurations can work even when DNS traffic enters the TUN.
- `system-start` owns the built-in adapter lifecycle: open TUN, assign address,
  add split-default routes, run until `Ctrl-C`, and clean up routes.

Stop condition:

- Unit tests cover IPv4/TCP packet construction and parsing, UDP DNS packet
  construction and parsing, and HTTP `CONNECT` request generation.
- Unit tests cover TCP idle-flow cleanup and DNS `SERVFAIL` generation when the
  DNS-over-TCP proxy path fails.
- `python3 linux/pdalink_lite.py system-plan` points to the built-in
  `system-start` command.
- `python3 linux/pdalink_lite.py system-status --no-probe-proxy` reports the
  built-in adapter.
- `python3 linux/pdalink_lite.py system-start` refuses without root instead of
  partially changing routes.
- `./tools/pdalink.sh system test --expect-offline` provides a one-command
  manual smoke that starts USB forwarding, runs the adapter, curls without proxy
  environment variables, and cleans up routes.
- Live route mutation is tested manually only when the operator intentionally
  runs `sudo python3 linux/pdalink_lite.py system-start`.

## Long-Lived TCP Stability Slice (PRD #3)

Target result:

- The repository has a documented live TCP-soak verification path for release using
  the existing no-proxy `pdalink0` path:
  - idle SSH keepalive-only
  - active SSH with periodic payload
  - SCP upload/download loop
- Issue #10 spike compares current built-in adapter behavior against
  transparent TCP-proxy and tun2socks/netstack alternatives while requiring that
  Android-side socketing remains HTTP `CONNECT`-only.
- Issue #11 release gate references the manual soak evidence schema and fail
  criteria.

Stop condition:

- `docs/MANUAL_TEST.md` has command blocks for all three soak cases and a
  machine-readable evidence schema.
- `docs/adr/0003-long-lived-tcp-data-path-options.md` documents the spike result,
  decision, and risk trade-offs.
- Delivery and release docs in this repo link PRD #3 checks so an operator can
  execute and verify release readiness without inferring undocumented commands.

## Native Linux Watcher Slice

Target result:

- The native Linux USB internet path can be operated as a normal-user
  background watcher after the one-time privilege installer has run.
- The watcher verifies Android debugging state with `adb devices -l` before
  attempting data-path setup.
- When an authorized device is present, it starts
  `dev.pdalink.prototype/.ProxyService`, creates or reuses only the
  matching `tcp:18080 -> tcp:8000` ADB forward, probes the forwarded proxy, and
  starts `system service start` through the non-interactive privilege helper.
- When the device disappears, becomes unauthorized/offline, or the proxy path
  fails, it stops only its managed system service and removes only the forward
  it created.

Stop condition:

- `./tools/pdalink.sh watch run`, `once`, and `status` are available.
- Unit tests cover authorized start, unauthorized cleanup, forward conflict,
  proxy failure cleanup, and read-only status behavior.
- The watcher does not overwrite unrelated ADB forwards.
- The watcher can be run without `sudo`; privileged route mutation remains
  behind the installed root helper and `sudo -n`.
- Documentation distinguishes native Linux direct-ADB operation from WSL 2
  development with a Windows-owned ADB relay.

## Fresh Clone Linux Installer Slice

Target result:

- A user or LLM agent can clone the repository and run one documented command
  to prepare native Linux USB internet testing.
- The installer checks Python, Linux tools, TUN availability, and package
  prerequisites before doing expensive Android setup.
- Android setup is repo-local: platform-tools, command-line tools, Gradle, and
  Temurin JDK 17 are downloaded into `.tools/` when needed.
- The installer builds the debug APK, runs the limited privilege helper
  installer, and branches on current USB debugging state.
- If an authorized Android device is attached, the installer installs and
  starts the app automatically. If the device is unauthorized or missing, it
  prints the exact command to run after the user approves debugging or plugs in
  the phone.
- `INSTALL.md` includes a concise LLM-agent prompt that tells an assistant what
  to run, what not to run directly, and what evidence to report.

Stop condition:

- `./tools/pdalink.sh install` is available.
- `tools/install_linux.sh --dry-run --skip-privileges --skip-android-build
  --skip-android-install --apk PATH` exercises the non-mutating path.
- Static tests cover bootstrap JDK download wiring, installer command flow,
  USB-state branches, and LLM-agent documentation.
- Full Linux unit tests and shell syntax checks pass.

## Windows USB Internet Usability Slice

Target result:

- Windows can use the Android USB proxy without passing `-x
  http://127.0.0.1:18080` to every command.
- A PowerShell wrapper starts the Android debug proxy, creates the ADB forward,
  verifies the forwarded proxy, backs up the current Windows user proxy
  settings, enables `http=127.0.0.1:18080;https=127.0.0.1:18080`, and restores
  the previous settings on stop.
- The wrapper refuses to overwrite an unrelated `tcp:18080` ADB forward.
- The wrapper leaves unrelated ADB forwards and the Windows default route
  untouched.

Stop condition:

- `tools/windows_usb_internet.ps1 start` enables Windows proxy-aware internet
  traffic through the Android USB proxy.
- `tools/windows_usb_internet.ps1 test` verifies that Windows system proxy
  resolution points at `127.0.0.1:18080` and that an HTTP request succeeds.
- `tools/windows_usb_internet.ps1 stop` restores the previous Windows user
  proxy settings, removes only the temporary PdaLink ADB forward, and stops the
  Android debug proxy.
- The script passes PowerShell syntax validation.

Boundary:

- This is the first usable Windows internet mode, but it is a system proxy mode,
  not a kernel network adapter. It covers Windows components and applications
  that honor WinINET/.NET system proxy settings. Raw sockets, ICMP, arbitrary
  UDP, and applications that bypass system proxy settings still require a
  full-tunnel adapter.

## Windows Wi-Fi Direct PAC And Experimental Tunnel Slice

Target result:

- Stage 1 joins the Android Wi-Fi Direct group and transactionally applies the
  exact Android PAC to WinINET without requiring per-application proxy setup.
- Stage 2 gives Windows a real Wintun virtual network interface so IPv4 traffic
  can use the Android Wi-Fi Direct proxy without application proxy support.
- A Windows packet adapter reuses the Linux adapter design: read IPv4 packets
  from TUN, convert TCP streams to HTTP `CONNECT` through the Android proxy, and
  convert UDP/53 DNS to TCP DNS through the same proxy.
- Route changes are opt-in and reversible: create interface, assign address,
  add split-default routes, preserve a connected route to the proxy endpoint,
  and remove routes/interface on stop.
- The first Windows full-tunnel smoke proves ordinary `curl` without proxy
  settings, DNS lookup, and route cleanup while an existing desktop connection
  remains the fallback recovery path.

Stop condition:

- `windows_wifi_direct_internet.ps1 start|status|test|stop` snapshots typed
  WinINET values, verifies before mutation, rejects drift, and restores exactly.
- The official signed Wintun 0.14.1 archive URL and SHA-256 are pinned; the
  architecture DLL is re-hashed before every elevated adapter start.
- Admin-rights setup/start/stop scripts exist and refuse route mutation when
  the proxy endpoint is untrusted or unreachable or foreign `/1` routes exist.
- A no-proxy Windows smoke test succeeds with the system proxy disabled.
- Cleanup restores Windows routes and removes the virtual interface.

Current status:

- PowerShell scripts, Wintun ctypes backend, protected state, rollback, static
  tests, fake Wintun lifecycle tests, and shared packet regression tests are
  implemented.
- Windows 10/11 real-host driver/WLAN/WinINET/route/DNS/cleanup smoke remains
  required, so the stage is implemented but unverified.
- The supported packet claim is IPv4 TCP plus UDP/53. ADR-0003 still classifies
  the hand-built TCP engine as a short-term diagnostic path. A WFP ALE
  redirector or mature netstack remains the product direction.

## Local-Only Hotspot Multi-Client Slice

Target result:

- Android reserves a Local-Only Hotspot and discovers its generated interface
  address instead of assuming the Wi-Fi Direct subnet.
- USB remains available while multiple ordinary Wi-Fi clients use the hotspot
  proxy on port `8001`.
- Wi-Fi client profiles remain local-only and do not install a default route.
- The app reports Android's selected SSID, password, security mode, interface,
  and proxy target.

Current status:

- Android ownership, lifecycle cleanup, dynamic address selection, transport
  mutual exclusion, and operator status are implemented.
- An ADB-shell-only, `DUMP`-protected receiver exposes the active reservation
  from process memory. Linux can update the local-only NetworkManager profile
  automatically or reject rotations using a hash-only pinned guard.
- A Linux host and Raspberry Pi Zero 2 W have passed simultaneous HTTPS proxy
  requests on one Local-Only Hotspot while USB remained active.
- The tested Windows 10 Intel Dual Band Wireless-AC 7260 adapter can scan the
  phone's WPA2/WPA3 transition AP but cannot associate with it. The public
  Android reservation API does not provide an app-selected WPA2 fallback.
- Carrier accounting behavior is not claimed. The acceptance evidence is
  limited to Android reporting a local-only AP with no tethering upstream.

## ADB-Free USB Transport Slice

Target result:

- The data path no longer requires USB debugging or `adb forward`.
- ADB remains the development and recovery transport, not the product
  acceptance path.
- The Android side exposes an explicit app-owned USB transport or documented
  local-only transport, and the Windows side connects through that transport
  before the proxy/TUN layers.

Stop condition:

- Transport choice is selected after device/API feasibility checks.
- The transport can carry the existing HTTP proxy stream without changing
  proxy semantics.
- A product-mode smoke succeeds with Android USB debugging disabled.
- A fallback ADB mode remains available for development.

## Scope

In scope:

- Android foreground service.
- Wi-Fi Direct group owner setup.
- HTTP proxy support for plain HTTP requests.
- HTTPS `CONNECT` tunneling.
- Cellular-preferring outbound sockets through `ConnectivityManager`.
- Linux CLI helpers for scan/connect/probe/env/curl-test.
- Linux `doctor` and `connect-auto` commands.
- USB-debugging prototype using ADB TCP forwarding to the Android proxy.
- Experimental Linux whole-system routing control plane using a TUN interface,
  split-default routes, and an explicit TUN-to-proxy adapter command contract.
- Built-in Linux TUN-to-HTTP-proxy adapter for IPv4 TCP and UDP/53 DNS over the
  USB-forwarded Android proxy.
- Windows system proxy automation for USB proxy-aware internet use.
- Windows Wi-Fi Direct WinINET PAC automation.
- Experimental Windows Wintun adapter for Wi-Fi Direct IPv4 TCP and UDP/53.
- Android operator-facing status snapshot.
- HTTP proxy guardrails for request bodies and malformed targets.

Out of scope for this slice:

- Vendor-specific USB compatibility.
- A custom USB framing protocol beyond ADB `forward`.
- Product-grade Windows WFP redirector or mature userspace netstack.
- Android `VpnService` packet tunnel.
- SOCKS4/SOCKS5.
- Full IPv6, ICMP, arbitrary UDP, QUIC/HTTP3, TCP congestion control parity, or
  production-grade TCP stack behavior. Current TCP hardening is limited to
  configurable idle-flow cleanup and DNS failure response behavior.
- Carrier-evasion behavior.
- Android instrumentation tests for proxy sockets. The current environment uses
  source/build validation plus live manual smoke instead.

## Implementation Mode

Mode A: main-led implementation.

Reason: this is a new prototype with few files and no existing production code.
Parallel implementation would mostly add integration overhead.

## Files

- `android/`: Android Java/Gradle app.
- `linux/pdalink_lite.py`: Linux CLI.
- `tests/test_linux_cli.py`: Python unit tests for Linux helper logic.
- `docs/MANUAL_TEST.md`: device validation checklist.
- `tools/`: optional local Android tool bootstrap/build helpers.

## Validation

Automated in this environment:

- `python3 -m unittest discover -s tests`
- `python3 -m py_compile linux/pdalink_lite.py`
- `bash -n tools/*.sh`
- `./tools/build_android_manual.sh`

Best-effort static Android validation:

- Source inspection and Gradle project shape.
- Manual SDK build signs `android/app/build/manual/app-debug.apk`.

Manual on device:

- Build/install Android debug APK.
- Start app and create Wi-Fi Direct group.
- Join from Linux using displayed SSID/passphrase.
- Run Linux `doctor`, `probe`, and `curl-test` commands.

Review:

- `$review` is executed after local tests pass and a meaningful diff exists.
- `pr@codex`/GitHub review is attempted only if a remote exists. This local
  repo currently has no remote, so the review gate degrades to local diff
  review and optional Codex CLI review.
