package dev.pdalink.client;

final class ReconnectPolicy {
    private ReconnectPolicy() {}
    static long delayMillis(int attempt) {
        return Math.min(60000L, 5000L << Math.max(0, Math.min(attempt, 4)));
    }
}
