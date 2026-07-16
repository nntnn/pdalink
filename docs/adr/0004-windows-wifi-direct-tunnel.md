# ADR-0004: Windows Wi-Fi Direct Tunnel Uses Wintun For The Prototype

## Status

Accepted for experimental implementation; Windows 10/11 live verification is
pending. This is not a product full-tunnel approval.

## Context

The Android Wi-Fi Direct endpoint is an HTTP/HTTPS `CONNECT` proxy at
`192.168.49.1:8001`, not a NAT gateway. A Windows user can use WinINET PAC for
proxy-aware applications, but applications that open raw TCP sockets need an
OS packet interception boundary.

The repository already has a Linux packet engine that accepts layer-3 IPv4
packets, maps TCP flows to Android HTTP `CONNECT`, and maps UDP/53 DNS to TCP
DNS. ADR-0003 limits this hand-built TCP state machine to short-term diagnostic
use because it does not provide mature kernel TCP loss, window, retransmission,
and congestion behavior.

Windows Filtering Platform ALE connect redirection would preserve Windows
kernel TCP semantics, but its redirect path requires a callout driver, service,
proxy component, Windows Driver Kit build, signing, installation, and native
validation. That is not a safe first implementation from the current Linux
development environment.

## Decision

Use the official signed Wintun 0.14.1 DLL for the experimental Windows stage:

- download only `https://www.wintun.net/builds/wintun-0.14.1.zip`;
- require archive SHA-256
  `07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51`;
- install only the host-architecture DLL and included license under an
  Administrators/SYSTEM-only ProgramData directory, set its owner, and reject
  untrusted owners or reparse points in the path;
- record the DLL hash and WireGuard signer thumbprint in a manifest, then
  recheck both hash and Authenticode signature before absolute-path loading;
- require the selected Python process architecture to match the native DLL;
- use `WintunCreateAdapter`, a 4 MiB session, receive/release and
  allocate/send packet pairs, then `WintunEndSession` and
  `WintunCloseAdapter`; never delete the global driver during normal stop.

The elevated controller must verify the Direct route and exact PAC before
mutation, refuse foreign split routes, persist recovery state before route
changes, add `192.168.49.1/32` on the verified Wi-Fi interface before the two
Wintun `/1` routes, and configure DNS only on the owned Wintun interface.
Cleanup removes exact owned entries in reverse and retains state on partial
failure. Route deletion first confirms the saved Wintun name/index identity so
an interface index reused after a crash cannot authorize deletion.

## Consequences

The user does not manually configure Windows proxy, routes, DNS, or an adapter.
Stage 1 automates PAC; Stage 2 transparently carries IPv4 TCP plus UDP/53 DNS.

The Stage 2 implementation still inherits the Linux diagnostic TCP engine's
limits. It does not carry IPv6, ICMP, arbitrary UDP, or QUIC. IPv6 may use an
existing physical default route, so documentation must not claim leak-proof
full tunnelling. Windows driver installation, sleep/resume, crash recovery,
route/DNS behavior, and exact stop restoration remain native release gates.

The product-direction follow-up is a WFP ALE connect redirector or a mature
netstack/tun2socks-style implementation that preserves the Android HTTP
`CONNECT` boundary while replacing the hand-built TCP state machine.

## References

- [Official Wintun distribution](https://www.wintun.net/)
- [Wintun API header](https://git.zx2c4.com/wintun/plain/api/wintun.h)
- [Microsoft WFP bind/connect redirection](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/using-bind-or-connect-redirection)
