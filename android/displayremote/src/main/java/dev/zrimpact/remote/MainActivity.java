package dev.zrimpact.remote;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.wifi.WifiNetworkSpecifier;
import android.os.Build;
import android.os.Bundle;
import android.text.method.ScrollingMovementMethod;
import android.view.HapticFeedbackConstants;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Random;

public final class MainActivity extends Activity {
    private static final int NEARBY_PERMISSION_REQUEST = 1001;
    private static final String ZERO_ADDRESS = "10.77.0.1";
    private static final int ZERO_PORT = 8787;
    private static final String PREFERENCES = "zr_display_remote";
    private static final String NETWORK_APPROVED = "network_approved";
    private static final String LOW_REFERENCE = "low_reference";
    private static final String HIGH_REFERENCE = "high_reference";
    private static final String ACTION_LOG = "action_log";
    private static final int SOCKET_TIMEOUT_MS = 5_000;
    private static final DateTimeFormatter LOG_TIME_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss", Locale.US);

    private final Random random = new SecureRandom();
    private final DisplayConnectionState connectionState =
            new DisplayConnectionState();
    private final DisplayNetworkPolicy reconnectPolicy =
            new DisplayNetworkPolicy();
    private final Runnable reconnectRunnable = () -> {
        reconnectScheduled = false;
        if (!isFinishing() && !isDestroyed()) {
            ensureDisplayNetwork();
        }
    };
    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback networkCallback;
    private Network displayNetwork;
    private ImpactDisplayState impactState = ImpactDisplayState.ready();
    private EditText line1View;
    private EditText line2View;
    private EditText lowReferenceView;
    private EditText highReferenceView;
    private TextView statusView;
    private TextView actionLogView;
    private Button defaultButton;
    private Button lowButton;
    private Button highButton;
    private Button zeroButton;
    private Button sendButton;
    private String pendingLine1;
    private String pendingLine2;
    private boolean networkRequestInFlight;
    private boolean networkApproved;
    private boolean connectionDefaultPending;
    private boolean reconnectScheduled;
    private boolean sending;
    private DisplayActionLog actionLog;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        connectivityManager =
                (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        networkApproved = getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getBoolean(NETWORK_APPROVED, false);
        line1View = findViewById(R.id.display_line1);
        line2View = findViewById(R.id.display_line2);
        lowReferenceView = findViewById(R.id.low_reference);
        highReferenceView = findViewById(R.id.high_reference);
        statusView = findViewById(R.id.connection_status);
        actionLogView = findViewById(R.id.action_log);
        defaultButton = findViewById(R.id.default_button);
        lowButton = findViewById(R.id.low_button);
        highButton = findViewById(R.id.high_button);
        zeroButton = findViewById(R.id.zero_button);
        sendButton = findViewById(R.id.send_button);

        actionLog = DisplayActionLog.restore(
                getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                        .getString(ACTION_LOG, ""));
        actionLogView.setMovementMethod(new ScrollingMovementMethod());
        renderActionLog();

        defaultButton.setOnClickListener(view -> {
            performButtonHaptic(view);
            sendGeneratedState(
                    getString(R.string.default_button),
                    ImpactDisplayState.ready(),
                    null);
        });
        lowButton.setOnClickListener(view -> {
            performButtonHaptic(view);
            sendLow();
        });
        highButton.setOnClickListener(view -> {
            performButtonHaptic(view);
            sendHigh();
        });
        zeroButton.setOnClickListener(view -> {
            performButtonHaptic(view);
            sendGeneratedState(
                    getString(R.string.zero_button),
                    impactState.nextZero(random),
                    null);
        });
        sendButton.setOnClickListener(view -> {
            performButtonHaptic(view);
            String line1 = line1View.getText().toString();
            String line2 = line2View.getText().toString();
            appendActionLog(
                    getString(R.string.send_button),
                    line1 + " / " + line2);
            queueCommand(line1, line2);
        });

        lowReferenceView.setText(getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(
                        LOW_REFERENCE,
                        String.format(
                                Locale.US,
                                "%.2f",
                                ImpactDisplayState.DEFAULT_LOW_REFERENCE)));
        highReferenceView.setText(getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(
                        HIGH_REFERENCE,
                        String.format(
                                Locale.US,
                                "%.2f",
                                ImpactDisplayState.DEFAULT_HIGH_REFERENCE)));
        applyState(impactState);
        setControlsEnabled(BuildConfig.DISPLAY_AP_CONFIGURED);
        if (!BuildConfig.DISPLAY_AP_CONFIGURED) {
            setStatus(R.string.status_not_configured, false);
        } else {
            ensureDisplayNetwork();
        }
    }

