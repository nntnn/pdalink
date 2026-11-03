package dev.pdalink.client;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.net.VpnService;
import android.net.wifi.WifiManager;
import android.net.wifi.ScanResult;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;

import dev.pdalink.shared.PdaLinkConnectionUri;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {
    private static final int REQUEST_PERMISSIONS = 100;
    private static final int REQUEST_VPN = 101;

    private EditText connectionCodeView;
    private TextView statusView;
    private TextView networkView;
    private TextView transportView;
    private TextView proxyView;
    private View connectionDetails;
    private Button connectButton;
    private Button disconnectButton;
    private PdaLinkConnectionUri.Bundle connection;

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String state = intent.getStringExtra(PdaLinkVpnService.EXTRA_STATE);
            String detail = intent.getStringExtra(PdaLinkVpnService.EXTRA_DETAIL);
            renderStatus(state, detail);
            if (getString(R.string.status_connected).equals(state) && connection != null
                    && connection.toUri().equals(intent.getStringExtra(PdaLinkVpnService.EXTRA_CONNECTION_URI))) {
                learnVisibleBssid();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        setContentView(R.layout.activity_client);

        connectionCodeView = findViewById(R.id.connection_code);
        statusView = findViewById(R.id.connection_status);
        networkView = findViewById(R.id.connection_network);
        transportView = findViewById(R.id.connection_transport);
        proxyView = findViewById(R.id.connection_proxy);
        connectionDetails = findViewById(R.id.connection_details);
        connectButton = findViewById(R.id.connect_button);
        disconnectButton = findViewById(R.id.disconnect_button);

        findViewById(R.id.scan_button).setOnClickListener(view -> scanQr());
        findViewById(R.id.paste_button).setOnClickListener(view -> pasteConnection());
        findViewById(R.id.use_code_button).setOnClickListener(view -> parseConnection(
                connectionCodeView.getText().toString()));
        connectButton.setOnClickListener(view -> connect());
        disconnectButton.setOnClickListener(view -> disconnect());
        renderStatus(getString(R.string.status_stopped), "");
        String saved = getPreferences(MODE_PRIVATE).getString("profile", "");
        if (!saved.isEmpty()) parseConnection(saved, false);
    }

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(PdaLinkVpnService.ACTION_STATUS);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, filter);
        }
        // The VPN lives in its own process; query it after registering so reopening
        // the activity retains the working Disconnect control during recovery.
        startService(new Intent(this, PdaLinkVpnService.class)
                .setAction(PdaLinkVpnService.ACTION_QUERY));
    }

    @Override
    protected void onStop() {
        unregisterReceiver(statusReceiver);
        super.onStop();
    }

    private void scanQr() {
        new IntentIntegrator(this)
                .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                .setPrompt(getString(R.string.scan_prompt))
                .setBeepEnabled(false)
                .setOrientationLocked(false)
                .initiateScan();
    }

    private void pasteConnection() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard == null || !clipboard.hasPrimaryClip()) {
            showError(getString(R.string.clipboard_empty));
            return;
        }
        ClipData clip = clipboard.getPrimaryClip();
        CharSequence value = clip == null || clip.getItemCount() == 0
                ? null : clip.getItemAt(0).coerceToText(this);
        parseConnection(value == null ? "" : value.toString());
    }

    private void parseConnection(String raw) {
        parseConnection(raw, true);
    }

    private void parseConnection(String raw, boolean imported) {
        try {
            connection = PdaLinkConnectionUri.parse(raw);
        } catch (IllegalArgumentException exception) {
            showError(exception.getMessage());
            return;
        }
        getPreferences(MODE_PRIVATE).edit().putString("profile", connection.toUri()).commit();
        if (imported) {
            startService(new Intent(this, PdaLinkVpnService.class)
                    .setAction(PdaLinkVpnService.ACTION_IMPORT)
                    .putExtra(PdaLinkVpnService.EXTRA_CONNECTION_URI, connection.toUri()));
        }
        if (imported) learnVisibleBssid();
        connectionCodeView.setText("");
        connectionDetails.setVisibility(View.VISIBLE);
        networkView.setText(connection.ssid);
        transportView.setText(connection.transport);
        proxyView.setText(connection.proxyUrl);
        connectButton.setEnabled(true);
        connectButton.setAlpha(1f);
        renderStatus(getString(R.string.status_ready), "");
    }

    // Read location-sensitive scans only from the visible activity. The service
    // remains the sole prefs writer because it runs in a separate process.
    @SuppressWarnings("deprecation")
    private void learnVisibleBssid() {
        if (connection == null || checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) return;
        try {
            WifiManager wifi = getApplicationContext().getSystemService(WifiManager.class);
            if (wifi == null) return;
            List<BssidSelection.Candidate> candidates = new ArrayList<>();
            for (ScanResult scan : wifi.getScanResults()) {
                candidates.add(new BssidSelection.Candidate(scan.SSID, scan.BSSID,
                        scan.capabilities, scan.level));
            }
            String bssid = BssidSelection.select(connection.ssid, connection.security, candidates);
            if (bssid != null) startService(new Intent(this, PdaLinkVpnService.class)
                    .setAction(PdaLinkVpnService.ACTION_LEARN_BSSID)
                    .putExtra(PdaLinkVpnService.EXTRA_CONNECTION_URI, connection.toUri())
                    .putExtra("bssid", bssid));
        } catch (SecurityException ignored) {
            // Location services or permission can be revoked while foregrounded.
        }
    }

    private void connect() {
        if (connection == null && !connectionCodeView.getText().toString().trim().isEmpty()) {
            parseConnection(connectionCodeView.getText().toString());
        }
        if (connection == null) {
            showError(getString(R.string.connection_required));
            return;
        }
        List<String> missing = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33) {
            addIfMissing(missing, Manifest.permission.NEARBY_WIFI_DEVICES);
            addIfMissing(missing, Manifest.permission.POST_NOTIFICATIONS);
        }
        // Learning the AP BSSID requires location permission even on Android 13+.
        addIfMissing(missing, Manifest.permission.ACCESS_COARSE_LOCATION);
        addIfMissing(missing, Manifest.permission.ACCESS_FINE_LOCATION);
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), REQUEST_PERMISSIONS);
            return;
        }
        requestVpnPermission();
    }

    private void addIfMissing(List<String> missing, String permission) {
        if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            missing.add(permission);
        }
    }

    private void requestVpnPermission() {
        Intent approval = VpnService.prepare(this);
        if (approval == null) {
            startVpn();
        } else {
            startActivityForResult(approval, REQUEST_VPN);
        }
    }

    private void startVpn() {
        Intent intent = new Intent(this, PdaLinkVpnService.class)
                .setAction(PdaLinkVpnService.ACTION_START)
                .putExtra(PdaLinkVpnService.EXTRA_CONNECTION_URI, connection.toUri());
        startForegroundService(intent);
        renderStatus(getString(R.string.status_starting), getString(R.string.waiting_for_wifi));
    }

    private void disconnect() {
        Intent intent = new Intent(this, PdaLinkVpnService.class)
                .setAction(PdaLinkVpnService.ACTION_STOP);
        startService(intent);
        renderStatus(getString(R.string.status_stopped), "");
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        IntentResult scan = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (scan != null) {
            if (scan.getContents() != null) {
                parseConnection(scan.getContents());
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_VPN) {
            if (resultCode == RESULT_OK) {
                startVpn();
            } else {
                showError(getString(R.string.vpn_permission_denied));
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != REQUEST_PERMISSIONS) {
            return;
        }
        for (int result : results) {
            if (result != PackageManager.PERMISSION_GRANTED) {
                showError(getString(R.string.wifi_permission_denied));
                return;
            }
        }
        requestVpnPermission();
    }

    private void renderStatus(String state, String detail) {
        String safeState = state == null || state.isEmpty() ? getString(R.string.status_stopped) : state;
        statusView.setText(detail == null || detail.isEmpty()
                ? safeState : getString(R.string.status_detail_format, safeState, detail));
        boolean active = getString(R.string.status_connected).equals(safeState)
                || getString(R.string.status_starting).equals(safeState);
        disconnectButton.setEnabled(active);
        disconnectButton.setAlpha(active ? 1f : 0.45f);
    }

    private void showError(String message) {
        renderStatus(getString(R.string.status_error), message == null ? "" : message);
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }
}
