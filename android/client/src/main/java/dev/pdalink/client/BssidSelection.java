package dev.pdalink.client;

import java.util.List;
import java.util.Locale;

final class BssidSelection {
    static final class Candidate {
        final String ssid, bssid, capabilities;
        final int level;
        Candidate(String ssid, String bssid, String capabilities, int level) {
            this.ssid = ssid; this.bssid = bssid;
            this.capabilities = capabilities; this.level = level;
        }
    }

    private static String unquote(String ssid) {
        return ssid != null && ssid.length() >= 2 && ssid.startsWith("\"") && ssid.endsWith("\"")
                ? ssid.substring(1, ssid.length() - 1) : ssid;
    }

    static String select(String ssid, String security, List<Candidate> scans) {
        String best = null;
        int strongest = Integer.MIN_VALUE;
        for (Candidate scan : scans) {
            String pin = ReconnectPolicy.normalizeBssid(scan.bssid);
            if (pin == null || !unquote(ssid).equals(unquote(scan.ssid))) continue;
            String caps = scan.capabilities == null ? "" : scan.capabilities.toUpperCase(Locale.ROOT);
            boolean psk = caps.contains("PSK") && (caps.contains("WPA2") || caps.contains("RSN"));
            boolean sae = caps.contains("SAE");
            boolean fits = "wpa2-psk".equals(security) ? psk
                    : "wpa3-sae".equals(security) ? sae
                    : "wpa2-wpa3".equals(security) && (psk || sae);
            if (fits && scan.level > strongest) { best = pin; strongest = scan.level; }
        }
        return best;
    }
}
