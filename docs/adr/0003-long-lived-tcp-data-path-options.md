# ADR-0003: Product Data Path Options for PRD #3

Status: Accepted for PRD #3 spike

Date: 2026-06-09

## Context

Issue #10 requires a focused comparison of long-lived TCP options before release
gating. Android-side proxy behavior must stay HTTP `CONNECT`-only in this slice,
and Linux should continue to deliver IPv4 TCP and UDP/53 through the existing
Android path and transport guardrails.

The current built-in TUN adapter is useful for diagnosis and short-term ADB
development validation, but it should not become a bespoke production TCP stack.
Long-lived SSH-class TCP needs either Linux kernel TCP semantics on the app side
or a mature userspace TCP/netstack library.

## Decision Drivers

- Preserve Android HTTP `CONNECT` semantics end-to-end.
- Minimize new dependencies and privilege changes for the short-term mitigation.
- Prefer kernel TCP or mature netstack behavior for product long-lived TCP.
- Keep the release proof tied to explicit logs and observable behavior.
- Keep recovery/debug behavior stable for the existing ADB/product USB gate.

## Options

1. Current built-in Linux TUN adapter
   - Uses `pdalink_lite` built-in packet path to convert TCP streams into HTTP
     `CONNECT` requests to the existing Android proxy endpoint.
   - Preserves Android semantics directly because the proxy endpoint contract is
     unchanged.
   - Already integrated with `system-start` cleanup/state/route lifecycle.
   - Still owns TCP flow state in Python, so retransmission, out-of-order,
     receive window, and backpressure behavior remain maintenance risks.

2. Transparent local TCP proxy shim
   - Intercepts outbound TCP via iptables redirects to a local daemon.
   - Lets the Linux kernel own the application-side TCP session while the local
     daemon opens Android HTTP `CONNECT` tunnels for the original destination.
   - Adds policy and NAT-style complexity, but keeps long-lived TCP semantics out
     of the hand-rolled TUN adapter.
   - Preserves Android `CONNECT` semantics if the shim explicitly emits
     `CONNECT` and handles tunnel lifecycle, authentication, and failure policy.

3. tun2socks / Android-netstack style proxy
   - Strong runtime libraries and broader ecosystem support.
   - Typical defaults are SOCKS-oriented; preserving Android `CONNECT` semantics
     needs an explicit HTTP proxy boundary or translation shim.
   - Reduces bespoke TCP implementation risk, but adds packaging and dependency
     evaluation before it can be called product-ready.

## Decision

Keep option 1 as the active short-term PRD #3 validation path, but do not promote
it as the product long-lived TCP core.

- The built-in adapter is already integrated and is the right place for #5-#8
  observability, timeout, anomaly counter, and large-stream tests.
- It does not require reworking Android app proxy contracts.
- It keeps recovery operations tied to existing `system-start`/`system-stop` command
  semantics.
- The preferred product direction is option 2, a transparent local TCP proxy,
  because Linux keeps application-side TCP behavior while Android remains an HTTP
  `CONNECT` proxy.
- Option 3 remains a candidate if a mature implementation can preserve the
  Android `CONNECT` boundary with acceptable packaging cost.

## Consequence

- The long-lived TCP verification work should validate the current adapter under
  active, idle, and SCP load so current users get evidence-backed mitigation.
- Product readiness still requires a follow-up implementation decision for the
  transparent TCP proxy or mature netstack path.
- Android proxy semantics stay fixed: host-side options must continue to emit
  HTTP `CONNECT` to the Android proxy rather than changing the Android contract.
