package dev.pdalink.prototype;

final class WifiDirectClientRecovery {
    private WifiDirectClientRecovery() {
    }

    static boolean shouldSchedule(
            int previousClientCount,
            int currentClientCount,
            boolean wifiDirectEnabled,
            boolean groupOperationInProgress) {
        return wifiDirectEnabled
                && !groupOperationInProgress
                && previousClientCount > 0
                && currentClientCount == 0;
    }
}
