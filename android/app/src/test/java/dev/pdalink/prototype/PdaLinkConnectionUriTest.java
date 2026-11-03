package dev.pdalink.prototype;

import static org.junit.Assert.assertEquals;

import dev.pdalink.shared.PdaLinkConnectionUri;

import org.junit.Test;

public class PdaLinkConnectionUriTest {
    @Test
    public void roundTripsReservedCharacters() {
        PdaLinkConnectionUri.Bundle created = PdaLinkConnectionUri.create(
                PdaLinkConnectionUri.TRANSPORT_LOCAL_HOTSPOT,
                "Pda Link+1",
                "WPA2/WPA3 transition",
                "pass&word=123",
                "http://10.111.107.245:8001");

        PdaLinkConnectionUri.Bundle parsed = PdaLinkConnectionUri.parse(created.toUri());

        assertEquals("Pda Link+1", parsed.ssid);
        assertEquals("pass&word=123", parsed.password);
        assertEquals(PdaLinkConnectionUri.SECURITY_TRANSITION, parsed.security);
        assertEquals("10.111.107.245", parsed.proxyHost);
        assertEquals(8001, parsed.proxyPort);
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsPublicProxyAddress() {
        PdaLinkConnectionUri.create(
                PdaLinkConnectionUri.TRANSPORT_LOCAL_HOTSPOT,
                "PdaLink",
                PdaLinkConnectionUri.SECURITY_WPA2,
                "password123",
                "http://8.8.8.8:8001");
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsDuplicateParameters() {
        PdaLinkConnectionUri.parse(
                "pdalink://connect?v=1&v=1&transport=local-hotspot&ssid=PdaLink"
                        + "&security=wpa2-psk&password=password123&proxy=http%3A%2F%2F10.0.0.1%3A8001");
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsNonAsciiPassword() {
        PdaLinkConnectionUri.create(
                PdaLinkConnectionUri.TRANSPORT_WIFI_DIRECT,
                "DIRECT-PD",
                PdaLinkConnectionUri.SECURITY_WPA2,
                "password\u2603",
                "http://192.168.49.1:8001");
    }
}
