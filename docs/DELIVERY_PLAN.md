# PdaLink Delivery Plan

This plan tracks the delivery path for:

```text
Android internet <-> USB <-> Linux OS internet
```

The project already has an end-to-end WSL 2 prototype: Android proxy over ADB
USB, Windows relay, Linux TUN interface, split default routes, IPv4 TCP, and
UDP/53 DNS. The next work is to make that path repeatable before expanding
packet coverage.

## Delivery Principles

- Keep existing Windows routes and unrelated desktop connectivity untouched.
- Prefer reversible operations with explicit state files and cleanup commands.
- Treat route mutation as privileged and unsafe unless the proxy path is proven
  reachable first.
- Treat ADB as the development and recovery USB transport. Product readiness
  requires an ADB-free USB path that works with Android USB debugging disabled.
- Keep native Linux and WSL 2 flows sharing the same core commands; isolate
  WSL-specific relay setup at the edge.
- Expand packet support only after the run/stop/status loop is reliable enough
  to debug failures repeatedly.

## Stage 3: Repeatable Linux/WSL 2 Service Operation

Goal: turn the working foreground prototype into an operator-safe run loop.

Current delivery slice:

- `system-start` writes a runtime state file after TUN routes are installed.
- `system-status` reports whether the runtime state is missing, running, stale,
  or invalid.
- `system-stop` reads that state, stops the recorded process when possible, and
  removes the recorded routes/interface.
- `system-down --apply` also removes the default state file after cleanup.
- `tools/pdalink.sh system stop` exposes the stop path.
- `tools/system_connect_test.sh` can use an explicit `--proxy-url` for WSL 2
  relay tests, waits on the state file/route rather than buffered logs alone,
  and checks both no-proxy TCP curl and UDP/53 DNS.
- `tools/pdalink.sh system service start/status/stop/logs` provides a
  repeatable local service wrapper with pid, state, and log files under
  `.tools/runtime`.
- `tools/install_linux_privileges.sh` installs a root-owned helper plus a
  narrow sudoers rule so native Linux runtime starts use `sudo -n` without
  interactive password prompts.
- `tools/pdalink.sh watch run/once/status` provides the native Linux watcher:
  it watches ADB authorization, starts the Android debug proxy, creates the USB
  forward, starts the system service, and cleans up when the device disappears
  or loses debugging authorization.
- `tools/pdalink.sh install` provides the fresh-clone installer: prerequisite
  checks, repo-local Android tool/JDK bootstrap, APK build, privilege helper
  install, and USB-state-aware Android app install or manual instructions.
- `INSTALL.md` gives both human and LLM-agent install/test instructions for an
  arbitrary native Linux machine.

Acceptance criteria:

- A user can run status before and after `system-start` and see the runtime
  state.
- A user can run the service wrapper, inspect logs, and stop through the same
  recorded state cleanup path.
- After one-time privilege setup, the service wrapper either starts without a
  password prompt or fails fast with a setup error.
- After one-time privilege setup, the watcher can be run as the normal user and
  complete the four native Linux steps: watch, authorize, start/connect, and
  cleanup on disconnect.
- A new clone can run `./tools/pdalink.sh install`; with an authorized USB
  debugging phone it installs the Android app automatically, and without one it
  leaves a concrete manual install command.
- A stale or backgrounded `system-start` can be cleaned with
  `sudo ./tools/pdalink.sh system stop`.
- Cleanup uses the recorded interface/routes instead of guessing from current
  defaults.
- Re-running start after stop does not leave duplicate routes, stale state, or
  an orphan `pdalink0`.
- WSL 2 continues to use a Windows relay only for reaching the Windows-local
  ADB forward.

Next Stage 3 slices:

1. Add a WSL 2 relay helper/status command that prints and optionally starts
   the exact Windows relay command.
2. Add a native Linux checklist for direct ADB forward access without Windows
   relay.
3. Expand service wrapper diagnostics for route setup, adapter startup, proxy
   probe, and cleanup.
4. Decide whether to integrate with `systemd` only after the state-file CLI
   path is stable.

