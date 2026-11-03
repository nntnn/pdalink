package dev.pdalink.prototype;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class WifiDirectPeerAddressTest {
    @Test
    public void normalizesAValidPeerAddress() {
        assertEquals(
                "8A:A2:9E:58:F7:91",
                WifiDirectPeerAddress.normalize(" 8a:a2:9e:58:f7:91 "));
    }

    @Test
    public void rejectsInvalidAddresses() {
        assertEquals("", WifiDirectPeerAddress.normalize(null));
        assertEquals("", WifiDirectPeerAddress.normalize("8a:a2:9e"));
        assertEquals("", WifiDirectPeerAddress.normalize("not-a-mac"));
    }

    @Test
    public void matchesWithoutCaseSensitivity() {
        assertTrue(WifiDirectPeerAddress.matches(
                "8A:A2:9E:58:F7:91",
                "8a:a2:9e:58:f7:91"));
        assertFalse(WifiDirectPeerAddress.matches(
                "8A:A2:9E:58:F7:91",
                "88:a2:9e:58:f7:91"));
    }
}
