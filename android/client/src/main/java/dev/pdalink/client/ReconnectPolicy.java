package dev.pdalink.client;

import dev.pdalink.shared.PdaLinkConnectionUri;
import java.util.Locale;

final class ReconnectPolicy {
    static final int BSSID_CHANGE_CHECKS = 3;
    // Pinned requests are auto-approved (no dialog to race). The platform does not retry a
    // pinned connect that failed while the group was down, so expire it quickly and re-request.
    static final int PINNED_TIMEOUT_MS = 15_000;
    private int changedBssidChecks;

    static String normalizeBssid(String value) {
        if (value == null) return null;
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        if (!normalized.matches("[0-9a-f]{2}(:[0-9a-f]{2}){5}")) return null;
        int firstOctet = Integer.parseInt(normalized.substring(0, 2), 16);
        if ((firstOctet & 1) != 0 || normalized.equals("00:00:00:00:00:00")
                || normalized.equals("02:00:00:00:00:00")) return null;
        return normalized;
    }

    static boolean sameWifiCredentials(PdaLinkConnectionUri.Bundle first,
                                       PdaLinkConnectionUri.Bundle second) {
        return first.ssid.equals(second.ssid) && first.security.equals(second.security)
                && first.password.equals(second.password) && first.transport.equals(second.transport);
    }

    String requestBssid(String savedBssid) {
        return changedBssidChecks >= BSSID_CHANGE_CHECKS ? null : normalizeBssid(savedBssid);
    }

    // Only fresh scans of this SSID count. Timeouts and missing/redacted scans
    // are not evidence that a stable Direct group changed its BSSID.
    void onScanCheck(String savedBssid, java.util.List<String> matchingBssids) {
        String pin = normalizeBssid(savedBssid);
        boolean other = false;
        boolean present = false;
        for (String value : matchingBssids) {
            String bssid = normalizeBssid(value);
            if (bssid == null) continue;
            if (bssid.equals(pin)) present = true;
            else other = true;
        }
        changedBssidChecks = pin != null && other && !present
                ? Math.min(BSSID_CHANGE_CHECKS, changedBssidChecks + 1) : 0;
    }

    void onWifiAvailable() {
        changedBssidChecks = 0;
    }

    static int timeoutMillis(String requestBssid) {
        // Zero means use requestNetwork's overload without a timeout: approval can take minutes.
        return normalizeBssid(requestBssid) == null ? 0 : PINNED_TIMEOUT_MS;
    }

    static long pinnedDelayMillis(int attempt) {
        return Math.min(30000L, 2000L << Math.max(0, Math.min(attempt, 4)));
    }

    static long delayMillis(int attempt) {
        return Math.min(60000L, 5000L << Math.max(0, Math.min(attempt, 4)));
    }
}
