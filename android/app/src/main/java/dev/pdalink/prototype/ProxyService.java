package dev.pdalink.prototype;

import android.annotation.TargetApi;
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
import android.net.wifi.SoftApConfiguration;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiManager;
import android.net.wifi.p2p.WifiP2pConfig;
import android.net.wifi.p2p.WifiP2pGroup;
import android.net.wifi.p2p.WifiP2pManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.util.Log;

import java.security.SecureRandom;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class ProxyService extends Service {
    private static final String TAG = "PdaLinkService";
    private static final String CHANNEL_ID = "pdalink_proxy";
    private static final int NOTIFICATION_ID = 77;
    private static final String PREFS_NAME = "pdalink_proxy";
    private static final String PREF_MODE = "mode";
    private static final String PREF_WIFI_DIRECT_ENABLED = "wifi_direct_enabled";
    private static final String PREF_LOCAL_HOTSPOT_ENABLED = "local_hotspot_enabled";
    private static final String PREF_WIFI_DIRECT_SSID = "wifi_direct_ssid";
    private static final String PREF_WIFI_DIRECT_PASSPHRASE = "wifi_direct_passphrase";
    private static final String PREF_WIFI_DIRECT_LEGACY_OWNED_SSID = "wifi_direct_legacy_owned_ssid";
    private static final String WIFI_DIRECT_HOST = "192.168.49.1";
    private static final String WIFI_DIRECT_CLIENT_PREFIX = "192.168.49.";
    private static final String USB_HOST = "127.0.0.1";
    private static final int[] USB_PROXY_CANDIDATE_PORTS = new int[]{8000};
    private static final int[] WIFI_PROXY_CANDIDATE_PORTS = new int[]{8001};

    private enum Mode {USB, PRODUCT_USB, WIFI_DIRECT, LOCAL_HOTSPOT}

    private volatile HttpProxyServer usbProxyServer;
    private volatile HttpProxyServer wifiProxyServer;
    private NetworkSelector networkSelector;
    private WifiP2pManager wifiP2pManager;
    private WifiP2pManager.Channel wifiP2pChannel;
    private BroadcastReceiver p2pReceiver;
    private WifiManager.LocalOnlyHotspotReservation localHotspotReservation;
    private Handler mainHandler;
    private ProductUsbBridge productUsbBridge;
    private Mode currentMode = Mode.USB;
    private boolean wifiDirectEnabled;
    private boolean localHotspotEnabled;
    private boolean localHotspotStartAuthorized;
    private boolean localHotspotStartPending;
    private boolean localHotspotPreparationPending;
    private String wifiProxyHost = WIFI_DIRECT_HOST;
    private String wifiClientPrefix = WIFI_DIRECT_CLIENT_PREFIX;
    private List<LocalHotspotAddressSelector.InterfaceAddress> localHotspotBaseline = new ArrayList<>();
    private int localHotspotGeneration;
    private String groupSsid = "";
    private String groupPassphrase = "";
    private String groupInterface = "";
    private String wifiSecurity = "";
    private String requestedWifiBand = WifiDirectBand.AUTO;
    private int groupFrequencyMhz;
    private int wifiGroupClientCount;
    private boolean wifiGroupOwned;
    private boolean wifiGroupCreationAccepted;
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
        if (savedMode == Mode.WIFI_DIRECT || savedMode == Mode.LOCAL_HOTSPOT) {
            currentMode = Mode.USB;
            if (savedMode == Mode.WIFI_DIRECT) {
                setWifiDirectEnabled(true);
            }
            saveMode(Mode.USB);
        } else {
            currentMode = savedMode;
            wifiDirectEnabled = loadWifiDirectEnabled();
        }
        localHotspotEnabled = false;
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .remove(PREF_LOCAL_HOTSPOT_ENABLED)
                .apply();
        if (wifiDirectEnabled && localHotspotEnabled) {
            setWifiDirectEnabled(false);
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
        if (ProxyActions.ACTION_START_WIFI_DIRECT.equals(action)) {
            boolean switching = localHotspotEnabled || localHotspotReservation != null;
            stopLocalHotspot();
            setWifiDirectEnabled(true);
            ensureUsbProxyServer();
            Runnable start = () -> {
                if (!wifiDirectEnabled) {
                    return;
                }
                setupWifiP2p();
                createGroup();
            };
            if (switching) {
                mainHandler.postDelayed(start, 1000);
            } else {
                start.run();
            }
            status("Transports " + modeLabel() + " active");
            return START_STICKY;
        }
        if (ProxyActions.ACTION_START_LOCAL_HOTSPOT.equals(action)) {
            if (Build.VERSION.SDK_INT < 26) {
                status("Local Hotspot requires Android 8.0 or later");
                return START_STICKY;
            }
            localHotspotStartAuthorized = true;
            setLocalHotspotEnabled(true);
            ensureUsbProxyServer();
            prepareLocalHotspot();
            status("Transports " + modeLabel() + " active");
            return START_STICKY;
        }
        if (ProxyActions.ACTION_STOP_USB.equals(action)) {
            closeUsbProxyServer();
            closeProductUsbBridge(true);
            if (!wifiDirectEnabled && !localHotspotEnabled) {
                status("Stopping USB proxy service");
                stopSelf();
                return START_NOT_STICKY;
            }
            status("USB proxy stopped; " + wifiTransportLabel() + " remains active");
            updateNotification();
            return START_STICKY;
        }
        if (ProxyActions.ACTION_STOP.equals(action)) {
            setWifiDirectEnabled(false);
            setLocalHotspotEnabled(false);
            status("Stopping service");
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ProxyActions.ACTION_STOP_WIFI.equals(action)) {
            ensureUsbProxyServer();
            stopWifiDirect();
            stopLocalHotspot();
            status("Wi-Fi stopped; USB proxy remains active");
            return START_STICKY;
        }
        if (ProxyActions.ACTION_STOP_WIFI_DIRECT.equals(action)) {
            ensureUsbProxyServer();
            stopWifiDirect();
            status("Wi-Fi Direct stopped; USB proxy remains active");
            updateNotification();
            return START_STICKY;
        }
        if (ProxyActions.ACTION_STOP_LOCAL_HOTSPOT.equals(action)) {
            ensureUsbProxyServer();
            stopLocalHotspot();
            status("Local Hotspot stopped; USB proxy remains active");
            return START_STICKY;
        }

        Mode requestedMode = parseMode(action);
        if (ProxyActions.ACTION_REFRESH.equals(action)) {
            ensureUsbProxyServer();
            if (wifiDirectEnabled) {
                setupWifiP2p();
                requestGroupInfo();
            } else if (localHotspotEnabled) {
                if (localHotspotReservation != null) {
                    scheduleLocalHotspotAddressDiscovery(localHotspotGeneration, 0);
                } else {
                    status("Local Hotspot is not active; start it explicitly to create a new reservation");
                }
            } else {
                status("Wi-Fi transport not active");
            }
            status("Proxies: " + proxySummary());
            updateNotification();
            return START_STICKY;
        }
        if (requestedMode == null) {
            requestedMode = currentMode == null ? loadSavedMode() : currentMode;
        }

        if (requestedMode != Mode.WIFI_DIRECT && requestedMode != Mode.LOCAL_HOTSPOT) {
            setPrimaryMode(requestedMode);
        }

        ensureUsbProxyServer();
        if (wifiDirectEnabled) {
            setupWifiP2p();
            createGroup();
        } else if (localHotspotEnabled && localHotspotReservation != null) {
            scheduleLocalHotspotAddressDiscovery(localHotspotGeneration, 0);
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
        LocalHotspotDebugState.clear();
        if (mainHandler != null) {
            mainHandler.removeCallbacksAndMessages(null);
        }
        if (wifiP2pManager != null && wifiP2pChannel != null) {
            removeWifiGroupBestEffort();
        }
        closeLocalHotspotReservation();
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
        if ((!wifiDirectEnabled && !localHotspotEnabled)
                || groupSsid.isEmpty()
                || wifiProxyHost.isEmpty()
                || wifiClientPrefix.isEmpty()) {
            return;
        }
        if (wifiProxyServer != null && wifiProxyServer.isRunning()) {
            return;
        }
        closeWifiProxyServer();
        wifiProxyServer = new HttpProxyServer(
                this,
                wifiProxyHost,
                wifiClientPrefix,
                WIFI_PROXY_CANDIDATE_PORTS,
                host -> networkSelector.openSocket(host.host, host.port),
                this::status);
        wifiProxyServer.start();
        clearProxyStartupError(wifiProxyServer);
        status(wifiTransportLabel() + " proxy: " + wifiProxySummary());
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
        if (!wifiDirectEnabled && !localHotspotEnabled) {
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
        if (!wifiGroupOwned || wifiP2pManager == null || wifiP2pChannel == null) {
            return;
        }
        wifiGroupOwned = false;
        wifiGroupCreationAccepted = false;
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
        wifiGroupOwned = false;
        wifiGroupCreationAccepted = false;
        clearWifiReceiver();
        closeWifiProxyServer();
        clearGroupInfo();
        wifiProxyHost = WIFI_DIRECT_HOST;
        wifiClientPrefix = WIFI_DIRECT_CLIENT_PREFIX;
    }

    private void prepareLocalHotspot() {
        if (!localHotspotEnabled
                || !localHotspotStartAuthorized
                || localHotspotPreparationPending
                || localHotspotStartPending
                || localHotspotReservation != null) {
            return;
        }
        localHotspotPreparationPending = true;
        setupWifiP2p();
        if (wifiP2pManager == null || wifiP2pChannel == null) {
            localHotspotPreparationPending = false;
            startLocalHotspot();
            return;
        }
        try {
            wifiP2pManager.requestGroupInfo(wifiP2pChannel, group -> {
                if (!localHotspotEnabled) {
                    localHotspotPreparationPending = false;
                    return;
                }
                if (group == null) {
                    finishWifiDirectCleanupForLocalHotspot();
                    return;
                }
                if (!isOwnedWifiGroup(group)) {
                    localHotspotPreparationPending = false;
                    setLocalHotspotEnabled(false);
                    rejectUnownedWifiGroup();
                    status("Local Hotspot blocked by a Wi-Fi Direct group not owned by PdaLink");
                    return;
                }
                acceptOwnedWifiGroup(group);
                removeOwnedWifiGroupForLocalHotspot();
            });
        } catch (SecurityException ex) {
            localHotspotPreparationPending = false;
            setLocalHotspotEnabled(false);
            status("Cannot inspect Wi-Fi Direct before Local Hotspot; permission missing");
        } catch (RuntimeException ex) {
            localHotspotPreparationPending = false;
            setLocalHotspotEnabled(false);
            status("Cannot inspect Wi-Fi Direct before Local Hotspot: " + ex.getMessage());
        }
    }

    private void removeOwnedWifiGroupForLocalHotspot() {
        try {
            wifiP2pManager.removeGroup(wifiP2pChannel, new WifiP2pManager.ActionListener() {
                @Override
                public void onSuccess() {
                    status("Owned Wi-Fi Direct group removed for Local Hotspot");
                    finishWifiDirectCleanupForLocalHotspot();
                }

                @Override
                public void onFailure(int reason) {
                    localHotspotPreparationPending = false;
                    setLocalHotspotEnabled(false);
                    status("Cannot remove owned Wi-Fi Direct group for Local Hotspot: "
                            + p2pReason(reason));
                }
            });
        } catch (SecurityException ex) {
            localHotspotPreparationPending = false;
            setLocalHotspotEnabled(false);
            status("Cannot remove owned Wi-Fi Direct group; permission missing");
        } catch (RuntimeException ex) {
            localHotspotPreparationPending = false;
            setLocalHotspotEnabled(false);
            status("Cannot remove owned Wi-Fi Direct group: " + ex.getMessage());
        }
    }

    private void finishWifiDirectCleanupForLocalHotspot() {
        setWifiDirectEnabled(false);
        wifiGroupOperationInProgress = false;
        wifiGroupOwned = false;
        wifiGroupCreationAccepted = false;
        clearWifiReceiver();
        closeWifiProxyServer();
        clearGroupInfo();
        wifiProxyHost = "";
        wifiClientPrefix = "";
        localHotspotPreparationPending = false;
        mainHandler.postDelayed(this::startLocalHotspot, 500);
    }

    @TargetApi(26)
    private void startLocalHotspot() {
        if (!localHotspotEnabled
                || !localHotspotStartAuthorized
                || localHotspotStartPending
                || localHotspotReservation != null) {
            return;
        }
        WifiManager wifiManager = (WifiManager) getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        if (wifiManager == null) {
            setLocalHotspotEnabled(false);
            status("Local Hotspot unavailable on this device");
            return;
        }
        localHotspotBaseline = snapshotLocalAddresses();
        LocalHotspotDebugState.clear();
        wifiProxyHost = "";
        wifiClientPrefix = "";
        closeWifiProxyServer();
        clearGroupInfo();
        int generation = ++localHotspotGeneration;
        localHotspotStartPending = true;
        try {
            wifiManager.startLocalOnlyHotspot(new WifiManager.LocalOnlyHotspotCallback() {
                @Override
                public void onStarted(WifiManager.LocalOnlyHotspotReservation reservation) {
                    if (!localHotspotEnabled || generation != localHotspotGeneration) {
                        reservation.close();
                        return;
                    }
                    localHotspotStartPending = false;
                    localHotspotStartAuthorized = false;
                    localHotspotReservation = reservation;
                    publishLocalHotspotConfiguration(reservation);
                    scheduleLocalHotspotAddressDiscovery(generation, 0);
                }

                @Override
                public void onStopped() {
                    if (generation != localHotspotGeneration) {
                        return;
                    }
                    localHotspotStartPending = false;
                    localHotspotStartAuthorized = false;
                    localHotspotReservation = null;
                    LocalHotspotDebugState.clear();
                    setLocalHotspotEnabled(false);
                    closeWifiProxyServer();
                    clearGroupInfo();
                    wifiProxyHost = "";
                    wifiClientPrefix = "";
                    status("Local Hotspot stopped by Android");
                }

                @Override
                public void onFailed(int reason) {
                    if (generation != localHotspotGeneration) {
                        return;
                    }
                    localHotspotStartPending = false;
                    localHotspotStartAuthorized = false;
                    localHotspotReservation = null;
                    LocalHotspotDebugState.clear();
                    setLocalHotspotEnabled(false);
                    closeWifiProxyServer();
                    clearGroupInfo();
                    status("Local Hotspot failed: reason " + reason);
                }
            }, mainHandler);
            status("Local Hotspot start requested");
        } catch (SecurityException ex) {
            localHotspotStartPending = false;
            localHotspotStartAuthorized = false;
            LocalHotspotDebugState.clear();
            setLocalHotspotEnabled(false);
            status("Cannot start Local Hotspot; permission missing");
        } catch (RuntimeException ex) {
            localHotspotStartPending = false;
            localHotspotStartAuthorized = false;
            LocalHotspotDebugState.clear();
            setLocalHotspotEnabled(false);
            status("Local Hotspot start failed: " + ex.getMessage());
        }
    }

    @TargetApi(26)
    private void publishLocalHotspotConfiguration(
            WifiManager.LocalOnlyHotspotReservation reservation) {
        if (Build.VERSION.SDK_INT >= 30) {
            SoftApConfiguration configuration = reservation.getSoftApConfiguration();
            groupSsid = valueOrEmpty(configuration.getSsid());
            groupPassphrase = valueOrEmpty(configuration.getPassphrase());
            wifiSecurity = softApSecurityLabel(configuration.getSecurityType());
        } else {
            WifiConfiguration configuration = reservation.getWifiConfiguration();
            groupSsid = unquoteWifiValue(configuration == null ? null : configuration.SSID);
            groupPassphrase = unquoteWifiValue(
                    configuration == null ? null : configuration.preSharedKey);
            wifiSecurity = "WPA2-PSK";
        }
        groupInterface = "discovering";
        groupFrequencyMhz = 0;
        wifiGroupClientCount = -1;
        wifiGroupOwned = false;
        status("Local Hotspot SSID: " + groupSsid);
        status("Local Hotspot password: available in app summary");
        status("Local Hotspot security: " + wifiSecurity);
    }

    @TargetApi(30)
    private String softApSecurityLabel(int securityType) {
        switch (securityType) {
            case SoftApConfiguration.SECURITY_TYPE_OPEN:
                return "Open";
            case SoftApConfiguration.SECURITY_TYPE_WPA2_PSK:
                return "WPA2-PSK";
            case SoftApConfiguration.SECURITY_TYPE_WPA3_SAE_TRANSITION:
                return "WPA2/WPA3 transition";
            case SoftApConfiguration.SECURITY_TYPE_WPA3_SAE:
                return "WPA3-SAE";
            case SoftApConfiguration.SECURITY_TYPE_WPA3_OWE_TRANSITION:
                return "OWE transition";
            case SoftApConfiguration.SECURITY_TYPE_WPA3_OWE:
                return "OWE";
            default:
                return "type " + securityType;
        }
    }

    private String unquoteWifiValue(String value) {
        String normalized = valueOrEmpty(value);
        if (normalized.length() >= 2
                && normalized.charAt(0) == '"'
                && normalized.charAt(normalized.length() - 1) == '"') {
            return normalized.substring(1, normalized.length() - 1);
        }
        return normalized;
    }

    private void scheduleLocalHotspotAddressDiscovery(int generation, int attempt) {
        if (!localHotspotEnabled
                || localHotspotReservation == null
                || generation != localHotspotGeneration) {
            return;
        }
        LocalHotspotAddressSelector.InterfaceAddress selected =
                LocalHotspotAddressSelector.select(
                        localHotspotBaseline,
                        snapshotLocalAddresses());
        if (selected != null) {
            boolean addressChanged = !selected.address.equals(wifiProxyHost);
            groupInterface = selected.interfaceName;
            wifiProxyHost = selected.address;
            wifiClientPrefix = LocalHotspotAddressSelector.clientPrefix(selected.address);
            if (addressChanged) {
                LocalHotspotDebugState.clear();
                closeWifiProxyServer();
            }
            ensureWifiProxyServer();
            publishLocalHotspotDebugState();
            status("Local Hotspot interface: " + groupInterface);
            status("Local Hotspot proxy target: " + wifiTarget());
            return;
        }
        if (attempt >= 40) {
            status("Local Hotspot interface unavailable after 10 seconds");
            return;
        }
        mainHandler.postDelayed(
                () -> scheduleLocalHotspotAddressDiscovery(generation, attempt + 1),
                250);
    }

    private List<LocalHotspotAddressSelector.InterfaceAddress> snapshotLocalAddresses() {
        try {
            return LocalHotspotAddressSelector.snapshot();
        } catch (SocketException ex) {
            status("Local Hotspot interface scan failed: " + ex.getMessage());
            return new ArrayList<>();
        }
    }

    private void stopLocalHotspot() {
        LocalHotspotDebugState.clear();
        localHotspotStartAuthorized = false;
        setLocalHotspotEnabled(false);
        closeLocalHotspotReservation();
        closeWifiProxyServer();
        clearGroupInfo();
        wifiProxyHost = "";
        wifiClientPrefix = "";
    }

    @TargetApi(26)
    private void closeLocalHotspotReservation() {
        LocalHotspotDebugState.clear();
        localHotspotStartAuthorized = false;
        localHotspotGeneration++;
        localHotspotStartPending = false;
        localHotspotPreparationPending = false;
        WifiManager.LocalOnlyHotspotReservation reservation = localHotspotReservation;
        localHotspotReservation = null;
        if (reservation != null) {
            reservation.close();
        }
    }

    private void publishLocalHotspotDebugState() {
        int port = wifiProxyPort();
        if (!localHotspotEnabled
                || localHotspotReservation == null
                || wifiProxyServer == null
                || !wifiProxyServer.isRunning()
                || groupSsid.isEmpty()
                || groupPassphrase.isEmpty()
                || groupInterface.isEmpty()
                || wifiProxyHost.isEmpty()
                || port <= 0) {
            return;
        }
        LocalHotspotDebugState.publish(
                groupSsid,
                groupPassphrase,
                groupInterface,
                wifiProxyHost,
                port,
                wifiSecurity);
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
        wifiSecurity = "";
        groupFrequencyMhz = 0;
        wifiGroupClientCount = 0;
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
                wifiGroupCreationAccepted = true;
                status("createGroup accepted for " + WifiDirectBand.label(requestedWifiBand));
                requestGroupInfo();
            }

            @Override
            public void onFailure(int reason) {
                wifiGroupOperationInProgress = false;
                wifiGroupCreationAccepted = false;
                status("createGroup rejected for " + WifiDirectBand.label(requestedWifiBand)
                        + ": " + p2pReason(reason));
                if (retryAfterBusy && reason == WifiP2pManager.BUSY) {
                    recoverBusyP2pGroup();
                }
            }
        };
        try {
            wifiGroupOperationInProgress = true;
            wifiGroupCreationAccepted = false;
            if (WifiDirectBand.supportsSelection()) {
                wifiP2pManager.createGroup(wifiP2pChannel, buildWifiP2pConfig(), listener);
            } else {
                wifiP2pManager.createGroup(wifiP2pChannel, listener);
            }
        } catch (SecurityException ex) {
            wifiGroupOperationInProgress = false;
            wifiGroupCreationAccepted = false;
            status("Missing Wi-Fi Direct permission: " + ex.getMessage());
        } catch (RuntimeException ex) {
            wifiGroupOperationInProgress = false;
            wifiGroupCreationAccepted = false;
            status("createGroup failed: " + ex.getMessage());
        }
    }

    @TargetApi(29)
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
        if (!isOwnedWifiGroup(group)) {
            rejectUnownedWifiGroup();
            return;
        }
        acceptOwnedWifiGroup(group);
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
                    wifiGroupOwned = false;
                    wifiGroupCreationAccepted = false;
                    clearGroupInfo();
                    status("Previous Wi-Fi Direct group removed for band switch");
                    createGroupInternal(false);
                }

                @Override
                public void onFailure(int reason) {
                    wifiGroupOperationInProgress = false;
                    status("Band switch removeGroup rejected: " + p2pReason(reason));
                    requestGroupInfo();
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
                retryBusyGroupWithoutRemoval();
            });
        } catch (SecurityException ex) {
            status("Cannot recover Wi-Fi Direct busy state; permission missing");
        } catch (RuntimeException ex) {
            status("requestGroupInfo after busy failed: " + ex.getMessage());
            retryBusyGroupWithoutRemoval();
        }
    }

    private void retryBusyGroupWithoutRemoval() {
        status("Wi-Fi Direct busy; leaving unowned group state untouched before one retry");
        retryCreateGroupOnce();
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
            wifiGroupOwned = false;
            wifiGroupCreationAccepted = false;
            clearGroupInfo();
            return;
        }
        if (group == null) {
            closeWifiProxyServer();
            wifiGroupOwned = false;
            wifiGroupCreationAccepted = false;
            clearGroupInfo();
            status("Wi-Fi Direct group not active yet");
            updateNotification();
            return;
        }
        if (!isOwnedWifiGroup(group)) {
            rejectUnownedWifiGroup();
            return;
        }
        acceptOwnedWifiGroup(group);
        groupSsid = valueOrEmpty(group.getNetworkName());
        groupPassphrase = valueOrEmpty(group.getPassphrase());
        groupInterface = valueOrEmpty(group.getInterface());
        wifiSecurity = "WPA2-PSK";
        groupFrequencyMhz = groupFrequency(group);
        if (groupSsid.isEmpty()) {
            closeWifiProxyServer();
            status("Wi-Fi Direct group missing network name");
            return;
        }
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
                + " (requested " + WifiDirectBand.label(requestedWifiBand) + ")"
                + "; clients " + wifiGroupClientCount);
        status("Wi-Fi Direct proxy target: " + wifiTarget());
        updateNotification();
    }

    private int groupFrequency(WifiP2pGroup group) {
        return Build.VERSION.SDK_INT >= 29 ? group.getFrequency() : 0;
    }

    private boolean isOwnedWifiGroup(WifiP2pGroup group) {
        if (group == null) {
            return false;
        }
        String[] credentials = loadOrCreateGroupCredentials();
        return WifiDirectGroupOwnership.isOwned(
                group.isGroupOwner(),
                valueOrEmpty(group.getNetworkName()),
                valueOrEmpty(group.getPassphrase()),
                WifiDirectBand.supportsSelection(),
                credentials[0],
                credentials[1],
                wifiGroupCreationAccepted,
                loadLegacyOwnedGroupSsid());
    }

    private void acceptOwnedWifiGroup(WifiP2pGroup group) {
        wifiGroupOwned = true;
        wifiGroupClientCount = group.getClientList() == null ? 0 : group.getClientList().size();
        if (!WifiDirectBand.supportsSelection() && wifiGroupCreationAccepted) {
            rememberLegacyOwnedGroupSsid(valueOrEmpty(group.getNetworkName()));
        }
        wifiGroupCreationAccepted = false;
        if (lastError.startsWith("Cannot use active Wi-Fi Direct group because PdaLink does not own it")) {
            lastError = "";
        }
    }

    private void rejectUnownedWifiGroup() {
        closeWifiProxyServer();
        wifiGroupOwned = false;
        wifiGroupCreationAccepted = false;
        clearGroupInfo();
        status("Cannot use active Wi-Fi Direct group because PdaLink does not own it; left untouched");
        updateNotification();
    }

    private String loadLegacyOwnedGroupSsid() {
        return valueOrEmpty(getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .getString(PREF_WIFI_DIRECT_LEGACY_OWNED_SSID, ""));
    }

    private void rememberLegacyOwnedGroupSsid(String ssid) {
        if (ssid.isEmpty()) {
            return;
        }
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putString(PREF_WIFI_DIRECT_LEGACY_OWNED_SSID, ssid)
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
        return ssid.matches("^DIRECT-PD-PdaLink-[A-Za-z0-9]{6}$")
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
        intent.putExtra(ProxyActions.EXTRA_WIFI_GROUP_OWNED, wifiGroupOwned);
        intent.putExtra(ProxyActions.EXTRA_WIFI_GROUP_CLIENT_COUNT, wifiGroupClientCount);
        intent.putExtra(ProxyActions.EXTRA_WIFI_TRANSPORT, wifiTransportLabel());
        intent.putExtra(ProxyActions.EXTRA_WIFI_SECURITY, wifiSecurity);
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
        return "USB " + usbProxySummary() + "; " + wifiTransportLabel()
                + " " + wifiProxySummary();
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
        if (!wifiDirectEnabled && !localHotspotEnabled) {
            return primaryMode;
        }
        String wifiMode = wifiTransportLabel();
        if (currentMode == Mode.PRODUCT_USB) {
            return primaryMode + " + " + wifiMode;
        }
        return localHotspotEnabled
                ? ProxyActions.MODE_USB_LOCAL_HOTSPOT
                : ProxyActions.MODE_USB_WIFI_DIRECT;
    }

    private String wifiTransportLabel() {
        if (localHotspotEnabled) {
            return ProxyActions.MODE_LOCAL_HOTSPOT;
        }
        if (wifiDirectEnabled) {
            return ProxyActions.MODE_WIFI_DIRECT;
        }
        return "Wi-Fi";
    }

    private String linuxTarget() {
        if (wifiDirectEnabled || localHotspotEnabled) {
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
        return port <= 0 || wifiProxyHost.isEmpty()
                ? "not ready"
                : "http://" + wifiProxyHost + ":" + port;
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
        if (ProxyActions.ACTION_START_LOCAL_HOTSPOT.equals(action)) {
            return Mode.LOCAL_HOTSPOT;
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

    private void setLocalHotspotEnabled(boolean enabled) {
        localHotspotEnabled = enabled;
        if (!enabled) {
            localHotspotStartAuthorized = false;
        }
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
