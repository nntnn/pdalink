package dev.pdalink.prototype;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class WifiDirectGroupOwnershipTest {
    @Test
    public void modernGroupRequiresExactCredentialsAndGroupOwnerRole() {
        assertTrue(owned(true, "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, ""));
        assertFalse(owned(false, "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, ""));
        assertFalse(owned(true, "DIRECT-XY-Other", "secret-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true, ""));
        assertFalse(owned(true, "DIRECT-PD-PdaLink-Ab1234", "wrong-pass", true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", true, ""));
    }

    @Test
    public void legacyGroupRequiresCurrentCreationOrRememberedSsid() {
        assertTrue(owned(true, "DIRECT-AB-Phone", "generated-pass", false,
                "", "", true, ""));
        assertTrue(owned(true, "DIRECT-AB-Phone", "generated-pass", false,
                "", "", false, "DIRECT-AB-Phone"));
        assertFalse(owned(true, "DIRECT-XY-Other", "generated-pass", false,
                "", "", false, "DIRECT-AB-Phone"));
    }

    @Test
    public void emptyOrMissingIdentityIsNeverOwned() {
        assertFalse(owned(true, "", "", true, "", "", true, ""));
        assertFalse(owned(true, null, "", false, "", "", true, ""));
        assertFalse(owned(true, "DIRECT-PD-PdaLink-Ab1234", null, true,
                "DIRECT-PD-PdaLink-Ab1234", "secret-pass", false, ""));
    }

    private boolean owned(
            boolean groupOwner,
            String actualSsid,
            String actualPassphrase,
            boolean configurableCredentials,
            String expectedSsid,
            String expectedPassphrase,
            boolean createdThisSession,
            String legacyOwnedSsid) {
        return WifiDirectGroupOwnership.isOwned(
                groupOwner,
                actualSsid,
                actualPassphrase,
                configurableCredentials,
                expectedSsid,
                expectedPassphrase,
                createdThisSession,
                legacyOwnedSsid);
    }
}
