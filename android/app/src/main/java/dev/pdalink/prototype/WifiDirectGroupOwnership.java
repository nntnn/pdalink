package dev.pdalink.prototype;

final class WifiDirectGroupOwnership {
    private WifiDirectGroupOwnership() {
    }

    static boolean isOwned(
            boolean groupOwner,
            String actualSsid,
            String actualPassphrase,
            boolean configurableCredentials,
            String expectedSsid,
            String expectedPassphrase,
            boolean createdThisSession,
            String legacyOwnedSsid) {
        if (!groupOwner || actualSsid == null || actualSsid.isEmpty()) {
            return false;
        }
        if (configurableCredentials) {
            return actualPassphrase != null
                    && expectedSsid != null
                    && expectedPassphrase != null
                    && actualSsid.equals(expectedSsid)
                    && actualPassphrase.equals(expectedPassphrase);
        }
        return createdThisSession || actualSsid.equals(legacyOwnedSsid);
    }
}
