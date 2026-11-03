package dev.pdalink.prototype;
import java.security.SecureRandom;
import org.junit.Test;
import static org.junit.Assert.*;

public class WifiDirectCredentialsTest {
    @Test public void generatedPairsFitPlatformAndSavedCredentialPolicy() {
        for (int i = 0; i < 100; i++) {
            String[] pair = WifiDirectCredentials.create(new SecureRandom());
            assertTrue(WifiDirectCredentials.valid(pair[0], pair[1]));
            assertTrue(pair[0].getBytes(java.nio.charset.StandardCharsets.UTF_8).length <= 32);
            assertEquals(16, pair[1].length());
        }
    }
    @Test public void detectsMissingOrInvalidSavedPairs() {
        assertFalse(WifiDirectCredentials.valid(null, null));
        assertFalse(WifiDirectCredentials.valid("DIRECT-PD-PdaLink-ABC123", "short"));
        assertFalse(WifiDirectCredentials.valid("other", "dummy-password"));
        assertTrue(WifiDirectCredentials.valid("DIRECT-PD-PdaLink-ABC123", "dummy-password"));
    }
}
