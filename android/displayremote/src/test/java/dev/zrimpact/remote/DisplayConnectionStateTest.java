package dev.zrimpact.remote;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class DisplayConnectionStateTest {
    @Test
    public void defaultIsRequestedOnceForEachConnection() {
        DisplayConnectionState state = new DisplayConnectionState();

        assertTrue(state.onAvailable());
        assertFalse(state.onAvailable());

        state.onLost();

        assertTrue(state.onAvailable());
        assertFalse(state.onAvailable());
    }
}
