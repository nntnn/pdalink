package dev.pdalink.prototype;

final class ZeroDisplayAvailability {
    private ZeroDisplayAvailability() {}

    static boolean commandsEnabled(boolean wifiDirectActive, boolean sending) {
        return wifiDirectActive && !sending;
    }

    static boolean isRecovering(boolean wifiDirectActive, int clientCount) {
        return wifiDirectActive && clientCount <= 0;
    }
}
