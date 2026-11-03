package dev.pdalink.prototype;

/** Monotonic-time gate; callers retry the latest visible content after delayMillis. */
final class NotificationGate {
    private String posted;
    private long postedAt;

    long delayMillis(String text, long now) {
        if (text.equals(posted)) return -1;
        return posted == null ? 0 : Math.max(0, 2000L - (now - postedAt));
    }

    void didPost(String text, long now) {
        posted = text;
        postedAt = now;
    }
}
