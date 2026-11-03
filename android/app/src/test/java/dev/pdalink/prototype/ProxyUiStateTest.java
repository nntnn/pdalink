package dev.pdalink.prototype;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ProxyUiStateTest {
    @Test
    public void activeHotspotUsesActiveToneAndHidesUnavailableClientCount() {
        ProxyUiState state = new ProxyUiState.Builder()
                .mode(ProxyActions.MODE_USB_LOCAL_HOTSPOT)
                .usbProxy("listening on 127.0.0.1:8000")
                .wifiProxy("listening on 10.1.2.3:8001")
                .ssid("AndroidShare")
                .passphrase("secret123")
                .wifiGroupClientCount(4)
                .build();

        assertEquals(ProxyUiState.Tone.ACTIVE, state.tone());
        assertTrue(state.isLocalHotspotActive());
        assertEquals("unavailable", state.wifiClientSummary());
        assertTrue(state.hasWifiCredentials());
    }

    @Test
    public void wifiDirectShowsJoinedGroupCount() {
        ProxyUiState state = new ProxyUiState.Builder()
                .mode(ProxyActions.MODE_USB_WIFI_DIRECT)
                .wifiGroupClientCount(2)
                .wifiGroupClientAddress("192.168.49.200")
                .build();

        assertTrue(state.isWifiDirectActive());
        assertEquals("2", state.wifiClientSummary());
        assertEquals("192.168.49.200", state.wifiGroupClientAddress);
    }

    @Test
    public void productUsbStateOnlyAppliesToProductMode() {
        ProxyUiState product = new ProxyUiState.Builder()
                .mode(ProxyActions.MODE_PRODUCT_USB)
                .build();
        ProxyUiState development = new ProxyUiState.Builder()
                .mode(ProxyActions.MODE_USB)
                .build();

        assertTrue(product.isProductUsb());
        assertFalse(development.isProductUsb());
    }

    @Test
    public void noneErrorDoesNotOverrideConnectionTone() {
        ProxyUiState state = new ProxyUiState.Builder()
                .usbProxy("listening on 127.0.0.1:8000")
                .lastError("none")
                .build();

        assertFalse(state.hasError());
        assertEquals(ProxyUiState.Tone.ACTIVE, state.tone());
    }

    @Test
    public void realErrorTakesAttentionTone() {
        ProxyUiState state = new ProxyUiState.Builder()
                .usbProxy("listening on 127.0.0.1:8000")
                .lastError("Cannot use active Wi-Fi Direct group")
                .build();

        assertTrue(state.hasError());
        assertEquals(ProxyUiState.Tone.ATTENTION, state.tone());
    }

    @Test
    public void passphraseIsMaskedWithoutLeakingLengthBeyondTwelve() {
        ProxyUiState shortSecret = new ProxyUiState.Builder().passphrase("1234").build();
        ProxyUiState longSecret = new ProxyUiState.Builder().passphrase("12345678901234567890").build();

        assertEquals(8, shortSecret.maskedPassphrase().length());
        assertEquals(12, longSecret.maskedPassphrase().length());
        assertFalse(shortSecret.maskedPassphrase().contains("1"));
    }

    @Test
    public void negativeCountersAreClamped() {
        ProxyUiState state = new ProxyUiState.Builder()
                .wifiGroupClientCount(-1)
                .proxyClientCount(-2)
                .upBytes(-3)
                .downBytes(-4)
                .build();

        assertEquals(0, state.wifiGroupClientCount);
        assertEquals(0, state.proxyClientCount);
        assertEquals(0L, state.totalBytes());
    }
}
