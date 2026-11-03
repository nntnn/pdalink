package dev.pdalink.prototype;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Insets;
import android.hardware.usb.UsbAccessory;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.EncodeHintType;
import com.google.zxing.WriterException;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.qrcode.QRCodeWriter;

import dev.pdalink.shared.PdaLinkConnectionUri;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.EnumMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public class MainActivity extends Activity {
    private static final int PERMISSION_REQUEST = 100;
    private static final int MAX_LOG_CHARS = 16 * 1024;
    private static final String ACTION_PRODUCT_USB_PERMISSION = "dev.pdalink.prototype.PRODUCT_USB_PERMISSION";
    private static final int PAGE_OVERVIEW = 0;
    private static final int PAGE_CONNECTIONS = 1;
    private static final int PAGE_DIAGNOSTICS = 2;
    private static final int PAGE_CLIENTS = 3;

    private static final class ActiveSessionRow {
        final ProxySessionSnapshot session;
        final TextView metadataView;

        ActiveSessionRow(ProxySessionSnapshot session, TextView metadataView) {
            this.session = session;
            this.metadataView = metadataView;
        }
    }

    private TextView summaryView;
    private TextView logView;
    private ScrollView logScroller;
    private RadioGroup wifiBandGroup;
    private RadioGroup usbModeGroup;
    private RadioGroup wifiModeGroup;
    private View wifiBandContainer;
    private View pageOverview;
    private View pageConnections;
    private View pageDiagnostics;
    private View pageClients;
    private TextView navOverview;
    private TextView navConnections;
    private TextView navDiagnostics;
    private TextView overviewStatusPill;
    private TextView overviewModeTitle;
    private TextView overviewEgress;
    private View errorBanner;
    private TextView errorBannerText;
    private TextView usbStatusView;
    private TextView usbModeView;
    private TextView usbTargetView;
    private Button copyUsbTargetButton;
    private TextView productUsbStatusView;
    private TextView wifiStatusView;
    private TextView wifiInactiveHint;
    private View wifiDetails;
    private TextView wifiTransportView;
    private TextView wifiSsidView;
    private TextView wifiPasswordView;
    private TextView wifiTargetView;
    private TextView wifiMetadataView;
    private Button revealWifiPasswordButton;
    private Button copyWifiSsidButton;
    private Button copyWifiPasswordButton;
    private Button copyWifiTargetButton;
    private Button showConnectionQrButton;
    private Button stopWifiButton;
    private TextView metricConnectionsView;
    private TextView metricTrafficView;
    private TextView metricEgressView;
    private View metricClientsButton;
    private TextView clientsSummaryView;
    private TextView sessionsTruncatedNote;
    private TextView activeSessionsTitle;
    private TextView recentSessionsTitle;
    private View activeSessionsEmpty;
    private View recentSessionsEmpty;
    private LinearLayout activeSessionsContainer;
    private LinearLayout recentSessionsContainer;
    private String selectedWifiBand = WifiDirectBand.AUTO;
    private String startModeAfterPermission;
    private UsbManager usbManager;
    private UsbAccessory pendingProductAccessory;
    private String pendingTrustFingerprint = "";
    private ProxyUiState currentUiState = new ProxyUiState.Builder().build();
    private boolean passwordVisible;
    private boolean syncingSelections;
    private boolean usbSelectionDirty;
    private boolean wifiSelectionDirty;
    private boolean sessionsTruncated;
    private int currentPage = PAGE_OVERVIEW;
    private List<ProxySessionSnapshot> proxySessions = new ArrayList<>();
    private final List<ActiveSessionRow> activeSessionRows = new ArrayList<>();
    private final Handler uiHandler = new Handler(Looper.getMainLooper());
    private final Runnable clientDurationTicker = new Runnable() {
        @Override
        public void run() {
            if (currentPage != PAGE_CLIENTS) {
                return;
            }
            refreshActiveSessionDurations();
            uiHandler.postDelayed(this, 1_000L);
        }
    };

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
        appendStatus("Ready. USB can run with one Wi-Fi transport.");
        handleProductUsbIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleProductUsbIntent(intent);
    }

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
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
        sendServiceAction(ProxyActions.ACTION_REFRESH);
        startClientDurationTicker();
    }

    @Override
    protected void onStop() {
        passwordVisible = false;
        renderPassword(currentUiState);
        uiHandler.removeCallbacks(clientDurationTicker);
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
        setContentView(R.layout.activity_main);

        View root = findViewById(R.id.screen_root);
        applyWindowInsets(root);
        configureSystemBars();

        pageOverview = findViewById(R.id.page_overview);
        pageConnections = findViewById(R.id.page_connections);
        pageDiagnostics = findViewById(R.id.page_diagnostics);
        pageClients = findViewById(R.id.page_clients);
        navOverview = findViewById(R.id.nav_overview);
        navConnections = findViewById(R.id.nav_connections);
        navDiagnostics = findViewById(R.id.nav_diagnostics);

        overviewStatusPill = findViewById(R.id.overview_status_pill);
        overviewModeTitle = findViewById(R.id.overview_mode_title);
        overviewEgress = findViewById(R.id.overview_egress);
        errorBanner = findViewById(R.id.error_banner);
        errorBannerText = findViewById(R.id.error_banner_text);
        usbStatusView = findViewById(R.id.usb_status);
        usbModeView = findViewById(R.id.usb_mode);
        usbTargetView = findViewById(R.id.usb_target);
        copyUsbTargetButton = findViewById(R.id.copy_usb_target_button);
        productUsbStatusView = findViewById(R.id.product_usb_status);
        wifiStatusView = findViewById(R.id.wifi_status);
        wifiInactiveHint = findViewById(R.id.wifi_inactive_hint);
        wifiDetails = findViewById(R.id.wifi_details);
        wifiTransportView = findViewById(R.id.wifi_transport);
        wifiSsidView = findViewById(R.id.wifi_ssid);
        wifiPasswordView = findViewById(R.id.wifi_password);
        wifiTargetView = findViewById(R.id.wifi_target);
        wifiMetadataView = findViewById(R.id.wifi_metadata);
        revealWifiPasswordButton = findViewById(R.id.reveal_wifi_password_button);
        copyWifiSsidButton = findViewById(R.id.copy_wifi_ssid_button);
        copyWifiPasswordButton = findViewById(R.id.copy_wifi_password_button);
        copyWifiTargetButton = findViewById(R.id.copy_wifi_target_button);
        showConnectionQrButton = findViewById(R.id.show_connection_qr_button);
        stopWifiButton = findViewById(R.id.stop_wifi_button);
        metricConnectionsView = findViewById(R.id.metric_connections_value);
        metricTrafficView = findViewById(R.id.metric_traffic_value);
        metricEgressView = findViewById(R.id.metric_egress_value);
        metricClientsButton = findViewById(R.id.metric_clients_button);
        clientsSummaryView = findViewById(R.id.clients_summary);
        sessionsTruncatedNote = findViewById(R.id.sessions_truncated_note);
        activeSessionsTitle = findViewById(R.id.active_sessions_title);
        recentSessionsTitle = findViewById(R.id.recent_sessions_title);
        activeSessionsEmpty = findViewById(R.id.active_sessions_empty);
        recentSessionsEmpty = findViewById(R.id.recent_sessions_empty);
        activeSessionsContainer = findViewById(R.id.active_sessions_container);
        recentSessionsContainer = findViewById(R.id.recent_sessions_container);
        summaryView = findViewById(R.id.diagnostic_summary);
        summaryView.setMovementMethod(new ScrollingMovementMethod());
        logView = findViewById(R.id.log_view);
        logScroller = findViewById(R.id.log_scroller);

        usbModeGroup = findViewById(R.id.usb_mode_group);
        wifiModeGroup = findViewById(R.id.wifi_mode_group);
        wifiBandGroup = findViewById(R.id.wifi_band_group);
        wifiBandContainer = findViewById(R.id.wifi_band_container);

        configureBandControls();
        configureSelectionControls();
        configureNavigation();
        configureActions();

        renderSnapshot(currentUiState);
        showPage(PAGE_OVERVIEW);
    }

    private void configureBandControls() {
        syncingSelections = true;
        if (WifiDirectBand.BAND_2_GHZ.equals(selectedWifiBand)) {
            wifiBandGroup.check(R.id.wifi_band_24);
        } else if (WifiDirectBand.BAND_5_GHZ.equals(selectedWifiBand)) {
            wifiBandGroup.check(R.id.wifi_band_5);
        } else {
            wifiBandGroup.check(R.id.wifi_band_automatic);
        }
        syncingSelections = false;

        boolean selectionSupported = WifiDirectBand.supportsSelection();
        findViewById(R.id.wifi_band_24).setEnabled(selectionSupported);
        findViewById(R.id.wifi_band_5).setEnabled(selectionSupported);
        findViewById(R.id.wifi_band_legacy_hint).setVisibility(
                selectionSupported ? View.GONE : View.VISIBLE);

        wifiBandGroup.setOnCheckedChangeListener((group, checkedId) -> {
            if (syncingSelections) {
                return;
            }
            if (checkedId == R.id.wifi_band_24) {
                selectedWifiBand = WifiDirectBand.BAND_2_GHZ;
            } else if (checkedId == R.id.wifi_band_5) {
                selectedWifiBand = WifiDirectBand.BAND_5_GHZ;
            } else {
                selectedWifiBand = WifiDirectBand.AUTO;
            }
            selectedWifiBand = WifiDirectBand.save(this, selectedWifiBand);
            appendStatus("Wi-Fi Direct band selected: " + WifiDirectBand.label(selectedWifiBand)
                    + ". Apply Wi-Fi mode to use it.");
        });
    }

    private void configureSelectionControls() {
        usbModeGroup.setOnCheckedChangeListener((group, checkedId) -> {
            if (!syncingSelections) {
                usbSelectionDirty = true;
            }
        });
        wifiModeGroup.setOnCheckedChangeListener((group, checkedId) -> {
            wifiBandContainer.setVisibility(
                    checkedId == R.id.wifi_mode_direct ? View.VISIBLE : View.GONE);
            if (!syncingSelections) {
                wifiSelectionDirty = true;
            }
        });
    }

    private void configureNavigation() {
        navOverview.setOnClickListener(v -> showPage(PAGE_OVERVIEW));
        navConnections.setOnClickListener(v -> showPage(PAGE_CONNECTIONS));
        navDiagnostics.setOnClickListener(v -> showPage(PAGE_DIAGNOSTICS));
        metricClientsButton.setOnClickListener(v -> showPage(PAGE_CLIENTS));
        findViewById(R.id.clients_back_button).setOnClickListener(v -> showPage(PAGE_OVERVIEW));
        findViewById(R.id.configure_wifi_button).setOnClickListener(v -> showPage(PAGE_CONNECTIONS));
        findViewById(R.id.error_diagnostics_button).setOnClickListener(v -> showPage(PAGE_DIAGNOSTICS));
    }

    private void configureActions() {
        findViewById(R.id.refresh_button).setOnClickListener(
                v -> sendServiceAction(ProxyActions.ACTION_REFRESH));
        findViewById(R.id.stop_all_button).setOnClickListener(v -> confirmStopAll());
        stopWifiButton.setOnClickListener(v -> sendServiceAction(ProxyActions.ACTION_STOP_WIFI));

        findViewById(R.id.apply_usb_mode_button).setOnClickListener(v -> {
            usbSelectionDirty = false;
            if (usbModeGroup.getCheckedRadioButtonId() == R.id.usb_mode_product) {
                startProductUsb();
            } else {
                startUsb();
            }
            showPage(PAGE_OVERVIEW);
        });

        findViewById(R.id.apply_wifi_mode_button).setOnClickListener(v -> {
            wifiSelectionDirty = false;
            int selectedMode = wifiModeGroup.getCheckedRadioButtonId();
            if (selectedMode == R.id.wifi_mode_local_hotspot) {
                startLocalHotspot();
            } else if (selectedMode == R.id.wifi_mode_direct) {
                startWifiDirect();
            } else {
                sendServiceAction(ProxyActions.ACTION_STOP_WIFI);
            }
            showPage(PAGE_OVERVIEW);
        });

        copyUsbTargetButton.setOnClickListener(v -> copyText(
                getString(R.string.usb_target_label), currentUiState.usbTarget));
        copyWifiSsidButton.setOnClickListener(v -> copyText(
                getString(R.string.ssid_label), currentUiState.ssid));
        copyWifiPasswordButton.setOnClickListener(v -> copyText(
                getString(R.string.password_label), currentUiState.passphrase));
        copyWifiTargetButton.setOnClickListener(v -> copyText(
                getString(R.string.wifi_target_label), currentUiState.wifiTarget));
        showConnectionQrButton.setOnClickListener(v -> showConnectionQr());
        revealWifiPasswordButton.setOnClickListener(v -> {
            passwordVisible = !passwordVisible;
            renderPassword(currentUiState);
        });
    }

    private void showPage(int page) {
        if (page != PAGE_OVERVIEW && passwordVisible) {
            passwordVisible = false;
            renderPassword(currentUiState);
        }
        pageOverview.setVisibility(page == PAGE_OVERVIEW ? View.VISIBLE : View.GONE);
        pageConnections.setVisibility(page == PAGE_CONNECTIONS ? View.VISIBLE : View.GONE);
        pageDiagnostics.setVisibility(page == PAGE_DIAGNOSTICS ? View.VISIBLE : View.GONE);
        pageClients.setVisibility(page == PAGE_CLIENTS ? View.VISIBLE : View.GONE);
        currentPage = page;
        navOverview.setSelected(page == PAGE_OVERVIEW || page == PAGE_CLIENTS);
        navConnections.setSelected(page == PAGE_CONNECTIONS);
        navDiagnostics.setSelected(page == PAGE_DIAGNOSTICS);
        if (page == PAGE_CLIENTS) {
            renderClientDetails();
            startClientDurationTicker();
        } else {
            uiHandler.removeCallbacks(clientDurationTicker);
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (currentPage == PAGE_CLIENTS) {
            showPage(PAGE_OVERVIEW);
            return;
        }
        super.onBackPressed();
    }

    private void confirmStopAll() {
        new AlertDialog.Builder(this)
                .setTitle(R.string.stop_all_confirm_title)
                .setMessage(R.string.stop_all_confirm_message)
                .setPositiveButton(R.string.stop_all, (dialog, which) ->
                        sendServiceAction(ProxyActions.ACTION_STOP))
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    private void copyText(String label, String value) {
        if (value == null || value.isEmpty()) {
            return;
        }
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard == null) {
            return;
        }
        clipboard.setPrimaryClip(ClipData.newPlainText(label, value));
        Toast.makeText(this, getString(R.string.copied_format, label), Toast.LENGTH_SHORT).show();
    }

    private void showConnectionQr() {
        PdaLinkConnectionUri.Bundle connection;
        try {
            connection = PdaLinkConnectionUri.create(
                    currentUiState.isLocalHotspotActive()
                            ? PdaLinkConnectionUri.TRANSPORT_LOCAL_HOTSPOT
                            : PdaLinkConnectionUri.TRANSPORT_WIFI_DIRECT,
                    currentUiState.ssid,
                    currentUiState.wifiSecurity,
                    currentUiState.passphrase,
                    currentUiState.wifiTarget);
        } catch (IllegalArgumentException exception) {
            appendStatus("Connection QR unavailable: " + exception.getMessage() + ".");
            return;
        }

        View content = getLayoutInflater().inflate(R.layout.dialog_connection_qr, null);
        ImageView image = content.findViewById(R.id.connection_qr_image);
        TextView network = content.findViewById(R.id.connection_qr_network);
        network.setText(connection.ssid);
        try {
            image.setImageBitmap(createQrBitmap(connection.toUri(), 720));
        } catch (WriterException exception) {
            appendStatus("Connection QR generation failed.");
            return;
        }

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(R.string.connection_qr_title)
                .setView(content)
                .setPositiveButton(android.R.string.ok, null)
                .setNeutralButton(R.string.copy_connection_uri, (ignored, which) -> copyText(
                        getString(R.string.connection_uri_label), connection.toUri()))
                .create();
        dialog.setOnShowListener(ignored -> {
            if (dialog.getWindow() != null) {
                dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            }
        });
        dialog.show();
    }

    private Bitmap createQrBitmap(String value, int size) throws WriterException {
        Map<EncodeHintType, Object> hints = new EnumMap<>(EncodeHintType.class);
        hints.put(EncodeHintType.MARGIN, 1);
        BitMatrix matrix = new QRCodeWriter().encode(
                value,
                BarcodeFormat.QR_CODE,
                size,
                size,
                hints);
        int[] pixels = new int[size * size];
        for (int y = 0; y < size; y++) {
            int row = y * size;
            for (int x = 0; x < size; x++) {
                pixels[row + x] = matrix.get(x, y) ? 0xff000000 : 0xffffffff;
            }
        }
        Bitmap bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        bitmap.setPixels(pixels, 0, size, 0, 0, size, size);
        return bitmap;
    }

    private void applyWindowInsets(View root) {
        int initialLeft = root.getPaddingLeft();
        int initialTop = root.getPaddingTop();
        int initialRight = root.getPaddingRight();
        int initialBottom = root.getPaddingBottom();
        root.setOnApplyWindowInsetsListener((view, windowInsets) -> {
            int left;
            int top;
            int right;
            int bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = windowInsets.getInsets(WindowInsets.Type.systemBars());
                left = bars.left;
                top = bars.top;
                right = bars.right;
                bottom = bars.bottom;
            } else {
                left = windowInsets.getSystemWindowInsetLeft();
                top = windowInsets.getSystemWindowInsetTop();
                right = windowInsets.getSystemWindowInsetRight();
                bottom = windowInsets.getSystemWindowInsetBottom();
            }
            view.setPadding(
                    initialLeft + left,
                    initialTop + top,
                    initialRight + right,
                    initialBottom + bottom);
            return windowInsets;
        });
        root.requestApplyInsets();
    }

    private void configureSystemBars() {
        boolean night = (getResources().getConfiguration().uiMode
                & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        int flags = 0;
        if (!night) {
            flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= 26) {
                flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            }
        }
        getWindow().getDecorView().setSystemUiVisibility(flags);
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

    private void startLocalHotspot() {
        if (Build.VERSION.SDK_INT < 26) {
            appendStatus("Local Hotspot requires Android 8.0 or later.");
            return;
        }
        if (requestRuntimePermissions(ProxyActions.MODE_LOCAL_HOTSPOT)) {
            startModeAfterPermission = ProxyActions.MODE_LOCAL_HOTSPOT;
            appendStatus("Waiting for Local Hotspot permissions.");
            return;
        }
        sendServiceAction(ProxyActions.ACTION_START_LOCAL_HOTSPOT);
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
            if (ProxyActions.MODE_WIFI_DIRECT.equals(mode)
                    || ProxyActions.MODE_LOCAL_HOTSPOT.equals(mode)) {
                addIfMissing(missing, Manifest.permission.NEARBY_WIFI_DEVICES);
            }
        } else if (ProxyActions.MODE_WIFI_DIRECT.equals(mode)
                || ProxyActions.MODE_LOCAL_HOTSPOT.equals(mode)) {
            addIfMissing(missing, Manifest.permission.ACCESS_COARSE_LOCATION);
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
        if (getString(R.string.activity_empty).equals(existing)) {
            existing = "";
        }
        String combined = entry + existing;
        if (combined.length() > MAX_LOG_CHARS) {
            combined = combined.substring(0, MAX_LOG_CHARS);
        }
        logView.setText(combined);
        logScroller.post(() -> logScroller.fullScroll(ScrollView.FOCUS_UP));
        if (isActionableError(line)) {
            showActionableError(line);
        }
    }

    private boolean isActionableError(String line) {
        String normalized = line.toLowerCase(Locale.ROOT);
        return normalized.contains("denied")
                || normalized.contains("failed")
                || normalized.contains("unavailable")
                || normalized.contains("missing")
                || normalized.contains("cannot")
                || normalized.contains("requires")
                || normalized.contains("not trusted");
    }

    private void showActionableError(String line) {
        errorBanner.setVisibility(View.VISIBLE);
        errorBannerText.setText(line);
        overviewStatusPill.setText(R.string.status_attention);
        overviewStatusPill.setBackgroundResource(R.drawable.bg_status_attention);
        overviewStatusPill.setTextColor(getColor(R.color.warning));
    }

    private void updateSnapshot(Intent intent) {
        String proxy = intent.getStringExtra(ProxyActions.EXTRA_PROXY_STATUS);
        String usbProxy = intent.getStringExtra(ProxyActions.EXTRA_USB_PROXY_STATUS);
        String target = intent.getStringExtra(ProxyActions.EXTRA_LINUX_TARGET);
        String usbTarget = intent.getStringExtra(ProxyActions.EXTRA_USB_TARGET);
        proxySessions = readSessionSnapshots(intent);
        sessionsTruncated = intent.getBooleanExtra(ProxyActions.EXTRA_SESSIONS_TRUNCATED, false);
        ProxyUiState state = new ProxyUiState.Builder()
                .mode(intent.getStringExtra(ProxyActions.EXTRA_MODE))
                .usbProxy(usbProxy == null || usbProxy.isEmpty() ? proxy : usbProxy)
                .wifiProxy(intent.getStringExtra(ProxyActions.EXTRA_WIFI_PROXY_STATUS))
                .egress(intent.getStringExtra(ProxyActions.EXTRA_EGRESS))
                .ssid(intent.getStringExtra(ProxyActions.EXTRA_GROUP_SSID))
                .passphrase(intent.getStringExtra(ProxyActions.EXTRA_GROUP_PASSPHRASE))
                .iface(intent.getStringExtra(ProxyActions.EXTRA_GROUP_INTERFACE))
                .wifiTransport(intent.getStringExtra(ProxyActions.EXTRA_WIFI_TRANSPORT))
                .wifiSecurity(intent.getStringExtra(ProxyActions.EXTRA_WIFI_SECURITY))
                .wifiGroupOwned(intent.getBooleanExtra(ProxyActions.EXTRA_WIFI_GROUP_OWNED, false))
                .wifiGroupClientCount(intent.getIntExtra(ProxyActions.EXTRA_WIFI_GROUP_CLIENT_COUNT, 0))
                .wifiGroupClientAddress(
                        intent.getStringExtra(ProxyActions.EXTRA_WIFI_GROUP_CLIENT_ADDRESS))
                .requestedBand(intent.getStringExtra(ProxyActions.EXTRA_WIFI_BAND))
                .frequencyMhz(intent.getIntExtra(ProxyActions.EXTRA_WIFI_FREQUENCY_MHZ, 0))
                .usbTarget(usbTarget == null || usbTarget.isEmpty() ? target : usbTarget)
                .wifiTarget(intent.getStringExtra(ProxyActions.EXTRA_WIFI_TARGET))
                .lastRequest(intent.getStringExtra(ProxyActions.EXTRA_LAST_REQUEST))
                .lastError(intent.getStringExtra(ProxyActions.EXTRA_LAST_ERROR))
                .productUsbStatus(intent.getStringExtra(ProxyActions.EXTRA_PRODUCT_USB_STATUS))
                .proxyClientCount(intent.getIntExtra(ProxyActions.EXTRA_CLIENT_COUNT, 0))
                .upBytes(intent.getLongExtra(ProxyActions.EXTRA_TRAFFIC_UP_BYTES, 0L))
                .downBytes(intent.getLongExtra(ProxyActions.EXTRA_TRAFFIC_DOWN_BYTES, 0L))
                .build();
        renderSnapshot(state);
    }

    private void renderSnapshot(ProxyUiState state) {
        if (!state.passphrase.equals(currentUiState.passphrase)) {
            passwordVisible = false;
        }
        currentUiState = state;
        renderGlobalStatus(state);
        renderUsbCard(state);
        renderWifiCard(state);
        renderMetrics(state);
        if (currentPage == PAGE_CLIENTS) {
            renderClientDetails();
        }
        renderDiagnostics(state);
        syncConnectionSelections(state);
    }

    private void renderGlobalStatus(ProxyUiState state) {
        int label;
        int background;
        int color;
        if (state.tone() == ProxyUiState.Tone.ATTENTION) {
            label = R.string.status_attention;
            background = R.drawable.bg_status_attention;
            color = R.color.warning;
        } else if (state.tone() == ProxyUiState.Tone.ACTIVE) {
            label = R.string.status_active;
            background = R.drawable.bg_status_active;
            color = R.color.success;
        } else if (state.tone() == ProxyUiState.Tone.STARTING) {
            label = R.string.status_starting;
            background = R.drawable.bg_status_idle;
            color = R.color.primary;
        } else {
            label = R.string.status_idle;
            background = R.drawable.bg_status_idle;
            color = R.color.idle;
        }
        overviewStatusPill.setText(label);
        overviewStatusPill.setBackgroundResource(background);
        overviewStatusPill.setTextColor(getColor(color));
        overviewModeTitle.setText(state.mode);
        overviewEgress.setText(getString(R.string.egress_format, state.egress));
        findViewById(R.id.stop_all_button).setVisibility(
                state.tone() == ProxyUiState.Tone.IDLE ? View.GONE : View.VISIBLE);

        errorBanner.setVisibility(state.hasError() ? View.VISIBLE : View.GONE);
        errorBannerText.setText(state.hasError() ? state.lastError : "");
    }

    private void renderUsbCard(ProxyUiState state) {
        usbStatusView.setText(proxyStatusLabel(state.usbProxy));
        usbStatusView.setTextColor(getColor(
                state.usbProxy.toLowerCase(Locale.ROOT).contains("listening")
                        ? R.color.success : R.color.text_secondary));
        usbModeView.setText(state.isProductUsb() ? R.string.usb_product : R.string.usb_development);
        usbTargetView.setText(state.hasUsbTarget() ? state.usbTarget : getString(R.string.value_unavailable));
        setCopyButtonState(copyUsbTargetButton, state.hasUsbTarget());

        productUsbStatusView.setVisibility(state.isProductUsb() ? View.VISIBLE : View.GONE);
        if (state.isProductUsb()) {
            productUsbStatusView.setText(getString(
                    R.string.detail_product_usb, state.productUsbStatus));
        }
    }

    private void renderWifiCard(ProxyUiState state) {
        boolean active = state.isWifiActive();
        boolean listening = state.wifiProxy.toLowerCase(Locale.ROOT).contains("listening");
        wifiStatusView.setText(!active
                ? R.string.status_not_active
                : (listening ? R.string.status_active : R.string.status_starting));
        wifiStatusView.setTextColor(getColor(!active
                ? R.color.text_secondary
                : (listening ? R.color.success : R.color.primary)));
        wifiInactiveHint.setVisibility(active ? View.GONE : View.VISIBLE);
        wifiDetails.setVisibility(active ? View.VISIBLE : View.GONE);
        stopWifiButton.setVisibility(active ? View.VISIBLE : View.GONE);
        if (!active) {
            passwordVisible = false;
            return;
        }

        wifiTransportView.setText(state.wifiTransport);
        wifiSsidView.setText(state.ssid.isEmpty() ? getString(R.string.value_unavailable) : state.ssid);
        wifiTargetView.setText(state.hasWifiTarget()
                ? state.wifiTarget : getString(R.string.value_unavailable));
        wifiMetadataView.setText(getString(
                R.string.wifi_metadata_format,
                state.wifiSecurity,
                state.wifiClientSummary()));
        renderPassword(state);
        copyWifiSsidButton.setEnabled(!state.ssid.isEmpty());
        copyWifiSsidButton.setAlpha(state.ssid.isEmpty() ? 0.45f : 1f);
        copyWifiPasswordButton.setEnabled(!state.passphrase.isEmpty());
        copyWifiPasswordButton.setAlpha(state.passphrase.isEmpty() ? 0.45f : 1f);
        setCopyButtonState(copyWifiTargetButton, state.hasWifiTarget());
        setCopyButtonState(showConnectionQrButton,
                state.hasWifiCredentials()
                        && !state.passphrase.isEmpty()
                        && state.hasWifiTarget());
    }

    private void renderPassword(ProxyUiState state) {
        boolean available = !state.passphrase.isEmpty();
        wifiPasswordView.setText(available
                ? (passwordVisible ? state.passphrase : state.maskedPassphrase())
                : getString(R.string.value_unavailable));
        wifiPasswordView.setContentDescription(getString(
                passwordVisible
                        ? R.string.password_visible_description
                        : R.string.password_hidden_description));
        revealWifiPasswordButton.setEnabled(available);
        revealWifiPasswordButton.setAlpha(available ? 1f : 0.45f);
        revealWifiPasswordButton.setText(passwordVisible ? R.string.hide : R.string.show);
    }

    private void renderMetrics(ProxyUiState state) {
        int activeClientCount = ProxySessionSnapshot.activeClientCount(proxySessions);
        int knownClientCount = ProxySessionSnapshot.knownClientCount(proxySessions);
        String activeSummary = getResources().getQuantityString(
                R.plurals.active_client_summary,
                activeClientCount,
                activeClientCount);
        String knownSummary = getResources().getQuantityString(
                R.plurals.known_client_summary,
                knownClientCount,
                knownClientCount);
        String summary = getString(
                R.string.client_summary_format,
                activeSummary,
                knownSummary);
        String activeClientLabel = getResources().getQuantityString(
                R.plurals.active_client_summary,
                activeClientCount,
                activeClientCount) + " " + getString(
                activeClientCount == 1 ? R.string.client_one : R.string.client_other);
        String knownClientLabel = getResources().getQuantityString(
                R.plurals.known_client_summary,
                knownClientCount,
                knownClientCount) + " " + getString(
                knownClientCount == 1 ? R.string.client_one : R.string.client_other);
        metricConnectionsView.setText(summary);
        metricClientsButton.setContentDescription(getString(
                R.string.client_summary_accessibility,
                activeClientLabel,
                knownClientLabel));
        clientsSummaryView.setText(summary);
        metricTrafficView.setText(formatBytes(state.totalBytes()));
        metricEgressView.setText(state.egress);
    }

    private List<ProxySessionSnapshot> readSessionSnapshots(Intent intent) {
        ArrayList<String> transports = intent.getStringArrayListExtra(
                ProxyActions.EXTRA_SESSION_TRANSPORTS);
        ArrayList<String> clientAddresses = intent.getStringArrayListExtra(
                ProxyActions.EXTRA_SESSION_CLIENT_ADDRESSES);
        ArrayList<String> methods = intent.getStringArrayListExtra(ProxyActions.EXTRA_SESSION_METHODS);
        ArrayList<String> destinations = intent.getStringArrayListExtra(
                ProxyActions.EXTRA_SESSION_DESTINATIONS);
        long[] startedAt = intent.getLongArrayExtra(ProxyActions.EXTRA_SESSION_STARTED_AT);
        long[] endedAt = intent.getLongArrayExtra(ProxyActions.EXTRA_SESSION_ENDED_AT);
        long[] upBytes = intent.getLongArrayExtra(ProxyActions.EXTRA_SESSION_UP_BYTES);
        long[] downBytes = intent.getLongArrayExtra(ProxyActions.EXTRA_SESSION_DOWN_BYTES);
        List<ProxySessionSnapshot> sessions = new ArrayList<>();
        if (transports == null || clientAddresses == null || methods == null || destinations == null
                || startedAt == null || endedAt == null || upBytes == null || downBytes == null) {
            return sessions;
        }
        int size = transports.size();
        size = Math.min(size, clientAddresses.size());
        size = Math.min(size, methods.size());
        size = Math.min(size, destinations.size());
        size = Math.min(size, startedAt.length);
        size = Math.min(size, endedAt.length);
        size = Math.min(size, upBytes.length);
        size = Math.min(size, downBytes.length);
        for (int index = 0; index < size; index++) {
            sessions.add(new ProxySessionSnapshot(
                    index + 1L,
                    transports.get(index),
                    clientAddresses.get(index),
                    methods.get(index),
                    destinations.get(index),
                    startedAt[index],
                    endedAt[index],
                    upBytes[index],
                    downBytes[index]));
        }
        return sessions;
    }

    private void renderClientDetails() {
        activeSessionRows.clear();
        activeSessionsContainer.removeAllViews();
        recentSessionsContainer.removeAllViews();
        int activeCount = ProxySessionSnapshot.activeSessionCount(proxySessions);
        int recentCount = proxySessions.size() - activeCount;
        activeSessionsTitle.setText(getString(R.string.active_sessions_section, activeCount));
        recentSessionsTitle.setText(getString(R.string.recent_sessions_section, recentCount));
        activeSessionsEmpty.setVisibility(activeCount == 0 ? View.VISIBLE : View.GONE);
        recentSessionsEmpty.setVisibility(recentCount == 0 ? View.VISIBLE : View.GONE);
        sessionsTruncatedNote.setVisibility(sessionsTruncated ? View.VISIBLE : View.GONE);
        for (ProxySessionSnapshot session : proxySessions) {
            addSessionRow(session.isActive() ? activeSessionsContainer : recentSessionsContainer, session);
        }
    }

    private void addSessionRow(LinearLayout container, ProxySessionSnapshot session) {
        View row = getLayoutInflater().inflate(R.layout.item_proxy_session, container, false);
        TextView clientView = row.findViewById(R.id.session_client);
        TextView statusView = row.findViewById(R.id.session_status);
        TextView destinationView = row.findViewById(R.id.session_destination);
        TextView metadataView = row.findViewById(R.id.session_metadata);
        TextView trafficView = row.findViewById(R.id.session_traffic);

        String client = session.isUsb()
                ? getString(R.string.session_usb_host)
                : (session.clientAddress.isEmpty()
                ? getString(R.string.session_unknown_client) : session.clientAddress);
        clientView.setText(getString(R.string.session_client_format, session.transport, client));
        statusView.setText(session.isActive() ? R.string.session_active : R.string.session_ended);
        statusView.setBackgroundResource(session.isActive()
                ? R.drawable.bg_status_active : R.drawable.bg_status_idle);
        statusView.setTextColor(getColor(session.isActive() ? R.color.success : R.color.idle));
        if (session.destination.isEmpty()) {
            destinationView.setText(R.string.session_waiting);
        } else {
            destinationView.setText(session.destination);
        }
        metadataView.setText(formatSessionMetadata(session, System.currentTimeMillis()));
        trafficView.setText(getString(
                R.string.session_traffic_format,
                formatBytes(session.downstreamBytes),
                formatBytes(session.upstreamBytes)));
        row.setContentDescription(clientView.getText() + ". " + destinationView.getText()
                + ". " + metadataView.getText() + ". " + trafficView.getText());
        container.addView(row);
        if (session.isActive()) {
            activeSessionRows.add(new ActiveSessionRow(session, metadataView));
        }
    }

    private String formatSessionMetadata(ProxySessionSnapshot session, long nowMillis) {
        String method = session.method.isEmpty() ? "Pending" : session.method;
        String started = formatClockTime(session.startedAtMillis);
        String duration = formatDuration(session.durationMillis(nowMillis));
        if (session.isActive()) {
            return getString(R.string.session_active_metadata, method, started, duration);
        }
        return getString(
                R.string.session_ended_metadata,
                method,
                started,
                formatClockTime(session.endedAtMillis),
                duration);
    }

    private String formatClockTime(long timeMillis) {
        if (timeMillis <= 0L) {
            return getString(R.string.value_unavailable);
        }
        return new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date(timeMillis));
    }

    private String formatDuration(long durationMillis) {
        long seconds = durationMillis / 1_000L;
        if (seconds < 1L) {
            return "<1s";
        }
        if (seconds < 60L) {
            return seconds + "s";
        }
        long minutes = seconds / 60L;
        if (minutes < 60L) {
            return minutes + "m " + (seconds % 60L) + "s";
        }
        return (minutes / 60L) + "h " + (minutes % 60L) + "m";
    }

    private void startClientDurationTicker() {
        uiHandler.removeCallbacks(clientDurationTicker);
        if (currentPage == PAGE_CLIENTS) {
            uiHandler.postDelayed(clientDurationTicker, 1_000L);
        }
    }

    private void refreshActiveSessionDurations() {
        long nowMillis = System.currentTimeMillis();
        for (ActiveSessionRow row : activeSessionRows) {
            row.metadataView.setText(formatSessionMetadata(row.session, nowMillis));
        }
    }

    private void renderDiagnostics(ProxyUiState state) {
        String ownership = "not active";
        if (state.isLocalHotspotActive()) {
            ownership = "app reservation";
        } else if (state.wifiGroupOwned) {
            ownership = "PdaLink";
        } else if (state.lastError.startsWith("Cannot use active Wi-Fi Direct group")) {
            ownership = "not owned";
        }

        String network = state.isWifiActive() ? state.ssid : "not active";
        if (state.isWifiActive() && !state.iface.isEmpty()) {
            network += " / " + state.iface;
        }
        String band = "not active";
        if (state.isLocalHotspotActive()) {
            band = "managed by Android";
        } else if (state.isWifiDirectActive()) {
            band = "requested " + WifiDirectBand.label(state.requestedBand)
                    + " / actual " + WifiDirectBand.frequencyLabel(state.frequencyMhz);
        }

        StringBuilder details = new StringBuilder();
        appendDetail(details, getString(R.string.detail_mode, state.mode));
        appendDetail(details, getString(R.string.detail_usb_proxy, state.usbProxy));
        appendDetail(details, getString(R.string.detail_wifi_proxy, state.wifiProxy));
        appendDetail(details, getString(R.string.detail_wifi_transport, state.wifiTransport));
        appendDetail(details, getString(R.string.detail_wifi_ownership, ownership));
        appendDetail(details, getString(R.string.detail_wifi_clients, state.wifiClientSummary()));
        appendDetail(details, getString(R.string.detail_wifi_security, state.wifiSecurity));
        appendDetail(details, getString(R.string.detail_proxy_connections, state.proxyClientCount));
        appendDetail(details, getString(
                R.string.detail_internet_usage,
                formatBytes(state.downBytes),
                formatBytes(state.upBytes)));
        appendDetail(details, getString(R.string.detail_egress, state.egress));
        appendDetail(details, getString(R.string.detail_product_usb, state.productUsbStatus));
        appendDetail(details, getString(R.string.detail_wifi_network, network));
        appendDetail(details, getString(R.string.detail_wifi_band, band));
        appendDetail(details, getString(R.string.detail_usb_target, state.usbTarget));
        appendDetail(details, getString(R.string.detail_wifi_target, state.wifiTarget));
        appendDetail(details, getString(R.string.detail_last_request, state.lastRequest));
        appendDetail(details, getString(R.string.detail_last_error, state.lastError));
        summaryView.setText(details.toString());
    }

    private void appendDetail(StringBuilder details, String line) {
        if (details.length() > 0) {
            details.append('\n');
        }
        details.append(line);
    }

    private void syncConnectionSelections(ProxyUiState state) {
        syncingSelections = true;
        if (!usbSelectionDirty) {
            usbModeGroup.check(state.isProductUsb()
                    ? R.id.usb_mode_product : R.id.usb_mode_development);
        }
        if (!wifiSelectionDirty) {
            if (state.isLocalHotspotActive()) {
                wifiModeGroup.check(R.id.wifi_mode_local_hotspot);
            } else if (state.isWifiDirectActive()) {
                wifiModeGroup.check(R.id.wifi_mode_direct);
            } else {
                wifiModeGroup.check(R.id.wifi_mode_off);
            }
        }
        wifiBandContainer.setVisibility(
                wifiModeGroup.getCheckedRadioButtonId() == R.id.wifi_mode_direct
                        ? View.VISIBLE : View.GONE);
        syncingSelections = false;
    }

    private int proxyStatusLabel(String proxyStatus) {
        String normalized = proxyStatus.toLowerCase(Locale.ROOT);
        if (normalized.contains("listening")) {
            return R.string.status_active;
        }
        if (normalized.contains("not running") || normalized.contains("not started")) {
            return R.string.status_not_active;
        }
        return R.string.status_starting;
    }

    private void setCopyButtonState(Button button, boolean enabled) {
        button.setEnabled(enabled);
        button.setAlpha(enabled ? 1f : 0.45f);
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
        } else if (ProxyActions.MODE_LOCAL_HOTSPOT.equals(mode)) {
            sendServiceAction(ProxyActions.ACTION_START_LOCAL_HOTSPOT);
        } else {
            sendServiceAction(ProxyActions.ACTION_START_USB);
        }
    }

}
