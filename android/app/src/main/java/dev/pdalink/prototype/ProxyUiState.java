package dev.pdalink.prototype;

import java.util.Locale;

final class ProxyUiState {
    enum Tone {
        ACTIVE,
        STARTING,
        IDLE,
        ATTENTION
    }

    final String mode;
    final String usbProxy;
    final String wifiProxy;
    final String egress;
    final String ssid;
    final String passphrase;
    final String iface;
    final String wifiTransport;
    final String wifiSecurity;
    final boolean wifiGroupOwned;
    final int wifiGroupClientCount;
    final String wifiGroupClientAddress;
    final String requestedBand;
    final int frequencyMhz;
    final String usbTarget;
    final String wifiTarget;
    final String lastRequest;
    final String lastError;
    final String productUsbStatus;
    final int proxyClientCount;
    final long upBytes;
    final long downBytes;

    private ProxyUiState(Builder builder) {
        mode = valueOrDefault(builder.mode, ProxyActions.MODE_USB);
        usbProxy = valueOrDefault(builder.usbProxy, "not running");
        wifiProxy = valueOrDefault(builder.wifiProxy, "not running");
        egress = valueOrDefault(builder.egress, "unknown");
        ssid = valueOrDefault(builder.ssid, "");
        passphrase = valueOrDefault(builder.passphrase, "");
        iface = valueOrDefault(builder.iface, "");
        wifiTransport = valueOrDefault(builder.wifiTransport, "Wi-Fi");
        wifiSecurity = valueOrDefault(builder.wifiSecurity, "not active");
        wifiGroupOwned = builder.wifiGroupOwned;
        wifiGroupClientCount = Math.max(0, builder.wifiGroupClientCount);
        wifiGroupClientAddress = valueOrDefault(builder.wifiGroupClientAddress, "");
        requestedBand = WifiDirectBand.normalize(builder.requestedBand);
        frequencyMhz = Math.max(0, builder.frequencyMhz);
        usbTarget = valueOrDefault(builder.usbTarget, "not ready");
        wifiTarget = valueOrDefault(builder.wifiTarget, "not ready");
        lastRequest = valueOrDefault(builder.lastRequest, "none");
        lastError = valueOrDefault(builder.lastError, "none");
        productUsbStatus = valueOrDefault(builder.productUsbStatus, "permission missing");
        proxyClientCount = Math.max(0, builder.proxyClientCount);
        upBytes = Math.max(0L, builder.upBytes);
        downBytes = Math.max(0L, builder.downBytes);
    }

    boolean isProductUsb() {
        return mode.contains(ProxyActions.MODE_PRODUCT_USB);
    }

    boolean isWifiDirectActive() {
        return mode.contains(ProxyActions.MODE_WIFI_DIRECT);
    }

    boolean isLocalHotspotActive() {
        return mode.contains(ProxyActions.MODE_LOCAL_HOTSPOT);
    }

    boolean isWifiActive() {
        return isWifiDirectActive() || isLocalHotspotActive();
    }

    boolean hasError() {
        return !isEmptyState(lastError);
    }

    Tone tone() {
        if (hasError()) {
            return Tone.ATTENTION;
        }
        if (isListening(usbProxy) || isListening(wifiProxy)) {
            return Tone.ACTIVE;
        }
        if (isStarting(usbProxy) || isStarting(wifiProxy)) {
            return Tone.STARTING;
        }
        return Tone.IDLE;
    }

    String maskedPassphrase() {
        if (passphrase.isEmpty()) {
            return "";
        }
        int length = Math.max(8, Math.min(12, passphrase.length()));
        StringBuilder masked = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            masked.append('\u2022');
        }
        return masked.toString();
    }

    String wifiClientSummary() {
        if (isLocalHotspotActive()) {
            return "unavailable";
        }
        if (isWifiDirectActive()) {
            return Integer.toString(wifiGroupClientCount);
        }
        return "not active";
    }

    boolean hasUsbTarget() {
        return isCopyable(usbTarget);
    }

    boolean hasWifiTarget() {
        return isCopyable(wifiTarget);
    }

    boolean hasWifiCredentials() {
        return isWifiActive() && !ssid.isEmpty();
    }

    long totalBytes() {
        return upBytes + downBytes;
    }

    private static boolean isListening(String value) {
        return value.toLowerCase(Locale.ROOT).contains("listening");
    }

    private static boolean isStarting(String value) {
        String normalized = value.toLowerCase(Locale.ROOT);
        return !normalized.contains("not running")
                && !normalized.contains("not started")
                && !normalized.contains("stopped");
    }

    private static boolean isCopyable(String value) {
        return value != null
                && !value.isEmpty()
                && !"not ready".equalsIgnoreCase(value)
                && !"not active".equalsIgnoreCase(value);
    }

    private static boolean isEmptyState(String value) {
        return value == null
                || value.isEmpty()
                || "none".equalsIgnoreCase(value)
                || "not active".equalsIgnoreCase(value);
    }

    private static String valueOrDefault(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    static final class Builder {
        private String mode;
        private String usbProxy;
        private String wifiProxy;
        private String egress;
        private String ssid;
        private String passphrase;
        private String iface;
        private String wifiTransport;
        private String wifiSecurity;
        private boolean wifiGroupOwned;
        private int wifiGroupClientCount;
        private String wifiGroupClientAddress;
        private String requestedBand;
        private int frequencyMhz;
        private String usbTarget;
        private String wifiTarget;
        private String lastRequest;
        private String lastError;
        private String productUsbStatus;
        private int proxyClientCount;
        private long upBytes;
        private long downBytes;

        Builder mode(String value) { mode = value; return this; }
        Builder usbProxy(String value) { usbProxy = value; return this; }
        Builder wifiProxy(String value) { wifiProxy = value; return this; }
        Builder egress(String value) { egress = value; return this; }
        Builder ssid(String value) { ssid = value; return this; }
        Builder passphrase(String value) { passphrase = value; return this; }
        Builder iface(String value) { iface = value; return this; }
        Builder wifiTransport(String value) { wifiTransport = value; return this; }
        Builder wifiSecurity(String value) { wifiSecurity = value; return this; }
        Builder wifiGroupOwned(boolean value) { wifiGroupOwned = value; return this; }
        Builder wifiGroupClientCount(int value) { wifiGroupClientCount = value; return this; }
        Builder wifiGroupClientAddress(String value) {
            wifiGroupClientAddress = value;
            return this;
        }
        Builder requestedBand(String value) { requestedBand = value; return this; }
        Builder frequencyMhz(int value) { frequencyMhz = value; return this; }
        Builder usbTarget(String value) { usbTarget = value; return this; }
        Builder wifiTarget(String value) { wifiTarget = value; return this; }
        Builder lastRequest(String value) { lastRequest = value; return this; }
        Builder lastError(String value) { lastError = value; return this; }
        Builder productUsbStatus(String value) { productUsbStatus = value; return this; }
        Builder proxyClientCount(int value) { proxyClientCount = value; return this; }
        Builder upBytes(long value) { upBytes = value; return this; }
        Builder downBytes(long value) { downBytes = value; return this; }

        ProxyUiState build() {
            return new ProxyUiState(this);
        }
    }
}
