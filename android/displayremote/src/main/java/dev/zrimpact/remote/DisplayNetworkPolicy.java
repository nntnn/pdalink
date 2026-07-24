package dev.zrimpact.remote;

final class DisplayNetworkPolicy {
    static final int FIRST_APPROVAL_TIMEOUT_MS = 60_000;
    static final int RECONNECT_TIMEOUT_MS = 60_000;
    private static final long[] RECONNECT_DELAYS_MS = {
            5_000,
            10_000,
            20_000,
            40_000,
            60_000
    };

    private int reconnectIndex;

    static int timeoutMillis(boolean networkApproved) {
        return networkApproved ? RECONNECT_TIMEOUT_MS : FIRST_APPROVAL_TIMEOUT_MS;
    }

    static boolean shouldRetry(boolean networkApproved) {
        return networkApproved;
    }

    long nextReconnectDelayMillis() {
        int index = Math.min(reconnectIndex, RECONNECT_DELAYS_MS.length - 1);
        reconnectIndex++;
        return RECONNECT_DELAYS_MS[index];
    }

    void onConnected() {
        reconnectIndex = 0;
    }
}
