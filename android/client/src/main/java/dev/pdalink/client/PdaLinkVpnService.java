package dev.pdalink.client;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.VpnService;
import android.net.wifi.WifiNetworkSpecifier;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.ParcelFileDescriptor;

import com.github.shadowsocks.bg.Tun2proxy;

import dev.pdalink.shared.PdaLinkConnectionUri;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class PdaLinkVpnService extends VpnService {
    public static final String ACTION_START = "dev.pdalink.client.START";
    public static final String ACTION_STOP = "dev.pdalink.client.STOP";
    public static final String ACTION_QUERY = "dev.pdalink.client.QUERY";
    public static final String ACTION_STATUS = "dev.pdalink.client.STATUS";
    public static final String EXTRA_CONNECTION_URI = "connection_uri";
    public static final String EXTRA_STATE = "state";
    public static final String EXTRA_DETAIL = "detail";

    private static final String CHANNEL_ID = "pdalink-client-vpn";
    private static final int NOTIFICATION_ID = 41;
    private static final int MTU = 1500;
    private static final int WIFI_TIMEOUT_MS = 60_000;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback networkCallback;
    private Network wifiNetwork;
    private ParcelFileDescriptor vpnInterface;
    // JNI owns a process-global engine. Retain its worker across Service instances
    // so a quick disconnect/start cannot run two native engines concurrently.
    private static Thread nativeThread;
    private PdaLinkConnectionUri.Bundle connection;
    private volatile boolean stopping;
    private int generation;
    private int retryAttempt;
    private String lastState;
    private String lastDetail = "";
    private final Runnable retry = () -> {
        if (stopping) return;
        if (nativeThread != null) {
            Tun2proxy.stop();
            mainHandler.postDelayed(this.retry, 1000L);
        } else {
            requestWifiNetwork();
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        connectivityManager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : intent.getAction();
        if (ACTION_QUERY.equals(action)) {
            sendStatus(lastState == null ? getString(R.string.status_stopped) : lastState, lastDetail);
            if (connection == null || stopping) stopSelf();
            return connection != null && !stopping ? START_STICKY : START_NOT_STICKY;
        }
        if (ACTION_STOP.equals(action)) {
            getSharedPreferences("vpn", MODE_PRIVATE).edit().putBoolean("enabled", false).commit();
            shutdown(getString(R.string.status_stopped), "");
            return START_NOT_STICKY;
        }
        boolean savedEnabled = getSharedPreferences("vpn", MODE_PRIVATE).getBoolean("enabled", false);
        if (!ACTION_START.equals(action) && !savedEnabled) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!stopping && connection != null) {
            sendStatus(lastState, lastDetail);
            return START_STICKY;
        }
        try {
            String raw = intent == null ? null : intent.getStringExtra(EXTRA_CONNECTION_URI);
            if (raw == null) raw = getSharedPreferences("vpn", MODE_PRIVATE).getString("profile", "");
            connection = PdaLinkConnectionUri.parse(raw);
            if (VpnService.prepare(this) != null) {
                stopSelf(); // Approval must be requested by the visible activity.
                return START_NOT_STICKY;
            }
            getSharedPreferences("vpn", MODE_PRIVATE).edit()
                    .putString("profile", connection.toUri()).putBoolean("enabled", true).commit();
        } catch (IllegalArgumentException exception) {
            sendStatus(getString(R.string.status_error), exception.getMessage());
            stopSelf();
            return START_NOT_STICKY;
        }
        stopping = false;
        startForeground(NOTIFICATION_ID, buildNotification(getString(R.string.waiting_for_wifi)));
        sendStatus(getString(R.string.status_starting), getString(R.string.waiting_for_wifi));
        mainHandler.removeCallbacks(retry);
        retry.run();
        return START_STICKY;
    }

    private void requestWifiNetwork() {
        final int requestGeneration = ++generation;
        WifiNetworkSpecifier.Builder wifi = new WifiNetworkSpecifier.Builder()
                .setSsid(connection.ssid);
        if (PdaLinkConnectionUri.SECURITY_WPA3.equals(connection.security)) {
            wifi.setWpa3Passphrase(connection.password);
        } else {
            wifi.setWpa2Passphrase(connection.password);
        }
        NetworkRequest request = new NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .setNetworkSpecifier(wifi.build())
                .build();
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                mainHandler.post(() -> {
                    if (requestGeneration == generation) handleWifiAvailable(network);
                });
            }

            @Override
            public void onLost(Network network) {
                mainHandler.post(() -> {
                    if (requestGeneration == generation && network.equals(wifiNetwork) && !stopping) {
                        reconnect(getString(R.string.wifi_lost));
                    }
                });
            }

            @Override
            public void onUnavailable() {
                mainHandler.post(() -> {
                    if (requestGeneration == generation) reconnect(getString(R.string.wifi_unavailable));
                });
            }
        };
        try {
            connectivityManager.requestNetwork(request, networkCallback, WIFI_TIMEOUT_MS);
        } catch (RuntimeException exception) {
            shutdown(getString(R.string.status_error), getString(R.string.wifi_request_failed));
        }
    }

    private void handleWifiAvailable(Network network) {
        if (stopping || wifiNetwork != null) {
            return;
        }
        wifiNetwork = network;
        if (!connectivityManager.bindProcessToNetwork(network)) {
            shutdown(getString(R.string.status_error), getString(R.string.wifi_bind_failed));
            return;
        }
        setUnderlyingNetworks(new Network[]{network});
        sendStatus(getString(R.string.status_starting), getString(R.string.checking_proxy));
        final int probeGeneration = generation;
        new Thread(() -> probeProxy(network, probeGeneration), "pdalink-proxy-probe").start();
    }

    private void probeProxy(Network network, int probeGeneration) {
        String failure = null;
        HttpURLConnection connectionHandle = null;
        try {
            URL pacUrl = new URL(connection.proxyUrl + "/proxy.pac");
            connectionHandle = (HttpURLConnection) network.openConnection(pacUrl);
            connectionHandle.setConnectTimeout(8_000);
            connectionHandle.setReadTimeout(8_000);
            connectionHandle.setUseCaches(false);
            connectionHandle.setInstanceFollowRedirects(false);
            int status = connectionHandle.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) {
                throw new IOException("PAC HTTP status " + status);
            }
            String body;
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    connectionHandle.getInputStream(), StandardCharsets.UTF_8))) {
                body = ProxyPacPolicy.read(reader);
            }
            if (!ProxyPacPolicy.matches(body, connection.proxyHost, connection.proxyPort)) {
                throw new IOException("PAC endpoint did not match the QR");
            }
        } catch (IOException exception) {
            failure = exception.getMessage();
        } finally {
            if (connectionHandle != null) {
                connectionHandle.disconnect();
            }
        }
        String result = failure;
        mainHandler.post(() -> {
            if (stopping || probeGeneration != generation || !network.equals(wifiNetwork)) {
                return;
            }
            if (result != null) {
                reconnect(getString(R.string.proxy_unreachable_format, result));
            } else {
                establishTunnel();
            }
        });
    }

    private void establishTunnel() {
        Builder builder = new Builder()
                .setSession(getString(R.string.vpn_session))
                .setMtu(MTU)
                .addAddress("10.66.0.2", 24)
                .addRoute("0.0.0.0", 0)
                .addDnsServer("198.18.0.1")
                .setBlocking(true);
        try {
            builder.addDisallowedApplication(getPackageName());
        } catch (PackageManager.NameNotFoundException exception) {
            shutdown(getString(R.string.status_error), getString(R.string.vpn_setup_failed));
            return;
        }
        vpnInterface = builder.establish();
        if (vpnInterface == null) {
            shutdown(getString(R.string.status_error), getString(R.string.vpn_setup_failed));
            return;
        }
        retryAttempt = 0;
        final int tunnelGeneration = generation;
        int tunFd = vpnInterface.getFd();
        String arguments = "tun2proxy"
                + " --tun-fd " + tunFd
                + " --close-fd-on-drop false"
                + " --proxy " + connection.proxyUrl
                + " --dns virtual"
                + " --dns-addr 8.8.8.8"
                + " --verbosity info";
        sendStatus(getString(R.string.status_connected), connection.ssid);
        updateNotification(getString(R.string.status_connected));
        nativeThread = new Thread(() -> {
            int result = Tun2proxy.run(arguments, (char) MTU);
            mainHandler.post(() -> {
                nativeThread = null;
                if (!stopping && tunnelGeneration == generation) {
                    reconnect(getString(R.string.tunnel_stopped_format, result));
                }
            });
        }, "pdalink-tun2proxy");
        nativeThread.start();
    }

    private void reconnect(String detail) {
        if (stopping) return;
        mainHandler.removeCallbacks(retry);
        releaseNetwork();
        sendStatus(getString(R.string.status_starting), detail);
        updateNotification(getString(R.string.waiting_for_wifi));
        mainHandler.postDelayed(retry, ReconnectPolicy.delayMillis(retryAttempt++));
    }

    private void releaseNetwork() {
        generation++; // Invalidates callbacks and probes from the previous request.
        if (nativeThread != null) {
            Tun2proxy.stop();
        }
        if (vpnInterface != null) {
            try {
                vpnInterface.close();
            } catch (IOException ignored) {
            }
            vpnInterface = null;
        }
        if (networkCallback != null) {
            try {
                connectivityManager.unregisterNetworkCallback(networkCallback);
            } catch (RuntimeException ignored) {
            }
            networkCallback = null;
        }
        wifiNetwork = null;
        connectivityManager.bindProcessToNetwork(null);
        setUnderlyingNetworks(null);
    }

    private void shutdown(String state, String detail) {
        if (stopping) {
            return;
        }
        stopping = true;
        mainHandler.removeCallbacks(retry);
        releaseNetwork();
        sendStatus(state, detail);
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void sendStatus(String state, String detail) {
        lastState = state;
        lastDetail = detail;
        Intent status = new Intent(ACTION_STATUS)
                .setPackage(getPackageName())
                .putExtra(EXTRA_STATE, state)
                .putExtra(EXTRA_DETAIL, detail == null ? "" : detail);
        sendBroadcast(status);
    }

    private void createNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        manager.createNotificationChannel(new NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel),
                NotificationManager.IMPORTANCE_LOW));
    }

    private Notification buildNotification(String detail) {
        Intent open = new Intent(this, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(
                this,
                0,
                open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_client)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(detail)
                .setContentIntent(pending)
                .setOngoing(true)
                .build();
    }

    private void updateNotification(String detail) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify(NOTIFICATION_ID, buildNotification(detail));
        }
    }

    @Override
    public void onRevoke() {
        getSharedPreferences("vpn", MODE_PRIVATE).edit().putBoolean("enabled", false).commit();
        shutdown(getString(R.string.status_stopped), getString(R.string.vpn_revoked));
        super.onRevoke();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return super.onBind(intent);
    }

    @Override
    public void onDestroy() {
        if (!stopping) {
            shutdown(getString(R.string.status_stopped), "");
        }
        super.onDestroy();
    }
}
