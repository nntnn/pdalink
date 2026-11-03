package dev.pdalink.prototype;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

final class ProxySessionSnapshot {
    final long id;
    final String transport;
    final String clientAddress;
    final String method;
    final String destination;
    final long startedAtMillis;
    final long endedAtMillis;
    final long upstreamBytes;
    final long downstreamBytes;

    ProxySessionSnapshot(
            long id,
            String transport,
            String clientAddress,
            String method,
            String destination,
            long startedAtMillis,
            long endedAtMillis,
            long upstreamBytes,
            long downstreamBytes) {
        this.id = id;
        this.transport = valueOrEmpty(transport);
        this.clientAddress = valueOrEmpty(clientAddress);
        this.method = valueOrEmpty(method);
        this.destination = valueOrEmpty(destination);
        this.startedAtMillis = Math.max(0L, startedAtMillis);
        this.endedAtMillis = Math.max(0L, endedAtMillis);
        this.upstreamBytes = Math.max(0L, upstreamBytes);
        this.downstreamBytes = Math.max(0L, downstreamBytes);
    }

    boolean isActive() {
        return endedAtMillis == 0L;
    }

    boolean isUsb() {
        return "USB".equals(transport);
    }

    long totalBytes() {
        return upstreamBytes + downstreamBytes;
    }

    long durationMillis(long nowMillis) {
        long end = isActive() ? Math.max(startedAtMillis, nowMillis) : endedAtMillis;
        return Math.max(0L, end - startedAtMillis);
    }

    static int activeSessionCount(List<ProxySessionSnapshot> sessions) {
        int count = 0;
        for (ProxySessionSnapshot session : sessions) {
            if (session.isActive()) {
                count++;
            }
        }
        return count;
    }

    static int activeClientCount(List<ProxySessionSnapshot> sessions) {
        Set<String> clients = new HashSet<>();
        for (ProxySessionSnapshot session : sessions) {
            if (session.isActive()) {
                clients.add(session.clientIdentity());
            }
        }
        return clients.size();
    }

    static int knownClientCount(List<ProxySessionSnapshot> sessions) {
        Set<String> clients = new HashSet<>();
        for (ProxySessionSnapshot session : sessions) {
            clients.add(session.clientIdentity());
        }
        return clients.size();
    }

    private String clientIdentity() {
        return transport + "\n" + clientAddress;
    }

    private static String valueOrEmpty(String value) {
        return value == null ? "" : value;
    }
}
