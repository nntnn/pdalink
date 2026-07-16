package dev.pdalink.prototype;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;

public class DebugControlActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        forwardAction(getIntent());
        finish();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        forwardAction(intent);
        finish();
    }

    private void forwardAction(Intent source) {
        Intent serviceIntent = new Intent(this, ProxyService.class);
        serviceIntent.setAction(normalizeAction(source == null ? null : source.getAction()));
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(serviceIntent);
        } else {
            startService(serviceIntent);
        }
    }

    private String normalizeAction(String action) {
        if (ProxyActions.ACTION_STOP.equals(action) || ProxyActions.ACTION_REFRESH.equals(action)) {
            return action;
        }
        if (ProxyActions.ACTION_START.equals(action)
                || ProxyActions.ACTION_START_USB.equals(action)
                || ProxyActions.ACTION_STOP_USB.equals(action)
                || ProxyActions.ACTION_START_PRODUCT_USB.equals(action)
                || ProxyActions.ACTION_START_WIFI_DIRECT.equals(action)
                || ProxyActions.ACTION_STOP_WIFI_DIRECT.equals(action)
                || ProxyActions.ACTION_START_LOCAL_HOTSPOT.equals(action)
                || ProxyActions.ACTION_STOP_LOCAL_HOTSPOT.equals(action)
                || ProxyActions.ACTION_STOP_WIFI.equals(action)) {
            return action;
        }
        return ProxyActions.ACTION_START_USB;
    }
}
