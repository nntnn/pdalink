package dev.pdalink.shared;

import java.io.UnsupportedEncodingException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public final class PdaLinkConnectionUri {
    public static final int VERSION = 1;
    public static final String TRANSPORT_LOCAL_HOTSPOT = "local-hotspot";
    public static final String TRANSPORT_WIFI_DIRECT = "wifi-direct";
    public static final String SECURITY_WPA2 = "wpa2-psk";
    public static final String SECURITY_WPA3 = "wpa3-sae";
    public static final String SECURITY_TRANSITION = "wpa2-wpa3";

    private static final Set<String> ALLOWED_KEYS = new HashSet<>();

    static {
        ALLOWED_KEYS.add("v");
        ALLOWED_KEYS.add("transport");
        ALLOWED_KEYS.add("ssid");
        ALLOWED_KEYS.add("security");
        ALLOWED_KEYS.add("password");
        ALLOWED_KEYS.add("proxy");
    }

    private PdaLinkConnectionUri() {
    }

    public static Bundle create(
            String transport,
            String ssid,
            String security,
            String password,
            String proxyUrl) {
        return validate(transport, ssid, security, password, proxyUrl);
    }

    public static Bundle parse(String raw) {
        if (raw == null || raw.trim().isEmpty()) {
            throw new IllegalArgumentException("Connection URI is empty");
        }
        URI uri;
        try {
            uri = new URI(raw.trim());
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("Connection URI is malformed", exception);
        }
        if (!"pdalink".equalsIgnoreCase(uri.getScheme())
                || !"connect".equalsIgnoreCase(uri.getHost())
                || uri.getRawUserInfo() != null
                || uri.getPort() != -1
                || (uri.getRawPath() != null && !uri.getRawPath().isEmpty())
                || uri.getRawFragment() != null) {
            throw new IllegalArgumentException("Connection URI target is invalid");
        }

        Map<String, String> values = parseQuery(uri.getRawQuery());
        if (!Integer.toString(VERSION).equals(values.get("v"))) {
            throw new IllegalArgumentException("Unsupported connection URI version");
        }
        return validate(
                required(values, "transport"),
                required(values, "ssid"),
                required(values, "security"),
                required(values, "password"),
                required(values, "proxy"));
    }

    public static String normalizeSecurity(String value) {
        String normalized = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        if (normalized.equals(SECURITY_WPA2) || normalized.equals("wpa2-psk")) {
            return SECURITY_WPA2;
        }
        if (normalized.equals(SECURITY_WPA3) || normalized.equals("wpa3-sae")) {
            return SECURITY_WPA3;
        }
        if (normalized.equals(SECURITY_TRANSITION)
                || normalized.equals("wpa2/wpa3 transition")) {
            return SECURITY_TRANSITION;
        }
        throw new IllegalArgumentException("Unsupported Wi-Fi security mode");
    }

    private static Bundle validate(
            String transport,
            String ssid,
            String security,
            String password,
            String proxyUrl) {
        String normalizedTransport = transport == null
                ? "" : transport.trim().toLowerCase(Locale.ROOT);
        if (!TRANSPORT_LOCAL_HOTSPOT.equals(normalizedTransport)
                && !TRANSPORT_WIFI_DIRECT.equals(normalizedTransport)) {
            throw new IllegalArgumentException("Unsupported Wi-Fi transport");
        }
        if (ssid == null || ssid.indexOf('\0') >= 0 || utf8Length(ssid) < 1 || utf8Length(ssid) > 32) {
            throw new IllegalArgumentException("SSID must be between 1 and 32 UTF-8 bytes");
        }
        if (password == null || password.length() < 8 || password.length() > 63) {
            throw new IllegalArgumentException("Wi-Fi password must be between 8 and 63 characters");
        }
        for (int index = 0; index < password.length(); index++) {
            char character = password.charAt(index);
            if (character < 0x20 || character > 0x7e) {
                throw new IllegalArgumentException("Wi-Fi password must contain printable ASCII characters");
            }
        }

        String normalizedSecurity = normalizeSecurity(security);
        URI proxy;
        try {
            proxy = new URI(proxyUrl == null ? "" : proxyUrl.trim());
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("Proxy URL is malformed", exception);
        }
        String host = proxy.getHost();
        int port = proxy.getPort();
        if (!"http".equalsIgnoreCase(proxy.getScheme())
                || host == null
                || proxy.getRawUserInfo() != null
                || port < 1
                || port > 65535
                || (proxy.getRawPath() != null && !proxy.getRawPath().isEmpty())
                || proxy.getRawQuery() != null
                || proxy.getRawFragment() != null
                || !isPrivateIpv4(host)) {
            throw new IllegalArgumentException("Proxy must be an HTTP endpoint on a private IPv4 address");
        }
        String canonicalProxy = "http://" + host + ":" + port;
        return new Bundle(
                normalizedTransport,
                ssid,
                normalizedSecurity,
                password,
                canonicalProxy,
                host,
                port);
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        if (rawQuery == null || rawQuery.isEmpty()) {
            throw new IllegalArgumentException("Connection URI has no parameters");
        }
        Map<String, String> values = new HashMap<>();
        for (String item : rawQuery.split("&", -1)) {
            int separator = item.indexOf('=');
            if (separator <= 0) {
                throw new IllegalArgumentException("Connection URI parameter is malformed");
            }
            String key = decode(item.substring(0, separator));
            String value = decode(item.substring(separator + 1));
            if (!ALLOWED_KEYS.contains(key)) {
                throw new IllegalArgumentException("Unknown connection URI parameter: " + key);
            }
            if (values.put(key, value) != null) {
                throw new IllegalArgumentException("Duplicate connection URI parameter: " + key);
            }
        }
        return values;
    }

    private static String required(Map<String, String> values, String key) {
        String value = values.get(key);
        if (value == null || value.isEmpty()) {
            throw new IllegalArgumentException("Missing connection URI parameter: " + key);
        }
        return value;
    }

    private static boolean isPrivateIpv4(String value) {
        String[] parts = value.split("\\.", -1);
        if (parts.length != 4) {
            return false;
        }
        int[] octets = new int[4];
        for (int index = 0; index < parts.length; index++) {
            if (parts[index].isEmpty() || (parts[index].length() > 1 && parts[index].startsWith("0"))) {
                return false;
            }
            try {
                octets[index] = Integer.parseInt(parts[index]);
            } catch (NumberFormatException exception) {
                return false;
            }
            if (octets[index] < 0 || octets[index] > 255) {
                return false;
            }
        }
        return octets[0] == 10
                || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
                || (octets[0] == 192 && octets[1] == 168);
    }

    private static int utf8Length(String value) {
        try {
            return value.getBytes("UTF-8").length;
        } catch (UnsupportedEncodingException exception) {
            throw new IllegalStateException("UTF-8 is unavailable", exception);
        }
    }

    private static String encode(String value) {
        try {
            return URLEncoder.encode(value, "UTF-8");
        } catch (UnsupportedEncodingException exception) {
            throw new IllegalStateException("UTF-8 is unavailable", exception);
        }
    }

    private static String decode(String value) {
        try {
            return URLDecoder.decode(value, "UTF-8");
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("Connection URI encoding is invalid", exception);
        } catch (UnsupportedEncodingException exception) {
            throw new IllegalStateException("UTF-8 is unavailable", exception);
        }
    }

    public static final class Bundle {
        public final String transport;
        public final String ssid;
        public final String security;
        public final String password;
        public final String proxyUrl;
        public final String proxyHost;
        public final int proxyPort;

        private Bundle(
                String transport,
                String ssid,
                String security,
                String password,
                String proxyUrl,
                String proxyHost,
                int proxyPort) {
            this.transport = transport;
            this.ssid = ssid;
            this.security = security;
            this.password = password;
            this.proxyUrl = proxyUrl;
            this.proxyHost = proxyHost;
            this.proxyPort = proxyPort;
        }

        public String toUri() {
            return "pdalink://connect"
                    + "?v=" + VERSION
                    + "&transport=" + encode(transport)
                    + "&ssid=" + encode(ssid)
                    + "&security=" + encode(security)
                    + "&password=" + encode(password)
                    + "&proxy=" + encode(proxyUrl);
        }
    }
}