    private void sendLow() {
        String input = lowReferenceView.getText().toString().trim();
        final double reference;
        try {
            reference = Double.parseDouble(input);
        } catch (NumberFormatException exception) {
            appendActionLog(
                    getString(R.string.low_button),
                    getString(R.string.log_invalid_reference));
            setStatus(R.string.low_reference_error, false);
            return;
        }
        if (!ImpactDisplayState.isValidLowReference(reference)) {
            appendActionLog(
                    getString(R.string.low_button),
                    getString(R.string.log_invalid_reference));
            setStatus(R.string.low_reference_error, false);
            return;
        }
        String normalized = String.format(Locale.US, "%.2f", reference);
        lowReferenceView.setText(normalized);
        getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .edit()
                .putString(LOW_REFERENCE, normalized)
                .apply();
        sendGeneratedState(
                getString(R.string.low_button),
                impactState.nextLow(random, reference),
                normalized);
    }

    private void sendHigh() {
        String input = highReferenceView.getText().toString().trim();
        final double reference;
        try {
            reference = Double.parseDouble(input);
        } catch (NumberFormatException exception) {
            appendActionLog(
                    getString(R.string.high_button),
                    getString(R.string.log_invalid_reference));
            setStatus(R.string.high_reference_error, false);
            return;
        }
        if (!ImpactDisplayState.isValidHighReference(reference)) {
            appendActionLog(
                    getString(R.string.high_button),
                    getString(R.string.log_invalid_reference));
            setStatus(R.string.high_reference_error, false);
            return;
        }
        String normalized = String.format(Locale.US, "%.2f", reference);
        highReferenceView.setText(normalized);
        getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .edit()
                .putString(HIGH_REFERENCE, normalized)
                .apply();
        sendGeneratedState(
                getString(R.string.high_button),
                impactState.nextHigh(random, reference),
                normalized);
    }

    private void sendGeneratedState(
            String action,
            ImpactDisplayState state,
            String reference) {
        String detail = state.logValue();
        if (reference != null) {
            detail += " (ref " + reference + ")";
        }
        appendActionLog(action, detail);
        sendState(state);
    }

    private void performButtonHaptic(View view) {
        view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
    }

    private void appendActionLog(String action, String detail) {
        actionLog.append(
                LocalDateTime.now().format(LOG_TIME_FORMAT),
                action,
                detail);
        getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .edit()
                .putString(ACTION_LOG, actionLog.serialize())
                .apply();
        renderActionLog();
    }

    private void renderActionLog() {
        String text = actionLog.displayText();
        actionLogView.setText(text.isEmpty()
                ? getString(R.string.log_empty)
                : text);
        actionLogView.scrollTo(0, 0);
    }

    private void sendState(ImpactDisplayState state) {
        if (sending) {
            return;
        }
        impactState = state;
        applyState(state);
        queueCommand(state.line1(), state.line2());
    }

    private void applyState(ImpactDisplayState state) {
        line1View.setText(state.line1());
        line2View.setText(state.line2());
    }

    private void queueCommand(String line1, String line2) {
        if (sending || !BuildConfig.DISPLAY_AP_CONFIGURED) {
            return;
        }
        try {
            DisplayProtocol.requestJson(line1, line2);
        } catch (IllegalArgumentException exception) {
            setStatus(exception.getMessage(), false);
            return;
        }
        pendingLine1 = line1;
        pendingLine2 = line2;
        if (displayNetwork == null) {
            cancelScheduledReconnect();
            ensureDisplayNetwork();
            return;
        }
        sendPendingCommand();
    }

