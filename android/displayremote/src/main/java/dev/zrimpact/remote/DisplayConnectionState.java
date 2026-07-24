package dev.zrimpact.remote;

final class DisplayConnectionState {
    private boolean connected;

    boolean onAvailable() {
        if (connected) {
            return false;
        }
        connected = true;
        return true;
    }

    void onLost() {
        connected = false;
    }
}
