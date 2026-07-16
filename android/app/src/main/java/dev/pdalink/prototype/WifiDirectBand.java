package dev.pdalink.prototype;

import android.content.Context;
import android.net.wifi.p2p.WifiP2pConfig;
import android.os.Build;

final class WifiDirectBand {
    static final String AUTO = "auto";
    static final String BAND_2_GHZ = "2ghz";
    static final String BAND_5_GHZ = "5ghz";

    private static final String PREFS_NAME = "pdalink_proxy";
    private static final String PREF_WIFI_DIRECT_BAND = "wifi_direct_band";

    private WifiDirectBand() {
    }

    static boolean supportsSelection() {
        return Build.VERSION.SDK_INT >= 29;
    }

    static String normalize(String value) {
        if (BAND_2_GHZ.equals(value) || BAND_5_GHZ.equals(value)) {
            return value;
        }
        return AUTO;
    }

    static String load(Context context) {
        String saved = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_WIFI_DIRECT_BAND, AUTO);
        String normalized = normalize(saved);
        return supportsSelection() ? normalized : AUTO;
    }

    static String save(Context context, String value) {
        String normalized = supportsSelection() ? normalize(value) : AUTO;
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(PREF_WIFI_DIRECT_BAND, normalized)
                .apply();
        return normalized;
    }

    static int toPlatformBand(String value) {
        switch (normalize(value)) {
            case BAND_2_GHZ:
                return WifiP2pConfig.GROUP_OWNER_BAND_2GHZ;
            case BAND_5_GHZ:
                return WifiP2pConfig.GROUP_OWNER_BAND_5GHZ;
            default:
                return WifiP2pConfig.GROUP_OWNER_BAND_AUTO;
        }
    }

    static boolean matchesFrequency(String value, int frequencyMhz) {
        switch (normalize(value)) {
            case BAND_2_GHZ:
                return frequencyMhz >= 2400 && frequencyMhz < 2500;
            case BAND_5_GHZ:
                return frequencyMhz >= 4900 && frequencyMhz < 5900;
            default:
                return true;
        }
    }

    static String label(String value) {
        switch (normalize(value)) {
            case BAND_2_GHZ:
                return "2.4 GHz";
            case BAND_5_GHZ:
                return "5 GHz";
            default:
                return "Automatic";
        }
    }

    static String frequencyLabel(int frequencyMhz) {
        if (frequencyMhz >= 2400 && frequencyMhz < 2500) {
            return "2.4 GHz (" + frequencyMhz + " MHz)";
        }
        if (frequencyMhz >= 4900 && frequencyMhz < 5900) {
            return "5 GHz (" + frequencyMhz + " MHz)";
        }
        return frequencyMhz > 0 ? frequencyMhz + " MHz" : "not active";
    }
}
