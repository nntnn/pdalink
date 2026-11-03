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

    @Test public void usesPinUntilThreeConsecutiveAcquisitionFailuresThenRelearns() {
        ReconnectPolicy policy = new ReconnectPolicy();
        String bssid = "7a:46:d4:b5:0b:39";
        assertNull(policy.requestBssid(null));
        assertNull(policy.requestBssid("02:00:00:00:00:00"));
        for (int failure = 0; failure < ReconnectPolicy.PINNED_FAILURE_LIMIT; failure++) {
            assertEquals(bssid, policy.requestBssid(bssid));
            policy.onRequestFailure(true);
        }
        assertNull(policy.requestBssid(bssid));
        // Further wildcard failures must keep waiting for approval, not revive the stale pin.
        policy.onRequestFailure(false);
        assertNull(policy.requestBssid(bssid));
        policy.onWifiAvailable();
        assertEquals("7a:46:d4:b5:0b:40", policy.requestBssid("7a:46:d4:b5:0b:40"));
    }

    @Test public void successfulWifiAcquisitionBreaksFailureStreak() {
        ReconnectPolicy policy = new ReconnectPolicy();
        policy.onRequestFailure(true);
        policy.onRequestFailure(true);
        policy.onWifiAvailable();
        policy.onRequestFailure(true);
        policy.onRequestFailure(true);
        assertEquals("7a:46:d4:b5:0b:39", policy.requestBssid("7a:46:d4:b5:0b:39"));
    }

    @Test public void onlyPinnedRequestsTimeOut() {
        assertEquals(60_000, ReconnectPolicy.timeoutMillis("7a:46:d4:b5:0b:39"));
        assertEquals(0, ReconnectPolicy.timeoutMillis(null));
        assertEquals(0, ReconnectPolicy.timeoutMillis("02:00:00:00:00:00"));
        ReconnectPolicy policy = new ReconnectPolicy();
        for (int i = 0; i < ReconnectPolicy.PINNED_FAILURE_LIMIT; i++) policy.onRequestFailure(true);
        assertEquals(0, ReconnectPolicy.timeoutMillis(policy.requestBssid("7a:46:d4:b5:0b:39")));
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
