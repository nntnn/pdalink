# Fixed Wi-Fi Direct group and reconnect

## Audit (2026-11-03, before implementation)

- Host `buildWifiP2pConfig` already saves SSID/PSK in private preferences on
  API 29+. Band changes, Stop all, and restart do not erase them. There is no
  explicit rotation action. Credential generation uses asynchronous persistence.
- Host service is sticky and restores enabled transports on process restart,
  but there is no boot/package-update receiver. Missing groups and channel
  disconnection can leave sharing idle. No Android tethering API is used:
  multiple clients use the app's own HTTP sockets, with a separate USB listener.
- Connection QR already supports Direct at 192.168.49.1:8001. Inactive UI selects
  Wi-Fi off; Direct needs recommendation/default selection.
- Android client discards imported profiles at process death, returns non-sticky,
  and shuts down on Wi-Fi loss, request timeout, PAC failure or native exit.
  Its isolated VPN process needs one owner for persistent profile state.
- Linux QR import calls the manual Direct profile helper (autoconnect=no).
  Local-only IPv4 settings exist, but IPv6 ignore can leave external IPv6
  configuration. Import only joins/probes; it does not enable desktop internet.
  GTK `pdalink_client.py` controls the USB supervisor, not QR import.
- Windows QR import defaults to join only. WLAN XML uses manual connection.
  WinINET PAC already persists over restart/reconnect; Wintun has a separate,
  experimental process/route lifecycle and no unattended reboot supervisor.
- Existing design Q9/Q19 and HOST_AUTOMATION_PLAN prohibit background joining;
  this task supersedes that rule for an explicitly imported Direct profile.

## Implementation contract

Only explicit credential rotation changes an existing valid saved Direct pair.
App data clearing/uninstall loses it; API <29 cannot pin it. Stop all disables
reboot recovery until sharing is started again, while retaining credentials.
Boot recovery occurs after unlock with granted Wi-Fi permissions; user force-stop,
permission revocation, disabled Wi-Fi and vendor background restrictions can
prevent it. The only added host permission is RECEIVE_BOOT_COMPLETED (normal,
not dangerous). No new dangerous permission or tethering API is introduced.

Android stores the imported URI privately with backup disabled. The VPN process
owns the enabled flag. A running foreground VPN retries transient outages;
Always-on VPN in Android Settings provides reboot startup. Network approval and
VPN approval remain mandatory. Android may require Wi-Fi approval again after
AP identity/BSSID changes even when SSID/PSK stay fixed. An unconditional
"forever, zero prompts" guarantee is therefore impossible with public APIs.
See [network requests](https://developer.android.com/develop/connectivity/wifi/wifi-bootstrap)
and [Always-on VPN](https://developer.android.com/develop/connectivity/vpn#always-on).
Do not enable lockdown: the proxy process must use the physical Wi-Fi network.

Linux imported Direct profiles opt in to NetworkManager autoconnect with unlimited
retries and retain the PSK in the OS credential store. They never supply default
routes or DNS. GNOME PAC is a persistent optional import action; other desktops
need their own proxy configuration. Reconnect restores new TCP connections;
existing streams interrupted by an outage are not resumable. USB remains separate.

Windows onboarding uses the existing QR script and persistent WinINET PAC,
with an automatic WLAN profile. This provides reboot recovery for proxy-aware
apps without a background PdaLink process. Experimental Wintun remains manual
across client reboot and is not the supported connect-once entry point.

## Verification

All live network/phone/driver/service operations are deferred to manual testing.
Automated checks use dummy credentials and fake command runners only.
