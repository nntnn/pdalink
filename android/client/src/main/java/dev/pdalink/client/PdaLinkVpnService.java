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
    private Thread nativeThread;
    private PdaLinkConnectionUri.Bundle connection;
    private volatile boolean stopping;

    @Override
    public void onCreate() {
        super.onCreate();
        connectivityManager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            shutdown(getString(R.string.status_stopped), "");
            return START_NOT_STICKY;
        }
        if (!ACTION_START.equals(action)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (networkCallback != null || vpnInterface != null || nativeThread != null) {
            sendStatus(getString(R.string.status_error), getString(R.string.already_running));
            return START_NOT_STICKY;
        }
        try {
            connection = PdaLinkConnectionUri.parse(intent.getStringExtra(EXTRA_CONNECTION_URI));
        } catch (IllegalArgumentException exception) {
            sendStatus(getString(R.string.status_error), exception.getMessage());
            stopSelf();
            return START_NOT_STICKY;
        }
        stopping = false;
        startForeground(NOTIFICATION_ID, buildNotification(getString(R.string.waiting_for_wifi)));
        sendStatus(getString(R.string.status_starting), getString(R.string.waiting_for_wifi));
        requestWifiNetwork();
        return START_NOT_STICKY;
    }

    private void requestWifiNetwork() {
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
                mainHandler.post(() -> handleWifiAvailable(network));
            }

            @Override
            public void onLost(Network network) {
                mainHandler.post(() -> {
                    if (network.equals(wifiNetwork) && !stopping) {
                        shutdown(getString(R.string.status_error), getString(R.string.wifi_lost));
                    }
                });
            }

            @Override
            public void onUnavailable() {
                mainHandler.post(() -> shutdown(
                        getString(R.string.status_error),
                        getString(R.string.wifi_unavailable)));
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
        new Thread(() -> probeProxy(network), "pdalink-proxy-probe").start();
    }

    private void probeProxy(Network network) {
        String failure = null;
        HttpURLConnection connectionHandle = null;
        try {
            URL pacUrl = new URL(connection.proxyUrl + "/proxy.pac");
            connectionHandle = (HttpURLConnection) network.openConnection(pacUrl);
            connectionHandle.setConnectTimeout(8_000);
            connectionHandle.setReadTimeout(8_000);
            connectionHandle.setUseCaches(false);
            int status = connectionHandle.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) {
                throw new IOException("PAC HTTP status " + status);
            }
            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    connectionHandle.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null && body.length() < 4096) {
                    body.append(line).append('\n');
                }
            }
            String expected = "PROXY " + connection.proxyHost + ":" + connection.proxyPort;
            if (body.indexOf(expected) < 0) {
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
            if (stopping || !network.equals(wifiNetwork)) {
                return;
            }
            if (result != null) {
                shutdown(getString(R.string.status_error),
                        getString(R.string.proxy_unreachable_format, result));
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
                if (!stopping) {
                    shutdown(
                            getString(R.string.status_error),
                            getString(R.string.tunnel_stopped_format, result));
                }
            });
        }, "pdalink-tun2proxy");
        nativeThread.start();
    }

    private void shutdown(String state, String detail) {
        if (stopping) {
            return;
        }
        stopping = true;
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
        sendStatus(state, detail);
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void sendStatus(String state, String detail) {
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
