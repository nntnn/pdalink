package dev.pdalink.prototype;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.net.wifi.p2p.WifiP2pConfig;
import android.net.wifi.p2p.WifiP2pGroup;
import android.net.wifi.p2p.WifiP2pManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.util.Log;

import java.security.SecureRandom;
import java.util.Locale;

public class ProxyService extends Service {
    private static final String TAG = "PdaLinkService";
    private static final String CHANNEL_ID = "pdalink_proxy";
    private static final int NOTIFICATION_ID = 77;
    private static final String PREFS_NAME = "pdalink_proxy";
    private static final String PREF_MODE = "mode";
    private static final String PREF_WIFI_DIRECT_ENABLED = "wifi_direct_enabled";
    private static final String PREF_WIFI_DIRECT_SSID = "wifi_direct_ssid";
    private static final String PREF_WIFI_DIRECT_PASSPHRASE = "wifi_direct_passphrase";
    private static final String WIFI_HOST = "192.168.49.1";
    private static final String WIFI_DIRECT_CLIENT_PREFIX = "192.168.49.";
    private static final String USB_HOST = "127.0.0.1";
    private static final int[] USB_PROXY_CANDIDATE_PORTS = new int[]{8000};
    private static final int[] WIFI_PROXY_CANDIDATE_PORTS = new int[]{8001};

    private enum Mode {USB, PRODUCT_USB, WIFI_DIRECT}