## Product USB Transport Gate

ADB remains the development and recovery transport. The ADB-free product path
is not release-ready until `product-usb check` reports ready with Android USB
debugging disabled, then a no-proxy TCP curl and UDP/53 DNS smoke succeeds
through the guarded system route path.

Current product-transport scaffolding:

- `product-usb status` reports consent, trusted-host, transport, proxy, and USB
  debugging readiness.
- `product-usb check` prints the no-ADB release gate and the manual route smoke
  commands.
- `product-usb aoa-start --dry-run` prints the Android Open Accessory
  descriptors and control-transfer sequence; non-dry-run requires explicit
  `--vid/--pid` and PyUSB.
- `system-start --require-product-usb` and `system-up --apply
  --require-product-usb` refuse route mutation until product USB readiness is
  proven.

## Stage 4: TCP/UDP Coverage Expansion

Goal: expand beyond the currently verified packet set without destabilizing the
delivery path.

Current verified coverage:

- IPv4 TCP through HTTP `CONNECT`.
- UDP/53 DNS by converting DNS over UDP to DNS over TCP through the proxy.
- Established TCP idle cleanup is disabled by default; operators can pass a
  configurable timeout explicitly for diagnostic or resource-bounded runs.
- DNS proxy failures return a UDP DNS `SERVFAIL` instead of silently dropping
  the query.

Known limits:

- General UDP is not implemented.
- ICMP/ping is not implemented.
- IPv6 is not implemented.
- TCP handling is intentionally minimal and should be hardened for long-lived
  and lossy flows.
- QUIC/HTTP3 depends on general UDP support and should not be promised yet.

Acceptance criteria for Stage 4:

- TCP regression tests cover retransmission, duplicate ACK, out-of-order input,
  FIN/RST close, idle timeout, and large response streams.
- DNS tests cover concurrent queries, proxy failure, timeout, and truncated or
  malformed responses.
- General UDP is either implemented with a clear transport strategy or
  explicitly blocked with diagnostics.
- IPv6 and ICMP are either supported or documented as out of scope with clear
  user-facing behavior.
- Smoke tests prove ordinary no-proxy Linux applications still work after the
  added packet handling.

Recommended order:

1. TCP retransmission and out-of-order handling.
2. Additional DNS malformed/truncated response hardening.
3. Large TCP stream and long-lived connection soak tests.
4. General UDP design decision.
5. ICMP and IPv6 scope decision.

## PRD #3 Long-Lived TCP Stability

Goal: complete the remaining PRD chain before calling this path product stable.

Current work remaining:

- Issue #9: add and standardize live SSH idle/active/SCP soak checks that use the
  existing no-proxy `pdalink0` path.
- Issue #10: compare product data-path options for long-lived TCP while preserving
  Android HTTP `CONNECT` semantics; keep the current TUN adapter as the short-term
  diagnostic path and prefer kernel TCP or mature netstack semantics for product
  long-lived TCP.
- Issue #11: gate release on the soak evidence schema and a no-regression
  acceptance check.

Acceptance for PRD #3:

- `docs/MANUAL_TEST.md` contains one command for each case (idle/active/scp),
  evidence schema, and pass/fail interpretation.
- `docs/adr/0003-long-lived-tcp-data-path-options.md` captures the comparison and
  preferred option for the next slice.
- Long-lived TCP evidence is collected before marking release ready and is
  reviewed alongside automated test output.

## Release Gate

Do not call this "Linux USB internet ready" until all of these are true:

- `python3 -m unittest discover -s tests` passes.
- `bash -n tools/*.sh` passes.
- `system-status`, `system-start`, `system-stop`, and `system-down --apply`
  are exercised on WSL 2.
- A no-proxy `curl -4` succeeds through `pdalink0`.
- UDP/53 DNS succeeds through `pdalink0`.
- Cleanup leaves no `pdalink0`, no split default routes, and no stale state.
- Live long-lived TCP evidence exists for PRD #3:
  - idle SSH soak
  - active SSH soak
  - SCP soak
- Long-lived evidence includes the required JSON block fields and zero disconnects.
