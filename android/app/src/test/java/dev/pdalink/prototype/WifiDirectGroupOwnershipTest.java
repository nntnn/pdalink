package dev.pdalink.prototype;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class WifiDirectGroupOwnershipTest {
    @Test
    public void modernGroupRequiresExactCredentialsAndGroupOwnerRole() {
        assertTrue(owned(true, "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, false, ""));
        assertFalse(owned(false, "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, false, ""));
        assertFalse(owned(true, "DIRECT-XY-Other", "secret-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true, false, ""));
        assertFalse(owned(true, "DIRECT-PD-PdaLink-Ab1234", "wrong-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true, false, ""));
    }

    @Test
    public void explicitlyNegotiatedModernGroupCanUsePlatformCredentials() {
        assertTrue(owned(true, "DIRECT-AN-Phone", "platform-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, true, ""));
        assertTrue(owned(true, "DIRECT-AN-Phone", "platform-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, false,
                "DIRECT-AN-Phone"));
        assertFalse(owned(false, "DIRECT-AN-Phone", "platform-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, true, ""));
    }

    @Test
    public void legacyGroupRequiresCurrentCreationOrRememberedSsid() {
        assertTrue(owned(true, "DIRECT-AB-Phone", "generated-pass", false,
                "", "", true, false, ""));
        assertTrue(owned(true, "DIRECT-AB-Phone", "generated-pass", false,
                "", "", false, false, "DIRECT-AB-Phone"));
        assertFalse(owned(true, "DIRECT-XY-Other", "generated-pass", false,
                "", "", false, false, "DIRECT-AB-Phone"));
    }

    @Test
    public void emptyOrMissingIdentityIsNeverOwned() {
        assertFalse(owned(true, "", "", true, "", "", true, true, ""));
        assertFalse(owned(true, null, "", false, "", "", true, true, ""));
        assertFalse(owned(true, "DIRECT-PD-PdaLink-Ab1234", null, true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, false, ""));
    }

    private boolean owned(
            boolean groupOwner,
            String actualSsid,
            String actualPassphrase,
            boolean configurableCredentials,
            String expectedSsid,
            String expectedPassphrase,
            boolean createdThisSession,
            boolean nativePeerNegotiated,
            String legacyOwnedSsid) {
        return WifiDirectGroupOwnership.isOwned(
                groupOwner,
                actualSsid,
                actualPassphrase,
                configurableCredentials,
                expectedSsid,
                expectedPassphrase,
                createdThisSession,
                nativePeerNegotiated,
                legacyOwnedSsid);
    }
}