    private void ensureDisplayNetwork() {
        if (!BuildConfig.DISPLAY_AP_CONFIGURED
                || displayNetwork != null
                || networkRequestInFlight) {
            return;
        }
        if (!hasNearbyWifiPermission()) {
            setStatus(R.string.status_permission, false);
            requestPermissions(
                    new String[]{Manifest.permission.NEARBY_WIFI_DEVICES},
                    NEARBY_PERMISSION_REQUEST);
            return;
        }

        WifiNetworkSpecifier specifier = new WifiNetworkSpecifier.Builder()
                .setSsid(BuildConfig.DISPLAY_AP_SSID)
                .setWpa2Passphrase(BuildConfig.DISPLAY_AP_PSK)
                .build();
        NetworkRequest request = new NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .setNetworkSpecifier(specifier)
                .build();
        networkRequestInFlight = true;
        setStatus(R.string.status_connecting, true);
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                runOnUiThread(() -> {
                    if (!connectionState.onAvailable()) {
                        return;
                    }
                    displayNetwork = network;
                    cancelScheduledReconnect();
                    reconnectPolicy.onConnected();
                    if (!networkApproved) {
                        networkApproved = true;
                        getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                                .edit()
                                .putBoolean(NETWORK_APPROVED, true)
                                .apply();
                    }
                    setStatus(R.string.status_connected, true);
                    connectionDefaultPending = true;
                    sendPendingCommand();
                });
            }

            @Override
            public void onLost(Network network) {
                runOnUiThread(() -> {
                    if (network.equals(displayNetwork)) {
                        displayNetwork = null;
                        connectionState.onLost();
                        connectionDefaultPending = false;
                        resetNetworkRequest(this);
                        setStatus(R.string.status_lost, false);
                        scheduleReconnect();
                    }
                });
            }

