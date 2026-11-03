package dev.pdalink.prototype;

import org.junit.Test;

import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ProxySessionStoreTest {
    @Test
    public void clientCountsSeparateActiveFromKnownClients() {
        ProxySessionStore store = new ProxySessionStore(4);
        store.begin("USB", "127.0.0.1", 100L);
        store.begin("USB", "127.0.0.1", 101L);
        ProxySessionStore.SessionHandle hotspotClient =
                store.begin("Local Hotspot", "192.168.43.20", 102L);
        store.finish(hotspotClient, 103L);

        List<ProxySessionSnapshot> sessions = store.snapshot();

        assertEquals(2, ProxySessionSnapshot.activeSessionCount(sessions));
        assertEquals(1, ProxySessionSnapshot.activeClientCount(sessions));
        assertEquals(2, ProxySessionSnapshot.knownClientCount(sessions));
    }

    @Test
    public void finishedSessionKeepsRequestTimingAndTraffic() {
        ProxySessionStore store = new ProxySessionStore(4);
        ProxySessionStore.SessionHandle handle = store.begin("Wi-Fi Direct", "192.168.49.2", 1_000L);
        store.updateRequest(handle, "CONNECT", "example.com:443");
        store.addTraffic(handle, 120L, 350L);
        store.finish(handle, 2_500L);

        ProxySessionSnapshot session = store.snapshot().get(0);

        assertFalse(session.isActive());
        assertEquals("CONNECT", session.method);
        assertEquals("example.com:443", session.destination);
        assertEquals(470L, session.totalBytes());
        assertEquals(1_500L, session.durationMillis(9_000L));
    }

    @Test
    public void recentHistoryIsBoundedAndNewestFirst() {
        ProxySessionStore store = new ProxySessionStore(2);
        for (int index = 1; index <= 3; index++) {
            ProxySessionStore.SessionHandle handle = store.begin("USB", "127.0.0.1", index * 100L);
            store.updateRequest(handle, "CONNECT", "host" + index + ":443");
            store.finish(handle, index * 100L + 50L);
        }

        List<ProxySessionSnapshot> sessions = store.snapshot();

        assertEquals(2, sessions.size());
        assertEquals("host3:443", sessions.get(0).destination);
        assertEquals("host2:443", sessions.get(1).destination);
    }

    @Test
    public void finishingTwiceDoesNotDuplicateHistory() {
        ProxySessionStore store = new ProxySessionStore(2);
        ProxySessionStore.SessionHandle handle = store.begin("USB", "127.0.0.1", 100L);

        store.finish(handle, 200L);
        store.finish(handle, 300L);

        assertEquals(1, store.snapshot().size());
        assertTrue(store.snapshot().get(0).endedAtMillis == 200L);
    }

    @Test
    public void excludedSessionIsRemovedWithoutEnteringHistory() {
        ProxySessionStore store = new ProxySessionStore(2);
        ProxySessionStore.SessionHandle handle = store.begin("USB", "127.0.0.1", 100L);
        store.updateRequest(handle, "GET", "PdaLink proxy setup");
        store.excludeFromHistory(handle);

        store.finish(handle, 200L);

        assertTrue(store.snapshot().isEmpty());
    }
}
