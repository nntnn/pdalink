package dev.pdalink.prototype;

import android.content.Context;
import android.content.SharedPreferences;

final class ProductUsbHostTrust {
    private static final String PREFS_NAME = "product_usb_host_trust";
    private static final long PROMPT_COOLDOWN_MS = 30_000L;

    private ProductUsbHostTrust() {
    }

    static boolean isTrusted(Context context, String fingerprint) {
        String normalized = normalizeFingerprint(fingerprint);
        if (normalized.isEmpty()) {
            return false;
        }
        return preferences(context).getBoolean(trustedKey(normalized), false);
    }

    static void trust(Context context, String fingerprint, String hostName) {
        String normalized = normalizeFingerprint(fingerprint);
        if (normalized.isEmpty()) {
            return;
        }
        preferences(context)
                .edit()
                .putBoolean(trustedKey(normalized), true)
                .putString(nameKey(normalized), hostName == null ? "" : hostName)
                .apply();
    }

    static boolean shouldPrompt(Context context, String fingerprint, long nowMillis) {
        String normalized = normalizeFingerprint(fingerprint);
        if (normalized.isEmpty()) {
            return false;
        }
        long lastPromptMillis = preferences(context).getLong(promptKey(normalized), 0L);
        return lastPromptMillis <= 0L || nowMillis - lastPromptMillis >= PROMPT_COOLDOWN_MS;
    }

    static void recordPrompt(Context context, String fingerprint, long nowMillis) {
        String normalized = normalizeFingerprint(fingerprint);
        if (normalized.isEmpty()) {
            return;
        }
        preferences(context)
                .edit()
                .putLong(promptKey(normalized), nowMillis)
                .apply();
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    private static String normalizeFingerprint(String fingerprint) {
        String normalized = fingerprint == null ? "" : fingerprint.trim();
        if ("unknown".equals(normalized)) {
            return "";
        }
        return normalized;
    }

    private static String trustedKey(String fingerprint) {
        return "trusted." + fingerprint;
    }

    private static String nameKey(String fingerprint) {
        return "name." + fingerprint;
    }

    private static String promptKey(String fingerprint) {
        return "prompt." + fingerprint;
    }
}
