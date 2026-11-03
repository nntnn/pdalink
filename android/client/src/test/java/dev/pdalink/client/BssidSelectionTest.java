package dev.pdalink.client;

import org.junit.Test;
import java.util.Arrays;
import java.util.Collections;
import static org.junit.Assert.*;

public class BssidSelectionTest {
    private BssidSelection.Candidate ap(String ssid, String bssid, String caps, int level) {
        return new BssidSelection.Candidate(ssid, bssid, caps, level);
    }

    @Test public void quotedSsidAndStrongestCompatibleBssid() {
        assertEquals("aa:bb:cc:dd:ee:02", BssidSelection.select("\"DIRECT-test\"", "wpa2-psk", Arrays.asList(
                ap("DIRECT-test", "aa:bb:cc:dd:ee:01", "[WPA2-PSK-CCMP]", -80),
                ap("\"DIRECT-test\"", "AA:BB:CC:DD:EE:02", "[RSN-PSK-CCMP]", -40),
                ap("DIRECT-test", "aa:bb:cc:dd:ee:03", "[SAE]", -20),
                ap("other", "aa:bb:cc:dd:ee:04", "[WPA2-PSK-CCMP]", -10))));
    }

    @Test public void invalidAndRedactedBssidsNeverWin() {
        for (String bssid : Arrays.asList(null, "", "malformed", "02:00:00:00:00:00",
                "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")) {
            assertNull(BssidSelection.select("ap", "wpa2-psk", Collections.singletonList(
                    ap("ap", bssid, "[WPA2-PSK-CCMP]", -10))));
        }
        assertNull(BssidSelection.select("ap", "wpa2-psk", Collections.emptyList()));
    }

    @Test public void securityModesAreRespected() {
        assertNull(BssidSelection.select("ap", "wpa3-sae", Collections.singletonList(
                ap("ap", "aa:bb:cc:dd:ee:01", "[WPA2-PSK-CCMP]", -30))));
        for (String security : Arrays.asList("wpa3-sae", "wpa2-wpa3")) {
            assertEquals("aa:bb:cc:dd:ee:01", BssidSelection.select("ap", security, Collections.singletonList(
                    ap("ap", "aa:bb:cc:dd:ee:01", "[RSN-SAE-CCMP]", -30))));
        }
        for (String caps : Arrays.asList(null, "[ESS]", "[WPA2-EAP-CCMP]", "[WPA-PSK-TKIP]")) {
            assertNull(BssidSelection.select("ap", "wpa2-psk", Collections.singletonList(
                    ap("ap", "aa:bb:cc:dd:ee:01", caps, -30))));
        }
    }
}
