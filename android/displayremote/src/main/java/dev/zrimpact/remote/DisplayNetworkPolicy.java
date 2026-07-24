package dev.zrimpact.remote;

final class DisplayNetworkPolicy {
    static final int FIRST_APPROVAL_TIMEOUT_MS = 60_000;
    static final int RECONNECT_TIMEOUT_MS = 10_000;

    private DisplayNetworkPolicy() {}

    static int timeoutMillis(boolean networkApproved) {
        return networkApproved ? RECONNECT_TIMEOUT_MS : FIRST_APPROVAL_TIMEOUT_MS;
    }

    static boolean shouldRetry(boolean networkApproved) {
        return networkApproved;
    }
}
