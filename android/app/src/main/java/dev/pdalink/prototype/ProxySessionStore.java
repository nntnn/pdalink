package dev.pdalink.prototype;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

final class ProxySessionStore {
    private static final int MAX_TRANSPORT_LENGTH = 32;
    private static final int MAX_CLIENT_ADDRESS_LENGTH = 64;
    private static final int MAX_METHOD_LENGTH = 16;
    private static final int MAX_DESTINATION_LENGTH = 255;

    static final class SessionHandle {
        private final long id;
        private final SessionRecord record;

        private SessionHandle(long id, SessionRecord record) {
            this.id = id;
            this.record = record;
        }
    }

    private static final class SessionRecord {
        final long id;
        final String transport;
        final String clientAddress;
        final long startedAtMillis;
        final AtomicLong upstreamBytes = new AtomicLong();
        final AtomicLong downstreamBytes = new AtomicLong();
        volatile String method = "";
        volatile String destination = "";
        volatile boolean retainInHistory = true;

        SessionRecord(long id, String transport, String clientAddress, long startedAtMillis) {
            this.id = id;
            this.transport = transport;
            this.clientAddress = clientAddress;
            this.startedAtMillis = startedAtMillis;
        }

        ProxySessionSnapshot snapshot(long endedAtMillis) {
            return new ProxySessionSnapshot(
                    id,
                    transport,
                    clientAddress,
                    method,
                    destination,
                    startedAtMillis,
                    endedAtMillis,
                    upstreamBytes.get(),
                    downstreamBytes.get());
        }
    }

    private final int maxRecentSessions;
    private final AtomicLong nextId = new AtomicLong();
    private final Map<Long, SessionRecord> activeSessions = new ConcurrentHashMap<>();
    private final ArrayDeque<ProxySessionSnapshot> recentSessions = new ArrayDeque<>();

    ProxySessionStore(int maxRecentSessions) {
        this.maxRecentSessions = Math.max(0, maxRecentSessions);
    }

    SessionHandle begin(String transport, String clientAddress, long startedAtMillis) {
        long id = nextId.incrementAndGet();
        SessionRecord record = new SessionRecord(
                id,
                bounded(transport, MAX_TRANSPORT_LENGTH),
                bounded(clientAddress, MAX_CLIENT_ADDRESS_LENGTH),
                Math.max(0L, startedAtMillis));
        activeSessions.put(id, record);
        return new SessionHandle(id, record);
    }

    void updateRequest(SessionHandle handle, String method, String destination) {
        if (!isActive(handle)) {
            return;
        }
        handle.record.method = bounded(method, MAX_METHOD_LENGTH);
        handle.record.destination = bounded(destination, MAX_DESTINATION_LENGTH);
    }

    void addTraffic(SessionHandle handle, long upstreamDelta, long downstreamDelta) {
        if (!isActive(handle)) {
            return;
        }
        if (upstreamDelta > 0L) {
            handle.record.upstreamBytes.addAndGet(upstreamDelta);
        }
        if (downstreamDelta > 0L) {
            handle.record.downstreamBytes.addAndGet(downstreamDelta);
        }
    }

    void excludeFromHistory(SessionHandle handle) {
        if (isActive(handle)) {
            handle.record.retainInHistory = false;
        }
    }

    void finish(SessionHandle handle, long endedAtMillis) {
        if (handle == null || activeSessions.remove(handle.id) != handle.record) {
            return;
        }
        ProxySessionSnapshot snapshot = handle.record.snapshot(
                Math.max(handle.record.startedAtMillis, endedAtMillis));
        if (!handle.record.retainInHistory) {
            return;
        }
        synchronized (recentSessions) {
            if (maxRecentSessions == 0) {
                return;
            }
            recentSessions.addFirst(snapshot);
            while (recentSessions.size() > maxRecentSessions) {
                recentSessions.removeLast();
            }
        }
    }

    List<ProxySessionSnapshot> snapshot() {
        List<ProxySessionSnapshot> result = new ArrayList<>();
        for (SessionRecord record : activeSessions.values()) {
            result.add(record.snapshot(0L));
        }
        Collections.sort(result, new Comparator<ProxySessionSnapshot>() {
            @Override
            public int compare(ProxySessionSnapshot left, ProxySessionSnapshot right) {
                int time = Long.compare(right.startedAtMillis, left.startedAtMillis);
                return time != 0 ? time : Long.compare(right.id, left.id);
            }
        });
        synchronized (recentSessions) {
            result.addAll(recentSessions);
        }
        return result;
    }

    private boolean isActive(SessionHandle handle) {
        return handle != null && activeSessions.get(handle.id) == handle.record;
    }

    private static String bounded(String value, int maxLength) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.length() <= maxLength) {
            return normalized;
        }
        return normalized.substring(0, maxLength);
    }
}
