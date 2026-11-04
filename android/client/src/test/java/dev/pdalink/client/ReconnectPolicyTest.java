package dev.pdalink.client;
import org.junit.Test;
import dev.pdalink.shared.PdaLinkConnectionUri;
import static org.junit.Assert.*;

public class ReconnectPolicyTest {
    @Test public void retriesForeverWithBoundedBackoff() {
        assertEquals(5000L, ReconnectPolicy.delayMillis(0));
        assertEquals(10000L, ReconnectPolicy.delayMillis(1));
        assertEquals(40000L, ReconnectPolicy.delayMillis(3));
        assertEquals(60000L, ReconnectPolicy.delayMillis(4));
        assertEquals(60000L, ReconnectPolicy.delayMillis(Integer.MAX_VALUE));
        assertEquals(5000L, ReconnectPolicy.delayMillis(-1));
        assertEquals(2000L, ReconnectPolicy.pinnedDelayMillis(0));
        assertEquals(8000L, ReconnectPolicy.pinnedDelayMillis(2));
        assertEquals(30000L, ReconnectPolicy.pinnedDelayMillis(4));
        assertEquals(30000L, ReconnectPolicy.pinnedDelayMillis(Integer.MAX_VALUE));
    }

    @Test public void normalizesUnicastBssidIncludingLocallyAdministeredDirectMac() {
        assertEquals("7a:46:d4:b5:0b:39", ReconnectPolicy.normalizeBssid(" 7A:46:D4:B5:0B:39 "));
        assertEquals("00:11:22:33:44:55", ReconnectPolicy.normalizeBssid("00:11:22:33:44:55"));
    }

    @Test public void rejectsRedactedMissingMalformedAndNonUnicastBssids() {
        String[] invalid = {null, "", "02:00:00:00:00:00", "00:00:00:00:00:00",
                "ff:ff:ff:ff:ff:ff", "01:11:22:33:44:55", "7a:46:d4:b5:0b",
                "7a:46:d4:b5:0b:3g", "7a-46-d4-b5-0b-39", "7a:46:d4:b5:0b:039"};
        for (String bssid : invalid) assertNull(bssid, ReconnectPolicy.normalizeBssid(bssid));
    }

    @Test public void failuresAndMissingScansKeepPinForever() {
        ReconnectPolicy policy = new ReconnectPolicy();
        for (int i = 0; i < 100; i++) {
            policy.onScanCheck(PIN, java.util.Collections.emptyList());
            assertEquals(PIN, policy.requestBssid(PIN));
        }
    }

    private static final String PIN = "7a:46:d4:b5:0b:39";
    private static final String OTHER = "7a:46:d4:b5:0b:40";

    @Test public void requiresConsecutiveChangedBssidEvidence() {
        ReconnectPolicy policy = new ReconnectPolicy();
        policy.onScanCheck(PIN, java.util.List.of(OTHER));
        policy.onScanCheck(PIN, java.util.List.of(OTHER));
        assertEquals(PIN, policy.requestBssid(PIN));
        policy.onScanCheck(PIN, java.util.List.of(PIN, OTHER));
        policy.onScanCheck(PIN, java.util.List.of(OTHER));
        policy.onScanCheck(PIN, java.util.List.of("02:00:00:00:00:00"));
        for (int i = 0; i < ReconnectPolicy.BSSID_CHANGE_CHECKS; i++)
            policy.onScanCheck(PIN, java.util.List.of(OTHER));
        assertNull(policy.requestBssid(PIN));
        policy.onWifiAvailable();
        // A redacted successful connection retains the stored pin.
        assertEquals(PIN, policy.requestBssid(PIN));
        assertEquals(OTHER, policy.requestBssid(OTHER));
    }

    @Test public void onlyPinnedRequestsTimeOut() {
        assertEquals(15_000, ReconnectPolicy.timeoutMillis(PIN));
        assertEquals(0, ReconnectPolicy.timeoutMillis(null));
        assertEquals(0, ReconnectPolicy.timeoutMillis("02:00:00:00:00:00"));
    }

    private static PdaLinkConnectionUri.Bundle profile(String transport, String ssid,
                                                       String security, String password, int port) {
        return PdaLinkConnectionUri.create(transport, ssid, security, password,
                "http://192.168.49.1:" + port);
    }

    @Test public void keepsPinOnlyForSameWifiCredentials() {
        PdaLinkConnectionUri.Bundle original = profile("wifi-direct", "DIRECT-test", "wpa2-psk", "password", 8001);
        assertTrue(ReconnectPolicy.sameWifiCredentials(original,
                profile("wifi-direct", "DIRECT-test", "wpa2-psk", "password", 8002)));
        assertFalse(ReconnectPolicy.sameWifiCredentials(original,
                profile("wifi-direct", "DIRECT-new", "wpa2-psk", "password", 8001)));
        assertFalse(ReconnectPolicy.sameWifiCredentials(original,
                profile("wifi-direct", "DIRECT-test", "wpa2-psk", "new-password", 8001)));
        assertFalse(ReconnectPolicy.sameWifiCredentials(original,
                profile("wifi-direct", "DIRECT-test", "wpa3-sae", "password", 8001)));
        assertFalse(ReconnectPolicy.sameWifiCredentials(original,
                profile("local-hotspot", "DIRECT-test", "wpa2-psk", "password", 8001)));
    }
}
