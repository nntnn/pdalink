package dev.pdalink.prototype;

import android.util.Base64;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;

final class LocalHotspotDebugState {
    private static String encodedSnapshot = "";

    private LocalHotspotDebugState() {
    }

    static synchronized void publish(
            String ssid,
            String password,
            String interfaceName,
            String host,
            int port,
            String security) {
        try {
            JSONObject payload = new JSONObject();
            payload.put("ssid", ssid);
            payload.put("password", password);
            payload.put("interface", interfaceName);
            payload.put("host", host);
            payload.put("port", port);
            payload.put("security", security);
            encodedSnapshot = Base64.encodeToString(
                    payload.toString().getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
        } catch (JSONException ex) {
            encodedSnapshot = "";
        }
    }

    static synchronized String encodedSnapshot() {
        return encodedSnapshot;
    }

    static synchronized void clear() {
        encodedSnapshot = "";
    }
}
