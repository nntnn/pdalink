package dev.pdalink.prototype;

import java.util.Locale;

final class WifiDirectPeerAddress {
    private WifiDirectPeerAddress() {
    }

    static String normalize(String value) {
        if (value == null) {
            return "";
        }
        String normalized = value.trim().toUpperCase(Locale.ROOT);
        return normalized.matches("([0-9A-F]{2}:){5}[0-9A-F]{2}")
                ? normalized
                : "";
    }

    static boolean matches(String expected, String actual) {
        String normalizedExpected = normalize(expected);
        return !normalizedExpected.isEmpty()
                && normalizedExpected.equals(normalize(actual));
    }
}