    private volatile HttpProxyServer usbProxyServer;
    private volatile HttpProxyServer wifiProxyServer;
    private NetworkSelector networkSelector;
    private WifiP2pManager wifiP2pManager;
    private WifiP2pManager.Channel wifiP2pChannel;
    private BroadcastReceiver p2pReceiver;
    private Handler mainHandler;
    private ProductUsbBridge productUsbBridge;
    private Mode currentMode = Mode.USB;
    private boolean wifiDirectEnabled;
    private String groupSsid = "";
    private String groupPassphrase = "";
    private String groupInterface = "";
    private String requestedWifiBand = WifiDirectBand.AUTO;
    private int groupFrequencyMhz;
    private boolean wifiGroupOperationInProgress;
    private String lastRequest = "";
    private String lastError = "";
    private String productUsbStatus = "permission missing";

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        startForegroundCompat("PdaLink starting");
        mainHandler = new Handler(getMainLooper());
        requestedWifiBand = WifiDirectBand.load(this);
        Mode savedMode = loadSavedMode();
        if (savedMode == Mode.WIFI_DIRECT) {
            currentMode = Mode.USB;
            setWifiDirectEnabled(true);
            saveMode(Mode.USB);
        } else {
            currentMode = savedMode;
            wifiDirectEnabled = loadWifiDirectEnabled();
        }
        networkSelector = new NetworkSelector(this, this::status);
        networkSelector.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ProxyActions.ACTION_START_WIFI_DIRECT.equals(action)
                && intent.hasExtra(ProxyActions.EXTRA_WIFI_BAND)) {
            requestedWifiBand = WifiDirectBand.save(
                    this,
                    intent.getStringExtra(ProxyActions.EXTRA_WIFI_BAND));
        }
        if (ProxyActions.ACTION_STOP.equals(action)) {
            setWifiDirectEnabled(false);
            status("Stopping service");
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ProxyActions.ACTION_STOP_WIFI_DIRECT.equals(action)) {
            ensureUsbProxyServer();
            stopWifiDirect();
            status("Wi-Fi Direct stopped; USB proxy remains active");
            updateNotification();
            return START_STICKY;
        }

        Mode requestedMode = parseMode(action);
        if (ProxyActions.ACTION_REFRESH.equals(action)) {
            ensureUsbProxyServer();
            if (wifiDirectEnabled) {
                setupWifiP2p();
                requestGroupInfo();
            } else {
                status("Wi-Fi Direct not active");
            }
            status("Proxies: " + proxySummary());
            updateNotification();
            return START_STICKY;
        }
        if (requestedMode == null) {
            requestedMode = currentMode == null ? loadSavedMode() : currentMode;
        }

        if (requestedMode == Mode.WIFI_DIRECT) {
            setWifiDirectEnabled(true);
        } else {
            setPrimaryMode(requestedMode);
        }

        ensureUsbProxyServer();
        if (wifiDirectEnabled) {
            setupWifiP2p();
            createGroup();
        }
        if (currentMode == Mode.PRODUCT_USB) {
            productUsbStatus = "permission granted; opening bridge";
            startProductUsbBridge();
        } else {
            closeProductUsbBridge(true);
        }
        status("Transports " + modeLabel() + " active");
        updateNotification();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (mainHandler != null) {
            mainHandler.removeCallbacksAndMessages(null);
        }
        if (wifiP2pManager != null && wifiP2pChannel != null) {
            removeWifiGroupBestEffort();
        }
        clearWifiReceiver();
        closeWifiProxyServer();
        closeUsbProxyServer();
        closeProductUsbBridge();
        if (networkSelector != null) {
            networkSelector.close();
            networkSelector = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void setPrimaryMode(Mode mode) {
        Mode primaryMode = mode == Mode.PRODUCT_USB ? Mode.PRODUCT_USB : Mode.USB;
        if (currentMode == Mode.PRODUCT_USB && primaryMode != Mode.PRODUCT_USB) {
            closeProductUsbBridge(true);
        }
        currentMode = primaryMode;
        saveMode(primaryMode);
    }

    private void ensureUsbProxyServer() {
        if (usbProxyServer != null && usbProxyServer.isRunning()) {
            return;
        }
        closeUsbProxyServer();
        usbProxyServer = new HttpProxyServer(
                this,
                USB_HOST,
                USB_PROXY_CANDIDATE_PORTS,
                host -> networkSelector.openSocket(host.host, host.port),
                this::status);
        usbProxyServer.start();
        clearProxyStartupError(usbProxyServer);
        status("USB proxy: " + usbProxySummary());
    }

    private void ensureWifiProxyServer() {
        if (!wifiDirectEnabled || groupSsid.isEmpty()) {
            return;
        }
        if (wifiProxyServer != null && wifiProxyServer.isRunning()) {
            return;
        }
        closeWifiProxyServer();
        wifiProxyServer = new HttpProxyServer(
                this,
                WIFI_HOST,
                WIFI_DIRECT_CLIENT_PREFIX,
                WIFI_PROXY_CANDIDATE_PORTS,
                host -> networkSelector.openSocket(host.host, host.port),
                this::status);
        wifiProxyServer.start();
        clearProxyStartupError(wifiProxyServer);
        status("Wi-Fi Direct proxy: " + wifiProxySummary());
    }

    private void closeUsbProxyServer() {
        HttpProxyServer server = usbProxyServer;
        usbProxyServer = null;
        if (server != null) {
            server.close();
        }
    }

    private void closeWifiProxyServer() {
        HttpProxyServer server = wifiProxyServer;
        wifiProxyServer = null;
        if (server != null) {
            server.close();
        }
    }

    private void setupWifiP2p() {
        if (!wifiDirectEnabled) {
            return;
        }
        if (wifiP2pManager != null && wifiP2pChannel != null && p2pReceiver != null) {
            return;
        }
        wifiP2pManager = (WifiP2pManager) getSystemService(Context.WIFI_P2P_SERVICE);
        if (wifiP2pManager == null) {
            status("Wi-Fi Direct unavailable on this device");
            return;
        }
        wifiP2pChannel = wifiP2pManager.initialize(this, getMainLooper(),
                () -> status("Wi-Fi Direct channel disconnected"));
        if (wifiP2pChannel == null) {
            status("Wi-Fi Direct channel initialization failed");
            return;
        }

        p2pReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                String action = intent.getAction();
                if (WifiP2pManager.WIFI_P2P_CONNECTION_CHANGED_ACTION.equals(action)
                        || WifiP2pManager.WIFI_P2P_THIS_DEVICE_CHANGED_ACTION.equals(action)) {
                    requestGroupInfo();
                }
            }
        };
        IntentFilter filter = new IntentFilter();
        filter.addAction(WifiP2pManager.WIFI_P2P_CONNECTION_CHANGED_ACTION);
        filter.addAction(WifiP2pManager.WIFI_P2P_THIS_DEVICE_CHANGED_ACTION);
        registerReceiver(p2pReceiver, filter);
    }

    private void clearWifiReceiver() {
        if (p2pReceiver != null) {
            try {
                unregisterReceiver(p2pReceiver);
            } catch (RuntimeException ignored) {
            }
            p2pReceiver = null;
        }
        wifiP2pChannel = null;
        wifiP2pManager = null;
    }

    private void removeWifiGroupBestEffort() {
        if (wifiP2pManager == null || wifiP2pChannel == null) {
            return;
        }
        try {
            wifiP2pManager.removeGroup(wifiP2pChannel, new SimpleActionListener("removeGroup"));
        } catch (RuntimeException ignored) {
            // Group removal is best-effort during service shutdown or mode switch.
        }
    }

    private void stopWifiDirect() {
        setWifiDirectEnabled(false);
        wifiGroupOperationInProgress = false;
        removeWifiGroupBestEffort();
        clearWifiReceiver();
        closeWifiProxyServer();
        clearGroupInfo();
    }

    private void startProductUsbBridge() {
        closeProductUsbBridge();
        int port = usbProxyPort();
        if (port <= 0) {
            productUsbStatus = "proxy unavailable";
            status("Product USB bridge waiting for proxy port.");
            return;
        }
        productUsbBridge = new ProductUsbBridge(this, port, line -> {
            if (line.startsWith("Product USB host trusted")) {
                productUsbStatus = "trusted; stream connected";
            } else if (line.startsWith("Product USB host approval required")) {
                productUsbStatus = "host approval required";
            } else if (line.startsWith("Product USB bridge connected")) {
                productUsbStatus = "permission granted; bridge connected";
            } else if (line.contains("failed") || line.contains("stopped") || line.contains("missing")) {
                productUsbStatus = line;
            }
            status(line);
        });
        if (!productUsbBridge.start()) {
            productUsbStatus = "permission granted; bridge unavailable";
            closeProductUsbBridge();
        }
    }

    private void closeProductUsbBridge() {
        closeProductUsbBridge(false);
    }

    private void closeProductUsbBridge(boolean resetStatus) {
        if (productUsbBridge != null) {
            productUsbBridge.close();
            productUsbBridge = null;
        }
        if (resetStatus) {
            productUsbStatus = "permission missing";
        }
    }

    private void clearGroupInfo() {
        groupSsid = "";
        groupPassphrase = "";
        groupInterface = "";
        groupFrequencyMhz = 0;
    }

    private String valueOrEmpty(String value) {
        return value == null ? "" : value;
    }

    private void createGroup() {
        if (!wifiDirectEnabled || wifiGroupOperationInProgress) {
            return;
        }
        if (wifiP2pManager == null || wifiP2pChannel == null) {
            setupWifiP2p();
        }
        if (wifiP2pManager == null || wifiP2pChannel == null) {
            return;
        }
        try {
            wifiP2pManager.requestGroupInfo(wifiP2pChannel, group -> {
                if (group != null) {
                    handleExistingGroup(group);
                    return;
                }
                createGroupInternal(true);
            });
        } catch (SecurityException ex) {
            status("Cannot read group info; permission missing");
        } catch (RuntimeException ex) {
            status("requestGroupInfo failed: " + ex.getMessage());
            createGroupInternal(true);
        }
    }

    private void createGroupInternal(boolean retryAfterBusy) {
        if (!wifiDirectEnabled || wifiGroupOperationInProgress
                || wifiP2pManager == null || wifiP2pChannel == null) {
            return;
        }
        WifiP2pManager.ActionListener listener = new WifiP2pManager.ActionListener() {
            @Override
            public void onSuccess() {
                wifiGroupOperationInProgress = false;
                status("createGroup accepted for " + WifiDirectBand.label(requestedWifiBand));
                requestGroupInfo();
            }

            @Override
            public void onFailure(int reason) {
                wifiGroupOperationInProgress = false;
                status("createGroup rejected for " + WifiDirectBand.label(requestedWifiBand)
                        + ": " + p2pReason(reason));
                if (retryAfterBusy && reason == WifiP2pManager.BUSY) {
                    recoverBusyP2pGroup();
                }
            }
        };
        try {
            wifiGroupOperationInProgress = true;
            if (WifiDirectBand.supportsSelection()) {
                wifiP2pManager.createGroup(wifiP2pChannel, buildWifiP2pConfig(), listener);
            } else {
                wifiP2pManager.createGroup(wifiP2pChannel, listener);
            }
        } catch (SecurityException ex) {
            wifiGroupOperationInProgress = false;
            status("Missing Wi-Fi Direct permission: " + ex.getMessage());
        } catch (RuntimeException ex) {
            wifiGroupOperationInProgress = false;
            status("createGroup failed: " + ex.getMessage());
        }
    }

    private WifiP2pConfig buildWifiP2pConfig() {
        String[] credentials = loadOrCreateGroupCredentials();
        return new WifiP2pConfig.Builder()
                .setNetworkName(credentials[0])
                .setPassphrase(credentials[1])
                .setGroupOperatingBand(WifiDirectBand.toPlatformBand(requestedWifiBand))
                .enablePersistentMode(false)
                .build();
    }

    private void handleExistingGroup(WifiP2pGroup group) {
        rememberGroupCredentials(group);
        int frequencyMhz = groupFrequency(group);
        if (WifiDirectBand.matchesFrequency(requestedWifiBand, frequencyMhz)) {
            publishGroupInfo(group);
            return;
        }
        recreateGroupForRequestedBand(frequencyMhz);
    }

    private void recreateGroupForRequestedBand(int currentFrequencyMhz) {
        if (wifiGroupOperationInProgress) {
            return;
        }
        closeWifiProxyServer();
        clearGroupInfo();
        status("Switching Wi-Fi Direct from " + WifiDirectBand.frequencyLabel(currentFrequencyMhz)
                + " to " + WifiDirectBand.label(requestedWifiBand));
        wifiGroupOperationInProgress = true;
        try {
            wifiP2pManager.removeGroup(wifiP2pChannel, new WifiP2pManager.ActionListener() {
                @Override
                public void onSuccess() {
                    wifiGroupOperationInProgress = false;
                    status("Previous Wi-Fi Direct group removed for band switch");
                    createGroupInternal(false);
                }

                @Override
                public void onFailure(int reason) {
                    wifiGroupOperationInProgress = false;
                    status("Band switch removeGroup rejected: " + p2pReason(reason));
                }
            });
        } catch (SecurityException ex) {
            wifiGroupOperationInProgress = false;
            status("Cannot switch Wi-Fi Direct band; permission missing");
        } catch (RuntimeException ex) {
            wifiGroupOperationInProgress = false;
            status("Band switch removeGroup failed: " + ex.getMessage());
        }
    }

    private void recoverBusyP2pGroup() {
        try {
            wifiP2pManager.requestGroupInfo(wifiP2pChannel, group -> {
                if (group != null) {
                    status("Wi-Fi Direct busy; checking existing group band");
                    handleExistingGroup(group);
                    return;
                }
                removeGroupBeforeRetry();
            });
        } catch (SecurityException ex) {
            status("Cannot recover Wi-Fi Direct busy state; permission missing");
        } catch (RuntimeException ex) {
            status("requestGroupInfo after busy failed: " + ex.getMessage());
            removeGroupBeforeRetry();
        }
    }

    private void removeGroupBeforeRetry() {
        status("Wi-Fi Direct busy; removing stale group before one retry");
        try {
            wifiGroupOperationInProgress = true;
            wifiP2pManager.removeGroup(wifiP2pChannel, new SimpleActionListener("removeGroup before retry") {
                @Override
                public void onSuccess() {
                    wifiGroupOperationInProgress = false;
                    super.onSuccess();
                    retryCreateGroupOnce();
                }

                @Override
                public void onFailure(int reason) {
                    wifiGroupOperationInProgress = false;
                    status("removeGroup before retry rejected: " + p2pReason(reason));
                    retryCreateGroupOnce();
                }
            });
        } catch (SecurityException ex) {
            wifiGroupOperationInProgress = false;
            status("Cannot remove stale Wi-Fi Direct group; permission missing");
        } catch (RuntimeException ex) {
            wifiGroupOperationInProgress = false;
            status("removeGroup before retry failed: " + ex.getMessage());
            retryCreateGroupOnce();
        }
    }

    private void retryCreateGroupOnce() {
        mainHandler.postDelayed(() -> {
            if (wifiDirectEnabled) {
                createGroupInternal(false);
            }
        }, 1000);
    }

    private void requestGroupInfo() {
        if (wifiP2pManager == null || wifiP2pChannel == null) {
            return;
        }
        try {
            wifiP2pManager.requestGroupInfo(wifiP2pChannel, this::publishGroupInfo);
        } catch (SecurityException ex) {
            status("Cannot read group info; permission missing");
        }
    }

    private void publishGroupInfo(WifiP2pGroup group) {
        if (!wifiDirectEnabled) {
            clearGroupInfo();
            return;
        }
        if (group == null) {
            closeWifiProxyServer();
            clearGroupInfo();
            status("Wi-Fi Direct group not active yet");
            updateNotification();
            return;
        }
        groupSsid = valueOrEmpty(group.getNetworkName());
        groupPassphrase = valueOrEmpty(group.getPassphrase());
        groupInterface = valueOrEmpty(group.getInterface());
        groupFrequencyMhz = groupFrequency(group);
        if (groupSsid.isEmpty()) {
            closeWifiProxyServer();
            status("Wi-Fi Direct group missing network name");
            return;
        }
        rememberGroupCredentials(group);
        if (!WifiDirectBand.matchesFrequency(requestedWifiBand, groupFrequencyMhz)) {
            closeWifiProxyServer();
            status("Wi-Fi Direct group used " + WifiDirectBand.frequencyLabel(groupFrequencyMhz)
                    + " instead of requested " + WifiDirectBand.label(requestedWifiBand));
            return;
        }
        ensureWifiProxyServer();
        status("Group SSID: " + groupSsid);
        status("Group password: available in app summary");
        status("Group interface: " + groupInterface);
        status("Group band: " + WifiDirectBand.frequencyLabel(groupFrequencyMhz)
                + " (requested " + WifiDirectBand.label(requestedWifiBand) + ")");
        status("Wi-Fi Direct proxy target: " + wifiTarget());
        updateNotification();
    }

    private int groupFrequency(WifiP2pGroup group) {
        return Build.VERSION.SDK_INT >= 29 ? group.getFrequency() : 0;
    }

    private void rememberGroupCredentials(WifiP2pGroup group) {
        String ssid = valueOrEmpty(group.getNetworkName());
        String passphrase = valueOrEmpty(group.getPassphrase());
        if (!validGroupCredentials(ssid, passphrase)) {
            return;
        }
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putString(PREF_WIFI_DIRECT_SSID, ssid)
                .putString(PREF_WIFI_DIRECT_PASSPHRASE, passphrase)
                .apply();
    }

    private String[] loadOrCreateGroupCredentials() {
        SharedPreferences preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String ssid = valueOrEmpty(preferences.getString(PREF_WIFI_DIRECT_SSID, ""));
        String passphrase = valueOrEmpty(preferences.getString(PREF_WIFI_DIRECT_PASSPHRASE, ""));
        if (validGroupCredentials(ssid, passphrase)) {
            return new String[]{ssid, passphrase};
        }
        SecureRandom random = new SecureRandom();
        ssid = "DIRECT-PD-PdaLink-" + randomToken(random, 6);
        passphrase = randomToken(random, 16);
        preferences.edit()
                .putString(PREF_WIFI_DIRECT_SSID, ssid)
                .putString(PREF_WIFI_DIRECT_PASSPHRASE, passphrase)
                .apply();
        return new String[]{ssid, passphrase};
    }

    private boolean validGroupCredentials(String ssid, String passphrase) {
        return ssid.matches("^DIRECT-[A-Za-z0-9]{2}.*")
                && ssid.getBytes(java.nio.charset.StandardCharsets.UTF_8).length <= 32
                && passphrase.matches("^[ -~]{8,63}$");
    }

    private String randomToken(SecureRandom random, int length) {
        String alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
        StringBuilder token = new StringBuilder(length);
        for (int index = 0; index < length; index++) {
            token.append(alphabet.charAt(random.nextInt(alphabet.length())));
        }
        return token.toString();
    }

    private void updateNotification() {
        startForegroundCompat(buildNotificationText());
    }

    private String buildNotificationText() {
        return modeLabel() + ": " + proxySummary() + " egress " + egressSummary();
    }

    private String egressSummary() {
        if (networkSelector == null) {
            return "unknown";
        }
        return networkSelector.getEgress();
    }

    private void status(String line) {
        recordStatusLine(line);
        Log.i(TAG, line);
        Intent intent = new Intent(ProxyActions.ACTION_STATUS);
        intent.setPackage(getPackageName());
        intent.putExtra(ProxyActions.EXTRA_STATUS, line);
        putSnapshotExtras(intent);
        sendBroadcast(intent);
        updateNotification();
    }

    private void recordStatusLine(String line) {
        if (line.startsWith("CONNECT ") || line.matches("[A-Z]+ http://.*")) {
            lastRequest = line;
        }
        String lower = line.toLowerCase(Locale.US);
        if (lower.contains("failed")
                || lower.contains("rejected")
                || lower.contains("unavailable")
                || lower.contains("missing")
                || lower.contains("cannot")
                || lower.contains("no proxy")) {
            lastError = line;
        }
    }

    private void clearProxyStartupError(HttpProxyServer server) {
        if (server == null || !server.isRunning()) {
            return;
        }
        if (lastError.startsWith("Port ") || "No proxy port available".equals(lastError)) {
            lastError = "";
        }
    }

    private void putSnapshotExtras(Intent intent) {
        intent.putExtra(ProxyActions.EXTRA_MODE, modeLabel());
        intent.putExtra(ProxyActions.EXTRA_PROXY_STATUS, proxySummary());
        intent.putExtra(ProxyActions.EXTRA_PROXY_PORT, usbProxyPort());
        intent.putExtra(ProxyActions.EXTRA_USB_PROXY_STATUS, usbProxySummary());
        intent.putExtra(ProxyActions.EXTRA_WIFI_PROXY_STATUS, wifiProxySummary());
        intent.putExtra(ProxyActions.EXTRA_EGRESS, egressSummary());
        intent.putExtra(ProxyActions.EXTRA_GROUP_SSID, groupSsid);
        intent.putExtra(ProxyActions.EXTRA_GROUP_PASSPHRASE, groupPassphrase);
        intent.putExtra(ProxyActions.EXTRA_GROUP_INTERFACE, groupInterface);
        intent.putExtra(ProxyActions.EXTRA_WIFI_BAND, requestedWifiBand);
        intent.putExtra(ProxyActions.EXTRA_WIFI_FREQUENCY_MHZ, groupFrequencyMhz);
        intent.putExtra(ProxyActions.EXTRA_LINUX_TARGET, linuxTarget());
        intent.putExtra(ProxyActions.EXTRA_USB_TARGET, usbTarget());
        intent.putExtra(ProxyActions.EXTRA_WIFI_TARGET, wifiTarget());
        intent.putExtra(ProxyActions.EXTRA_LAST_REQUEST, latestRequest());
        intent.putExtra(ProxyActions.EXTRA_LAST_ERROR, lastError);
        intent.putExtra(ProxyActions.EXTRA_PRODUCT_USB_STATUS, productUsbStatus);
        intent.putExtra(ProxyActions.EXTRA_CLIENT_COUNT,
                clientCount(usbProxyServer) + clientCount(wifiProxyServer));
        intent.putExtra(ProxyActions.EXTRA_TRAFFIC_UP_BYTES,
                upstreamBytes(usbProxyServer) + upstreamBytes(wifiProxyServer));
        intent.putExtra(ProxyActions.EXTRA_TRAFFIC_DOWN_BYTES,
                downstreamBytes(usbProxyServer) + downstreamBytes(wifiProxyServer));
    }

    private String proxySummary() {
        return "USB " + usbProxySummary() + "; Wi-Fi Direct " + wifiProxySummary();
    }

    private String usbProxySummary() {
        HttpProxyServer server = usbProxyServer;
        return server == null ? "not running" : server.listenSummary();
    }

    private String wifiProxySummary() {
        HttpProxyServer server = wifiProxyServer;
        return server == null ? "not running" : server.listenSummary();
    }

    private int usbProxyPort() {
        HttpProxyServer server = usbProxyServer;
        return server == null ? -1 : server.getPort();
    }

    private int wifiProxyPort() {
        HttpProxyServer server = wifiProxyServer;
        return server == null ? -1 : server.getPort();
    }

    private String modeLabel() {
        String primaryMode;
        if (currentMode == Mode.PRODUCT_USB) {
            primaryMode = ProxyActions.MODE_PRODUCT_USB;
        } else {
            primaryMode = ProxyActions.MODE_USB;
        }
        if (!wifiDirectEnabled) {
            return primaryMode;
        }
        if (currentMode == Mode.PRODUCT_USB) {
            return primaryMode + " + " + ProxyActions.MODE_WIFI_DIRECT;
        }
        return ProxyActions.MODE_USB_WIFI_DIRECT;
    }

    private String linuxTarget() {
        if (wifiDirectEnabled) {
            return wifiTarget();
        }
        if (currentMode == Mode.PRODUCT_USB) {
            return "product-usb://proxy";
        }
        return usbTarget();
    }

    private String usbTarget() {
        int port = usbProxyPort();
        return port <= 0 ? "not ready" : "http://" + USB_HOST + ":" + port;
    }

    private String wifiTarget() {
        int port = wifiProxyPort();
        return port <= 0 ? "not ready" : "http://" + WIFI_HOST + ":" + port;
    }

    private String latestRequest() {
        if (!lastRequest.isEmpty()) {
            return lastRequest;
        }
        HttpProxyServer wifiServer = wifiProxyServer;
        String wifiRequest = wifiServer == null ? "" : wifiServer.getLastRequest();
        if (!wifiRequest.isEmpty()) {
            return wifiRequest;
        }
        HttpProxyServer usbServer = usbProxyServer;
        return usbServer == null ? "" : usbServer.getLastRequest();
    }

    private int clientCount(HttpProxyServer server) {
        return server == null ? 0 : server.getClientCount();
    }

    private long upstreamBytes(HttpProxyServer server) {
        return server == null ? 0L : server.getUpstreamBytes();
    }

    private long downstreamBytes(HttpProxyServer server) {
        return server == null ? 0L : server.getDownstreamBytes();
    }

    private Mode parseMode(String action) {
        if (ProxyActions.ACTION_START_WIFI_DIRECT.equals(action)) {
            return Mode.WIFI_DIRECT;
        }
        if (ProxyActions.ACTION_START_PRODUCT_USB.equals(action)) {
            return Mode.PRODUCT_USB;
        }
        if (ProxyActions.ACTION_START_USB.equals(action) || ProxyActions.ACTION_START.equals(action)) {
            return Mode.USB;
        }
        return null;
    }

    private Mode loadSavedMode() {
        SharedPreferences preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String value = preferences.getString(PREF_MODE, Mode.USB.name());
        try {
            return Mode.valueOf(value == null ? Mode.USB.name() : value);
        } catch (IllegalArgumentException ex) {
            return Mode.USB;
        }
    }

    private void saveMode(Mode mode) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putString(PREF_MODE, mode.name())
                .apply();
    }

    private boolean loadWifiDirectEnabled() {
        return getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .getBoolean(PREF_WIFI_DIRECT_ENABLED, false);
    }

    private void setWifiDirectEnabled(boolean enabled) {
        wifiDirectEnabled = enabled;
        saveWifiDirectEnabled(enabled);
    }

    private void saveWifiDirectEnabled(boolean enabled) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putBoolean(PREF_WIFI_DIRECT_ENABLED, enabled)
                .apply();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "PdaLink proxy",
                NotificationManager.IMPORTANCE_LOW);
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(channel);
    }

    private void startForegroundCompat(String text) {
        Intent intent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this,
                0,
                intent,
                Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);

        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        String title = "PdaLink Prototype";
        Notification notification = builder
                .setContentTitle(title)
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .build();

        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private class SimpleActionListener implements WifiP2pManager.ActionListener {
        private final String name;

        SimpleActionListener(String name) {
            this.name = name;
        }

        @Override
        public void onSuccess() {
            status(name + " accepted");
        }

        @Override
        public void onFailure(int reason) {
            status(name + " rejected: " + p2pReason(reason));
        }
    }

    private String p2pReason(int reason) {
        switch (reason) {
            case WifiP2pManager.ERROR:
                return "ERROR(0)";
            case WifiP2pManager.P2P_UNSUPPORTED:
                return "P2P_UNSUPPORTED(1)";
            case WifiP2pManager.BUSY:
                return "BUSY(2)";
            case WifiP2pManager.NO_SERVICE_REQUESTS:
                return "NO_SERVICE_REQUESTS(3)";
            default:
                return "UNKNOWN(" + reason + ")";
        }
    }
}