            @Override
            public void onUnavailable() {
                runOnUiThread(() -> {
                    resetNetworkRequest(this);
                    setStatus(R.string.status_unavailable, false);
                    if (DisplayNetworkPolicy.shouldRetry(networkApproved)) {
                        scheduleReconnect();
                    }
                });
            }
        };
        try {
            connectivityManager.requestNetwork(
                    request,
                    networkCallback,
                    DisplayNetworkPolicy.timeoutMillis(networkApproved));
        } catch (RuntimeException exception) {
            networkRequestInFlight = false;
            networkCallback = null;
            setStatus(exception.getMessage(), false);
            if (DisplayNetworkPolicy.shouldRetry(networkApproved)) {
                scheduleReconnect();
            }
        }
    }

    private void scheduleReconnect() {
        if (reconnectScheduled
                || networkRequestInFlight
                || displayNetwork != null
                || !BuildConfig.DISPLAY_AP_CONFIGURED) {
            return;
        }
        long delayMillis = reconnectPolicy.nextReconnectDelayMillis();
        reconnectScheduled = true;
        setStatus(
                getString(
                        R.string.status_retrying,
                        Math.max(1, delayMillis / 1_000)),
                false);
        statusView.postDelayed(reconnectRunnable, delayMillis);
    }

    private void cancelScheduledReconnect() {
        if (!reconnectScheduled || statusView == null) {
            return;
        }
        statusView.removeCallbacks(reconnectRunnable);
        reconnectScheduled = false;
    }

    private void sendPendingCommand() {
        Network network = displayNetwork;
        if (network == null || sending) {
            return;
        }
        boolean automaticDefault = connectionDefaultPending;
        String line1;
        String line2;
        if (automaticDefault) {
            connectionDefaultPending = false;
            ImpactDisplayState ready = ImpactDisplayState.ready();
            line1 = ready.line1();
            line2 = ready.line2();
            if (pendingLine1 == null) {
                impactState = ready;
                applyState(ready);
            }
        } else {
            line1 = pendingLine1;
            line2 = pendingLine2;
            if (line1 == null || line2 == null) {
                return;
            }
            pendingLine1 = null;
            pendingLine2 = null;
        }
        sending = true;
        setControlsEnabled(false);
        setStatus(R.string.status_sending, true);
        new Thread(() -> {
            String result = send(network, line1, line2);
            runOnUiThread(() -> {
                sending = false;
                setControlsEnabled(BuildConfig.DISPLAY_AP_CONFIGURED);
                if (result == null) {
                    setStatus(
                            automaticDefault
                                    ? R.string.status_default_accepted
                                    : R.string.status_accepted,
                            true);
                } else {
                    setStatus(result, false);
                }
                sendPendingCommand();
            });
        }, "zr-display-send").start();
    }

    private String send(Network network, String line1, String line2) {
        try (Socket socket = network.getSocketFactory().createSocket()) {
            socket.connect(
                    new InetSocketAddress(ZERO_ADDRESS, ZERO_PORT),
                    SOCKET_TIMEOUT_MS);
            socket.setSoTimeout(SOCKET_TIMEOUT_MS);
            BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(
                    socket.getOutputStream(),
                    StandardCharsets.UTF_8));
            writer.write(DisplayProtocol.requestJson(line1, line2));
            writer.write('\n');
            writer.flush();
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                    socket.getInputStream(),
                    StandardCharsets.UTF_8));
            String response = reader.readLine();
            if (response == null) {
                return "Zero closed the connection without a response.";
            }
            JSONObject payload = new JSONObject(response);
            if (payload.optBoolean("ok", false)) {
                return null;
            }
            return payload.optString("error", "Zero rejected the display command.");
        } catch (Exception exception) {
            String detail = exception.getMessage();
            return detail == null || detail.isEmpty()
                    ? "Could not reach the Zero display server."
                    : "Could not reach Zero: " + detail;
        }
    }

    private boolean hasNearbyWifiPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                || checkSelfPermission(Manifest.permission.NEARBY_WIFI_DEVICES)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void resetNetworkRequest(
            ConnectivityManager.NetworkCallback expectedCallback) {
        if (networkCallback != expectedCallback) {
            return;
        }
        try {
            connectivityManager.unregisterNetworkCallback(expectedCallback);
        } catch (RuntimeException ignored) {
        }
        networkCallback = null;
        networkRequestInFlight = false;
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != NEARBY_PERMISSION_REQUEST) {
            return;
        }
        if (grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            ensureDisplayNetwork();
        } else {
            setStatus(R.string.status_permission, false);
        }
    }

    private void setControlsEnabled(boolean enabled) {
        boolean active = enabled && !sending;
        defaultButton.setEnabled(active);
        lowButton.setEnabled(active);
        highButton.setEnabled(active);
        zeroButton.setEnabled(active);
        sendButton.setEnabled(active);
        line1View.setEnabled(active);
        line2View.setEnabled(active);
        lowReferenceView.setEnabled(active);
        highReferenceView.setEnabled(active);
    }

    private void setStatus(int stringId, boolean positive) {
        setStatus(getString(stringId), positive);
    }

    private void setStatus(String text, boolean positive) {
        statusView.setText(text == null || text.isEmpty()
                ? getString(R.string.status_unavailable)
                : text);
        statusView.setTextColor(getColor(positive ? R.color.success : R.color.error));
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (BuildConfig.DISPLAY_AP_CONFIGURED
                && displayNetwork == null
                && !networkRequestInFlight) {
            cancelScheduledReconnect();
            ensureDisplayNetwork();
        }
    }

    @Override
    protected void onDestroy() {
        cancelScheduledReconnect();
        if (networkCallback != null) {
            resetNetworkRequest(networkCallback);
        }
        displayNetwork = null;
        connectionState.onLost();
        connectionDefaultPending = false;
        super.onDestroy();
    }
}
