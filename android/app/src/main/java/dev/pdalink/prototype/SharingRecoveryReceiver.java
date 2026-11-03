package dev.pdalink.prototype;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** Restores only an explicitly enabled Direct transport after unlock/update. */
public final class SharingRecoveryReceiver extends BroadcastReceiver {
    static boolean shouldRestore(String action, boolean enabled) {
        return enabled && (Intent.ACTION_BOOT_COMPLETED.equals(action)
                || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action));
    }

    @Override public void onReceive(Context context, Intent intent) {
        if (!shouldRestore(intent.getAction(), context.getSharedPreferences(
                "pdalink_proxy", Context.MODE_PRIVATE).getBoolean("wifi_direct_enabled", false))) {
            return;
        }
        Intent service = new Intent(context, ProxyService.class);
        // Null/default action restores saved modes without changing credentials or band.
        try {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(service);
            else context.startService(service);
        } catch (IllegalStateException | SecurityException ignored) {
            // User can reopen the app if platform policy or revoked permissions blocks startup.
        }
    }
}
