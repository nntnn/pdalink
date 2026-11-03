package dev.pdalink.client;

import dev.pdalink.shared.PdaLinkConnectionUri;
import java.util.Locale;

final class ReconnectPolicy {
    static final int PINNED_FAILURE_LIMIT = 3;
    static final int PINNED_TIMEOUT_MS = 60_000;
    private int pinnedFailures;

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
        return pinnedFailures >= PINNED_FAILURE_LIMIT ? null : normalizeBssid(savedBssid);
    }

    void onRequestFailure(boolean pinned) {
        if (pinned) pinnedFailures = Math.min(PINNED_FAILURE_LIMIT, pinnedFailures + 1);
    }

    void onWifiAvailable() {
        pinnedFailures = 0;
    }

    static int timeoutMillis(String requestBssid) {
        // Zero means use requestNetwork's overload without a timeout: approval can take minutes.
        return normalizeBssid(requestBssid) == null ? 0 : PINNED_TIMEOUT_MS;
    }

    static long delayMillis(int attempt) {
        return Math.min(60000L, 5000L << Math.max(0, Math.min(attempt, 4)));
    }
}
