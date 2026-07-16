package dev.pdalink.prototype;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.app.PendingIntent;
import android.hardware.usb.UsbAccessory;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Bundle;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int PERMISSION_REQUEST = 100;
    private static final int MAX_LOG_CHARS = 16 * 1024;
    private static final String ACTION_PRODUCT_USB_PERMISSION = "dev.pdalink.prototype.PRODUCT_USB_PERMISSION";

    private TextView summaryView;
    private TextView logView;
    private ScrollView logScroller;
    private RadioGroup wifiBandGroup;
    private String selectedWifiBand = WifiDirectBand.AUTO;
    private String startModeAfterPermission;
    private UsbManager usbManager;
    private UsbAccessory pendingProductAccessory;
    private String pendingTrustFingerprint = "";

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String line = intent.getStringExtra(ProxyActions.EXTRA_STATUS);
            updateSnapshot(intent);
            if (line != null) {
                appendStatus(line);
                maybePromptProductUsbHostTrust(line);
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        usbManager = (UsbManager) getSystemService(Context.USB_SERVICE);
        selectedWifiBand = WifiDirectBand.load(this);
        buildUi();
        appendStatus("Ready. USB and Wi-Fi Direct can run together.");
        handleProductUsbIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleProductUsbIntent(intent);
    }

    @Override
    protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(ProxyActions.ACTION_STATUS);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, filter);
        }
        IntentFilter usbFilter = new IntentFilter(ACTION_PRODUCT_USB_PERMISSION);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(productUsbPermissionReceiver, usbFilter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(productUsbPermissionReceiver, usbFilter);
        }
    }

    @Override
    protected void onStop() {
        unregisterReceiver(productUsbPermissionReceiver);
        unregisterReceiver(statusReceiver);
        super.onStop();
    }

    private final BroadcastReceiver productUsbPermissionReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!ACTION_PRODUCT_USB_PERMISSION.equals(intent.getAction())) {
                return;
            }
            UsbAccessory accessory = intent.getParcelableExtra(UsbManager.EXTRA_ACCESSORY);
            boolean granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false);
            if (!granted) {
                appendStatus("Product USB permission denied.");
                pendingProductAccessory = null;
                return;
            }
            startProductUsbWithAccessory(accessory == null ? pendingProductAccessory : accessory);
        }
    };

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(16);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("PdaLink Prototype");
        title.setTextSize(22);
        root.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView hint = new TextView(this);
        hint.setText("USB stays on 127.0.0.1:8000; Wi-Fi Direct adds 192.168.49.1:8001.");
        hint.setTextSize(14);
        root.addView(hint);

        summaryView = new TextView(this);
        summaryView.setText("Mode: " + ProxyActions.MODE_USB
                + "\nUSB proxy: not started\nWi-Fi Direct proxy: not started"
                + "\nInternet usage: down 0 B / up 0 B\nEgress: unknown"
                + "\nWi-Fi Direct: not active"
                + "\nWi-Fi Direct band: requested " + WifiDirectBand.label(selectedWifiBand)
                + " / actual not active"
                + "\nUSB target: not ready\nWi-Fi Direct target: not ready");
        summaryView.setTextSize(14);
        summaryView.setTextIsSelectable(true);
        summaryView.setPadding(0, dp(12), 0, 0);
        root.addView(summaryView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView wifiBandTitle = new TextView(this);
        wifiBandTitle.setText("Wi-Fi Direct band");
        wifiBandTitle.setTextSize(14);
        wifiBandTitle.setPadding(0, dp(12), 0, 0);
        root.addView(wifiBandTitle);

        wifiBandGroup = new RadioGroup(this);
        wifiBandGroup.setOrientation(RadioGroup.HORIZONTAL);
        addWifiBandOption("Automatic", WifiDirectBand.AUTO, true);
        addWifiBandOption("2.4 GHz", WifiDirectBand.BAND_2_GHZ, WifiDirectBand.supportsSelection());
        addWifiBandOption("5 GHz", WifiDirectBand.BAND_5_GHZ, WifiDirectBand.supportsSelection());
        wifiBandGroup.setOnCheckedChangeListener((group, checkedId) -> {
            RadioButton selected = findViewById(checkedId);
            if (selected == null || !(selected.getTag() instanceof String)) {
                return;
            }
            selectedWifiBand = WifiDirectBand.save(this, (String) selected.getTag());
            appendStatus("Wi-Fi Direct band selected: " + WifiDirectBand.label(selectedWifiBand)
                    + ". Tap Add Wi-Fi Direct to apply.");
        });
        root.addView(wifiBandGroup, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        if (!WifiDirectBand.supportsSelection()) {
            TextView legacyBandHint = new TextView(this);
            legacyBandHint.setText("Android 9 or earlier supports Automatic band selection only.");
            legacyBandHint.setTextSize(12);
            root.addView(legacyBandHint);
        }

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.setPadding(0, dp(12), 0, dp(12));

        Button startUsb = new Button(this);
        startUsb.setText("Start USB");
        startUsb.setOnClickListener(v -> startUsb());
        buttons.addView(startUsb, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button startProductUsb = new Button(this);
        startProductUsb.setText("Start Product USB");
        startProductUsb.setOnClickListener(v -> startProductUsb());
        buttons.addView(startProductUsb, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button startWifi = new Button(this);
        startWifi.setText("Add Wi-Fi Direct");
        startWifi.setOnClickListener(v -> startWifiDirect());
        buttons.addView(startWifi, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        root.addView(buttons);

        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.HORIZONTAL);
        controls.setPadding(0, 0, 0, dp(12));

        Button stopWifi = new Button(this);
        stopWifi.setText("Stop Wi-Fi Direct");
        stopWifi.setOnClickListener(v -> sendServiceAction(ProxyActions.ACTION_STOP_WIFI_DIRECT));
        controls.addView(stopWifi, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button refresh = new Button(this);
        refresh.setText("Refresh");
        refresh.setOnClickListener(v -> sendServiceAction(ProxyActions.ACTION_REFRESH));
        controls.addView(refresh, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button stop = new Button(this);
        stop.setText("Stop All");
        stop.setOnClickListener(v -> sendServiceAction(ProxyActions.ACTION_STOP));
        controls.addView(stop, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        root.addView(controls);

        logView = new TextView(this);
        logView.setTextSize(13);
        logView.setTextIsSelectable(true);

        logScroller = new ScrollView(this);
        logScroller.addView(logView);
        root.addView(logScroller, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1));

        setContentView(root);
    }

    private void addWifiBandOption(String label, String value, boolean enabled) {
        RadioButton option = new RadioButton(this);
        option.setId(android.view.View.generateViewId());
        option.setText(label);
        option.setTag(value);
        option.setEnabled(enabled);
        option.setChecked(value.equals(selectedWifiBand));
        wifiBandGroup.addView(option, new RadioGroup.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1));
    }

    private void startUsb() {
        if (requestRuntimePermissions(ProxyActions.MODE_USB)) {
            startModeAfterPermission = ProxyActions.MODE_USB;
            appendStatus("Waiting for required permissions.");
            return;
        }
        sendServiceAction(ProxyActions.ACTION_START_USB);
    }

    private void startProductUsb() {
        UsbAccessory accessory = firstProductUsbAccessory();
        if (accessory == null) {
            appendStatus("Product USB accessory not connected.");
            return;
        }
        if (usbManager == null) {
            appendStatus("Product USB manager unavailable.");
            return;
        }
        if (!usbManager.hasPermission(accessory)) {
            pendingProductAccessory = accessory;
            int permissionFlags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
            PendingIntent permissionIntent = PendingIntent.getBroadcast(
                    this,
                    0,
                    new Intent(ACTION_PRODUCT_USB_PERMISSION).setPackage(getPackageName()),
                    permissionFlags);
            usbManager.requestPermission(accessory, permissionIntent);
            appendStatus("Waiting for Product USB permission.");
            return;
        }
        startProductUsbWithAccessory(accessory);
    }

    private void startProductUsbWithAccessory(UsbAccessory accessory) {
        if (accessory == null) {
            appendStatus("Product USB accessory missing after permission.");
            return;
        }
        pendingProductAccessory = null;
        appendStatus("Product USB permission granted for " + accessoryName(accessory) + ".");
        sendProductUsbServiceAction(accessory);
    }

    private void maybePromptProductUsbHostTrust(String line) {
        String prefix = "Product USB host approval required: ";
        if (!line.startsWith(prefix)) {
            return;
        }
        String raw = line.substring(prefix.length()).trim();
        int hostIndex = raw.indexOf(" host ");
        String fingerprint = hostIndex >= 0 ? raw.substring(0, hostIndex).trim() : raw;
        String hostName = hostIndex >= 0 ? raw.substring(hostIndex + " host ".length()).trim() : "unknown";
        if (fingerprint.isEmpty() || fingerprint.equals(pendingTrustFingerprint)) {
            return;
        }
        long nowMillis = System.currentTimeMillis();
        if (!ProductUsbHostTrust.shouldPrompt(this, fingerprint, nowMillis)) {
            return;
        }
        ProductUsbHostTrust.recordPrompt(this, fingerprint, nowMillis);
        pendingTrustFingerprint = fingerprint;
        new AlertDialog.Builder(this)
                .setTitle("Trust Product USB host?")
                .setMessage("Host: " + hostName + "\nFingerprint: " + fingerprint)
                .setPositiveButton("Trust", (dialog, which) -> {
                    ProductUsbHostTrust.trust(this, fingerprint, hostName);
                    appendStatus("Product USB host trusted locally: " + fingerprint + ".");
                    pendingTrustFingerprint = "";
                    sendServiceAction(ProxyActions.ACTION_START_PRODUCT_USB);
                })
                .setNegativeButton("Cancel", (dialog, which) -> {
                    appendStatus("Product USB host not trusted: " + fingerprint + ".");
                    pendingTrustFingerprint = "";
                })
                .setOnCancelListener(dialog -> pendingTrustFingerprint = "")
                .show();
    }

    private UsbAccessory firstProductUsbAccessory() {
        if (usbManager == null) {
            return null;
        }
        UsbAccessory[] accessories = usbManager.getAccessoryList();
        if (accessories == null || accessories.length == 0) {
            return null;
        }
        return accessories[0];
    }

    private void handleProductUsbIntent(Intent intent) {
        if (intent == null || !UsbManager.ACTION_USB_ACCESSORY_ATTACHED.equals(intent.getAction())) {
            return;
        }
        UsbAccessory accessory = intent.getParcelableExtra(UsbManager.EXTRA_ACCESSORY);
        if (accessory == null) {
            appendStatus("Product USB accessory attach intent had no accessory.");
            return;
        }
        appendStatus("Product USB accessory attached: " + accessoryName(accessory));
        startProductUsb();
    }

    private String accessoryName(UsbAccessory accessory) {
        String model = accessory.getModel();
        String manufacturer = accessory.getManufacturer();
        if (manufacturer == null || manufacturer.isEmpty()) {
            manufacturer = "unknown";
        }
        if (model == null || model.isEmpty()) {
            model = "unknown";
        }
        return manufacturer + " " + model;
    }

    private void startWifiDirect() {
        if (requestRuntimePermissions(ProxyActions.MODE_WIFI_DIRECT)) {
            startModeAfterPermission = ProxyActions.MODE_WIFI_DIRECT;
            appendStatus("Waiting for Wi-Fi Direct permissions.");
            return;
        }
        sendWifiDirectAction();
    }

    private void sendWifiDirectAction() {
        Intent intent = new Intent(this, ProxyService.class);
        intent.setAction(ProxyActions.ACTION_START_WIFI_DIRECT);
        intent.putExtra(ProxyActions.EXTRA_WIFI_BAND, selectedWifiBand);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }

    private void sendServiceAction(String action) {
        Intent intent = new Intent(this, ProxyService.class);
        intent.setAction(action);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }

    private void sendProductUsbServiceAction(UsbAccessory accessory) {
        Intent intent = new Intent(this, ProxyService.class);
        intent.setAction(ProxyActions.ACTION_START_PRODUCT_USB);
        intent.putExtra(UsbManager.EXTRA_ACCESSORY, accessory);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }

    private boolean requestRuntimePermissions(String mode) {
        List<String> missing = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33) {
            addIfMissing(missing, Manifest.permission.POST_NOTIFICATIONS);
            if (ProxyActions.MODE_WIFI_DIRECT.equals(mode)) {
                addIfMissing(missing, Manifest.permission.NEARBY_WIFI_DEVICES);
            }
        } else if (ProxyActions.MODE_WIFI_DIRECT.equals(mode)) {
            addIfMissing(missing, Manifest.permission.ACCESS_FINE_LOCATION);
        }
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), PERMISSION_REQUEST);
            return true;
        }
        return false;
    }

    private void addIfMissing(List<String> missing, String permission) {
        if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            missing.add(permission);
        }
    }

    private void appendStatus(String line) {
        String timestamp = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
        String entry = timestamp + "  " + line + "\n";
        String existing = logView.getText().toString();
        String combined = entry + existing;
        if (combined.length() > MAX_LOG_CHARS) {
            combined = combined.substring(0, MAX_LOG_CHARS);
        }
        logView.setText(combined);
        logScroller.post(() -> logScroller.fullScroll(ScrollView.FOCUS_UP));
    }

    private void updateSnapshot(Intent intent) {
        String mode = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_MODE), ProxyActions.MODE_USB);
        String proxy = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_PROXY_STATUS), "not running");
        String usbProxy = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_USB_PROXY_STATUS), proxy);
        String wifiProxy = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_WIFI_PROXY_STATUS), "not running");
        String egress = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_EGRESS), "unknown");
        String ssid = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_GROUP_SSID), "not active");
        String passphrase = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_GROUP_PASSPHRASE), "");
        String iface = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_GROUP_INTERFACE), "");
        String requestedBand = WifiDirectBand.normalize(
                intent.getStringExtra(ProxyActions.EXTRA_WIFI_BAND));
        int frequencyMhz = intent.getIntExtra(ProxyActions.EXTRA_WIFI_FREQUENCY_MHZ, 0);
        String target = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_LINUX_TARGET), "not ready");
        String usbTarget = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_USB_TARGET), target);
        String wifiTarget = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_WIFI_TARGET), "not ready");
        String request = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_LAST_REQUEST), "none");
        String error = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_LAST_ERROR), "none");
        String productUsb = valueOrDefault(intent.getStringExtra(ProxyActions.EXTRA_PRODUCT_USB_STATUS), "permission missing");
        int clients = intent.getIntExtra(ProxyActions.EXTRA_CLIENT_COUNT, 0);
        long upBytes = intent.getLongExtra(ProxyActions.EXTRA_TRAFFIC_UP_BYTES, 0L);
        long downBytes = intent.getLongExtra(ProxyActions.EXTRA_TRAFFIC_DOWN_BYTES, 0L);

        String wifi = "not active";
        if (mode.contains(ProxyActions.MODE_WIFI_DIRECT)) {
            wifi = ssid;
            if (!passphrase.isEmpty()) {
                wifi += " / password " + passphrase;
            }
            if (!iface.isEmpty()) {
                wifi += " / " + iface;
            }
        }

        summaryView.setText("Mode: " + mode
                + "\nUSB proxy: " + usbProxy
                + "\nWi-Fi Direct proxy: " + wifiProxy
                + "\nActive proxy clients: " + clients
                + "\nInternet usage: down " + formatBytes(downBytes) + " / up " + formatBytes(upBytes)
                + "\nEgress: " + egress
                + "\nProduct USB: " + productUsb
                + "\nWi-Fi Direct: " + wifi
                + "\nWi-Fi Direct band: requested " + WifiDirectBand.label(requestedBand)
                + " / actual " + WifiDirectBand.frequencyLabel(frequencyMhz)
                + "\nUSB target: " + usbTarget
                + "\nWi-Fi Direct target: " + wifiTarget
                + "\nLast request: " + request
                + "\nLast error: " + error);
    }

    private String valueOrDefault(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    private String formatBytes(long bytes) {
        if (bytes < 1024L) {
            return bytes + " B";
        }
        double value = bytes;
        String[] units = {"B", "KB", "MB", "GB", "TB"};
        int unit = 0;
        while (value >= 1024.0 && unit < units.length - 1) {
            value /= 1024.0;
            unit++;
        }
        return String.format(Locale.US, "%.1f %s", value, units[unit]);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != PERMISSION_REQUEST) {
            return;
        }
        boolean denied = false;
        for (int result : grantResults) {
            if (result != PackageManager.PERMISSION_GRANTED) {
                denied = true;
                break;
            }
        }
        if (denied) {
            appendStatus("Permission denied. Required permissions are needed to start the selected mode.");
            startModeAfterPermission = null;
            return;
        }
        appendStatus("Permissions granted.");
        if (startModeAfterPermission == null) {
            return;
        }
        String mode = startModeAfterPermission;
        startModeAfterPermission = null;
        if (ProxyActions.MODE_WIFI_DIRECT.equals(mode)) {
            sendWifiDirectAction();
        } else {
            sendServiceAction(ProxyActions.ACTION_START_USB);
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
